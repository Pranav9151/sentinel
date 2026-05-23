"""
Sprint 8 Strategy 3 candidate: Indian equity pairs mean reversion.

Research-only. Finds simple price-ratio deviations between predefined liquid
pairs and produces paper/research signals with strict stop levels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from sentinel.core.types import utc_now
from sentinel.data.mock_data import mock_ohlcv

STRATEGY_VERSION = "3.0.0-sprint8-research"

DEFAULT_PAIRS = [
    ("HDFCBANK", "ICICIBANK"),
    ("TCS", "INFY"),
    ("TATASTEEL", "JSWSTEEL"),
    ("SBIN", "AXISBANK"),
]


@dataclass(frozen=True)
class PairRankEntry:
    pair_id: str
    long_symbol: str
    short_symbol: str
    z_score: float
    hedge_ratio: float
    spread_vol: float
    rank: int = 0


@dataclass(frozen=True)
class PairSignal:
    pair_id: str
    long_symbol: str
    short_symbol: str
    action: str
    z_score: float
    hedge_ratio: float
    notional_per_leg: float
    stop_z: float
    target_z: float
    generated_at: str
    strategy_version: str = STRATEGY_VERSION


@dataclass(frozen=True)
class PairsBacktestResult:
    strategy_name: str
    oos_sharpe: float
    oos_total_return_pct: float
    oos_max_drawdown_pct: float
    oos_n_trades: int
    deflated_sharpe_ratio: float
    computed_at: str

    def passes_research_gate(self) -> bool:
        return self.oos_sharpe >= 0.5 and self.deflated_sharpe_ratio > 0.90


class PairsMeanReversionIN:
    """Research-only Strategy 3 candidate."""

    def __init__(
        self,
        portfolio_value: float = 300_000.0,
        pairs: Sequence[tuple[str, str]] = DEFAULT_PAIRS,
        entry_z: float = 1.5,
    ) -> None:
        self.portfolio_value = portfolio_value
        self.pairs = list(pairs)
        self.entry_z = entry_z

    def rank_pairs(self, days: int = 260) -> list[PairRankEntry]:
        entries: list[PairRankEntry] = []
        for a, b in self.pairs:
            a_closes = _closes(a, days)
            b_closes = _closes(b, days)
            ratio = _ratio(a_closes, b_closes)
            if len(ratio) < 60:
                continue
            mean = sum(ratio[-120:]) / len(ratio[-120:])
            std = _std(ratio[-120:])
            if std <= 0:
                continue
            z = (ratio[-1] - mean) / std
            if abs(z) < self.entry_z:
                continue
            if z > 0:
                long_symbol, short_symbol = b, a
                hedge_ratio = ratio[-1]
            else:
                long_symbol, short_symbol = a, b
                hedge_ratio = 1 / ratio[-1] if ratio[-1] else 1.0
            entries.append(PairRankEntry(
                pair_id=f"{a}/{b}",
                long_symbol=long_symbol,
                short_symbol=short_symbol,
                z_score=round(z, 3),
                hedge_ratio=round(hedge_ratio, 4),
                spread_vol=round(std, 4),
            ))
        entries.sort(key=lambda e: abs(e.z_score), reverse=True)
        return [
            PairRankEntry(
                pair_id=e.pair_id,
                long_symbol=e.long_symbol,
                short_symbol=e.short_symbol,
                z_score=e.z_score,
                hedge_ratio=e.hedge_ratio,
                spread_vol=e.spread_vol,
                rank=i,
            )
            for i, e in enumerate(entries, 1)
        ]

    def run(self) -> list[PairSignal]:
        notional = min(self.portfolio_value * 0.05, 25_000.0)
        return [
            PairSignal(
                pair_id=e.pair_id,
                long_symbol=e.long_symbol,
                short_symbol=e.short_symbol,
                action="enter_pair_reversion",
                z_score=e.z_score,
                hedge_ratio=e.hedge_ratio,
                notional_per_leg=round(notional, 2),
                stop_z=3.0 if e.z_score > 0 else -3.0,
                target_z=0.25 if e.z_score > 0 else -0.25,
                generated_at=utc_now().isoformat(),
            )
            for e in self.rank_pairs()
        ]

    def backtest(self, days: int = 300) -> PairsBacktestResult:
        daily_pnl: list[float] = []
        trades = 0
        notional = self.portfolio_value * 0.05
        for a, b in self.pairs:
            a_closes = _closes(a, days)
            b_closes = _closes(b, days)
            ratio = _ratio(a_closes, b_closes)
            if len(ratio) < 160:
                continue
            for i in range(121, len(ratio) - 1):
                window = ratio[i - 120:i]
                mean = sum(window) / len(window)
                std = _std(window)
                if std <= 0:
                    continue
                z = (ratio[i] - mean) / std
                if abs(z) < self.entry_z:
                    daily_pnl.append(0.0)
                    continue
                direction = -1 if z > 0 else 1
                ratio_return = (ratio[i + 1] - ratio[i]) / max(abs(ratio[i]), 1e-9)
                daily_pnl.append(direction * ratio_return * notional)
                trades += 1
        if not daily_pnl:
            return PairsBacktestResult(
                "PairsMeanReversionIN", 0.0, 0.0, 0.0, 0, 0.0, utc_now().isoformat()
            )
        total_return = sum(daily_pnl) / self.portfolio_value * 100
        sharpe = _sharpe(daily_pnl)
        return PairsBacktestResult(
            strategy_name="PairsMeanReversionIN",
            oos_sharpe=round(sharpe, 3),
            oos_total_return_pct=round(total_return, 2),
            oos_max_drawdown_pct=round(_max_drawdown(daily_pnl), 2),
            oos_n_trades=trades,
            deflated_sharpe_ratio=round(_deflated_sharpe_proxy(sharpe, len(daily_pnl), 3), 3),
            computed_at=utc_now().isoformat(),
        )


def _closes(symbol: str, days: int) -> list[float]:
    return [float(bar["close"]) for bar in mock_ohlcv(symbol, days=days)]


def _ratio(a: Sequence[float], b: Sequence[float]) -> list[float]:
    n = min(len(a), len(b))
    return [a[-n + i] / b[-n + i] for i in range(n) if b[-n + i] > 0]


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def _sharpe(daily_pnl: Sequence[float]) -> float:
    if len(daily_pnl) < 5:
        return 0.0
    mean = sum(daily_pnl) / len(daily_pnl)
    std = _std(daily_pnl)
    return 0.0 if std == 0 else (mean / std) * math.sqrt(252)


def _max_drawdown(daily_pnl: Sequence[float]) -> float:
    equity = 100.0
    peak = equity
    max_dd = 0.0
    for pnl in daily_pnl:
        equity += pnl / 3000.0
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100)
    return max_dd


def _deflated_sharpe_proxy(sharpe: float, observations: int, trials: int) -> float:
    if observations <= 1:
        return 0.0
    penalty = math.sqrt(max(1.0, math.log(max(2, trials))) / observations)
    return 1 / (1 + math.exp(-(sharpe - penalty)))
