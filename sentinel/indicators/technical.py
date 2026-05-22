"""
sentinel/indicators/technical.py
===================================
Complete technical indicator engine for Project Sentinel.

Computes all indicators used across screeners and strategies:
- Moving averages (EMA 9/20/50, SMA 200)
- Momentum (RSI 14, Stochastic 14-3-3, MACD 12-26-9)
- Volatility (ATR 14, Bollinger Bands 20-2)
- Volume (OBV, Volume ratio, Delivery %)
- Trend (MTF alignment score, London Bias for forex)
- Support/Resistance (52-week high/low, pivot points)

All functions take a list of OHLCV bars and return a dict of computed values.
The final bar's values are what screeners and strategies consume.

Documented in: ARCHITECTURE_v5.md §7.6, GLOBAL_FOREX_MODULE.md §F3
"""

from __future__ import annotations

import math
from typing import Any, Optional

from sentinel.core.types import OHLCV, utc_now


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _closes(bars: list[OHLCV]) -> list[float]:
    return [float(b.close) for b in bars]

def _highs(bars: list[OHLCV]) -> list[float]:
    return [float(b.high) for b in bars]

def _lows(bars: list[OHLCV]) -> list[float]:
    return [float(b.low) for b in bars]

def _volumes(bars: list[OHLCV]) -> list[float]:
    return [float(b.volume) for b in bars]

def _ema(values: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2.0 / (period + 1)
    result = [float("nan")] * len(values)
    # Seed with SMA of first `period` values
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    for i in range(period, len(values)):
        result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result

def _sma(values: list[float], period: int) -> list[float]:
    """Simple Moving Average."""
    result = [float("nan")] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = sum(values[i - period + 1: i + 1]) / period
    return result

def _rolling_max(values: list[float], period: int) -> list[float]:
    result = [float("nan")] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = max(values[i - period + 1: i + 1])
    return result

def _rolling_min(values: list[float], period: int) -> list[float]:
    result = [float("nan")] * len(values)
    for i in range(period - 1, len(values)):
        result[i] = min(values[i - period + 1: i + 1])
    return result

def _is_valid(v: float) -> bool:
    return v is not None and not math.isnan(v)


# ─────────────────────────────────────────────
# INDIVIDUAL INDICATORS
# ─────────────────────────────────────────────

def compute_rsi(bars: list[OHLCV], period: int = 14) -> float:
    """RSI (Relative Strength Index)."""
    closes = _closes(bars)
    if len(closes) < period + 1:
        return float("nan")

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    # Wilder's smoothing
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_macd(
    bars: list[OHLCV],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, float]:
    """MACD line, signal line, and histogram."""
    closes = _closes(bars)
    if len(closes) < slow + signal:
        return {"macd": float("nan"), "signal": float("nan"), "histogram": float("nan")}

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [
        ema_fast[i] - ema_slow[i] if _is_valid(ema_fast[i]) and _is_valid(ema_slow[i])
        else float("nan")
        for i in range(len(closes))
    ]
    valid_macd = [v for v in macd_line if _is_valid(v)]
    if len(valid_macd) < signal:
        return {"macd": float("nan"), "signal": float("nan"), "histogram": float("nan")}

    signal_line = _ema(valid_macd, signal)
    macd_val = valid_macd[-1]
    sig_val = signal_line[-1]
    hist_val = macd_val - sig_val if _is_valid(sig_val) else float("nan")

    return {
        "macd": round(macd_val, 4),
        "signal": round(sig_val, 4) if _is_valid(sig_val) else float("nan"),
        "histogram": round(hist_val, 4) if _is_valid(hist_val) else float("nan"),
        "bullish_crossover": (
            _is_valid(macd_val) and _is_valid(sig_val) and macd_val > sig_val
        ),
    }


def compute_stochastic(
    bars: list[OHLCV],
    k_period: int = 14,
    d_period: int = 3,
    smooth: int = 3,
) -> dict[str, float]:
    """Stochastic Oscillator %K and %D."""
    if len(bars) < k_period + d_period:
        return {"k": float("nan"), "d": float("nan")}

    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)

    k_values = []
    for i in range(k_period - 1, len(closes)):
        period_high = max(highs[i - k_period + 1: i + 1])
        period_low = min(lows[i - k_period + 1: i + 1])
        if period_high == period_low:
            k_values.append(50.0)
        else:
            k_values.append(
                100 * (closes[i] - period_low) / (period_high - period_low)
            )

    # Smooth %K
    if smooth > 1 and len(k_values) >= smooth:
        smoothed_k = _sma(k_values, smooth)
        k = smoothed_k[-1]
    else:
        k = k_values[-1]

    # %D = SMA of smoothed %K
    if len(k_values) >= d_period:
        d_values = _sma(k_values, d_period)
        d = d_values[-1]
    else:
        d = float("nan")

    return {
        "k": round(k, 2) if _is_valid(k) else float("nan"),
        "d": round(d, 2) if _is_valid(d) else float("nan"),
        "overbought": k > 80 if _is_valid(k) else False,
        "oversold": k < 20 if _is_valid(k) else False,
    }


def compute_atr(bars: list[OHLCV], period: int = 14) -> float:
    """Average True Range — used for stop loss placement."""
    if len(bars) < period + 1:
        return float("nan")

    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)

    true_ranges = []
    for i in range(1, len(bars)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    # Wilder's smoothing
    atr = sum(true_ranges[:period]) / period
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return round(atr, 2)


def compute_bollinger_bands(
    bars: list[OHLCV],
    period: int = 20,
    std_dev: float = 2.0,
) -> dict[str, float]:
    """Bollinger Bands — upper, middle, lower, and %B position."""
    closes = _closes(bars)
    if len(closes) < period:
        return {"upper": float("nan"), "middle": float("nan"),
                "lower": float("nan"), "pct_b": float("nan"), "squeeze": False}

    mid_values = _sma(closes, period)
    mid = mid_values[-1]

    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = math.sqrt(variance)

    upper = mid + std_dev * std
    lower = mid - std_dev * std
    current = closes[-1]

    band_width = upper - lower
    pct_b = (current - lower) / band_width if band_width > 0 else 0.5
    squeeze = std < (mean * 0.015)  # Squeeze when band is <1.5% of price

    return {
        "upper": round(upper, 2),
        "middle": round(mid, 2),
        "lower": round(lower, 2),
        "pct_b": round(pct_b, 3),
        "squeeze": squeeze,
        "bandwidth_pct": round(band_width / mid * 100, 2) if mid > 0 else 0,
    }


def compute_moving_averages(bars: list[OHLCV]) -> dict[str, Any]:
    """Compute all moving averages and MA-based signals."""
    closes = _closes(bars)
    current = closes[-1]

    ema9  = _ema(closes, 9)[-1]
    ema20 = _ema(closes, 20)[-1]
    ema50 = _ema(closes, 50)[-1]
    sma200 = _sma(closes, 200)[-1]
    ema21 = _ema(closes, 21)[-1]   # Forex: 21 EMA
    ema55 = _ema(closes, 55)[-1]   # Forex: 55 EMA

    def above(ma):
        return current > ma if _is_valid(ma) else None

    # MA Stack: all MAs aligned (bullish = price > all MAs)
    valid_mas = [v for v in [ema9, ema20, ema50, sma200] if _is_valid(v)]
    ma_stack_bullish = all(current > ma for ma in valid_mas) if valid_mas else None
    ma_stack_bearish = all(current < ma for ma in valid_mas) if valid_mas else None

    return {
        "ema_9":  round(ema9, 2)  if _is_valid(ema9)  else None,
        "ema_20": round(ema20, 2) if _is_valid(ema20) else None,
        "ema_50": round(ema50, 2) if _is_valid(ema50) else None,
        "sma_200": round(sma200, 2) if _is_valid(sma200) else None,
        "ema_21": round(ema21, 2) if _is_valid(ema21) else None,
        "ema_55": round(ema55, 2) if _is_valid(ema55) else None,
        "above_ema9":   above(ema9),
        "above_ema20":  above(ema20),
        "above_ema50":  above(ema50),
        "above_sma200": above(sma200),
        "ma_stack_bullish": ma_stack_bullish,
        "ma_stack_bearish": ma_stack_bearish,
    }


def compute_volume_analysis(bars: list[OHLCV], period: int = 20) -> dict[str, Any]:
    """Volume-based signals: ratio, OBV trend, delivery %."""
    volumes = _volumes(bars)
    closes = _closes(bars)

    if len(volumes) < period:
        return {"vol_ratio": float("nan"), "obv_trend": "unknown",
                "high_volume": False, "delivery_avg_pct": None}

    vol_ma = sum(volumes[-period:]) / period
    current_vol = volumes[-1]
    vol_ratio = current_vol / vol_ma if vol_ma > 0 else 1.0

    # OBV (On Balance Volume)
    obv = 0.0
    obv_values = []
    for i in range(1, len(bars)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
        obv_values.append(obv)

    obv_trend = "rising" if len(obv_values) >= 5 and obv_values[-1] > obv_values[-5] else "falling"

    # Delivery % average (NSE equity only)
    delivery_values = [b.delivery_pct for b in bars[-period:] if b.delivery_pct is not None]
    delivery_avg = sum(delivery_values) / len(delivery_values) if delivery_values else None

    return {
        "vol_ratio": round(vol_ratio, 2),
        "vol_ma_20": round(vol_ma, 0),
        "current_vol": int(current_vol),
        "high_volume": vol_ratio > 2.0,
        "very_high_volume": vol_ratio > 3.0,
        "obv_trend": obv_trend,
        "delivery_avg_pct": round(delivery_avg, 1) if delivery_avg else None,
        "high_delivery": delivery_avg > 60 if delivery_avg else None,
    }


def compute_support_resistance(bars: list[OHLCV]) -> dict[str, Any]:
    """52-week high/low and recent swing levels."""
    if len(bars) < 5:
        return {}

    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    current = closes[-1]

    # 52-week high/low
    period = min(252, len(bars))
    high_52w = max(highs[-period:])
    low_52w = min(lows[-period:])

    # Distance from 52W levels
    dist_from_high_pct = (current - high_52w) / high_52w * 100
    dist_from_low_pct = (current - low_52w) / low_52w * 100

    # Is it breaking 52W high?
    near_52w_high = dist_from_high_pct > -2.0  # Within 2% of 52W high
    at_52w_high = dist_from_high_pct >= 0        # At or above 52W high

    # Recent swing high/low (last 20 bars)
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])

    return {
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
        "dist_from_52w_high_pct": round(dist_from_high_pct, 2),
        "dist_from_52w_low_pct": round(dist_from_low_pct, 2),
        "near_52w_high": near_52w_high,
        "at_52w_high": at_52w_high,
        "near_52w_low": dist_from_low_pct < 15,  # Within 15% of 52W low
        "recent_swing_high": round(recent_high, 2),
        "recent_swing_low": round(recent_low, 2),
    }


def compute_pivot_points(bars: list[OHLCV]) -> dict[str, float]:
    """
    Classic weekly pivot points for support/resistance.
    Used primarily in the forex screener (S7).
    Uses the previous bar's H/L/C.
    """
    if len(bars) < 2:
        return {}

    prev = bars[-2]
    high = float(prev.high)
    low = float(prev.low)
    close = float(prev.close)

    pivot = (high + low + close) / 3
    r1 = 2 * pivot - low
    r2 = pivot + (high - low)
    r3 = high + 2 * (pivot - low)
    s1 = 2 * pivot - high
    s2 = pivot - (high - low)
    s3 = low - 2 * (high - pivot)

    return {
        "pivot": round(pivot, 4),
        "r1": round(r1, 4),
        "r2": round(r2, 4),
        "r3": round(r3, 4),
        "s1": round(s1, 4),
        "s2": round(s2, 4),
        "s3": round(s3, 4),
    }


# ─────────────────────────────────────────────
# MASTER INDICATOR COMPUTATION
# ─────────────────────────────────────────────

def compute_all(bars: list[OHLCV]) -> dict[str, Any]:
    """
    Compute ALL indicators for a bar series in one call.
    Returns a flat dict of indicator values for the latest bar.

    This is the primary function called by screeners and strategies.

    Args:
        bars: List of OHLCV bars, oldest first, all UTC-aware timestamps

    Returns:
        Dict of indicator values. All float values.
        NaN values indicate insufficient data.
    """
    if not bars or len(bars) < 2:
        return {"error": "Insufficient data", "bar_count": len(bars)}

    result: dict[str, Any] = {
        "symbol": bars[-1].symbol,
        "computed_at": utc_now().isoformat(),
        "bar_count": len(bars),
        "latest_close": float(bars[-1].close),
        "latest_timestamp": bars[-1].timestamp.isoformat(),
    }

    # Moving averages
    result.update(compute_moving_averages(bars))

    # RSI
    result["rsi_14"] = compute_rsi(bars, 14)
    result["rsi_7"]  = compute_rsi(bars, 7)

    # RSI zones
    rsi = result["rsi_14"]
    if _is_valid(rsi):
        result["rsi_zone"] = (
            "overbought" if rsi > 70 else
            "oversold"   if rsi < 30 else
            "bullish"    if rsi > 55 else
            "bearish"    if rsi < 45 else
            "neutral"
        )

    # MACD
    macd = compute_macd(bars)
    result.update({f"macd_{k}": v for k, v in macd.items()})

    # Stochastic
    stoch = compute_stochastic(bars)
    result.update({f"stoch_{k}": v for k, v in stoch.items()})

    # ATR
    result["atr_14"] = compute_atr(bars, 14)
    result["atr_14_pct"] = (
        round(result["atr_14"] / result["latest_close"] * 100, 2)
        if result["latest_close"] > 0 and _is_valid(result["atr_14"])
        else float("nan")
    )

    # Bollinger Bands
    bb = compute_bollinger_bands(bars)
    result.update({f"bb_{k}": v for k, v in bb.items()})

    # Volume
    vol = compute_volume_analysis(bars)
    result.update(vol)

    # Support/Resistance
    sr = compute_support_resistance(bars)
    result.update(sr)

    # Pivot Points
    pivots = compute_pivot_points(bars)
    result.update({f"pivot_{k}": v for k, v in pivots.items()})

    # MTF trend classification (for the current timeframe)
    result["trend_direction"] = _classify_trend(result)
    result["trend_strength"] = _trend_strength(result)

    return result


def _classify_trend(indicators: dict[str, Any]) -> str:
    """Classify current trend from indicator values."""
    signals = []

    # MA alignment
    if indicators.get("ma_stack_bullish"):
        signals.append(1)
    elif indicators.get("ma_stack_bearish"):
        signals.append(-1)
    else:
        signals.append(0)

    # RSI
    rsi = indicators.get("rsi_14", 50)
    if _is_valid(rsi):
        signals.append(1 if rsi > 55 else (-1 if rsi < 45 else 0))

    # MACD
    if indicators.get("macd_bullish_crossover"):
        signals.append(1)
    elif indicators.get("macd_histogram", 0) < 0:
        signals.append(-1)
    else:
        signals.append(0)

    avg = sum(signals) / len(signals) if signals else 0

    if avg >= 0.67:
        return "BULLISH"
    elif avg >= 0.33:
        return "LEANING_BULLISH"
    elif avg <= -0.67:
        return "BEARISH"
    elif avg <= -0.33:
        return "LEANING_BEARISH"
    else:
        return "NEUTRAL"


def _trend_strength(indicators: dict[str, Any]) -> str:
    """Rate trend strength: STRONG / MODERATE / WEAK."""
    score = 0

    if indicators.get("ma_stack_bullish") or indicators.get("ma_stack_bearish"):
        score += 2
    if indicators.get("high_volume"):
        score += 1
    if indicators.get("obv_trend") in ("rising",):
        score += 1

    rsi = indicators.get("rsi_14", 50)
    if _is_valid(rsi) and (rsi > 60 or rsi < 40):
        score += 1

    if score >= 4:
        return "STRONG"
    elif score >= 2:
        return "MODERATE"
    else:
        return "WEAK"


# ─────────────────────────────────────────────
# MTF ALIGNMENT SCORE
# ─────────────────────────────────────────────

def compute_mtf_score(
    weekly_bars: list[OHLCV],
    daily_bars: list[OHLCV],
    h4_bars: Optional[list[OHLCV]] = None,
    h1_bars: Optional[list[OHLCV]] = None,
) -> dict[str, Any]:
    """
    Multi-Timeframe alignment score.
    Score range: -4 to +4 (each timeframe contributes -1, 0, or +1)

    +4 = all timeframes bullish (strong setup)
    -4 = all timeframes bearish (strong short setup)
    ±2 or better required for Trade Research Card generation

    Documented in: GLOBAL_FOREX_MODULE.md §F3, ARCHITECTURE_v5.md §8
    """
    scores = {}
    total = 0

    for label, bars_input in [
        ("weekly", weekly_bars),
        ("daily", daily_bars),
        ("h4", h4_bars),
        ("h1", h1_bars),
    ]:
        if not bars_input or len(bars_input) < 10:
            scores[label] = 0
            continue

        ind = compute_all(bars_input)
        trend = ind.get("trend_direction", "NEUTRAL")

        if trend == "BULLISH":
            s = 1
        elif trend == "LEANING_BULLISH":
            s = 1
        elif trend == "BEARISH":
            s = -1
        elif trend == "LEANING_BEARISH":
            s = -1
        else:
            s = 0

        scores[label] = s
        total += s

    direction = "BULLISH" if total >= 2 else ("BEARISH" if total <= -2 else "MIXED")
    trade_card_eligible = abs(total) >= 2

    return {
        "mtf_score": total,
        "mtf_min": -4,
        "mtf_max": 4,
        "direction": direction,
        "trade_card_eligible": trade_card_eligible,
        "timeframe_scores": scores,
        "interpretation": (
            f"MTF {total:+d}/4 — {direction}. "
            f"{'Strong setup.' if abs(total) >= 3 else 'Moderate setup.' if abs(total) == 2 else 'Mixed — wait for alignment.'}"
        ),
    }
