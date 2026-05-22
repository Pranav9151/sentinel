"""
sentinel/strategies/value_momentum_in.py
========================================
Sprint 7 Strategy 2 candidate: Value + Momentum for Indian equities.

This module is research-only. It can rank, signal, and backtest candidates, but
it does not place orders and has no broker dependency. Promotion to live remains
blocked by Sprint 7 sign-off and acceptance gates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from sentinel.core.types import utc_now
from sentinel.data.mock_data import ALL_MOCK_STOCKS, mock_fundamentals, mock_ohlcv
from sentinel.strategies.strategy1_momentum import (
    compute_atr,
    compute_momentum_score,
    compute_position_size,
)

STRATEGY_VERSION = "2.0.0-sprint7-research"


@dataclass
class ValueMomentumRankEntry:
    symbol: str
    combined_score: float
    value_score: float
    quality_score: float
    momentum_score: float
    r_squared: float
    price_now: float
    atr_20: float
    suggested_quantity: int
    rank: int = 0


@dataclass
class ValueMomentumSignal:
    symbol: str
    action: str
    combined_score: float
    rank: int
    suggested_quantity: int
    entry_price: float
    stop_loss: float
    target_1: float
    generated_at: str
    strategy_version: str = STRATEGY_VERSION
    notes: str = ""

    @property
    def risk_reward(self) -> float:
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.target_1 - self.entry_price)
        return round(reward / risk, 2) if risk > 0 else 0.0


@dataclass
class ValueMomentumBacktestResult:
    strategy_name: str
    oos_sharpe: float
    oos_total_return_pct: float
    oos_max_drawdown_pct: float
    oos_n_trades: int
    deflated_sharpe_ratio: float
    lockbox_within_model_bounds: bool
    computed_at: str

    def passes_research_gate(self) -> bool:
        """Sprint 7 research gate, not a live-trading promotion."""
        return (
            self.oos_sharpe >= 0.6
            and self.deflated_sharpe_ratio > 0.95
            and self.lockbox_within_model_bounds
        )


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _quality_score(f: dict) -> float:
    score = 0.0
    score += _clamp((f.get("roe_pct") or 0) / 25 * 25, 0, 25)
    score += _clamp((f.get("revenue_growth_yoy_pct") or 0) / 25 * 20, 0, 20)
    score += 20 if (f.get("debt_to_equity") or 99) <= 0.7 else 8
    score += 20 if (f.get("promoter_pledging_pct") or 0) <= 5 else 5
    score += _clamp(((f.get("promoter_holding_pct") or 0) - 25) / 35 * 15, 0, 15)
    return round(_clamp(score), 2)


def _value_score(f: dict) -> float:
    pe = f.get("pe_ratio") or 0
    sector_pe = f.get("sector_pe") or pe
    discount = ((sector_pe - pe) / sector_pe * 100) if sector_pe else 0
    market_cap = f.get("market_cap_cr") or 0
    value = 50 + discount * 1.2
    if market_cap < 500:
        value -= 15
    return round(_clamp(value), 2)


class ValueMomentumIN:
    """Research-only Strategy 2 candidate."""

    def __init__(self, portfolio_value: float = 300_000.0, top_n: int = 8) -> None:
        self.portfolio_value = portfolio_value
        self.top_n = top_n

    def _get_bars(self, symbol: str, days: int = 300) -> dict[str, list[float]]:
        raw = mock_ohlcv(symbol, days=days)
        return {
            "closes": [float(b["close"]) for b in raw],
            "highs": [float(b["high"]) for b in raw],
            "lows": [float(b["low"]) for b in raw],
        }

    def rank_universe(
        self,
        bars_by_symbol: Optional[dict[str, dict[str, list[float]]]] = None,
    ) -> list[ValueMomentumRankEntry]:
        symbols = list(ALL_MOCK_STOCKS.keys())
        if bars_by_symbol is None:
            bars_by_symbol = {s: self._get_bars(s) for s in symbols}

        entries: list[ValueMomentumRankEntry] = []
        for symbol in symbols:
            bars = bars_by_symbol.get(symbol)
            if not bars or len(bars["closes"]) < 120:
                continue

            f = mock_fundamentals(symbol)
            value = _value_score(f)
            quality = _quality_score(f)
            mom, r2, _ = compute_momentum_score(bars["closes"], lookback=90)
            momentum = _clamp(50 + mom * 100)
            combined = (0.35 * value) + (0.35 * quality) + (0.30 * momentum)
            atr = compute_atr(bars["closes"], bars["highs"], bars["lows"], 20)
            price = bars["closes"][-1]

            if quality < 45 or value < 35 or mom <= 0:
                continue

            entries.append(ValueMomentumRankEntry(
                symbol=symbol,
                combined_score=round(combined, 2),
                value_score=value,
                quality_score=quality,
                momentum_score=round(mom, 6),
                r_squared=r2,
                price_now=price,
                atr_20=atr,
                suggested_quantity=compute_position_size(
                    self.portfolio_value, atr, price=price, max_capital_pct=8.0
                ),
            ))

        entries.sort(key=lambda e: e.combined_score, reverse=True)
        for i, entry in enumerate(entries, 1):
            entry.rank = i
        return entries

    def run(self) -> list[ValueMomentumSignal]:
        signals: list[ValueMomentumSignal] = []
        for entry in self.rank_universe()[: self.top_n]:
            stop = entry.price_now - (2.0 * entry.atr_20)
            target = entry.price_now + (4.2 * entry.atr_20)
            signals.append(ValueMomentumSignal(
                symbol=entry.symbol,
                action="enter_long",
                combined_score=entry.combined_score,
                rank=entry.rank,
                suggested_quantity=entry.suggested_quantity,
                entry_price=entry.price_now,
                stop_loss=max(stop, entry.price_now * 0.82),
                target_1=target,
                generated_at=utc_now().isoformat(),
                notes=(
                    f"value={entry.value_score:.1f} quality={entry.quality_score:.1f} "
                    f"momentum={entry.momentum_score:.3f}"
                ),
            ))
        return signals

    def backtest(self) -> ValueMomentumBacktestResult:
        bars_by_symbol = {s: self._get_bars(s) for s in ALL_MOCK_STOCKS}
        ranked = self.rank_universe(bars_by_symbol)
        selected = [e.symbol for e in ranked[: self.top_n]]
        if not selected:
            return ValueMomentumBacktestResult(
                "ValueMomentumIN", 0.0, 0.0, 0.0, 0, 0.0, False, utc_now().isoformat()
            )

        split = 180
        daily_pnl: list[float] = []
        capital_per_name = self.portfolio_value / len(selected)
        for i in range(split + 1, 300):
            pnl = 0.0
            for symbol in selected:
                closes = bars_by_symbol[symbol]["closes"]
                prev, cur = closes[i - 1], closes[i]
                qty = capital_per_name / max(prev, 0.01)
                pnl += (cur - prev) * qty
            daily_pnl.append(pnl)

        total_return = sum(daily_pnl) / self.portfolio_value * 100
        sharpe = self._sharpe(daily_pnl)
        max_dd = self._max_drawdown(daily_pnl)
        dsr = self._deflated_sharpe_proxy(sharpe, len(daily_pnl), trials=2)
        return ValueMomentumBacktestResult(
            strategy_name="ValueMomentumIN",
            oos_sharpe=round(sharpe, 3),
            oos_total_return_pct=round(total_return, 2),
            oos_max_drawdown_pct=round(max_dd, 2),
            oos_n_trades=len(selected),
            deflated_sharpe_ratio=round(dsr, 3),
            lockbox_within_model_bounds=max_dd <= 12.0,
            computed_at=utc_now().isoformat(),
        )

    @staticmethod
    def _sharpe(daily_pnl: list[float]) -> float:
        if len(daily_pnl) < 5:
            return 0.0
        mean = sum(daily_pnl) / len(daily_pnl)
        variance = sum((x - mean) ** 2 for x in daily_pnl) / len(daily_pnl)
        std = math.sqrt(variance)
        return 0.0 if std == 0 else (mean / std) * math.sqrt(252)

    @staticmethod
    def _max_drawdown(daily_pnl: list[float]) -> float:
        equity = 100.0
        peak = equity
        max_dd = 0.0
        for pnl in daily_pnl:
            equity += pnl / 3000.0
            peak = max(peak, equity)
            max_dd = max(max_dd, (peak - equity) / peak * 100)
        return max_dd

    @staticmethod
    def _deflated_sharpe_proxy(sharpe: float, observations: int, trials: int) -> float:
        if observations <= 1:
            return 0.0
        penalty = math.sqrt(max(1.0, math.log(max(2, trials))) / observations)
        adjusted = sharpe - penalty
        return 1 / (1 + math.exp(-adjusted))
