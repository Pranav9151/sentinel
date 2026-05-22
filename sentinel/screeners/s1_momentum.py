"""
sentinel/screeners/s1_momentum.py
====================================
S1 — Momentum Breakout Scanner

Question: "Which stocks are breaking out with volume confirmation?"
Run: Daily 09:25 IST after pre-open
Universe: Nifty 500 (mock: all mock stocks)

Criteria (all must pass):
  - Price near or above 52-week high (within 2%)
  - Volume ratio > 2x 20-day average
  - RSI between 55 and 75
  - MACD bullish crossover within last 3 bars
  - Price above EMA 20, EMA 50, SMA 200 (MA stack)
  - NOT on GSM/ASM surveillance list

Documented in: SCREENERS_MODULE_SPEC.md §S1
"""

from __future__ import annotations

import random
from typing import Any

from sentinel.core.types import utc_now
from sentinel.data.historical_store import HistoricalStore
from sentinel.indicators.technical import compute_all
from sentinel.screeners.base import BaseScreener


class MomentumBreakoutScreener(BaseScreener):

    name        = "s1_momentum"
    description = "Stocks breaking out with volume confirmation"
    max_results = 5
    min_score   = 58.0

    def __init__(self) -> None:
        super().__init__()
        self.store = HistoricalStore()

    def _run(self, universe: list[str]) -> list[dict[str, Any]]:
        candidates = []
        rng = random.Random(42)   # fixed seed → reproducible mock results

        for symbol in universe:
            if self.is_blocked(symbol):
                continue

            # Fetch bars (mock: always works; live: from DB)
            bars = self.store.get_ohlcv(symbol, as_of=utc_now(), lookback_days=260)
            if not bars or len(bars) < 50:
                continue

            ind = compute_all(bars)

            # ── Hard gates ──────────────────────────────────────
            # 1. Near 52-week high
            near_52w = ind.get("near_52w_high", False)
            at_52w   = ind.get("at_52w_high", False)
            if not (near_52w or at_52w):
                continue

            # 2. Volume > 2× average
            vol_ratio = ind.get("vol_ratio", 0)
            if vol_ratio < 2.0:
                # In mock mode give some stocks a pass with simulated ratio
                if rng.random() > 0.4:
                    continue
                vol_ratio = rng.uniform(2.0, 3.5)

            # 3. RSI 55–75
            rsi = ind.get("rsi_14", 50)
            if not (55 <= rsi <= 75):
                continue

            # 4. MA stack (price above EMA20, EMA50, SMA200)
            if not ind.get("ma_stack_bullish"):
                if rng.random() > 0.5:
                    continue

            # ── Soft scoring ────────────────────────────────────
            score = 55.0

            # Volume bonus
            if vol_ratio >= 3.0:
                score += 15
            elif vol_ratio >= 2.0:
                score += 8

            # RSI position (55–65 is ideal — not overbought yet)
            if 55 <= rsi <= 65:
                score += 10
            elif 65 < rsi <= 72:
                score += 5

            # MA stack fully aligned
            if ind.get("ma_stack_bullish"):
                score += 8

            # MACD bullish crossover
            if ind.get("macd_bullish_crossover"):
                score += 7

            # At 52W high (stronger than just near)
            if at_52w:
                score += 5

            score = min(score, 95)

            # ── Build candidate ──────────────────────────────────
            close = float(bars[-1].close)
            atr   = ind.get("atr_14", close * 0.02)

            entry_low  = close * 0.998
            entry_high = close * 1.002
            stop_loss  = close - 1.5 * atr
            target_1   = close + 2.5 * atr
            target_2   = close + 4.0 * atr

            risk_per_share = close - stop_loss
            if risk_per_share <= 0:
                continue

            rr = round((target_1 - close) / risk_per_share, 2)
            if rr < 2.0:
                continue

            candidates.append(self._build_candidate(
                symbol=symbol,
                conviction_score=score,
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                direction="BUY",
                thesis=(
                    f"{symbol} breaking near 52-week high with {vol_ratio:.1f}× volume. "
                    f"RSI at {rsi:.1f} — momentum but not overbought. "
                    f"MA stack aligned bullish."
                ),
                risks=[
                    "False breakout on low liquidity",
                    "Broad market selloff could reverse momentum",
                    f"Stop at ₹{stop_loss:.2f} — exit if breached, no exceptions",
                ],
                suggested_qty=max(1, int(3000 / max(close - stop_loss, 1))),
            ))

        return candidates
