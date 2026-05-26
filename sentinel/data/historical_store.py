"""
sentinel/data/historical_store.py
===================================
Historical OHLCV data storage and retrieval.

Sprint 2: Uses SQLite (zero setup, works immediately).
Sprint 7+: Migrate to PostgreSQL + TimescaleDB for production scale.

CRITICAL — Point-in-Time (PIT) Correctness:
Every query takes an `as_of` datetime. Data after that timestamp
is NEVER returned, even if it exists in the database.
This prevents lookahead bias in backtests.

The LookaheadBiasError is raised if code attempts to use future data.

Documented in: ARCHITECTURE_v5.md §7, FORENSIC_ANALYSIS_v5.md §2.4
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sentinel.core.errors import LookaheadBiasError
from sentinel.core.types import OHLCV, utc_now, validate_utc
from sentinel.data.kite_connector import KiteConnector
from sentinel.data.forex_connector import ForexConnector

logger = logging.getLogger(__name__)

DB_PATH = Path("sentinel_data.db")
_DB_INIT_LOCK = threading.RLock()
_DB_INITIALIZED = False


# ─────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with proper settings."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row          # Rows behave like dicts
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_read_connection() -> sqlite3.Connection:
    """Get a fast read-only SQLite connection for dashboard/query paths."""
    if not DB_PATH.exists():
        return get_connection()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_database() -> None:
    """
    Create all tables if they don't exist.
    Safe to call multiple times — uses IF NOT EXISTS.
    """
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return

    with _DB_INIT_LOCK:
        if _DB_INITIALIZED:
            return

        conn = get_read_connection()
        try:
            try:
                conn.execute("PRAGMA journal_mode=WAL") # Better concurrency
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                logger.warning("SQLite journal_mode setup skipped because database is busy.")
            conn.executescript("""
            -- OHLCV table for both equities and forex
            CREATE TABLE IF NOT EXISTS ohlcv (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                timestamp   TEXT NOT NULL,   -- ISO format UTC
                open        REAL NOT NULL,
                high        REAL NOT NULL,
                low         REAL NOT NULL,
                close       REAL NOT NULL,
                volume      INTEGER NOT NULL,
                timeframe   TEXT NOT NULL,   -- '1d','4h','1h','15m','5m'
                asset_type  TEXT NOT NULL,   -- 'equity','forex','commodity'
                delivery_pct REAL,           -- NSE equity only
                inserted_at TEXT NOT NULL,   -- when we stored it (UTC ISO)
                UNIQUE(symbol, timestamp, timeframe)
            );

            CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_ts
                ON ohlcv(symbol, timestamp, timeframe);

            -- Fundamental data (updated quarterly)
            CREATE TABLE IF NOT EXISTS fundamentals (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT NOT NULL,
                as_of_date      TEXT NOT NULL,   -- UTC ISO
                pe_ratio        REAL,
                sector_pe       REAL,
                roe_pct         REAL,
                roce_pct        REAL,
                debt_to_equity  REAL,
                revenue_growth_yoy_pct  REAL,
                ebitda_margin_pct       REAL,
                net_profit_margin_pct   REAL,
                promoter_holding_pct    REAL,
                promoter_pledging_pct   REAL,
                fii_holding_pct         REAL,
                dii_holding_pct         REAL,
                market_cap_cr           REAL,
                high_52w        REAL,
                low_52w         REAL,
                inserted_at     TEXT NOT NULL,
                UNIQUE(symbol, as_of_date)
            );

            CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol_date
                ON fundamentals(symbol, as_of_date);

            -- FII/DII daily flows
            CREATE TABLE IF NOT EXISTS fii_dii_flows (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT NOT NULL UNIQUE,   -- YYYY-MM-DD
                fii_buy_cr  REAL,
                fii_sell_cr REAL,
                fii_net_cr  REAL,
                dii_buy_cr  REAL,
                dii_sell_cr REAL,
                dii_net_cr  REAL,
                inserted_at TEXT NOT NULL
            );

            -- GSM/ASM surveillance list
            CREATE TABLE IF NOT EXISTS surveillance_list (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                list_type   TEXT NOT NULL,   -- 'GSM' or 'ASM'
                stage       TEXT,            -- GSM stage 1-6
                effective_date TEXT NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1,
                inserted_at TEXT NOT NULL,
                UNIQUE(symbol, list_type, effective_date)
            );

            -- Market events calendar
            CREATE TABLE IF NOT EXISTS market_calendar (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date  TEXT NOT NULL,
                event_time  TEXT,           -- UTC ISO or NULL if all-day
                currency    TEXT,           -- affected currency/country
                event_name  TEXT NOT NULL,
                impact      TEXT NOT NULL,  -- 'LOW','MEDIUM','HIGH','CRITICAL'
                consensus   TEXT,
                previous    TEXT,
                actual      TEXT,           -- NULL until released
                source      TEXT,
                inserted_at TEXT NOT NULL,
                UNIQUE(event_date, currency, event_name)
            );

            -- Data ingestion log (track what was fetched and when)
            CREATE TABLE IF NOT EXISTS ingestion_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source      TEXT NOT NULL,
                symbol      TEXT,
                data_type   TEXT NOT NULL,  -- 'ohlcv','fundamentals','fii_dii'
                from_date   TEXT,
                to_date     TEXT,
                records_count INTEGER,
                status      TEXT NOT NULL,  -- 'success','error'
                error_msg   TEXT,
                duration_ms INTEGER,
                inserted_at TEXT NOT NULL
            );
        """)
            conn.commit()
            _DB_INITIALIZED = True
            logger.info(f"Database initialized at {DB_PATH.absolute()}")
        finally:
            conn.close()


# ─────────────────────────────────────────────
# OHLCV STORAGE AND RETRIEVAL
# ─────────────────────────────────────────────

class HistoricalStore:
    """
    Stores and retrieves historical OHLCV data with PIT correctness.

    Usage:
        store = HistoricalStore()
        store.ingest_equity("RELIANCE", days=365)
        bars = store.get_ohlcv("RELIANCE", as_of=utc_now(), lookback_days=200)
    """

    def __init__(self) -> None:
        init_database()
        self.kite = KiteConnector()
        self.forex = ForexConnector()

    def ingest_equity(
        self,
        symbol: str,
        days: int = 365,
        timeframe: str = "day",
        force_refresh: bool = False,
    ) -> int:
        """
        Fetch and store historical equity data from Kite Connect.
        Returns number of new records inserted.
        """
        start_time = utc_now()
        logger.info(f"Ingesting {symbol} ({timeframe}, {days} days)...")

        try:
            bars = self.kite.get_historical(symbol, days=days, timeframe=timeframe)
            count = self._store_ohlcv(bars, asset_type="equity")
            self._log_ingestion("kite", symbol, "ohlcv", days, count, "success",
                                int((utc_now() - start_time).total_seconds() * 1000))
            logger.info(f"  {symbol}: {count} new bars stored")
            return count
        except Exception as e:
            self._log_ingestion("kite", symbol, "ohlcv", days, 0, "error", 0,
                                str(e))
            logger.error(f"  {symbol} ingestion failed: {e}")
            return 0

    def ingest_forex(
        self,
        pair: str,
        periods: int = 365,
        timeframe: str = "1day",
    ) -> int:
        """Fetch and store historical forex OHLCV data."""
        start_time = utc_now()
        logger.info(f"Ingesting forex {pair} ({timeframe}, {periods} bars)...")

        try:
            bars = self.forex.get_forex_ohlcv(pair, periods=periods, timeframe=timeframe)
            count = self._store_ohlcv(bars, asset_type="forex")
            self._log_ingestion("twelve_data", pair, "ohlcv", periods, count, "success",
                                int((utc_now() - start_time).total_seconds() * 1000))
            logger.info(f"  {pair}: {count} new bars stored")
            return count
        except Exception as e:
            self._log_ingestion("twelve_data", pair, "ohlcv", periods, 0, "error", 0, str(e))
            logger.error(f"  {pair} ingestion failed: {e}")
            return 0

    def ingest_nifty500_batch(self, timeframe: str = "day") -> dict[str, int]:
        """
        Ingest historical data for all Nifty 500 stocks.
        In mock mode: uses the 35 mock stocks.
        Returns dict of {symbol: records_inserted}.
        """
        from sentinel.data.mock_data import ALL_MOCK_STOCKS
        results = {}
        symbols = list(ALL_MOCK_STOCKS.keys())
        logger.info(f"Batch ingesting {len(symbols)} symbols...")

        for i, symbol in enumerate(symbols):
            count = self.ingest_equity(symbol, days=365, timeframe=timeframe)
            results[symbol] = count
            if (i + 1) % 10 == 0:
                logger.info(f"  Progress: {i+1}/{len(symbols)} symbols")

        total = sum(results.values())
        logger.info(f"Batch complete: {total} total records across {len(symbols)} symbols")
        return results

    def get_ohlcv(
        self,
        symbol: str,
        as_of: datetime,
        lookback_days: int = 365,
        timeframe: str = "day",
    ) -> list[OHLCV]:
        """
        Retrieve historical OHLCV bars with strict PIT correctness.

        CRITICAL: Only returns bars where timestamp <= as_of.
        Bars AFTER as_of are NEVER returned — this prevents lookahead bias.

        Args:
            symbol: Stock or forex symbol
            as_of: Point-in-time reference. No data after this is returned.
            lookback_days: How many calendar days back to fetch
            timeframe: '1d', '4h', '1h', '15m', '5m'

        Returns:
            List of OHLCV bars, oldest first, all timestamps <= as_of
        """
        validate_utc(as_of, f"HistoricalStore.get_ohlcv as_of for {symbol}")

        from_ts = (as_of - timedelta(days=lookback_days)).isoformat()
        to_ts = as_of.isoformat()

        conn = get_read_connection()
        try:
            rows = conn.execute("""
                SELECT symbol, timestamp, open, high, low, close, volume,
                       timeframe, delivery_pct
                FROM ohlcv
                WHERE symbol = ?
                  AND timeframe = ?
                  AND timestamp >= ?
                  AND timestamp <= ?
                ORDER BY timestamp ASC
            """, (symbol, timeframe, from_ts, to_ts)).fetchall()

            bars = []
            for row in rows:
                ts = datetime.fromisoformat(row["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)

                # Double-check PIT correctness — should never trigger
                # but this is the safety net
                if ts > as_of:
                    raise LookaheadBiasError(
                        feature_name=f"ohlcv.{symbol}",
                        data_timestamp=ts.isoformat(),
                        as_of_timestamp=as_of.isoformat(),
                    )

                bars.append(OHLCV(
                    symbol=row["symbol"],
                    timestamp=ts,
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=int(row["volume"]),
                    timeframe=row["timeframe"],
                    delivery_pct=row["delivery_pct"],
                ))

            return bars

        finally:
            conn.close()

    def get_latest_close(self, symbol: str, as_of: datetime) -> Optional[Decimal]:
        """Get the most recent closing price as of the given timestamp."""
        bars = self.get_ohlcv(symbol, as_of=as_of, lookback_days=5)
        return bars[-1].close if bars else None

    def get_available_symbols(self, timeframe: str = "day") -> list[str]:
        """List all symbols that have data in the store."""
        conn = get_read_connection()
        try:
            rows = conn.execute("""
                SELECT DISTINCT symbol FROM ohlcv
                WHERE timeframe = ?
                ORDER BY symbol
            """, (timeframe,)).fetchall()
            return [r["symbol"] for r in rows]
        finally:
            conn.close()

    def get_data_coverage(self, symbol: str, timeframe: str = "day") -> dict:
        """Returns date range and count of bars stored for a symbol."""
        conn = get_connection()
        try:
            row = conn.execute("""
                SELECT MIN(timestamp) as earliest,
                       MAX(timestamp) as latest,
                       COUNT(*) as total_bars
                FROM ohlcv
                WHERE symbol = ? AND timeframe = ?
            """, (symbol, timeframe)).fetchone()

            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "earliest": row["earliest"],
                "latest": row["latest"],
                "total_bars": row["total_bars"] or 0,
            }
        finally:
            conn.close()

    def is_data_fresh(
        self,
        symbol: str,
        max_age_hours: float = 24,
        timeframe: str = "day",
    ) -> bool:
        """Check if stored data for a symbol is fresh enough."""
        coverage = self.get_data_coverage(symbol, timeframe)
        if not coverage["latest"]:
            return False
        latest = datetime.fromisoformat(coverage["latest"])
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_hours = (utc_now() - latest).total_seconds() / 3600
        return age_hours <= max_age_hours

    def _store_ohlcv(self, bars: list[OHLCV], asset_type: str = "equity") -> int:
        """Store OHLCV bars, skipping duplicates. Returns count of new records."""
        if not bars:
            return 0

        now_str = utc_now().isoformat()
        records = [
            (
                b.symbol,
                b.timestamp.isoformat(),
                float(b.open),
                float(b.high),
                float(b.low),
                float(b.close),
                b.volume,
                b.timeframe,
                asset_type,
                b.delivery_pct,
                now_str,
            )
            for b in bars
        ]

        conn = get_connection()
        try:
            before = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
            conn.executemany("""
                INSERT OR IGNORE INTO ohlcv
                (symbol, timestamp, open, high, low, close, volume,
                 timeframe, asset_type, delivery_pct, inserted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            conn.commit()
            after = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
            return after - before
        finally:
            conn.close()

    def _log_ingestion(
        self,
        source: str,
        symbol: Optional[str],
        data_type: str,
        records: int,
        count: int,
        status: str,
        duration_ms: int,
        error: str = "",
    ) -> None:
        now_str = utc_now().isoformat()
        conn = get_connection()
        try:
            conn.execute("""
                INSERT INTO ingestion_log
                (source, symbol, data_type, to_date, records_count,
                 status, error_msg, duration_ms, inserted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (source, symbol, data_type, now_str, count,
                  status, error or None, duration_ms, now_str))
            conn.commit()
        finally:
            conn.close()
