"""
Production delivery readiness checks.

This module produces a machine-readable checklist for whether Sentinel can be
treated as live-production ready. It intentionally blocks when evidence is not
present; it does not infer maturity from code completeness alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from sentinel.core.config import OperatorProfile, load_config
from sentinel.ops.audit import AppendOnlyAuditLog
from sentinel.ops.killswitch import KILLSWITCH_SECRET, is_kill_active, validate_killswitch_secret
from sentinel.research.sprint7_factory import build_sprint7_research_snapshot


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str
    category: str


@dataclass(frozen=True)
class DeploymentReadinessReport:
    ready: bool
    checks: list[ReadinessCheck]

    @property
    def blockers(self) -> list[ReadinessCheck]:
        return [check for check in self.checks if not check.passed]

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "detail": check.detail,
                    "category": check.category,
                }
                for check in self.checks
            ],
        }


def build_deployment_readiness_report(
    profile: OperatorProfile | None = None,
) -> DeploymentReadinessReport:
    profile = profile or load_config()
    research = build_sprint7_research_snapshot(profile)
    audit_verification = AppendOnlyAuditLog().verify()
    checks = [
        ReadinessCheck(
            name="Trading stage",
            passed=profile.trading_stage in {"quarantine", "production"},
            detail=f"Current stage is {profile.trading_stage!r}.",
            category="live_gate",
        ),
        ReadinessCheck(
            name="Emergency fund",
            passed=profile.emergency_fund_months_confirmed >= 6,
            detail=f"{profile.emergency_fund_months_confirmed} months confirmed; require >= 6.",
            category="operator_safety",
        ),
        ReadinessCheck(
            name="Operator sign-off",
            passed=bool(profile.section_7_6_signoff_commit_hash),
            detail="Section 7.6 sign-off hash must be recorded.",
            category="operator_safety",
        ),
        ReadinessCheck(
            name="Kill switch inactive",
            passed=not is_kill_active(),
            detail="Kill switch must be inactive before market operations.",
            category="operations",
        ),
        ReadinessCheck(
            name="Kill switch secret validation",
            passed=(
                KILLSWITCH_SECRET != "CHANGE_THIS_SECRET"
                and validate_killswitch_secret(KILLSWITCH_SECRET)
            ),
            detail="KILLSWITCH_SECRET must be configured and must not use the default.",
            category="security",
        ),
        ReadinessCheck(
            name="Strategy factory live approval",
            passed=research.live_approved,
            detail=f"Promotion status: {research.promotion_status}.",
            category="strategy",
        ),
        ReadinessCheck(
            name="Research allocation",
            passed=bool(research.target_weights) and abs(sum(research.target_weights.values()) - 1.0) < 0.01,
            detail=f"Allocation method: {research.allocation_method}.",
            category="strategy",
        ),
        ReadinessCheck(
            name="Audit log integrity",
            passed=audit_verification.valid,
            detail=(
                f"{audit_verification.event_count} audit events verified."
                if audit_verification.valid
                else audit_verification.first_error
            ),
            category="operations",
        ),
    ]
    return DeploymentReadinessReport(
        ready=all(check.passed for check in checks),
        checks=checks,
    )
