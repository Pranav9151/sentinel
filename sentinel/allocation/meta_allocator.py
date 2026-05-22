"""
Sprint 7 inverse-variance meta allocator.

Research-only: computes target weights across strategies. It does not place
orders or change live capital by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class StrategyRiskSnapshot:
    strategy_id: str
    annualized_vol_pct: float
    max_weight: float = 0.70
    min_weight: float = 0.0
    enabled: bool = True


class InverseVarianceAllocator:
    """Allocate higher weight to lower-volatility enabled strategies."""

    def allocate(self, snapshots: Sequence[StrategyRiskSnapshot]) -> dict[str, float]:
        active = [s for s in snapshots if s.enabled and s.annualized_vol_pct > 0]
        if not active:
            return {}

        raw = {
            s.strategy_id: 1.0 / (s.annualized_vol_pct ** 2)
            for s in active
        }
        total = sum(raw.values())
        weights = {sid: val / total for sid, val in raw.items()}

        capped: dict[str, float] = {}
        residual_ids: list[str] = []
        residual = 1.0
        for s in active:
            w = max(s.min_weight, min(s.max_weight, weights[s.strategy_id]))
            capped[s.strategy_id] = w
            residual -= w
            if w < s.max_weight:
                residual_ids.append(s.strategy_id)

        if abs(residual) > 1e-9 and residual_ids:
            share = residual / len(residual_ids)
            for sid in residual_ids:
                capped[sid] += share

        norm = sum(capped.values())
        if norm <= 0:
            return {}
        return {sid: round(w / norm, 4) for sid, w in capped.items()}
