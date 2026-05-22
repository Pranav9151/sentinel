"""
sentinel/reporting/monthly_letter.py
======================================
Monthly Letter to Operator's Future Self — Sprint 6.

End-of-month template that the operator's future-self reads at
the start of the next month. Records facts, patterns, and commitments.

Documented in: SPRINT_ROADMAP_v2.md §R8.4, §R12.6
"""
from __future__ import annotations
import logging
import os
from typing import Any
from sentinel.core.types import utc_now
from sentinel.core.guardrails import get_override_count_rolling

logger = logging.getLogger(__name__)
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


class MonthlyLetter:
    def __init__(self, paper_trader: Any = None, stage_manager: Any = None) -> None:
        self._pt = paper_trader
        self._sm = stage_manager

    def generate(self, operator_reflection: str = "") -> dict[str, Any]:
        now = utc_now()
        report: dict[str, Any] = {
            "report_type": "monthly_letter",
            "month": now.strftime("%B %Y"),
            "generated_at": now.isoformat(),
            "mock_mode": MOCK_MODE,
        }

        if self._pt:
            s = self._pt.get_performance_summary()
            report["month_performance"] = {
                "total_trades": s.total_trades,
                "win_rate_pct": s.win_rate_pct,
                "sharpe_ratio": s.sharpe_ratio,
                "total_pnl_inr": float(s.total_pnl_inr),
                "max_drawdown_pct": s.max_drawdown_pct,
                "current_equity_inr": float(s.current_equity_inr),
            }

        if self._sm:
            st = self._sm.get_status()
            report["discipline"] = {
                "stage": st["stage"],
                "days_in_stage": st["days_in_stage"],
                "total_live_trades": st["total_live_trades"],
                "override_count_30d": get_override_count_rolling(30),
                "next_trade_gated": st["next_trade_gated"],
            }

        report["exit_criteria_check"] = self._check_exit(report)
        report["operator_reflection"] = operator_reflection
        report["template_commitments"] = [
            "I will complete my pre-mortem journal for every trade.",
            "I will not override a guardrail without writing down the full thesis.",
            "I will review my bias-vs-outcome report before increasing position size.",
            "I will re-read §R12.3 (the three rules I must never break) on the 1st.",
        ]
        return report

    def _check_exit(self, report: dict[str, Any]) -> dict[str, Any]:
        """Check §R12.6 exit criteria."""
        flags: list[str] = []
        perf = report.get("month_performance", {})
        disc = report.get("discipline", {})

        if perf.get("sharpe_ratio", 1.0) < 0:
            flags.append("Sharpe < 0 → review §R12.6 exit criterion 3")
        if perf.get("max_drawdown_pct", 0) > 15:
            flags.append("Max drawdown > 15% → approaching exit criterion 2")
        if disc.get("override_count_30d", 0) >= 3:
            flags.append("Three-override rule fired this month → exit criterion 8 proximity")
        return {
            "flags": flags,
            "any_triggered": len(flags) > 0,
            "criteria_ref": "SPRINT_ROADMAP_v2.md §R12.6",
        }

    def format_text(self, report: dict[str, Any]) -> str:
        month = report.get("month", "")
        perf = report.get("month_performance", {})
        disc = report.get("discipline", {})
        ec = report.get("exit_criteria_check", {})
        commitments = report.get("template_commitments", [])

        lines = [
            "═══════════════════════════════════════",
            f"  MONTHLY LETTER — {month}",
            "  From: current-self | To: future-self",
            "═══════════════════════════════════════",
            "",
            "WHAT HAPPENED THIS MONTH",
            "─────────────────────────",
        ]
        if perf:
            lines += [
                f"  Trades: {perf.get('total_trades', 0)} | Win rate: {perf.get('win_rate_pct', 0):.0f}%",
                f"  P&L: ₹{perf.get('total_pnl_inr', 0):+,.0f} | Sharpe: {perf.get('sharpe_ratio', 0):.2f}",
                f"  Max drawdown: {perf.get('max_drawdown_pct', 0):.1f}%",
                f"  Equity: ₹{perf.get('current_equity_inr', 0):,.0f}",
            ]
        if disc:
            lines += [
                "",
                f"  Stage: {disc.get('stage', '').upper()} ({disc.get('days_in_stage', 0)} days)",
                f"  Guardrail overrides: {disc.get('override_count_30d', 0)}/3",
            ]
        if ec.get("any_triggered"):
            lines += ["", "⚠️  EXIT CRITERIA FLAGS:"]
            for f in ec.get("flags", []):
                lines.append(f"  → {f}")
        lines += [
            "",
            "REFLECTION",
            "───────────",
            report.get("operator_reflection") or "(no reflection written this month)",
            "",
            "COMMITMENTS FOR NEXT MONTH",
            "───────────────────────────",
        ]
        for c in commitments:
            lines.append(f"  ☐ {c}")
        lines += [
            "",
            "─────────────────────────────────────",
            "Read this before your first trade next month.",
            "─────────────────────────────────────",
        ]
        return "\n".join(lines)
