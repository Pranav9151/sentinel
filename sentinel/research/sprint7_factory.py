"""
Sprint 7 research factory orchestration.

Builds the operator-visible Strategy 1 vs Strategy 2 research snapshot:
backtest metrics, inverse-volatility allocation, correlation matrix, and
promotion gates. This module never places orders and never enables live
deployment by itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from sentinel.allocation.correlation_monitor import CorrelationMonitor, StrategyReturnSeries
from sentinel.allocation.hrp_allocator import HierarchicalRiskParityAllocator
from sentinel.allocation.meta_allocator import InverseVarianceAllocator, StrategyRiskSnapshot
from sentinel.core.config import OperatorProfile, load_config
from sentinel.core.types import utc_now
from sentinel.data.mock_data import mock_ohlcv
from sentinel.strategies.pairs_mean_reversion_in import PairsMeanReversionIN
from sentinel.strategies.strategy1_momentum import CrossSectionalMomentumIN
from sentinel.strategies.value_momentum_in import ValueMomentumIN


@dataclass(frozen=True)
class StrategyResearchMetric:
    strategy_id: str
    display_name: str
    status: str
    oos_sharpe: float
    oos_total_return_pct: float
    oos_max_drawdown_pct: float
    oos_n_trades: int
    annualized_vol_pct: float
    research_gate_passed: bool


@dataclass(frozen=True)
class PromotionGate:
    name: str
    passed: bool
    detail: str
    severity: str = "blocker"


@dataclass(frozen=True)
class Sprint7ResearchSnapshot:
    generated_at: str
    stage: str
    strategy_metrics: list[StrategyResearchMetric]
    target_weights: dict[str, float]
    correlation_matrix: dict[str, dict[str, float]]
    high_correlation_pairs: list[tuple[str, str, float]]
    allocation_method: str
    gates: list[PromotionGate]
    promotion_status: str
    promotion_memo: str

    @property
    def live_approved(self) -> bool:
        return all(g.passed for g in self.gates)

    @property
    def live_blockers(self) -> list[PromotionGate]:
        return [g for g in self.gates if not g.passed and g.severity == "blocker"]


def build_sprint7_research_snapshot(
    profile: OperatorProfile | None = None,
    lookback_days: int = 120,
) -> Sprint7ResearchSnapshot:
    """Build a complete Strategy Factory snapshot for Sprint 7."""
    profile = profile or load_config()
    portfolio_value = float(profile.total_portfolio_value_inr)

    strategy1 = CrossSectionalMomentumIN(portfolio_value=portfolio_value, top_n=5)
    strategy2 = ValueMomentumIN(portfolio_value=portfolio_value, top_n=5)
    strategy3 = PairsMeanReversionIN(portfolio_value=portfolio_value)

    s1_backtest = strategy1.backtest()
    s2_backtest = strategy2.backtest()
    s3_backtest = strategy3.backtest()

    s1_symbols = [entry.symbol for entry in strategy1.rank_universe()[:5]]
    s2_symbols = [entry.symbol for entry in strategy2.rank_universe()[:5]]
    return_series = [
        StrategyReturnSeries("strategy1_momentum", _equal_weight_returns(s1_symbols, lookback_days)),
        StrategyReturnSeries("strategy2_value_momentum", _equal_weight_returns(s2_symbols, lookback_days)),
        StrategyReturnSeries(
            "strategy3_pairs_mean_reversion",
            _pairs_reversion_returns(strategy3.pairs, lookback_days),
        ),
    ]

    monitor = CorrelationMonitor()
    correlation_matrix = monitor.matrix(return_series)
    high_corr = monitor.high_correlation_pairs(return_series, threshold=0.4)

    s1_vol = _annualized_vol_pct(return_series[0].daily_returns)
    s2_vol = _annualized_vol_pct(return_series[1].daily_returns)
    s3_vol = _annualized_vol_pct(return_series[2].daily_returns)
    hrp = HierarchicalRiskParityAllocator().allocate(return_series)
    if hrp.weights:
        target_weights = hrp.weights
        allocation_method = hrp.method
    else:
        target_weights = InverseVarianceAllocator().allocate([
            StrategyRiskSnapshot("strategy1_momentum", annualized_vol_pct=s1_vol, max_weight=0.70),
            StrategyRiskSnapshot("strategy2_value_momentum", annualized_vol_pct=s2_vol, max_weight=0.70),
            StrategyRiskSnapshot(
                "strategy3_pairs_mean_reversion",
                annualized_vol_pct=s3_vol,
                max_weight=0.35,
            ),
        ])
        allocation_method = "inverse_variance"

    metrics = [
        StrategyResearchMetric(
            strategy_id="strategy1_momentum",
            display_name="Strategy 1 - Cross-sectional Momentum",
            status="incumbent",
            oos_sharpe=s1_backtest.oos_sharpe,
            oos_total_return_pct=s1_backtest.oos_total_return_pct,
            oos_max_drawdown_pct=s1_backtest.oos_max_drawdown_pct,
            oos_n_trades=s1_backtest.oos_n_trades,
            annualized_vol_pct=s1_vol,
            research_gate_passed=s1_backtest.passes_acceptance_gate(),
        ),
        StrategyResearchMetric(
            strategy_id="strategy2_value_momentum",
            display_name="Strategy 2 - Value Momentum",
            status="research_candidate",
            oos_sharpe=s2_backtest.oos_sharpe,
            oos_total_return_pct=s2_backtest.oos_total_return_pct,
            oos_max_drawdown_pct=s2_backtest.oos_max_drawdown_pct,
            oos_n_trades=s2_backtest.oos_n_trades,
            annualized_vol_pct=s2_vol,
            research_gate_passed=s2_backtest.passes_research_gate(),
        ),
        StrategyResearchMetric(
            strategy_id="strategy3_pairs_mean_reversion",
            display_name="Strategy 3 - Pairs Mean Reversion",
            status="research_candidate",
            oos_sharpe=s3_backtest.oos_sharpe,
            oos_total_return_pct=s3_backtest.oos_total_return_pct,
            oos_max_drawdown_pct=s3_backtest.oos_max_drawdown_pct,
            oos_n_trades=s3_backtest.oos_n_trades,
            annualized_vol_pct=s3_vol,
            research_gate_passed=s3_backtest.passes_research_gate(),
        ),
    ]

    gates = _promotion_gates(profile, metrics, correlation_matrix)
    status = "LIVE_APPROVED" if all(g.passed for g in gates) else "RESEARCH_ONLY"
    memo = _promotion_memo(status, gates, metrics, target_weights)

    return Sprint7ResearchSnapshot(
        generated_at=utc_now().isoformat(),
        stage=profile.trading_stage,
        strategy_metrics=metrics,
        target_weights=target_weights,
        correlation_matrix=correlation_matrix,
        high_correlation_pairs=high_corr,
        allocation_method=allocation_method,
        gates=gates,
        promotion_status=status,
        promotion_memo=memo,
    )


def _promotion_gates(
    profile: OperatorProfile,
    metrics: Sequence[StrategyResearchMetric],
    correlation_matrix: dict[str, dict[str, float]],
) -> list[PromotionGate]:
    metric_by_id = {m.strategy_id: m for m in metrics}
    corr = correlation_matrix.get("strategy1_momentum", {}).get(
        "strategy2_value_momentum",
        1.0,
    )
    return [
        PromotionGate(
            "Strategy 1 acceptance gate",
            metric_by_id["strategy1_momentum"].research_gate_passed,
            "Strategy 1 OOS Sharpe must remain above the Sprint 5 acceptance floor.",
        ),
        PromotionGate(
            "Strategy 2 research gate",
            metric_by_id["strategy2_value_momentum"].research_gate_passed,
            "Strategy 2 needs OOS Sharpe >= 0.6, DSR > 0.95, and bounded drawdown.",
        ),
        PromotionGate(
            "Strategy 3 research gate",
            metric_by_id["strategy3_pairs_mean_reversion"].research_gate_passed,
            "Strategy 3 must clear the Sprint 8 research lifecycle before promotion.",
        ),
        PromotionGate(
            "Strategy correlation gate",
            abs(corr) < 0.4,
            f"Strategy 1 vs Strategy 2 correlation is {corr:.2f}; Sprint 7 requires < 0.40.",
        ),
        PromotionGate(
            "90 clean live days",
            False,
            "No auditable 90-day clean live-trading record is present in the repo.",
        ),
        PromotionGate(
            "Emergency fund gate",
            profile.emergency_fund_months_confirmed >= 6,
            (
                f"Emergency fund months confirmed: {profile.emergency_fund_months_confirmed}; "
                "Sprint 6+ requires >= 6."
            ),
        ),
        PromotionGate(
            "Operator sign-off gate",
            bool(profile.section_7_6_signoff_commit_hash),
            "Section 7.6 sign-off commit hash must be recorded before live expansion.",
        ),
        PromotionGate(
            "Trading stage gate",
            profile.trading_stage in {"quarantine", "production"},
            f"Current stage is {profile.trading_stage!r}; live expansion is blocked in paper mode.",
        ),
    ]


def _promotion_memo(
    status: str,
    gates: Sequence[PromotionGate],
    metrics: Sequence[StrategyResearchMetric],
    weights: dict[str, float],
) -> str:
    blockers = [g for g in gates if not g.passed and g.severity == "blocker"]
    lines = [
        f"Promotion status: {status}",
        "",
        "Strategy metrics:",
    ]
    for metric in metrics:
        weight = weights.get(metric.strategy_id, 0.0) * 100
        lines.append(
            f"- {metric.display_name}: Sharpe {metric.oos_sharpe:.2f}, "
            f"return {metric.oos_total_return_pct:+.2f}%, "
            f"max DD {metric.oos_max_drawdown_pct:.2f}%, "
            f"research weight {weight:.1f}%"
        )
    lines.extend(["", "Blocking gates:"])
    if blockers:
        lines.extend(f"- {gate.name}: {gate.detail}" for gate in blockers)
    else:
        lines.append("- None")
    return "\n".join(lines)


def _equal_weight_returns(symbols: Sequence[str], lookback_days: int) -> list[float]:
    if not symbols:
        return []

    per_symbol: list[list[float]] = []
    for symbol in symbols:
        bars = mock_ohlcv(symbol, days=lookback_days + 1)
        closes = [float(bar["close"]) for bar in bars]
        returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
            if closes[i - 1] > 0
        ]
        if returns:
            per_symbol.append(returns)

    if not per_symbol:
        return []

    n = min(len(series) for series in per_symbol)
    return [
        round(sum(series[-n + i] for series in per_symbol) / len(per_symbol), 6)
        for i in range(n)
    ]


def _pairs_reversion_returns(
    pairs: Sequence[tuple[str, str]],
    lookback_days: int,
) -> list[float]:
    per_pair: list[list[float]] = []
    for a, b in pairs:
        a_bars = mock_ohlcv(a, days=lookback_days + 121)
        b_bars = mock_ohlcv(b, days=lookback_days + 121)
        a_closes = [float(bar["close"]) for bar in a_bars]
        b_closes = [float(bar["close"]) for bar in b_bars]
        n = min(len(a_closes), len(b_closes))
        ratios = [
            a_closes[-n + i] / b_closes[-n + i]
            for i in range(n)
            if b_closes[-n + i] > 0
        ]
        returns: list[float] = []
        for i in range(121, len(ratios)):
            window = ratios[i - 120:i]
            mean = sum(window) / len(window)
            std = _std(window)
            if std <= 0:
                returns.append(0.0)
                continue
            z = (ratios[i - 1] - mean) / std
            direction = -1 if z > 0 else 1
            ratio_return = (ratios[i] - ratios[i - 1]) / max(abs(ratios[i - 1]), 1e-9)
            returns.append(round(direction * ratio_return, 6))
        if returns:
            per_pair.append(returns[-lookback_days:])

    if not per_pair:
        return []
    n = min(len(series) for series in per_pair)
    return [
        round(sum(series[-n + i] for series in per_pair) / len(per_pair), 6)
        for i in range(n)
    ]


def _annualized_vol_pct(daily_returns: Sequence[float]) -> float:
    if len(daily_returns) < 2:
        return 0.0
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((value - mean) ** 2 for value in daily_returns) / len(daily_returns)
    return round(math.sqrt(variance) * math.sqrt(252) * 100, 2)


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
