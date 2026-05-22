"""Sprint 7 strategy-correlation monitor."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class StrategyReturnSeries:
    strategy_id: str
    daily_returns: Sequence[float]


class CorrelationMonitor:
    """Compute pairwise strategy return correlations."""

    def matrix(self, series: Sequence[StrategyReturnSeries]) -> dict[str, dict[str, float]]:
        ids = [s.strategy_id for s in series]
        data = {s.strategy_id: list(s.daily_returns) for s in series}
        return {
            a: {b: round(self._corr(data[a], data[b]), 4) for b in ids}
            for a in ids
        }

    def high_correlation_pairs(
        self,
        series: Sequence[StrategyReturnSeries],
        threshold: float = 0.4,
    ) -> list[tuple[str, str, float]]:
        mat = self.matrix(series)
        ids = list(mat)
        pairs: list[tuple[str, str, float]] = []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                corr = mat[a][b]
                if abs(corr) >= threshold:
                    pairs.append((a, b, corr))
        return pairs

    @staticmethod
    def _corr(a: list[float], b: list[float]) -> float:
        n = min(len(a), len(b))
        if n < 2:
            return 0.0
        x, y = a[-n:], b[-n:]
        mx, my = sum(x) / n, sum(y) / n
        cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        vx = sum((v - mx) ** 2 for v in x)
        vy = sum((v - my) ** 2 for v in y)
        denom = math.sqrt(vx * vy)
        return 0.0 if denom == 0 else cov / denom
