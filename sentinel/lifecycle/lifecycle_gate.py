"""
Sprint 8 strategy lifecycle gate.

Automates promotion recommendations for each strategy without allowing stage
skips. This is evidence evaluation only; execution systems must still enforce
live-order and operator sign-off gates separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StrategyLifecycleStage(str, Enum):
    RESEARCH = "research"
    SHADOW = "shadow"
    PAPER = "paper"
    QUARANTINE_LIVE = "quarantine_live"
    PRODUCTION = "production"


STAGE_ORDER = [
    StrategyLifecycleStage.RESEARCH,
    StrategyLifecycleStage.SHADOW,
    StrategyLifecycleStage.PAPER,
    StrategyLifecycleStage.QUARANTINE_LIVE,
    StrategyLifecycleStage.PRODUCTION,
]


@dataclass(frozen=True)
class StrategyLifecycleEvidence:
    strategy_id: str
    current_stage: StrategyLifecycleStage
    oos_sharpe: float = 0.0
    deflated_sharpe_ratio: float = 0.0
    live_correlation_to_incumbent: float = 1.0
    shadow_days: int = 0
    paper_days: int = 0
    quarantine_live_days: int = 0
    clean_live_days: int = 0
    operational_breaches: int = 0
    max_drawdown_pct: float = 0.0
    operator_signoff_present: bool = False
    capital_expansion_signoff_present: bool = False


@dataclass(frozen=True)
class LifecycleGateResult:
    strategy_id: str
    current_stage: StrategyLifecycleStage
    recommended_stage: StrategyLifecycleStage
    can_promote: bool
    blockers: list[str]
    warnings: list[str]


class StrategyLifecycleGate:
    """Evaluate one-step strategy lifecycle promotions."""

    def evaluate(self, evidence: StrategyLifecycleEvidence) -> LifecycleGateResult:
        blockers: list[str] = []
        warnings: list[str] = []
        next_stage = self._next_stage(evidence.current_stage)

        if next_stage == evidence.current_stage:
            return LifecycleGateResult(
                strategy_id=evidence.strategy_id,
                current_stage=evidence.current_stage,
                recommended_stage=evidence.current_stage,
                can_promote=False,
                blockers=["Strategy is already in terminal production stage"],
                warnings=[],
            )

        if evidence.operational_breaches > 0:
            blockers.append("Operational breaches must be zero before promotion")
        if evidence.max_drawdown_pct > 12.0:
            blockers.append("Max drawdown exceeds 12% lifecycle limit")

        if evidence.current_stage == StrategyLifecycleStage.RESEARCH:
            self._research_to_shadow(evidence, blockers)
        elif evidence.current_stage == StrategyLifecycleStage.SHADOW:
            self._shadow_to_paper(evidence, blockers)
        elif evidence.current_stage == StrategyLifecycleStage.PAPER:
            self._paper_to_quarantine(evidence, blockers)
        elif evidence.current_stage == StrategyLifecycleStage.QUARANTINE_LIVE:
            self._quarantine_to_production(evidence, blockers)

        if abs(evidence.live_correlation_to_incumbent) >= 0.4:
            warnings.append("Correlation benefit is weak; keep allocation capped")

        can_promote = not blockers
        return LifecycleGateResult(
            strategy_id=evidence.strategy_id,
            current_stage=evidence.current_stage,
            recommended_stage=next_stage if can_promote else evidence.current_stage,
            can_promote=can_promote,
            blockers=blockers,
            warnings=warnings,
        )

    @staticmethod
    def _next_stage(stage: StrategyLifecycleStage) -> StrategyLifecycleStage:
        idx = STAGE_ORDER.index(stage)
        if idx >= len(STAGE_ORDER) - 1:
            return stage
        return STAGE_ORDER[idx + 1]

    @staticmethod
    def _research_to_shadow(evidence: StrategyLifecycleEvidence, blockers: list[str]) -> None:
        if evidence.oos_sharpe < 0.6:
            blockers.append("Research OOS Sharpe must be >= 0.60")
        if evidence.deflated_sharpe_ratio < 0.95:
            blockers.append("Research DSR must be >= 0.95")

    @staticmethod
    def _shadow_to_paper(evidence: StrategyLifecycleEvidence, blockers: list[str]) -> None:
        if evidence.shadow_days < 30:
            blockers.append("Shadow mode requires at least 30 clean days")
        if abs(evidence.live_correlation_to_incumbent) >= 0.4:
            blockers.append("Live correlation to incumbent must be < 0.40")

    @staticmethod
    def _paper_to_quarantine(evidence: StrategyLifecycleEvidence, blockers: list[str]) -> None:
        if evidence.paper_days < 60:
            blockers.append("Paper mode requires at least 60 clean days")
        if not evidence.operator_signoff_present:
            blockers.append("Operator sign-off is required before quarantine live")

    @staticmethod
    def _quarantine_to_production(
        evidence: StrategyLifecycleEvidence,
        blockers: list[str],
    ) -> None:
        if evidence.quarantine_live_days < 90 or evidence.clean_live_days < 90:
            blockers.append("Production requires 90 clean quarantine-live days")
        if not evidence.capital_expansion_signoff_present:
            blockers.append("Capital expansion sign-off is required before production")
