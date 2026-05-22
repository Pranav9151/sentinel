"""Resolve duplicate strategy intents before any order-routing step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class StrategyIntent:
    strategy_id: str
    symbol: str
    action: str
    conviction_score: float
    priority: int = 100
    notes: str = ""


@dataclass(frozen=True)
class ArbitrationDecision:
    symbol: str
    selected: StrategyIntent | None
    rejected: tuple[StrategyIntent, ...]
    reason: str


class MultiStrategyArbiter:
    """
    One-symbol, one-action arbiter.

    Conflicting actions are blocked for operator review. Compatible duplicate
    entries pick the highest priority, then highest conviction.
    """

    def resolve(self, intents: Sequence[StrategyIntent]) -> list[ArbitrationDecision]:
        by_symbol: dict[str, list[StrategyIntent]] = {}
        for intent in intents:
            by_symbol.setdefault(intent.symbol, []).append(intent)

        decisions: list[ArbitrationDecision] = []
        for symbol, group in by_symbol.items():
            actions = {g.action for g in group}
            if len(actions) > 1:
                decisions.append(ArbitrationDecision(
                    symbol=symbol,
                    selected=None,
                    rejected=tuple(group),
                    reason="conflicting_strategy_actions_require_operator_review",
                ))
                continue

            ranked = sorted(group, key=lambda g: (g.priority, -g.conviction_score))
            selected = ranked[0]
            decisions.append(ArbitrationDecision(
                symbol=symbol,
                selected=selected,
                rejected=tuple(ranked[1:]),
                reason="highest_priority_then_conviction_selected",
            ))
        return decisions
