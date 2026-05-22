"""
sentinel/screeners/s2_s7.py
==============================
Screeners S2 through S7.

S2 — Fundamental Value + Technical Reversal
S3 — Sector Momentum Tracker
S4 — Penny & Small Cap (HIGH RISK)
S5 — Smart Institutional Tracker
S6 — MF Conviction Watchlist
S7 — Forex Opportunity Scanner

All inherit BaseScreener. All use mock data in MOCK_MODE.
Documented in: SCREENERS_MODULE_SPEC.md §S2 through §S7
"""

from __future__ import annotations

import random
from typing import Any

from sentinel.core.types import utc_now
from sentinel.data.historical_store import HistoricalStore
from sentinel.data.fundamental_store import FundamentalStore
from sentinel.data.forex_connector import ForexConnector
from sentinel.data.mock_data import (
    ALL_MOCK_STOCKS,
)
from sentinel.indicators.technical import compute_all
from sentinel.screeners.base import BaseScreener


# ─────────────────────────────────────────────
# S2 — FUNDAMENTAL VALUE + TECHNICAL REVERSAL
# ─────────────────────────────────────────────

class ValueReversalScreener(BaseScreener):
    """
    Weekly run (Sunday 18:00 IST).
    Finds quality stocks at value prices showing early recovery signals.
    Documented in: SCREENERS_MODULE_SPEC.md §S2
    """

    name        = "s2_value"
    description = "Quality stocks at value prices with technical reversal"
    max_results = 5
    min_score   = 55.0

    def __init__(self) -> None:
        super().__init__()
        self.store    = HistoricalStore()
        self.fund     = FundamentalStore()

    def _run(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates = []
        rng = random.Random(99)

        for symbol in universe:
            if self.is_blocked(symbol):
                continue

            # Fundamental gate
            self.fund.ingest(symbol)
            data = self.fund.get_latest(symbol)
            if not data:
                continue

            roe = data.get("roe_pct") or 0
            de  = data.get("debt_to_equity") or 99
            prm = data.get("promoter_holding_pct") or 0
            plg = data.get("promoter_pledging_pct") or 100
            rev = data.get("revenue_growth_yoy_pct") or 0
            pe  = data.get("pe_ratio") or 0
            spe = data.get("sector_pe") or pe

            # Hard fundamental gates
            if roe < 12 or de > 1.5 or prm < 35 or plg > 10:
                continue

            # PE below sector (value screen)
            pe_discount = (spe - pe) / spe * 100 if spe > 0 else 0
            if pe_discount < 5 and rng.random() > 0.3:
                continue

            # Technical: near 52W low or reversal pattern
            bars = self.store.get_ohlcv(symbol, as_of=utc_now(), lookback_days=260)
            if not bars or len(bars) < 30:
                continue
            ind = compute_all(bars)
            dist_low = ind.get("dist_from_52w_low_pct", 100)
            if dist_low > 20 and rng.random() > 0.4:
                continue

            rsi = ind.get("rsi_14", 50)
            # Oversold or recovering
            if rsi > 60 and rng.random() > 0.3:
                continue

            # ── Soft scoring ─────────────────────────────────
            score = 50.0
            score += min(roe / 5, 10)            # ROE contribution
            score += min(pe_discount / 3, 10)    # Value discount
            if plg < 2:
                score += 5
            if rev > 15:
                score += 8
            if rsi < 40:
                score += 7    # Oversold = potential reversal
            if ind.get("macd_bullish_crossover"):
                score += 5
            score = min(score, 90)

            close    = float(bars[-1].close)
            atr      = ind.get("atr_14", close * 0.02)
            stop_loss = close - 1.8 * atr
            target_1  = close + 3.0 * atr
            rr = (target_1 - close) / max(close - stop_loss, 0.01)
            if rr < 2.0:
                continue

            candidates.append(self._build_candidate(
                symbol=symbol,
                conviction_score=score,
                entry_low=close * 0.997,
                entry_high=close * 1.003,
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=close + 5.0 * atr,
                thesis=(
                    f"{symbol}: ROE {roe:.1f}%, D/E {de:.2f}x, "
                    f"PE {pe:.1f}x vs sector {spe:.1f}x "
                    f"({pe_discount:.0f}% discount). "
                    f"RSI {rsi:.1f} — potential reversal zone."
                ),
                risks=[
                    "Value trap risk — check if earnings deteriorating",
                    "Sector headwind may persist",
                    f"Stop at ₹{stop_loss:.2f}",
                ],
                suggested_qty=max(1, int(3000 / max(close - stop_loss, 1))),
            ))

        return candidates


# ─────────────────────────────────────────────
# S3 — SECTOR MOMENTUM TRACKER
# ─────────────────────────────────────────────

class SectorMomentumScreener(BaseScreener):
    """
    Daily run (15:45 IST post-close).
    Identifies which sectors smart money is rotating into.
    Documented in: SCREENERS_MODULE_SPEC.md §S3
    """

    name        = "s3_sector"
    description = "Sector rotation — top performing sectors and lead stocks"
    max_results = 9   # top 3 stocks × top 3 sectors
    min_score   = 50.0

    # Sector → mock 5-day relative performance vs Nifty
    MOCK_SECTOR_RS = {
        "IT":            2.4,
        "Banking":       1.1,
        "Pharma":        1.8,
        "Capital Goods": 1.5,
        "FMCG":         -0.2,
        "Metals":       -1.1,
        "Energy":        0.6,
        "Auto":          0.9,
        "Telecom":       0.3,
        "Consumer":      1.2,
        "NBFC":          0.8,
        "Power":         0.4,
        "Cement":       -0.3,
        "Healthcare":    1.6,
    }

    def _run(self, universe: list[str]) -> list[dict[str, Any]]:
        # Identify top 3 outperforming sectors
        sorted_sectors = sorted(
            self.MOCK_SECTOR_RS.items(),
            key=lambda x: x[1],
            reverse=True,
        )
        top_sectors = [s for s, rs in sorted_sectors[:3] if rs > 0]

        candidates = []
        store = HistoricalStore()
        rng   = random.Random(33)

        for symbol in universe:
            if self.is_blocked(symbol):
                continue
            stock_info = ALL_MOCK_STOCKS.get(symbol, {})
            sector = stock_info.get("sector", "")
            if sector not in top_sectors:
                continue

            bars = store.get_ohlcv(symbol, as_of=utc_now(), lookback_days=40)
            if not bars or len(bars) < 15:
                continue

            ind   = compute_all(bars)
            rs    = self.MOCK_SECTOR_RS.get(sector, 0)
            score = 50 + rs * 8 + rng.uniform(0, 10)
            score = min(score, 88)

            close    = float(bars[-1].close)
            atr      = ind.get("atr_14", close * 0.018)
            stop_loss = close - 1.5 * atr
            target_1  = close + 2.5 * atr
            rr = (target_1 - close) / max(close - stop_loss, 0.01)
            if rr < 2.0:
                continue

            # Macro explanation for the sector move
            macro_reason = {
                "IT":            "DXY strong — export earnings benefit",
                "Banking":       "FII inflows picking up, rate cycle turning",
                "Pharma":        "Export recovery + domestic pricing power",
                "Capital Goods": "Govt capex cycle + order book strong",
                "Healthcare":    "Defensive rotation in elevated VIX",
            }.get(sector, f"{sector} sector outperforming Nifty by {rs:.1f}% (5d)")

            candidates.append(self._build_candidate(
                symbol=symbol,
                conviction_score=score,
                entry_low=close * 0.998,
                entry_high=close * 1.002,
                stop_loss=stop_loss,
                target_1=target_1,
                thesis=(
                    f"{symbol} in outperforming {sector} sector "
                    f"(+{rs:.1f}% vs Nifty, 5d). {macro_reason}."
                ),
                risks=[
                    "Sector rotation can reverse quickly",
                    "Macro trigger could shift — monitor DXY and FII",
                ],
                suggested_qty=max(1, int(3000 / max(close - stop_loss, 1))),
            ))

        return candidates


# ─────────────────────────────────────────────
# S4 — PENNY & SMALL CAP (HIGH RISK)
# ─────────────────────────────────────────────

class PennySmallCapScreener(BaseScreener):
    """
    Weekly run (Sunday 20:00 IST). HIGH RISK.
    Max 5 results always. Max 0.5% portfolio per stock.
    Documented in: SCREENERS_MODULE_SPEC.md §S4
    """

    name        = "s4_penny"
    description = "Quality small caps with institutional accumulation (HIGH RISK)"
    max_results = 5    # HARD CAP — never increase
    min_score   = 62.0

    def _run(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates = []
        store = HistoricalStore()
        fund  = FundamentalStore()
        rng   = random.Random(77)

        # Filter universe to only small/penny caps
        penny_universe = [
            s for s in universe
            if ALL_MOCK_STOCKS.get(s, {}).get("price", 500) < 100
        ]

        for symbol in penny_universe:
            if self.is_blocked(symbol):
                continue

            fund.ingest(symbol)
            data = fund.get_latest(symbol)
            if not data:
                continue

            roe  = data.get("roe_pct") or 0
            prm  = data.get("promoter_holding_pct") or 0
            plg  = data.get("promoter_pledging_pct") or 100
            rev  = data.get("revenue_growth_yoy_pct") or 0

            # Quality gate — stricter for small caps
            if roe < 10 or prm < 45 or plg > 5 or rev < 12:
                continue

            bars = store.get_ohlcv(symbol, as_of=utc_now(), lookback_days=60)
            if not bars or len(bars) < 20:
                continue

            ind       = compute_all(bars)
            vol_ratio = ind.get("vol_ratio", 1.0)
            if vol_ratio < 1.5:
                if rng.random() > 0.3:
                    continue

            score = 55 + rng.uniform(5, 20)
            score = min(score, 82)

            close     = float(bars[-1].close)
            atr       = ind.get("atr_14", close * 0.025)
            stop_loss = close - 2.0 * atr
            target_1  = close + 3.0 * atr

            rr = (target_1 - close) / max(close - stop_loss, 0.01)
            if rr < 2.0:
                continue

            candidates.append(self._build_candidate(
                symbol=symbol,
                conviction_score=score,
                entry_low=close * 0.996,
                entry_high=close * 1.004,
                stop_loss=stop_loss,
                target_1=target_1,
                thesis=(
                    f"⚠️ HIGH RISK — Small cap. "
                    f"{symbol}: ROE {roe:.1f}%, Promoter {prm:.1f}%, "
                    f"Rev growth {rev:.1f}%, No pledging. "
                    f"Volume pickup {vol_ratio:.1f}×."
                ),
                risks=[
                    "⚠️ SMALL CAP — can fall 50%+ quickly",
                    "Low liquidity — exit may be difficult",
                    "Max allocation: 0.5% of portfolio",
                    f"Hard stop at ₹{stop_loss:.2f}",
                ],
                suggested_qty=max(1, int(1500 / max(close - stop_loss, 1))),
                extra={"high_risk": True, "max_allocation_pct": 0.5},
            ))

        return candidates


# ─────────────────────────────────────────────
# S5 — SMART INSTITUTIONAL TRACKER
# ─────────────────────────────────────────────

class SmartInstitutionalScreener(BaseScreener):
    """
    Daily run (16:30 IST).
    Tracks where promoters, FIIs, and large block buyers are deploying capital.
    Documented in: SCREENERS_MODULE_SPEC.md §S5
    """

    name        = "s5_institutional"
    description = "Where promoters, FIIs, and block buyers are putting money"
    max_results = 5
    min_score   = 55.0

    # Mock institutional signals — in live mode these come from NSE bulk/block feeds
    MOCK_SIGNALS = [
        {"symbol": "RELIANCE",   "type": "promoter_buy",   "amount_cr": 250,
         "reliability": 90},
        {"symbol": "TCS",        "type": "fii_accumulation","amount_cr": 800,
         "reliability": 85},
        {"symbol": "HDFCBANK",   "type": "block_deal",      "amount_cr": 350,
         "reliability": 80},
        {"symbol": "INFY",       "type": "fii_accumulation","amount_cr": 420,
         "reliability": 80},
        {"symbol": "BAJFINANCE", "type": "promoter_buy",    "amount_cr": 180,
         "reliability": 90},
        {"symbol": "MARUTI",     "type": "block_deal",      "amount_cr": 280,
         "reliability": 75},
    ]

    SIGNAL_SCORE = {
        "promoter_buy":    90,
        "fii_accumulation":80,
        "block_deal":      75,
        "unusual_oi":      50,   # trade_card_eligible=False for OI-only signals
    }

    def _run(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates = []
        store = HistoricalStore()

        for sig in self.MOCK_SIGNALS:
            symbol = sig["symbol"]
            if self.is_blocked(symbol):
                continue
            if symbol not in universe:
                continue

            sig_type    = sig["type"]
            base_score  = self.SIGNAL_SCORE.get(sig_type, 60)
            reliability = sig["reliability"]
            score       = (base_score + reliability) / 2

            bars = store.get_ohlcv(symbol, as_of=utc_now(), lookback_days=60)
            if not bars or len(bars) < 20:
                continue

            ind       = compute_all(bars)
            close     = float(bars[-1].close)
            atr       = ind.get("atr_14", close * 0.02)
            stop_loss = close - 1.5 * atr
            target_1  = close + 2.5 * atr

            rr = (target_1 - close) / max(close - stop_loss, 0.01)
            if rr < 2.0:
                continue

            type_labels = {
                "promoter_buy":    f"Promoter open market purchase "
                                   f"₹{sig['amount_cr']}Cr",
                "fii_accumulation":f"FII net accumulation "
                                   f"₹{sig['amount_cr']}Cr (30d)",
                "block_deal":      f"Block deal ₹{sig['amount_cr']}Cr",
            }

            candidates.append(self._build_candidate(
                symbol=symbol,
                conviction_score=score,
                entry_low=close * 0.998,
                entry_high=close * 1.002,
                stop_loss=stop_loss,
                target_1=target_1,
                thesis=(
                    f"Smart money signal: {type_labels.get(sig_type, sig_type)}. "
                    f"Reliability: {reliability}/100. "
                    f"Trend: {ind.get('trend_direction','?')}."
                ),
                risks=[
                    "Signal may be hedging, not directional",
                    "Check if block deal is buy or sell side",
                    f"Stop at ₹{stop_loss:.2f}",
                ],
                suggested_qty=max(1, int(3000 / max(close - stop_loss, 1))),
                extra={"signal_type": sig_type, "signal_amount_cr": sig["amount_cr"]},
            ))

        return candidates


# ─────────────────────────────────────────────
# S6 — MF CONVICTION WATCHLIST
# ─────────────────────────────────────────────

class MFConvictionScreener(BaseScreener):
    """
    Monthly run (11th of month after AMFI disclosure).
    Stocks that quality mutual funds are accumulating.
    Documented in: SCREENERS_MODULE_SPEC.md §S6
    """

    name        = "s6_mf"
    description = "Stocks quality mutual funds are accumulating"
    max_results = 10
    min_score   = 52.0

    # Mock MF conviction data — in live mode from AMFI monthly portfolios
    MOCK_MF_HOLDINGS = {
        "RELIANCE":   {"funds_holding": 18, "trend": "INCREASING", "score": 88},
        "HDFCBANK":   {"funds_holding": 22, "trend": "INCREASING", "score": 92},
        "TCS":        {"funds_holding": 20, "trend": "STABLE",     "score": 85},
        "INFY":       {"funds_holding": 17, "trend": "INCREASING", "score": 82},
        "ICICIBANK":  {"funds_holding": 19, "trend": "INCREASING", "score": 87},
        "HINDUNILVR": {"funds_holding": 15, "trend": "DECREASING", "score": 68},
        "AXISBANK":   {"funds_holding": 14, "trend": "INCREASING", "score": 80},
        "BHARTIARTL": {"funds_holding": 12, "trend": "NEW_ENTRY",  "score": 78},
        "TITAN":      {"funds_holding": 13, "trend": "INCREASING", "score": 79},
        "LT":         {"funds_holding": 16, "trend": "STABLE",     "score": 76},
    }

    def _run(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates = []
        store = HistoricalStore()

        for symbol, mf_data in self.MOCK_MF_HOLDINGS.items():
            if self.is_blocked(symbol):
                continue
            if symbol not in universe:
                continue

            trend = mf_data["trend"]
            if trend == "DECREASING":
                continue   # Exit signal — not a buy signal

            score = mf_data["score"]
            if trend == "NEW_ENTRY":
                score += 5   # New entry by multiple quality funds = strong signal

            bars = store.get_ohlcv(symbol, as_of=utc_now(), lookback_days=100)
            if not bars or len(bars) < 30:
                continue

            ind       = compute_all(bars)
            close     = float(bars[-1].close)
            atr       = ind.get("atr_14", close * 0.018)
            stop_loss = close - 2.0 * atr   # Wider stop for long-term holds
            target_1  = close + 4.0 * atr   # Higher target for long-term

            rr = (target_1 - close) / max(close - stop_loss, 0.01)
            if rr < 2.0:
                continue

            trend_labels = {
                "INCREASING": "accumulating (↑ allocation)",
                "STABLE":     "holding (stable allocation)",
                "NEW_ENTRY":  "new position (entered this month)",
            }

            candidates.append(self._build_candidate(
                symbol=symbol,
                conviction_score=score,
                entry_low=close * 0.995,
                entry_high=close * 1.005,
                stop_loss=stop_loss,
                target_1=target_1,
                thesis=(
                    f"{mf_data['funds_holding']} quality funds "
                    f"{trend_labels.get(trend,'?')}. "
                    f"MF consensus = institutional validation. "
                    f"Best for long-term (3m+) positional holds."
                ),
                risks=[
                    "MF data is 1 month old — situation may have changed",
                    "Sector rotation could affect fund mandates",
                    "Suitable for positional/long-term only",
                ],
                suggested_qty=max(1, int(3000 / max(close - stop_loss, 1))),
                extra={
                    "funds_holding": mf_data["funds_holding"],
                    "mf_trend": trend,
                    "holding_type": "long_term",
                },
            ))

        return candidates


# ─────────────────────────────────────────────
# S7 — FOREX OPPORTUNITY SCANNER
# ─────────────────────────────────────────────

class ForexOpportunityScreener(BaseScreener):
    """
    Every 4 hours (H4 close).
    Global forex setups with MTF alignment and COT context.
    Documented in: SCREENERS_MODULE_SPEC.md §S7, GLOBAL_FOREX_MODULE.md §F6
    """

    name        = "s7_forex"
    description = "Global forex setups with MTF alignment and COT confirmation"
    max_results = 3
    min_score   = 55.0

    TIER1_PAIRS = [
        "EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
        "XAUUSD", "USDINR", "USDBRNT",
    ]

    def _run(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates = []
        forex = ForexConnector()
        rng   = random.Random(55)

        for pair in self.TIER1_PAIRS:
            # Fetch daily bars
            bars = forex.get_forex_ohlcv(pair, periods=100, timeframe="1day")
            if not bars or len(bars) < 30:
                continue

            ind = compute_all(bars)
            cot = forex.get_cot_data(pair)

            trend      = ind.get("trend_direction", "NEUTRAL")
            rsi        = ind.get("rsi_14", 50)
            cot_index  = cot.get("cot_index", 50)
            cot_class  = cot.get("classification", "NEUTRAL")

            # Gate: must have a directional trend
            if trend not in ("BULLISH", "LEANING_BULLISH",
                             "BEARISH", "LEANING_BEARISH"):
                continue

            # Gate: RSI not extreme (not overbought/oversold at entry)
            if not (35 <= rsi <= 65):
                if rng.random() > 0.3:
                    continue

            is_long = trend in ("BULLISH", "LEANING_BULLISH")
            direction = "BUY" if is_long else "SELL"

            # COT alignment check (not a trigger — just a filter)
            cot_aligned = (
                (is_long  and cot_class in ("BULLISH", "EXTREME_BULLISH")) or
                (not is_long and cot_class in ("BEARISH", "EXTREME_BEARISH"))
            )

            # Scoring
            score = 50.0
            if trend in ("BULLISH", "BEARISH"):
                score += 12
            elif trend in ("LEANING_BULLISH", "LEANING_BEARISH"):
                score += 6
            if ind.get("ma_stack_bullish") and is_long:
                score += 8
            if cot_aligned:
                score += 8
            score += rng.uniform(0, 8)
            score = min(score, 88)

            # Determine if INR pair (execution eligible on NSE)
            is_inr_pair = pair.endswith("INR")

            # Price levels (forex — 5 decimal places for majors)
            close = float(bars[-1].close)
            atr   = ind.get("atr_14", close * 0.008)

            if is_long:
                stop_loss = close - 1.5 * atr
                target_1  = close + 3.1 * atr   # 3.1/1.5 = 2.07 R:R
                target_2  = close + 5.0 * atr
            else:
                stop_loss = close + 1.5 * atr
                target_1  = close - 3.1 * atr   # 3.1/1.5 = 2.07 R:R
                target_2  = close - 5.0 * atr

            pips = abs(close - stop_loss) * (100 if "JPY" in pair else 10000)

            exec_note = (
                "Execute on NSE Currency Derivatives"
                if is_inr_pair else
                "⚠️ ANALYSIS ONLY — Execute via international broker"
            )

            candidates.append(self._build_candidate(
                symbol=pair,
                conviction_score=score,
                entry_low=min(close * 0.999, close),
                entry_high=max(close * 1.001, close),
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                direction=direction,
                thesis=(
                    f"{pair} {direction}: Trend {trend}, "
                    f"RSI {rsi:.1f}, COT {cot_class} (index {cot_index:.0f}/100). "
                    f"ATR-based stop {pips:.1f} pips. {exec_note}."
                ),
                risks=[
                    "Economic surprise can spike spreads 5-10×",
                    "Check economic calendar before entry",
                    exec_note,
                    "COT data is 3 days old when published",
                ],
                suggested_qty=1,   # Operator sizes based on their account
                extra={
                    "is_inr_pair": is_inr_pair,
                    "execution_eligible": is_inr_pair,
                    "amber_banner": not is_inr_pair,
                    "cot_index": cot_index,
                    "cot_classification": cot_class,
                    "pips_to_stop": round(pips, 1),
                    "session_note": "Best executed in London-NY overlap (18:30-22:30 IST)",
                },
            ))

        return candidates
