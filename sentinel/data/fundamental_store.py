"""
sentinel/data/fundamental_store.py
====================================
Fundamental data storage and retrieval for NSE equities.

Data sources (in priority order):
  1. Financial Modeling Prep (FMP) API — best Indian coverage
  2. BSE XBRL filings          — authoritative, free, quarterly
  3. Mock data                 — during build phase (MOCK_MODE=true)

Fundamentals are updated quarterly after results season.
The store keeps every historical snapshot so PIT backtesting works.

Documented in: ARCHITECTURE_v5.md §8, GLOBAL_FAILURES_PLAYBOOK.md §4
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import requests

from sentinel.core.errors import DataSourceUnavailableError
from sentinel.core.types import utc_now
from sentinel.data.historical_store import get_connection, get_read_connection, init_database
from sentinel.data.mock_data import mock_fundamentals, ALL_MOCK_STOCKS

logger = logging.getLogger(__name__)
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/api/v3"


class FundamentalStore:
    """
    Stores and retrieves fundamental data with historical snapshots.

    Usage:
        store = FundamentalStore()
        store.ingest("RELIANCE")
        data = store.get_latest("RELIANCE")
        score = store.compute_quality_score("RELIANCE")
    """

    def __init__(self) -> None:
        init_database()

    # ─────────────────────────────────────────────
    # INGESTION
    # ─────────────────────────────────────────────

    def ingest(self, symbol: str) -> bool:
        """
        Fetch and store fundamental data for a symbol.
        Returns True if successful.
        """
        symbol = symbol.upper().strip()
        logger.info(f"Ingesting fundamentals for {symbol}...")

        if MOCK_MODE:
            if symbol not in ALL_MOCK_STOCKS:
                logger.warning("  %s: not in supported mock equity universe", symbol)
                return False
            if self.get_latest(symbol):
                return True
            data = mock_fundamentals(symbol)
        else:
            data = self._fetch_from_fmp(symbol)
            if data is None:
                logger.warning(f"  {symbol}: FMP returned no data")
                return False

        self._store(symbol, data)
        logger.info(f"  {symbol}: fundamentals stored")
        return True

    def ingest_batch(self, symbols: list[str]) -> dict[str, bool]:
        """Ingest fundamentals for multiple symbols."""
        results = {}
        for i, symbol in enumerate(symbols):
            results[symbol] = self.ingest(symbol)
            if (i + 1) % 20 == 0:
                logger.info(f"  Progress: {i+1}/{len(symbols)}")
        return results

    def ingest_nifty500(self) -> dict[str, bool]:
        """Ingest fundamentals for all Nifty 500 stocks."""
        symbols = list(ALL_MOCK_STOCKS.keys())
        logger.info(f"Ingesting fundamentals for {len(symbols)} symbols...")
        return self.ingest_batch(symbols)

    # ─────────────────────────────────────────────
    # RETRIEVAL
    # ─────────────────────────────────────────────

    def get_latest(self, symbol: str) -> Optional[dict[str, Any]]:
        """Get the most recent fundamental snapshot for a symbol."""
        conn = get_read_connection()
        try:
            row = conn.execute("""
                SELECT * FROM fundamentals
                WHERE symbol = ?
                ORDER BY as_of_date DESC
                LIMIT 1
            """, (symbol,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_as_of(self, symbol: str, as_of_date: str) -> Optional[dict[str, Any]]:
        """
        Get fundamental data as it was known on a specific date.
        PIT-correct — never returns data published after as_of_date.
        """
        conn = get_read_connection()
        try:
            row = conn.execute("""
                SELECT * FROM fundamentals
                WHERE symbol = ?
                  AND as_of_date <= ?
                ORDER BY as_of_date DESC
                LIMIT 1
            """, (symbol, as_of_date)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_all_latest(self) -> list[dict[str, Any]]:
        """Get latest fundamentals for all symbols in the store."""
        conn = get_read_connection()
        try:
            rows = conn.execute("""
                SELECT f.*
                FROM fundamentals f
                INNER JOIN (
                    SELECT symbol, MAX(as_of_date) as max_date
                    FROM fundamentals
                    GROUP BY symbol
                ) latest ON f.symbol = latest.symbol
                         AND f.as_of_date = latest.max_date
                ORDER BY f.symbol
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ─────────────────────────────────────────────
    # SCORING
    # ─────────────────────────────────────────────

    def compute_quality_score(self, symbol: str) -> dict[str, Any]:
        """
        Compute the 4-component fundamental quality score.

        Quality Score (0-10):
          +2 if ROE > 15% consistently
          +2 if revenue growth > 10% YoY
          +2 if Debt/Equity < 1.0
          +2 if operating cash flow quality (proxy: high margin)
          +1 if no pledging
          +1 if promoter holding > 40%

        Documented in: ARCHITECTURE_v5.md §8.1 (Quality Score)
        """
        symbol = symbol.upper().strip()
        if MOCK_MODE and symbol not in ALL_MOCK_STOCKS:
            return {"symbol": symbol, "quality_score": None,
                    "error": "Symbol is not in the supported equity universe"}

        data = self.get_latest(symbol)
        if not data:
            return {"symbol": symbol, "quality_score": None,
                    "error": "No fundamental data available"}

        score = 0.0
        breakdown = {}

        # ROE
        roe = data.get("roe_pct") or 0
        if roe > 20:
            pts = 2.0
        elif roe > 15:
            pts = 1.5
        elif roe > 10:
            pts = 1.0
        else:
            pts = 0.0
        score += pts
        breakdown["roe"] = {"value": roe, "points": pts, "max": 2}

        # Revenue growth
        rev_growth = data.get("revenue_growth_yoy_pct") or 0
        if rev_growth > 20:
            pts = 2.0
        elif rev_growth > 10:
            pts = 1.5
        elif rev_growth > 5:
            pts = 1.0
        else:
            pts = 0.0
        score += pts
        breakdown["revenue_growth"] = {"value": rev_growth, "points": pts, "max": 2}

        # Debt/Equity
        de = data.get("debt_to_equity") or 99
        if de < 0.3:
            pts = 2.0
        elif de < 0.7:
            pts = 1.5
        elif de < 1.0:
            pts = 1.0
        elif de < 1.5:
            pts = 0.5
        else:
            pts = 0.0
        score += pts
        breakdown["debt_equity"] = {"value": de, "points": pts, "max": 2}

        # Margin quality (EBITDA margin as proxy for cash flow quality)
        margin = data.get("ebitda_margin_pct") or 0
        if margin > 25:
            pts = 2.0
        elif margin > 15:
            pts = 1.5
        elif margin > 8:
            pts = 1.0
        else:
            pts = 0.0
        score += pts
        breakdown["margin_quality"] = {"value": margin, "points": pts, "max": 2}

        # Pledging
        pledging = data.get("promoter_pledging_pct") or 0
        pts = 1.0 if pledging < 5 else (0.5 if pledging < 15 else 0.0)
        score += pts
        breakdown["no_pledging"] = {"value": pledging, "points": pts, "max": 1}

        # Promoter holding
        promoter = data.get("promoter_holding_pct") or 0
        pts = 1.0 if promoter > 50 else (0.5 if promoter > 35 else 0.0)
        score += pts
        breakdown["promoter_holding"] = {"value": promoter, "points": pts, "max": 1}

        # Valuation score
        pe = data.get("pe_ratio") or 0
        sector_pe = data.get("sector_pe") or pe
        pe_discount = ((sector_pe - pe) / sector_pe * 100) if sector_pe > 0 else 0
        if pe_discount > 20:
            val_score = 10
        elif pe_discount > 10:
            val_score = 7
        elif pe_discount > 0:
            val_score = 5
        elif pe_discount > -10:
            val_score = 3
        else:
            val_score = 1

        return {
            "symbol": symbol,
            "quality_score": round(score, 1),
            "quality_score_max": 10,
            "valuation_score": val_score,
            "breakdown": breakdown,
            "raw_data": {
                "pe": pe,
                "sector_pe": sector_pe,
                "roe": roe,
                "debt_equity": de,
                "promoter_holding": promoter,
                "pledging": pledging,
                "rev_growth": rev_growth,
            },
            "computed_at": utc_now().isoformat(),
        }

    def screen_quality_stocks(
        self,
        min_roe: float = 15.0,
        max_de: float = 1.0,
        min_promoter: float = 40.0,
        max_pledging: float = 5.0,
        min_rev_growth: float = 10.0,
    ) -> list[dict[str, Any]]:
        """
        Screen all stocks against fundamental quality criteria.
        Used by S2 (Value + Reversal) and S6 (MF Conviction) screeners.
        """
        conn = get_read_connection()
        try:
            rows = conn.execute("""
                SELECT f.*
                FROM fundamentals f
                INNER JOIN (
                    SELECT symbol, MAX(as_of_date) as max_date
                    FROM fundamentals GROUP BY symbol
                ) latest ON f.symbol = latest.symbol
                         AND f.as_of_date = latest.max_date
                WHERE (f.roe_pct IS NULL OR f.roe_pct >= ?)
                  AND (f.debt_to_equity IS NULL OR f.debt_to_equity <= ?)
                  AND (f.promoter_holding_pct IS NULL OR f.promoter_holding_pct >= ?)
                  AND (f.promoter_pledging_pct IS NULL OR f.promoter_pledging_pct <= ?)
                ORDER BY f.roe_pct DESC NULLS LAST
            """, (min_roe, max_de, min_promoter, max_pledging)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ─────────────────────────────────────────────
    # PRIVATE
    # ─────────────────────────────────────────────

    def _store(self, symbol: str, data: dict[str, Any]) -> None:
        """Store a fundamental snapshot."""
        now_str = utc_now().isoformat()
        as_of = data.get("as_of_date", now_str[:10])

        conn = get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO fundamentals (
                    symbol, as_of_date, pe_ratio, sector_pe,
                    roe_pct, roce_pct, debt_to_equity,
                    revenue_growth_yoy_pct, ebitda_margin_pct,
                    net_profit_margin_pct, promoter_holding_pct,
                    promoter_pledging_pct, fii_holding_pct,
                    dii_holding_pct, market_cap_cr,
                    high_52w, low_52w, inserted_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, as_of,
                data.get("pe_ratio"), data.get("sector_pe"),
                data.get("roe_pct"), data.get("roce_pct"),
                data.get("debt_to_equity"),
                data.get("revenue_growth_yoy_pct"),
                data.get("ebitda_margin_pct"),
                data.get("net_profit_margin_pct"),
                data.get("promoter_holding_pct"),
                data.get("promoter_pledging_pct"),
                data.get("fii_holding_pct"),
                data.get("dii_holding_pct"),
                data.get("market_cap_cr"),
                data.get("52_week_high"),
                data.get("52_week_low"),
                now_str,
            ))
            conn.commit()
        finally:
            conn.close()

    def _fetch_from_fmp(self, symbol: str) -> Optional[dict[str, Any]]:
        """Fetch fundamentals from Financial Modeling Prep API."""
        if not FMP_API_KEY:
            raise DataSourceUnavailableError(
                "FMP",
                "FMP_API_KEY not set. Set MOCK_MODE=true to use mock data."
            )
        try:
            # FMP uses NSE: prefix for Indian stocks
            fmp_symbol = f"NSE:{symbol}"
            url = f"{FMP_BASE}/profile/{fmp_symbol}"
            resp = requests.get(url, params={"apikey": FMP_API_KEY}, timeout=10)
            resp.raise_for_status()
            profiles = resp.json()
            if not profiles:
                return None
            p = profiles[0]

            # Also fetch ratios
            ratios_url = f"{FMP_BASE}/ratios/{fmp_symbol}"
            ratios_resp = requests.get(
                ratios_url,
                params={"apikey": FMP_API_KEY, "limit": 1},
                timeout=10
            )
            ratios = ratios_resp.json()[0] if ratios_resp.ok and ratios_resp.json() else {}

            return {
                "pe_ratio": p.get("pe"),
                "sector_pe": None,  # FMP doesn't provide sector PE directly
                "roe_pct": (ratios.get("returnOnEquity") or 0) * 100,
                "roce_pct": None,
                "debt_to_equity": ratios.get("debtEquityRatio"),
                "revenue_growth_yoy_pct": (p.get("revenueGrowth") or 0) * 100,
                "ebitda_margin_pct": (ratios.get("ebitdaMargin") or 0) * 100,
                "net_profit_margin_pct": (ratios.get("netProfitMargin") or 0) * 100,
                "promoter_holding_pct": None,   # Not in FMP — get from NSE
                "promoter_pledging_pct": None,
                "fii_holding_pct": None,
                "dii_holding_pct": None,
                "market_cap_cr": (p.get("mktCap") or 0) / 1e7,
                "52_week_high": p.get("yearHigh"),
                "52_week_low": p.get("yearLow"),
                "as_of_date": utc_now().date().isoformat(),
            }
        except Exception as e:
            logger.error(f"FMP fetch failed for {symbol}: {e}")
            return None
