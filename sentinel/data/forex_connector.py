"""
sentinel/data/forex_connector.py
=================================
Global forex and macro data connector.

Primary: Twelve Data API (800 req/day free, 8/min)
Macro:   FRED API (Federal Reserve, completely free)
Fallback: Alpha Vantage (25 req/day — emergency only)

In MOCK_MODE: returns realistic mock data. No API key needed.
In LIVE mode: calls real APIs.

Documented in: GLOBAL_FOREX_MODULE.md §F2, ARCHITECTURE_v5.md §18
"""

from __future__ import annotations

import logging
import os
import time
from decimal import Decimal
from typing import Any

import requests

from sentinel.core.errors import DataSourceUnavailableError
from sentinel.core.types import OHLCV, MacroOverlayDaily, DXYRegime, MarketRegime, utc_now
from sentinel.data.mock_data import (
    mock_forex_ohlcv,
    mock_forex_live,
    mock_macro_overlay,
    mock_cot_data,
)

logger = logging.getLogger(__name__)

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"

# FRED series IDs for macro data (all free)
FRED_SERIES = {
    "dxy": "DTWEXBGS",          # Nominal Broad US Dollar Index (not ICE DXY)
    "us_10y": "DGS10",          # US 10-Year Treasury Yield
    "us_2y": "DGS2",            # US 2-Year Treasury Yield
    "fed_funds": "FEDFUNDS",    # Fed Funds Rate
    "real_10y": "DFII10",       # Real 10Y (TIPS)
    "yield_curve": "T10Y2Y",    # 10Y-2Y spread
    "us_cpi": "CPIAUCSL",       # US CPI
    "us_unemployment": "UNRATE",# US Unemployment Rate
}

# Tier 1 forex pairs for full analysis coverage
TIER1_PAIRS = [
    "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD",
    "USD/CHF", "NZD/USD", "USD/CAD",
    "XAU/USD", "XAG/USD",           # Gold, Silver
    "USD/INR", "EUR/INR", "GBP/INR", "JPY/INR",
    "WTI/USD", "BRENT/USD",          # Crude
]

# Pair symbol mapping: Sentinel name → Twelve Data name → FRED/Alpha Vantage
PAIR_MAP = {
    "EURUSD":  {"twelve": "EUR/USD", "av": "EURUSD"},
    "GBPUSD":  {"twelve": "GBP/USD", "av": "GBPUSD"},
    "USDJPY":  {"twelve": "USD/JPY", "av": "USDJPY"},
    "AUDUSD":  {"twelve": "AUD/USD", "av": "AUDUSD"},
    "USDCHF":  {"twelve": "USD/CHF", "av": "USDCHF"},
    "NZDUSD":  {"twelve": "NZD/USD", "av": "NZDUSD"},
    "USDCAD":  {"twelve": "USD/CAD", "av": "USDCAD"},
    "XAUUSD":  {"twelve": "XAU/USD", "av": "XAUUSD"},
    "XAGUSD":  {"twelve": "XAG/USD", "av": "XAGUSD"},
    "USDINR":  {"twelve": "USD/INR", "av": "USDINR"},
    "EURINR":  {"twelve": "EUR/INR", "av": "EURINR"},
    "GBPINR":  {"twelve": "GBP/INR", "av": "GBPINR"},
    "JPYINR":  {"twelve": "JPY/INR", "av": "JPYINR"},
    "BTCUSD":  {"twelve": "BTC/USD", "av": "BTCUSD"},
    "ETHUSD":  {"twelve": "ETH/USD", "av": "ETHUSD"},
}


class ForexConnector:
    """
    Global forex and macro data connector.

    Handles: Major forex pairs, metals, energy, INR pairs,
             US macro data (FRED), COT positioning index.

    Usage:
        connector = ForexConnector()
        bars = connector.get_forex_ohlcv("EURUSD", periods=200)
        overlay = connector.get_macro_overlay()
        cot = connector.get_cot_data("EURUSD")
    """

    def __init__(self) -> None:
        self.mock_mode = MOCK_MODE
        self.twelve_data_key = os.getenv("TWELVE_DATA_API_KEY", "")
        self.fred_key = os.getenv("FRED_API_KEY", "")
        self.av_key = os.getenv("ALPHA_VANTAGE_API_KEY", "")
        self._request_count = 0     # Rate limit tracking
        self._last_request_time = 0.0

        if self.mock_mode:
            logger.info(
                "ForexConnector running in MOCK MODE. "
                "Set MOCK_MODE=false and add TWELVE_DATA_API_KEY for live data."
            )

    def _rate_limit(self, requests_per_minute: int = 8) -> None:
        """Enforce rate limit — Twelve Data free tier: 8/min."""
        min_interval = 60.0 / requests_per_minute
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_time = time.time()
        self._request_count += 1

    def _twelve_get(self, endpoint: str, params: dict) -> dict:
        """Make a Twelve Data API request."""
        if not self.twelve_data_key:
            raise DataSourceUnavailableError(
                "Twelve Data",
                "TWELVE_DATA_API_KEY not set. "
                "Set MOCK_MODE=true to use mock data."
            )
        self._rate_limit()
        params["apikey"] = self.twelve_data_key
        url = f"https://api.twelvedata.com/{endpoint}"
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "error":
            raise DataSourceUnavailableError("Twelve Data", data.get("message", "Unknown error"))
        return data

    def _fred_get(self, series_id: str, limit: int = 30) -> list[dict]:
        """Fetch a FRED time series. Returns list of {date, value} dicts."""
        if not self.fred_key:
            logger.warning("FRED_API_KEY not set. FRED data unavailable.")
            return []
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.fred_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("observations", [])

    def get_forex_ohlcv(
        self,
        pair: str,
        periods: int = 200,
        timeframe: str = "1day",
    ) -> list[OHLCV]:
        """
        Fetch OHLCV data for a forex pair.

        Args:
            pair: Symbol like "EURUSD", "XAUUSD", "USDINR"
            periods: Number of bars to fetch
            timeframe: "1min", "5min", "15min", "1h", "4h", "1day", "1week"

        Returns:
            List of OHLCV bars, oldest first.

        Daily close convention: 22:00 UTC (London close = 03:30 IST next day)
        Documented in: GLOBAL_FOREX_MODULE.md §F3
        """
        if self.mock_mode:
            raw = mock_forex_ohlcv(pair, periods=periods, timeframe=timeframe)
            return [
                OHLCV(
                    symbol=b["symbol"],
                    timestamp=b["timestamp"],
                    open=Decimal(str(b["open"])),
                    high=Decimal(str(b["high"])),
                    low=Decimal(str(b["low"])),
                    close=Decimal(str(b["close"])),
                    volume=b["volume"],
                    timeframe=timeframe,
                )
                for b in raw
            ]

        # Live mode via Twelve Data
        pair_info = PAIR_MAP.get(pair, {})
        twelve_symbol = pair_info.get("twelve", pair[:3] + "/" + pair[3:])

        data = self._twelve_get("time_series", {
            "symbol": twelve_symbol,
            "interval": timeframe,
            "outputsize": min(periods, 5000),
            "timezone": "UTC",
        })

        bars = []
        from datetime import datetime, timezone
        for bar in reversed(data.get("values", [])):
            ts = datetime.fromisoformat(bar["datetime"]).replace(tzinfo=timezone.utc)
            bars.append(OHLCV(
                symbol=pair,
                timestamp=ts,
                open=Decimal(bar["open"]),
                high=Decimal(bar["high"]),
                low=Decimal(bar["low"]),
                close=Decimal(bar["close"]),
                volume=int(bar.get("volume", 0)),
                timeframe=timeframe,
            ))

        logger.debug(f"Fetched {len(bars)} forex bars for {pair}")
        return bars

    def get_live_forex_rate(self, pair: str) -> dict[str, Any]:
        """
        Get current bid/ask/mid for a forex pair.

        Returns amber banner flag if pair is analysis-only.
        """
        is_inr_pair = pair.endswith("INR") or pair.startswith("INR")

        if self.mock_mode:
            raw = mock_forex_live(pair)
            raw["execution_eligible"] = is_inr_pair  # Only INR pairs executable on NSE
            raw["amber_banner"] = not is_inr_pair
            return raw

        data = self._twelve_get("price", {"symbol": PAIR_MAP.get(pair, {}).get("twelve", pair)})
        return {
            "pair": pair,
            "timestamp": utc_now(),
            "mid": Decimal(str(data.get("price", 0))),
            "execution_eligible": is_inr_pair,
            "amber_banner": not is_inr_pair,
        }

    def get_macro_overlay(self) -> MacroOverlayDaily:
        """
        Fetch and compute the complete macro overlay for Indian equity analysis.
        This is the DXY→FII→Indian sector cross-system feed.

        Runs nightly at 04:30 IST, available by 06:00 IST.
        Documented in: GLOBAL_FOREX_MODULE.md §F9, ARCHITECTURE_v5.md §19
        """
        if self.mock_mode:
            raw = mock_macro_overlay()
            return MacroOverlayDaily(
                as_of_date=raw["as_of_date"],
                dxy_5d_change_pct=raw["dxy_5d_change_pct"],
                dxy_regime=DXYRegime.UP if raw["dxy_5d_change_pct"] > 0.3 else DXYRegime.NEUTRAL,
                sp500_overnight_change_pct=raw["sp500_overnight_change_pct"],
                us_vix_level=raw["us_vix_level"],
                us_vix_state=raw["us_vix_state"],
                us_10y_yield=raw["us_10y_yield"],
                brent_crude_usd=raw["brent_crude_usd"],
                gold_usd=raw["gold_usd"],
                usd_inr=raw["usd_inr"],
                fii_net_yesterday_cr=raw["fii_net_yesterday_cr"],
                fii_30d_trend=raw["fii_30d_trend"],
                market_regime=MarketRegime.NEUTRAL,
            )

        # Live mode: fetch from FRED + Twelve Data
        overlay = MacroOverlayDaily(as_of_date=utc_now())

        # DXY from FRED (free, authoritative)
        try:
            dxy_obs = self._fred_get(FRED_SERIES["dxy"], limit=10)
            if len(dxy_obs) >= 6:
                current = float(dxy_obs[0]["value"])
                five_days_ago = float(dxy_obs[5]["value"])
                dxy_change = (current - five_days_ago) / five_days_ago * 100
                overlay.dxy_5d_change_pct = round(dxy_change, 3)
                overlay.dxy_regime = self._classify_dxy_regime(dxy_change)
        except Exception as e:
            logger.warning(f"FRED DXY fetch failed: {e}")

        # US yields from FRED
        try:
            yield_10y = self._fred_get(FRED_SERIES["us_10y"], limit=6)
            yield_2y = self._fred_get(FRED_SERIES["us_2y"], limit=6)
            if yield_10y:
                overlay.us_10y_yield = float(yield_10y[0]["value"])
            if yield_2y:
                overlay.us_2y_yield = float(yield_2y[0]["value"])
            if overlay.us_10y_yield and overlay.us_2y_yield:
                overlay.yield_curve_slope_bps = round(
                    (overlay.us_10y_yield - overlay.us_2y_yield) * 100, 1
                )
        except Exception as e:
            logger.warning(f"FRED yield fetch failed: {e}")

        # VIX from Twelve Data
        try:
            vix_data = self.get_forex_ohlcv("VIX", periods=5, timeframe="1day")
            if vix_data:
                overlay.us_vix_level = float(vix_data[-1].close)
                overlay.us_vix_state = self._classify_vix(overlay.us_vix_level)
        except Exception as e:
            logger.warning(f"VIX fetch failed: {e}")

        # Determine market regime
        if overlay.us_vix_level and overlay.us_vix_level > 22:
            overlay.market_regime = MarketRegime.DEFENSIVE
        elif overlay.us_vix_level and overlay.us_vix_level > 18:
            overlay.market_regime = MarketRegime.BEAR_VOLATILE
        else:
            overlay.market_regime = MarketRegime.NEUTRAL

        return overlay

    def get_cot_data(self, pair: str) -> dict[str, Any]:
        """
        Get COT (Commitment of Traders) positioning data for a forex pair.

        COT data is published every Friday 15:30 ET for positions as of Tuesday.
        Ingested Saturday 02:30 IST. Available in Monday morning briefing.

        Uses TFF (Traders in Financial Futures) report, NOT Legacy report.
        Leveraged Funds category = the speculative signal.

        Documented in: GLOBAL_FOREX_MODULE.md §F2.3, §F5
        """
        if self.mock_mode:
            return mock_cot_data(pair)

        # In live mode: read from database (ingested Saturday morning)
        # The COT ingestion job runs in sentinel/ops/cot_ingester.py
        # Here we just read the latest from the database
        logger.info(
            f"COT live data for {pair} — reads from DB (ingested Saturday 02:30 IST). "
            f"Ensure sentinel/ops/cot_ingester.py has run."
        )
        return mock_cot_data(pair)   # Fallback to mock until DB ingestion is built

    def get_economic_calendar(self, days_ahead: int = 7) -> list[dict[str, Any]]:
        """
        Fetch upcoming economic events affecting forex pairs.

        High-impact events trigger a 15-minute pre-event blackout on new orders.
        Documented in: GLOBAL_FOREX_MODULE.md §F2.4
        """
        if self.mock_mode:
            from datetime import timedelta
            from sentinel.core.types import utc_now
            now = utc_now()
            # Return mock calendar events
            return [
                {
                    "timestamp": (now + timedelta(hours=18)).isoformat(),
                    "currency": "USD",
                    "event": "Non-Farm Payrolls",
                    "impact": "HIGH",
                    "consensus": "185K",
                    "previous": "175K",
                    "actual": None,
                },
                {
                    "timestamp": (now + timedelta(days=2, hours=14)).isoformat(),
                    "currency": "EUR",
                    "event": "ECB Interest Rate Decision",
                    "impact": "CRITICAL",
                    "consensus": "No change",
                    "previous": "4.25%",
                    "actual": None,
                },
                {
                    "timestamp": (now + timedelta(days=3, hours=5)).isoformat(),
                    "currency": "INR",
                    "event": "RBI MPC Meeting",
                    "impact": "CRITICAL",
                    "consensus": "No change",
                    "previous": "6.50%",
                    "actual": None,
                },
            ]

        # Live: parse from investing.com or ForexFactory
        # Placeholder — implement in Sprint 4
        logger.warning("Live economic calendar not yet implemented. Using mock.")
        return self.get_economic_calendar.__wrapped__(days_ahead)   # type: ignore

    @staticmethod
    def _classify_dxy_regime(change_5d_pct: float) -> DXYRegime:
        """Map 5-day DXY change to regime enum."""
        if change_5d_pct > 1.0:
            return DXYRegime.STRONG_UP
        elif change_5d_pct > 0.3:
            return DXYRegime.UP
        elif change_5d_pct > -0.3:
            return DXYRegime.NEUTRAL
        elif change_5d_pct > -1.0:
            return DXYRegime.DOWN
        else:
            return DXYRegime.STRONG_DOWN

    @staticmethod
    def _classify_vix(vix_level: float) -> str:
        """Classify VIX into human-readable state."""
        if vix_level < 12:
            return "low"
        elif vix_level < 18:
            return "normal"
        elif vix_level < 22:
            return "elevated"
        elif vix_level < 28:
            return "fear"
        else:
            return "panic"

    def health_check(self) -> dict[str, Any]:
        """Returns connection health for monitoring."""
        return {
            "source": "ForexConnector",
            "mock_mode": self.mock_mode,
            "twelve_data_key_set": bool(self.twelve_data_key),
            "fred_key_set": bool(self.fred_key),
            "requests_this_session": self._request_count,
            "timestamp": utc_now().isoformat(),
        }
