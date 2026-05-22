"""
sentinel/data/kite_connector.py
================================
Zerodha Kite Connect data connector.

In MOCK_MODE: returns realistic mock data. No API key needed.
In LIVE mode: connects to Kite Connect REST + WebSocket APIs.

Provides:
- Historical OHLCV data (now free since Feb 2025)
- Live WebSocket tick data
- Instrument master file
- Order placement interface (paper mode and live)

Documented in: ARCHITECTURE_v5.md §6.1, SPRINT_ROADMAP_v2.md Sprint 1
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Optional

from sentinel.core.errors import DataSourceUnavailableError
from sentinel.core.types import OHLCV, Tick, utc_now
from sentinel.data.mock_data import (
    mock_ohlcv,
    mock_live_tick,
    ALL_MOCK_STOCKS,
    stable_seed,
)

logger = logging.getLogger(__name__)

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


class KiteConnector:
    """
    Zerodha Kite Connect data connector.

    Usage:
        connector = KiteConnector()
        bars = connector.get_historical("RELIANCE", days=200, timeframe="day")
        tick = connector.get_live_tick("RELIANCE")

    In MOCK_MODE all methods return mock data instantly.
    Switch to live by setting MOCK_MODE=false in .env and
    providing real Kite API credentials.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        user_id: str = "",
    ) -> None:
        self.api_key = api_key or os.getenv("ZERODHA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ZERODHA_API_SECRET", "")
        self.user_id = user_id or os.getenv("ZERODHA_USER_ID", "")
        self.mock_mode = MOCK_MODE
        self._kite = None           # Real kiteconnect.KiteConnect instance
        self._access_token: str = ""
        self._last_ping: Optional[datetime] = None

        if self.mock_mode:
            logger.info(
                "KiteConnector running in MOCK MODE. "
                "All data is simulated. Set MOCK_MODE=false for live data."
            )
        else:
            self._init_live()

    def _init_live(self) -> None:
        """
        Initialize live Kite Connect connection.
        Requires: ZERODHA_API_KEY, ZERODHA_API_SECRET in .env
        Requires: Static IP whitelisted at Zerodha (mandatory since April 2025)
        """
        if not self.api_key:
            raise DataSourceUnavailableError(
                "Kite Connect",
                "ZERODHA_API_KEY not set in .env. "
                "Set MOCK_MODE=true to use mock data during build phase."
            )
        try:
            from kiteconnect import KiteConnect     # type: ignore
            self._kite = KiteConnect(api_key=self.api_key)
            logger.info("Kite Connect initialized. Waiting for access token...")
        except ImportError:
            raise DataSourceUnavailableError(
                "Kite Connect",
                "kiteconnect package not installed. Run: pip install kiteconnect"
            )

    def set_access_token(self, request_token: str) -> str:
        """
        Exchange request token for access token.
        Called after user logs in via Kite login URL.
        Returns the access token (save this for the session).

        In production: this is called once per day after login.
        """
        if self.mock_mode:
            logger.info("[MOCK] Access token exchange simulated.")
            self._access_token = "mock_access_token_" + request_token[:8]
            return self._access_token

        if self._kite is None:
            raise DataSourceUnavailableError("Kite Connect", "Not initialized.")

        data = self._kite.generate_session(request_token, api_secret=self.api_secret)
        self._access_token = data["access_token"]
        self._kite.set_access_token(self._access_token)
        logger.info(f"Kite access token set for user {self.user_id}")
        return self._access_token

    def get_historical(
        self,
        symbol: str,
        days: int = 365,
        timeframe: str = "day",
        exchange: str = "NSE",
    ) -> list[OHLCV]:
        """
        Fetch historical OHLCV data.

        Args:
            symbol: NSE symbol e.g. "RELIANCE", "HDFCBANK"
            days: Number of days of history
            timeframe: "minute", "3minute", "5minute", "10minute",
                      "15minute", "30minute", "60minute", "day", "week", "month"
            exchange: "NSE", "BSE", "NFO" (for F&O)

        Returns:
            List of OHLCV objects sorted oldest-first.

        Note: Historical data is FREE on Kite Connect since Feb 8, 2025.
        """
        if self.mock_mode:
            raw_bars = mock_ohlcv(symbol, days=days, timeframe=timeframe)
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
                    delivery_pct=b.get("delivery_pct"),
                )
                for b in raw_bars
            ]

        # Live mode
        if not self._access_token:
            raise DataSourceUnavailableError(
                "Kite Connect",
                "No access token. Call set_access_token() after login."
            )

        # Map symbol to Kite instrument token
        instrument_token = self._get_instrument_token(symbol, exchange)
        if not instrument_token:
            raise DataSourceUnavailableError(
                "Kite Connect",
                f"Could not find instrument token for {symbol}:{exchange}"
            )

        to_date = utc_now()
        from_date = to_date - timedelta(days=days)

        # Kite API returns data in IST — we convert to UTC
        raw = self._kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date.strftime("%Y-%m-%d"),
            to_date=to_date.strftime("%Y-%m-%d"),
            interval=timeframe,
        )

        bars = []
        for b in raw:
            # Kite returns aware datetimes in IST
            ts = b["date"]
            if ts.tzinfo is None:
                from zoneinfo import ZoneInfo
                ts = ts.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
            ts_utc = ts.astimezone(timezone.utc)

            bars.append(OHLCV(
                symbol=symbol,
                timestamp=ts_utc,
                open=Decimal(str(b["open"])),
                high=Decimal(str(b["high"])),
                low=Decimal(str(b["low"])),
                close=Decimal(str(b["close"])),
                volume=int(b["volume"]),
                timeframe=timeframe,
            ))

        logger.debug(f"Fetched {len(bars)} bars for {symbol} from Kite Connect.")
        return bars

    def get_live_tick(self, symbol: str) -> Tick:
        """
        Get the latest price for a symbol.
        In mock mode: returns simulated live price.
        In live mode: fetches LTP from Kite quote API.
        """
        if self.mock_mode:
            raw = mock_live_tick(symbol)
            return Tick(
                symbol=raw["symbol"],
                timestamp=raw["timestamp"],
                ltp=Decimal(str(raw["ltp"])),
                volume=raw["volume"],
                bid=Decimal(str(raw["bid"])) if raw.get("bid") else None,
                ask=Decimal(str(raw["ask"])) if raw.get("ask") else None,
                change_pct=raw.get("change_pct"),
            )

        if not self._access_token:
            raise DataSourceUnavailableError("Kite Connect", "No access token.")

        quote = self._kite.quote([f"NSE:{symbol}"])
        data = quote.get(f"NSE:{symbol}", {})

        return Tick(
            symbol=symbol,
            timestamp=utc_now(),
            ltp=Decimal(str(data.get("last_price", 0))),
            volume=int(data.get("volume", 0)),
            bid=Decimal(str(data.get("depth", {}).get("buy", [{}])[0].get("price", 0))),
            ask=Decimal(str(data.get("depth", {}).get("sell", [{}])[0].get("price", 0))),
            change_pct=float(data.get("net_change", 0)),
        )

    def get_instruments(self, exchange: str = "NSE") -> list[dict]:
        """
        Get the full instrument master list.
        Contains all symbols, tokens, lot sizes, tick sizes.
        Should be refreshed daily at startup.
        """
        if self.mock_mode:
            return [
                {
                    "tradingsymbol": symbol,
                    "name": info["name"],
                    "exchange": "NSE",
                    "segment": "NSE",
                    "instrument_type": "EQ",
                    "lot_size": 1,
                    "tick_size": 0.05,
                }
                for symbol, info in ALL_MOCK_STOCKS.items()
            ]

        if not self._access_token:
            raise DataSourceUnavailableError("Kite Connect", "No access token.")

        return self._kite.instruments(exchange)

    def get_quote(self, symbols: list[str], exchange: str = "NSE") -> dict[str, Any]:
        """
        Get quotes for multiple symbols at once.
        More efficient than individual get_live_tick calls.
        """
        if self.mock_mode:
            return {
                symbol: {
                    "last_price": ALL_MOCK_STOCKS.get(symbol, {}).get("price", 500.0),
                    "volume": 1_000_000,
                    "change": 0.0,
                }
                for symbol in symbols
            }

        if not self._access_token:
            raise DataSourceUnavailableError("Kite Connect", "No access token.")

        kite_symbols = [f"{exchange}:{s}" for s in symbols]
        return self._kite.quote(kite_symbols)

    def _get_instrument_token(self, symbol: str, exchange: str = "NSE") -> Optional[int]:
        """
        Look up instrument token for a symbol.
        In production: should cache this from instruments() call at startup.
        """
        if self.mock_mode:
            return stable_seed(symbol) % 1_000_000  # Fake token for mock

        # In live mode: look up from cached instruments list
        # This should be pre-loaded at startup for performance
        instruments = self.get_instruments(exchange)
        for inst in instruments:
            if inst.get("tradingsymbol") == symbol:
                return inst.get("instrument_token")
        return None

    @property
    def is_connected(self) -> bool:
        """Check if connector has a valid session."""
        if self.mock_mode:
            return True
        return bool(self._access_token)

    def health_check(self) -> dict[str, Any]:
        """Returns connection health status."""
        return {
            "source": "Kite Connect",
            "mock_mode": self.mock_mode,
            "connected": self.is_connected,
            "user_id": self.user_id if not self.mock_mode else "MOCK_USER",
            "timestamp": utc_now().isoformat(),
        }
