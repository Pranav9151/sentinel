"""
sentinel/data/mock_data.py
==========================
Realistic mock data for all connectors during build phase.

When MOCK_MODE=true, every connector calls functions from this module
instead of hitting real APIs. The data is realistic enough to test
all downstream logic — screeners, strategies, dashboard, alerts.

When you add real API keys and set MOCK_MODE=false, this file
is completely bypassed. Nothing else in the system changes.

This is how you build and test 100% of the system
before spending a single rupee on API subscriptions.
"""

from __future__ import annotations

import random
from datetime import timedelta
from hashlib import sha256
from typing import Any

from sentinel.core.types import utc_now


# ─────────────────────────────────────────────
# MOCK NSE EQUITY DATA
# ─────────────────────────────────────────────

# Realistic Nifty 50 stocks with approximate prices (May 2025)
MOCK_NSE_STOCKS: dict[str, dict] = {
    "RELIANCE":   {"name": "Reliance Industries", "price": 2950.0,  "sector": "Energy"},
    "TCS":        {"name": "Tata Consultancy Services", "price": 3800.0, "sector": "IT"},
    "HDFCBANK":   {"name": "HDFC Bank", "price": 1720.0, "sector": "Banking"},
    "INFY":       {"name": "Infosys", "price": 1580.0,  "sector": "IT"},
    "ICICIBANK":  {"name": "ICICI Bank", "price": 1280.0, "sector": "Banking"},
    "HINDUNILVR": {"name": "Hindustan Unilever", "price": 2380.0, "sector": "FMCG"},
    "BHARTIARTL": {"name": "Bharti Airtel", "price": 1890.0, "sector": "Telecom"},
    "ITC":        {"name": "ITC Limited", "price": 445.0,  "sector": "FMCG"},
    "KOTAKBANK":  {"name": "Kotak Mahindra Bank", "price": 1920.0, "sector": "Banking"},
    "LT":         {"name": "Larsen & Toubro", "price": 3650.0, "sector": "Capital Goods"},
    "AXISBANK":   {"name": "Axis Bank", "price": 1150.0, "sector": "Banking"},
    "WIPRO":      {"name": "Wipro", "price": 495.0,   "sector": "IT"},
    "MARUTI":     {"name": "Maruti Suzuki", "price": 12800.0, "sector": "Auto"},
    "SUNPHARMA":  {"name": "Sun Pharma", "price": 1780.0, "sector": "Pharma"},
    "TITAN":      {"name": "Titan Company", "price": 3450.0, "sector": "Consumer"},
    "ULTRACEMCO": {"name": "UltraTech Cement", "price": 11200.0, "sector": "Cement"},
    "BAJFINANCE":  {"name": "Bajaj Finance", "price": 7200.0, "sector": "NBFC"},
    "NESTLEIND":  {"name": "Nestle India", "price": 2280.0, "sector": "FMCG"},
    "POWERGRID":  {"name": "Power Grid Corp", "price": 320.0,  "sector": "Power"},
    "TATASTEEL":  {"name": "Tata Steel", "price": 160.0,  "sector": "Metals"},
    "SBIN":       {"name": "State Bank of India", "price": 815.0,  "sector": "Banking"},
    "NTPC":       {"name": "NTPC", "price": 365.0,  "sector": "Power"},
    "ONGC":       {"name": "Oil & Natural Gas Corp", "price": 272.0, "sector": "Energy"},
    "JSWSTEEL":   {"name": "JSW Steel", "price": 960.0,  "sector": "Metals"},
    "TATAMOTORS": {"name": "Tata Motors", "price": 750.0,  "sector": "Auto"},
}

# Realistic Nifty 500 midcap additions for screeners
MOCK_MIDCAP_STOCKS: dict[str, dict] = {
    "ABCAPITAL":  {"name": "Aditya Birla Capital", "price": 195.0, "sector": "NBFC"},
    "CHOLAFIN":   {"name": "Cholamandalam Finance", "price": 1180.0, "sector": "NBFC"},
    "PERSISTENT": {"name": "Persistent Systems", "price": 5800.0, "sector": "IT"},
    "Dixon":      {"name": "Dixon Technologies", "price": 15200.0, "sector": "Consumer"},
    "KPITTECH":   {"name": "KPIT Technologies", "price": 1680.0, "sector": "IT"},
    "APOLLOHOSP": {"name": "Apollo Hospitals", "price": 6800.0, "sector": "Healthcare"},
    "TRENT":      {"name": "Trent", "price": 5600.0, "sector": "Retail"},
    "ASTRAL":     {"name": "Astral", "price": 1950.0, "sector": "Building Materials"},
    "POLYCAB":    {"name": "Polycab India", "price": 6500.0, "sector": "Capital Goods"},
    "DEEPAKNITRITE": {"name": "Deepak Nitrite", "price": 2650.0, "sector": "Chemicals"},
}

ALL_MOCK_STOCKS = {**MOCK_NSE_STOCKS, **MOCK_MIDCAP_STOCKS}


def stable_seed(value: str) -> int:
    """Deterministic seed independent of Python's per-process hash salt."""
    return int.from_bytes(sha256(value.encode("utf-8")).digest()[:8], "big")


def mock_ohlcv(
    symbol: str,
    days: int = 200,
    timeframe: str = "1d",
) -> list[dict[str, Any]]:
    """
    Generate realistic OHLCV data for a symbol.
    Uses a random walk with drift to simulate real price behaviour.
    """
    if symbol not in ALL_MOCK_STOCKS:
        base_price = 500.0
    else:
        base_price = ALL_MOCK_STOCKS[symbol]["price"]

    # Generate backwards from today
    bars = []
    price = base_price
    now = utc_now()

    # Random seed based on symbol for consistency across calls
    rng = random.Random(stable_seed(symbol))

    for i in range(days, 0, -1):
        # Random walk with slight upward drift
        daily_return = rng.gauss(0.0003, 0.015)   # ~7% annual drift, 15% vol
        price = max(price * (1 + daily_return), 1.0)

        high = price * (1 + abs(rng.gauss(0, 0.008)))
        low = price * (1 - abs(rng.gauss(0, 0.008)))
        open_price = low + (high - low) * rng.random()
        volume = int(rng.gauss(1_000_000, 300_000))

        bar_time = now - timedelta(days=i)
        # Set to market close time (10:00 UTC = 15:30 IST)
        bar_time = bar_time.replace(hour=10, minute=0, second=0, microsecond=0)

        bars.append({
            "symbol": symbol,
            "timestamp": bar_time,
            "open": round(open_price, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(price, 2),
            "volume": max(100_000, volume),
            "timeframe": timeframe,
            "delivery_pct": round(rng.uniform(35, 75), 1),
        })

    return bars


def mock_live_tick(symbol: str) -> dict[str, Any]:
    """Generate a realistic live price tick."""
    if symbol not in ALL_MOCK_STOCKS:
        ltp = 500.0
    else:
        ltp = ALL_MOCK_STOCKS[symbol]["price"]

    # Add small random variation
    ltp = ltp * (1 + random.gauss(0, 0.002))

    return {
        "symbol": symbol,
        "timestamp": utc_now(),
        "ltp": round(ltp, 2),
        "volume": random.randint(100_000, 5_000_000),
        "bid": round(ltp * 0.9995, 2),
        "ask": round(ltp * 1.0005, 2),
        "change_pct": round(random.gauss(0.2, 1.2), 2),
    }


def mock_fundamentals(symbol: str) -> dict[str, Any]:
    """
    Generate realistic fundamental data for a stock.
    Values are sector-appropriate approximations.
    """
    rng = random.Random(stable_seed(symbol + "fundamentals"))
    stock = ALL_MOCK_STOCKS.get(symbol, {"sector": "Other"})
    sector = stock.get("sector", "Other")

    # Sector-specific PE ranges
    sector_pe = {
        "IT": (25, 40), "Banking": (12, 20), "FMCG": (40, 65),
        "Pharma": (20, 35), "Metals": (8, 15), "Energy": (10, 18),
        "Auto": (15, 28), "Capital Goods": (25, 45), "NBFC": (15, 30),
        "Other": (15, 30),
    }
    pe_range = sector_pe.get(sector, (15, 30))

    return {
        "symbol": symbol,
        "pe_ratio": round(rng.uniform(*pe_range), 1),
        "sector_pe": round(rng.uniform(*pe_range) * 0.95, 1),
        "roe_pct": round(rng.uniform(12, 28), 1),
        "roce_pct": round(rng.uniform(10, 25), 1),
        "debt_to_equity": round(rng.uniform(0.1, 1.5), 2),
        "revenue_growth_yoy_pct": round(rng.uniform(8, 25), 1),
        "ebitda_margin_pct": round(rng.uniform(12, 35), 1),
        "net_profit_margin_pct": round(rng.uniform(6, 22), 1),
        "promoter_holding_pct": round(rng.uniform(35, 72), 1),
        "promoter_pledging_pct": round(rng.uniform(0, 8), 1),
        "fii_holding_pct": round(rng.uniform(8, 35), 1),
        "dii_holding_pct": round(rng.uniform(5, 25), 1),
        "market_cap_cr": round(rng.uniform(5000, 800000), 0),
        "52_week_high": round(ALL_MOCK_STOCKS.get(symbol, {}).get("price", 500) * rng.uniform(1.0, 1.45), 2),
        "52_week_low": round(ALL_MOCK_STOCKS.get(symbol, {}).get("price", 500) * rng.uniform(0.55, 0.99), 2),
    }


# ─────────────────────────────────────────────
# MOCK FOREX DATA
# ─────────────────────────────────────────────

MOCK_FOREX_PRICES: dict[str, float] = {
    "EURUSD": 1.0852,
    "GBPUSD": 1.2680,
    "USDJPY": 149.85,
    "AUDUSD": 0.6485,
    "USDCHF": 0.9012,
    "NZDUSD": 0.5985,
    "USDCAD": 1.3620,
    "XAUUSD": 2385.50,   # Gold
    "XAGUSD": 28.45,     # Silver
    "USDINR": 83.62,
    "EURINR": 90.72,
    "GBPINR": 105.98,
    "JPYINR": 55.82,
    "USDWTI": 78.45,     # WTI Crude
    "USDBRNT": 82.10,    # Brent Crude
    "DXY": 104.25,       # Dollar Index
    "BTCUSD": 67500.0,
    "ETHUSD": 3280.0,
}


def mock_forex_ohlcv(
    pair: str,
    periods: int = 200,
    timeframe: str = "1d",
) -> list[dict[str, Any]]:
    """Generate realistic forex OHLCV data."""
    base_price = MOCK_FOREX_PRICES.get(pair, 1.0)
    rng = random.Random(stable_seed(pair))

    bars = []
    price = base_price
    now = utc_now()

    # Forex uses 22:00 UTC as daily close (London convention)
    for i in range(periods, 0, -1):
        daily_return = rng.gauss(0, 0.006)
        price = max(price * (1 + daily_return), 0.0001)

        spread_pct = 0.002
        high = price * (1 + abs(rng.gauss(0, spread_pct)))
        low = price * (1 - abs(rng.gauss(0, spread_pct)))
        open_price = low + (high - low) * rng.random()

        bar_time = now - timedelta(days=i)
        bar_time = bar_time.replace(hour=22, minute=0, second=0, microsecond=0)

        bars.append({
            "symbol": pair,
            "timestamp": bar_time,
            "open": round(open_price, 5),
            "high": round(high, 5),
            "low": round(low, 5),
            "close": round(price, 5),
            "volume": int(rng.uniform(50000, 500000)),
            "timeframe": timeframe,
        })

    return bars


def mock_forex_live(pair: str) -> dict[str, Any]:
    """Generate live forex quote."""
    base = MOCK_FOREX_PRICES.get(pair, 1.0)
    price = base * (1 + random.gauss(0, 0.001))
    spread = base * 0.0003

    return {
        "pair": pair,
        "timestamp": utc_now(),
        "bid": round(price - spread / 2, 5),
        "ask": round(price + spread / 2, 5),
        "mid": round(price, 5),
        "change_pct_24h": round(random.gauss(0.1, 0.5), 3),
    }


# ─────────────────────────────────────────────
# MOCK MACRO DATA
# ─────────────────────────────────────────────

def mock_macro_overlay() -> dict[str, Any]:
    """
    Generate realistic macro overlay data.
    Reflects approximate market conditions.
    """
    return {
        "as_of_date": utc_now(),
        "dxy_level": 104.25,
        "dxy_5d_change_pct": 0.28,
        "dxy_vs_200d_pct": 1.8,
        "dxy_regime": "UP",

        "sp500_overnight_change_pct": 0.35,
        "nasdaq_overnight_change_pct": 0.55,
        "us_vix_level": 16.8,
        "us_vix_state": "normal",

        "us_10y_yield": 4.45,
        "us_10y_5d_change_bps": -3.2,
        "us_2y_yield": 4.85,
        "yield_curve_slope_bps": -40,  # Inverted

        "brent_crude_usd": 82.10,
        "brent_5d_change_pct": -1.2,
        "gold_usd": 2385.50,
        "gold_5d_change_pct": 0.8,

        "usd_inr": 83.62,
        "usd_inr_5d_change_pct": 0.15,
        "fii_net_yesterday_cr": -850.0,     # FII net sell ₹850 crore
        "fii_30d_trend": "distributing",

        "market_regime": "NEUTRAL",
        "india_vix": 13.5,
    }


def mock_fii_dii_data() -> dict[str, Any]:
    """Generate realistic FII/DII flow data."""
    return {
        "date": utc_now().date(),
        "fii_buy_cr": round(random.uniform(3000, 8000), 0),
        "fii_sell_cr": round(random.uniform(3500, 8500), 0),
        "fii_net_cr": round(random.uniform(-2000, 1500), 0),
        "dii_buy_cr": round(random.uniform(2000, 6000), 0),
        "dii_sell_cr": round(random.uniform(1500, 5000), 0),
        "dii_net_cr": round(random.uniform(-500, 2000), 0),
    }


# ─────────────────────────────────────────────
# MOCK COT DATA
# ─────────────────────────────────────────────

def mock_cot_data(pair: str) -> dict[str, Any]:
    """
    Generate realistic COT positioning data.
    COT index 0-100: 0=max short, 100=max long.
    """
    rng = random.Random(stable_seed(pair + "cot"))
    cot_index = round(rng.uniform(20, 80), 1)

    if cot_index >= 80:
        classification = "EXTREME_BULLISH"
    elif cot_index >= 60:
        classification = "BULLISH"
    elif cot_index >= 40:
        classification = "NEUTRAL"
    elif cot_index >= 20:
        classification = "BEARISH"
    else:
        classification = "EXTREME_BEARISH"

    return {
        "pair": pair,
        "report_date": utc_now().date(),
        "data_as_of": (utc_now() - timedelta(days=3)).date(),
        "cot_index": cot_index,
        "classification": classification,
        "net_speculative_position": int(rng.uniform(-50000, 50000)),
        "net_spec_change_week": int(rng.uniform(-5000, 5000)),
        "staleness_days": 3,
        "is_stale_warning": False,  # True if > 7 days old
    }


# ─────────────────────────────────────────────
# MOCK MARKET INTERNALS
# ─────────────────────────────────────────────

def mock_market_internals() -> dict[str, Any]:
    """Nifty 50 market internals data."""
    advances = random.randint(20, 45)
    declines = 50 - advances

    return {
        "date": utc_now().date(),
        "nifty50_close": round(random.uniform(22000, 24500), 2),
        "nifty50_change_pct": round(random.gauss(0.2, 0.8), 2),
        "banknifty_close": round(random.uniform(47000, 53000), 2),
        "banknifty_change_pct": round(random.gauss(0.15, 1.0), 2),
        "india_vix": round(random.uniform(12, 20), 2),
        "advances": advances,
        "declines": declines,
        "unchanged": 5,
        "advance_decline_ratio": round(advances / max(declines, 1), 2),
        "nifty_pcr": round(random.uniform(0.85, 1.35), 2),
        "new_52w_highs": random.randint(5, 80),
        "new_52w_lows": random.randint(2, 30),
    }


def mock_gsm_asm_list() -> list[str]:
    """
    Return a small list of mock symbols on surveillance lists.
    In production this is fetched fresh from NSE/BSE every trading day.
    """
    return [
        "MOCKGSM1", "MOCKGSM2", "MOCKASM1",
        "MOCKBADSTOCK", "MOCKPUMP1",
    ]
