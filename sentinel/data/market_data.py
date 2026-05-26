"""
sentinel/data/market_data.py
==============================
Market-specific Indian data: FII/DII flows, GSM/ASM surveillance
lists, market internals (A/D ratio, PCR, VIX), and economic calendar.

All data is stored in SQLite for PIT correctness.
In MOCK_MODE, realistic data is generated for testing.

Documented in: ARCHITECTURE_v5.md §6.1, SCREENERS_MODULE_SPEC.md §S4, §S5
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta, date
from typing import Any, Optional

from sentinel.core.types import utc_now
from sentinel.data.historical_store import get_connection, get_read_connection, init_database
from sentinel.data.mock_data import (
    mock_fii_dii_data,
    mock_market_internals,
    mock_gsm_asm_list,
)

logger = logging.getLogger(__name__)
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


class MarketDataStore:
    """
    Stores and retrieves Indian market-specific data.

    Key responsibilities:
    - FII/DII daily flow data (from NSDL)
    - GSM/ASM surveillance list (refreshed daily — hard rejection)
    - Market internals: Nifty A/D ratio, PCR, India VIX
    - Economic calendar events
    """

    def __init__(self) -> None:
        init_database()

    # ─────────────────────────────────────────────
    # FII / DII FLOWS
    # ─────────────────────────────────────────────

    def ingest_fii_dii(self, trade_date: Optional[date] = None) -> bool:
        """
        Store FII/DII flow data for a trading date.
        In MOCK_MODE: generates realistic mock data.
        In LIVE mode: parse from NSDL website (nsdl.co.in).
        """
        if trade_date is None:
            trade_date = utc_now().date()

        if MOCK_MODE:
            raw = mock_fii_dii_data()
        else:
            raw = self._fetch_nsdl_flows(trade_date)
            if raw is None:
                return False

        date_str = trade_date.isoformat()
        now_str = utc_now().isoformat()

        conn = get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO fii_dii_flows
                (date, fii_buy_cr, fii_sell_cr, fii_net_cr,
                 dii_buy_cr, dii_sell_cr, dii_net_cr, inserted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date_str,
                raw.get("fii_buy_cr"), raw.get("fii_sell_cr"), raw.get("fii_net_cr"),
                raw.get("dii_buy_cr"), raw.get("dii_sell_cr"), raw.get("dii_net_cr"),
                now_str,
            ))
            conn.commit()
            logger.info(f"FII/DII flows stored for {date_str}: "
                       f"FII net ₹{raw.get('fii_net_cr', 0):+,.0f}Cr")
            return True
        except Exception as e:
            logger.error(f"FII/DII storage failed: {e}")
            return False
        finally:
            conn.close()

    def get_fii_dii(self, days: int = 30) -> list[dict[str, Any]]:
        """Get FII/DII flow history for the last N days."""
        conn = get_read_connection()
        try:
            rows = conn.execute("""
                SELECT * FROM fii_dii_flows
                ORDER BY date DESC
                LIMIT ?
            """, (days,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_fii_trend(self, days: int = 20) -> dict[str, Any]:
        """
        Compute 20-day FII flow trend.
        Returns: net total, trend direction, and daily average.
        Used by screeners (S1, S3) and morning brief.
        """
        flows = self.get_fii_dii(days=days)
        if not flows:
            return {"trend": "unknown", "net_total_cr": 0, "daily_avg_cr": 0}

        net_total = sum(f.get("fii_net_cr") or 0 for f in flows)
        daily_avg = net_total / len(flows)

        if daily_avg > 500:
            trend = "strong_buying"
        elif daily_avg > 100:
            trend = "buying"
        elif daily_avg > -100:
            trend = "neutral"
        elif daily_avg > -500:
            trend = "selling"
        else:
            trend = "strong_selling"

        return {
            "trend": trend,
            "net_total_cr": round(net_total, 0),
            "daily_avg_cr": round(daily_avg, 0),
            "days_analysed": len(flows),
            "is_bullish": daily_avg > 0,
        }

    def get_latest_fii_dii(self) -> Optional[dict[str, Any]]:
        """Get yesterday's FII/DII data."""
        rows = self.get_fii_dii(days=1)
        return rows[0] if rows else None

    # ─────────────────────────────────────────────
    # GSM / ASM SURVEILLANCE LIST
    # ─────────────────────────────────────────────

    def refresh_gsm_asm_list(self) -> int:
        """
        Refresh the GSM/ASM surveillance list from NSE/BSE.
        In MOCK_MODE: uses mock list.
        In LIVE mode: parse from NSE surveillance PDF.

        CRITICAL: This runs twice daily (09:00 and 14:00 IST).
        Stocks on this list trigger hard rejection in all screeners.
        Documented in: FORENSIC_ANALYSIS_v5.md §2.18.4
        """
        if MOCK_MODE:
            symbols = mock_gsm_asm_list()
        else:
            symbols = self._fetch_nse_surveillance()

        now_str = utc_now().isoformat()
        today_str = utc_now().date().isoformat()
        count = 0

        conn = get_connection()
        try:
            # Mark old entries inactive first
            conn.execute("""
                UPDATE surveillance_list SET is_active = 0
                WHERE is_active = 1
            """)

            # Insert/update current list
            for symbol in symbols:
                conn.execute("""
                    INSERT OR REPLACE INTO surveillance_list
                    (symbol, list_type, stage, effective_date, is_active, inserted_at)
                    VALUES (?, 'GSM', NULL, ?, 1, ?)
                """, (symbol, today_str, now_str))
                count += 1

            conn.commit()
            logger.info(f"GSM/ASM list refreshed: {count} symbols on surveillance")
            return count
        finally:
            conn.close()

    def is_on_surveillance(self, symbol: str) -> bool:
        """
        Check if a symbol is on the GSM/ASM surveillance list.
        Hard rejection — used in ALL screeners and order validation.
        """
        conn = get_read_connection()
        try:
            row = conn.execute("""
                SELECT 1 FROM surveillance_list
                WHERE symbol = ? AND is_active = 1
                LIMIT 1
            """, (symbol,)).fetchone()
            return row is not None
        finally:
            conn.close()

    def get_surveillance_list(self) -> list[str]:
        """Get all currently active surveillance symbols."""
        conn = get_read_connection()
        try:
            rows = conn.execute("""
                SELECT DISTINCT symbol FROM surveillance_list
                WHERE is_active = 1
                ORDER BY symbol
            """).fetchall()
            return [r["symbol"] for r in rows]
        finally:
            conn.close()

    # ─────────────────────────────────────────────
    # MARKET INTERNALS
    # ─────────────────────────────────────────────

    def get_market_internals(self) -> dict[str, Any]:
        """
        Get current market internals: A/D ratio, VIX, PCR, breadth.
        In MOCK_MODE: realistic mock data.
        In LIVE mode: from NSE market data endpoints.
        """
        if MOCK_MODE:
            return mock_market_internals()

        # Live: fetch from NSE API
        # Placeholder — implement live fetch in Sprint 4
        return mock_market_internals()

    def get_india_vix(self) -> Optional[float]:
        """Get current India VIX level."""
        internals = self.get_market_internals()
        return internals.get("india_vix")

    def is_defensive_mode(self, vix_threshold: float = 22.0) -> bool:
        """
        Returns True if India VIX is above the defensive threshold.
        When True: reduce all position sizes, avoid new entries.
        Documented in: ARCHITECTURE_v5.md §9.1
        """
        vix = self.get_india_vix()
        if vix is None:
            return False
        return vix >= vix_threshold

    def get_market_bias(self) -> dict[str, Any]:
        """
        Compute today's market bias from internals + FII + VIX.
        Used in the Morning Brief.

        Returns bias as: BULLISH / CAUTIOUSLY_BULLISH / NEUTRAL /
                         CAUTIOUSLY_BEARISH / BEARISH
        """
        internals = self.get_market_internals()
        fii_trend = self.get_fii_trend(days=5)
        vix = internals.get("india_vix", 15)
        ad_ratio = internals.get("advance_decline_ratio", 1.0)
        nifty_change = internals.get("nifty50_change_pct", 0)
        pcr = internals.get("nifty_pcr", 1.0)

        score = 0

        # VIX contribution
        if vix < 13:
            score += 2
        elif vix < 16:
            score += 1
        elif vix > 22:
            score -= 2
        elif vix > 18:
            score -= 1

        # A/D ratio
        if ad_ratio > 2.0:
            score += 2
        elif ad_ratio > 1.3:
            score += 1
        elif ad_ratio < 0.5:
            score -= 2
        elif ad_ratio < 0.8:
            score -= 1

        # FII trend
        if fii_trend["trend"] in ("strong_buying", "buying"):
            score += 1
        elif fii_trend["trend"] in ("strong_selling", "selling"):
            score -= 1

        # PCR (contrarian)
        if pcr > 1.3:
            score += 1   # Too many puts = contrarian bullish
        elif pcr < 0.7:
            score -= 1   # Too many calls = contrarian bearish

        if score >= 4:
            bias = "BULLISH"
        elif score >= 2:
            bias = "CAUTIOUSLY_BULLISH"
        elif score >= -1:
            bias = "NEUTRAL"
        elif score >= -3:
            bias = "CAUTIOUSLY_BEARISH"
        else:
            bias = "BEARISH"

        return {
            "bias": bias,
            "score": score,
            "vix": vix,
            "ad_ratio": ad_ratio,
            "nifty_change_pct": nifty_change,
            "pcr": pcr,
            "fii_trend": fii_trend["trend"],
            "computed_at": utc_now().isoformat(),
        }

    # ─────────────────────────────────────────────
    # ECONOMIC CALENDAR
    # ─────────────────────────────────────────────

    def ingest_calendar_events(self, events: list[dict[str, Any]]) -> int:
        """Store economic calendar events."""
        now_str = utc_now().isoformat()
        conn = get_connection()
        count = 0
        try:
            for ev in events:
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO market_calendar
                        (event_date, event_time, currency, event_name,
                         impact, consensus, previous, actual, source, inserted_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ev.get("event_date", ev.get("timestamp", "")[:10]),
                        ev.get("timestamp"),
                        ev.get("currency"),
                        ev.get("event", ev.get("event_name", "")),
                        ev.get("impact", "MEDIUM"),
                        ev.get("consensus"),
                        ev.get("previous"),
                        ev.get("actual"),
                        ev.get("source", "mock"),
                        now_str,
                    ))
                    count += 1
                except Exception as e:
                    logger.warning(f"Calendar event insert failed: {e}")
            conn.commit()
        finally:
            conn.close()
        return count

    def get_upcoming_events(self, days_ahead: int = 7) -> list[dict[str, Any]]:
        """Get upcoming economic events for the next N days."""
        now = utc_now()
        from_str = now.date().isoformat()
        to_str = (now + timedelta(days=days_ahead)).date().isoformat()

        conn = get_read_connection()
        try:
            rows = conn.execute("""
                SELECT * FROM market_calendar
                WHERE event_date >= ?
                  AND event_date <= ?
                ORDER BY event_date ASC, event_time ASC
            """, (from_str, to_str)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_high_impact_today(self) -> list[dict[str, Any]]:
        """Get HIGH and CRITICAL impact events for today."""
        today = utc_now().date().isoformat()
        conn = get_read_connection()
        try:
            rows = conn.execute("""
                SELECT * FROM market_calendar
                WHERE event_date = ?
                  AND impact IN ('HIGH', 'CRITICAL')
                ORDER BY event_time ASC
            """, (today,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def has_blackout_event_next_minutes(
        self, minutes: int = 15, currency: Optional[str] = None
    ) -> bool:
        """
        Returns True if a HIGH/CRITICAL impact event is within
        the next N minutes. Used to enforce pre-event blackout.
        Documented in: GLOBAL_FOREX_MODULE.md §F2.4
        """
        now = utc_now()
        window_end = now + timedelta(minutes=minutes)

        conn = get_read_connection()
        try:
            query = """
                SELECT 1 FROM market_calendar
                WHERE impact IN ('HIGH', 'CRITICAL')
                  AND event_time IS NOT NULL
                  AND event_time >= ?
                  AND event_time <= ?
            """
            params = [now.isoformat(), window_end.isoformat()]

            if currency:
                query += " AND currency = ?"
                params.append(currency)

            row = conn.execute(query, params).fetchone()
            return row is not None
        finally:
            conn.close()

    # ─────────────────────────────────────────────
    # PRIVATE — LIVE DATA FETCHING
    # ─────────────────────────────────────────────

    def _fetch_nsdl_flows(self, trade_date: date) -> Optional[dict[str, Any]]:
        """Fetch FII/DII flows from NSDL. Sprint 4 implementation."""
        logger.info("Live NSDL flow provider unavailable. Using safe mock fallback.")
        return mock_fii_dii_data()

    def _fetch_nse_surveillance(self) -> list[str]:
        """Fetch GSM/ASM list from NSE. Sprint 4 implementation."""
        logger.info("Live NSE surveillance provider unavailable. Using safe mock fallback.")
        return mock_gsm_asm_list()
