"""
Sprint 8 hierarchical risk parity allocator.

For 3+ strategies this approximates HRP with deterministic clustering and
recursive bisection. For 1-2 strategies, use the simpler inverse-variance
allocator instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sentinel.allocation.correlation_monitor import StrategyReturnSeries


@dataclass(frozen=True)
class HRPAllocationResult:
    weights: dict[str, float]
    ordered_strategy_ids: list[str]
    method: str


class HierarchicalRiskParityAllocator:
    """Correlation-aware allocation for three or more strategy return streams."""

    def allocate(self, series: Sequence[StrategyReturnSeries]) -> HRPAllocationResult:
        active = [s for s in series if len(s.daily_returns) >= 2]
        if len(active) < 3:
            return HRPAllocationResult({}, [s.strategy_id for s in active], "insufficient_strategies")

        cov = _covariance_matrix(active)
        ordered_ids = _quasi_diagonal_order(active, cov)
        weights = {sid: 1.0 for sid in ordered_ids}
        self._recursive_bisect(ordered_ids, cov, weights)
        total = sum(weights.values())
        normalized = {sid: round(weights[sid] / total, 4) for sid in ordered_ids}
        return HRPAllocationResult(normalized, ordered_ids, "hrp_recursive_bisection")

    def _recursive_bisect(
        self,
        ordered_ids: list[str],
        cov: dict[str, dict[str, float]],
        weights: dict[str, float],
    ) -> None:
        clusters = [ordered_ids]
        while clusters:
            cluster = clusters.pop(0)
            if len(cluster) <= 1:
                continue
            split = len(cluster) // 2
            left, right = cluster[:split], cluster[split:]
            left_var = _cluster_variance(left, cov)
            right_var = _cluster_variance(right, cov)
            denom = left_var + right_var
            if denom <= 0:
                left_alloc = 0.5
            else:
                left_alloc = right_var / denom
            right_alloc = 1.0 - left_alloc
            for sid in left:
                weights[sid] *= left_alloc
            for sid in right:
                weights[sid] *= right_alloc
            clusters.extend([left, right])


def _quasi_diagonal_order(
    series: Sequence[StrategyReturnSeries],
    cov: dict[str, dict[str, float]],
) -> list[str]:
    remaining = [s.strategy_id for s in series]
    if len(remaining) <= 2:
        return remaining

    ordered = [remaining.pop(0)]
    while remaining:
        tail = ordered[-1]
        next_id = min(remaining, key=lambda sid: _distance(tail, sid, cov))
        ordered.append(next_id)
        remaining.remove(next_id)
    return ordered


def _distance(a: str, b: str, cov: dict[str, dict[str, float]]) -> float:
    va = cov[a][a]
    vb = cov[b][b]
    corr = cov[a][b] / ((va * vb) ** 0.5) if va > 0 and vb > 0 else 0.0
    return ((1.0 - corr) / 2.0) ** 0.5


def _covariance_matrix(
    series: Sequence[StrategyReturnSeries],
) -> dict[str, dict[str, float]]:
    ids = [s.strategy_id for s in series]
    data = {s.strategy_id: list(s.daily_returns) for s in series}
    n = min(len(vals) for vals in data.values())
    clipped = {sid: vals[-n:] for sid, vals in data.items()}
    means = {sid: sum(vals) / n for sid, vals in clipped.items()}
    return {
        a: {
            b: sum((clipped[a][i] - means[a]) * (clipped[b][i] - means[b]) for i in range(n)) / n
            for b in ids
        }
        for a in ids
    }


def _cluster_variance(cluster: Sequence[str], cov: dict[str, dict[str, float]]) -> float:
    inv_vars = {
        sid: 1.0 / cov[sid][sid]
        for sid in cluster
        if cov[sid][sid] > 0
    }
    if not inv_vars:
        return 0.0
    total_inv = sum(inv_vars.values())
    weights = {sid: inv_vars[sid] / total_inv for sid in inv_vars}
    return sum(
        weights[a] * weights[b] * cov[a][b]
        for a in weights
        for b in weights
    )
