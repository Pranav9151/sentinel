"""
sentinel/reporting/weekly_review.py
=====================================
Weekly Review Report — Sprint 6.

Sunday 19:00 IST delivery.
Covers: 7-day performance, override analysis, bias-vs-outcome prompts.

Documented in: SPRINT_ROADMAP_v2.md §R8.4
"""
from __future__ import annotations
import logging
import os
from typing import Any
from sentinel.core.types import utc_now
from sentinel.core.guardrails import get_recent_overrides

logger = logging.getLogger(__name__)
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


class WeeklyReviewReport:
    def __init__(self, paper_trader: Any = None, stage_manager: Any = None) -> None:
        self._pt = paper_trader
        self._sm = stage_manager

    def generate(self) -> dict[str, Any]:
        now = utc_now()
        report: dict[str, Any] = {
            "report_type": "weekly_review",
            "generated_at": now.isoformat(),
            "week_ending": now.strftime("%Y-%m-%d"),
            "mock_mode": MOCK_MODE,
        }

        if self._pt:
            s = self._pt.get_performance_summary()
            report["performance"] = {
                "total_trades": s.total_trades,
                "closed_trades": s.closed_trades,
                "win_rate_pct": s.win_rate_pct,
                "profit_factor": s.profit_factor,
                "sharpe_ratio": s.sharpe_ratio,
                "max_drawdown_pct": s.max_drawdown_pct,
                "total_pnl_inr": float(s.total_pnl_inr),
                "current_equity_inr": float(s.current_equity_inr),
            }
        else:
            report["performance"] = {"note": "No paper trader"}

        ov_7d = get_recent_overrides(7)
        ov_30d = get_recent_overrides(30)
        report["override_analysis"] = {
            "count_7d": len(ov_7d),
            "count_30d": len(ov_30d),
            "threshold": 3,
            "guardrails_triggered": list({r.get("guardrail", "") for r in ov_30d}),
            "proximity_to_demotion": f"{len(ov_30d)}/3 in rolling 30 days",
            "recent": ov_7d,
        }

        if self._sm:
            st = self._sm.get_status()
            report["stage"] = {
                "current": st["stage"],
                "days_in_stage": st["days_in_stage"],
                "total_live_trades": st["total_live_trades"],
                "allocated_capital_inr": st["allocated_capital_inr"],
            }

        report["reflection_prompts"] = [
            "Did I follow the pre-mortem process for every trade this week?",
            "Did any override feel justified in hindsight? Was the guardrail right?",
            "Is my win rate consistent with backtest expectations (40–55%)?",
            "Did I feel FOMO on any setup I passed on? Was passing correct?",
            "Am I trading my system or my emotions?",
        ]
        return report

    def format_telegram(self, report: dict[str, Any]) -> str:
        perf = report.get("performance", {})
        ov = report.get("override_analysis", {})
        stage = report.get("stage", {})
        lines = [f"📋 *Weekly Review — w/e {report.get('week_ending', '')}*", ""]
        if "total_trades" in perf:
            lines += [
                f"Trades: {perf['closed_trades']} closed | Win rate: {perf['win_rate_pct']:.0f}%",
                f"Profit factor: {perf['profit_factor']:.2f} | Sharpe: {perf['sharpe_ratio']:.2f}",
                f"P&L: ₹{perf['total_pnl_inr']:+,.0f}",
                "",
            ]
        lines.append(
            f"Overrides 7d: {ov.get('count_7d', 0)} | 30d: {ov.get('count_30d', 0)}/3"
        )
        if ov.get("count_30d", 0) >= 2:
            lines.append("⚠️ Approaching three-override demotion threshold!")
        if "current" in stage:
            lines.append(
                f"Stage: {stage['current'].upper()} | "
                f"Live trades: {stage.get('total_live_trades', 0)}"
            )
        return "\n".join(lines)
