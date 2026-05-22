"""
sentinel/reporting/daily_postmarket.py
========================================
Daily Post-Market Report — Sprint 6.

Delivered at 16:00 IST via Telegram + dashboard.
Covers: realized P&L, stage status, slippage model, costs.

Documented in: SPRINT_ROADMAP_v2.md §R8.4
"""
from __future__ import annotations
import logging
import os
from typing import Any
from sentinel.core.types import utc_now

logger = logging.getLogger(__name__)
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


class DailyPostMarketReport:
    """Daily 16:00 IST post-market summary."""

    def __init__(self, paper_trader: Any = None, stage_manager: Any = None) -> None:
        self._pt = paper_trader
        self._sm = stage_manager

    def generate(self) -> dict[str, Any]:
        now = utc_now()
        report: dict[str, Any] = {
            "report_type": "daily_postmarket",
            "generated_at": now.isoformat(),
            "trading_date": now.strftime("%Y-%m-%d"),
            "mock_mode": MOCK_MODE,
        }

        if self._pt:
            s = self._pt.get_performance_summary()
            report["pnl"] = {
                "total_realized_inr": float(s.total_realized_pnl_inr),
                "total_unrealized_inr": float(s.total_unrealized_pnl_inr),
                "total_pnl_inr": float(s.total_pnl_inr),
                "open_trades": s.open_trades,
                "closed_trades": s.closed_trades,
                "win_rate_pct": s.win_rate_pct,
                "current_equity_inr": float(s.current_equity_inr),
            }
        else:
            report["pnl"] = {"note": "No paper trader connected"}

        if self._sm:
            st = self._sm.get_status()
            report["stage"] = {
                "current": st["stage"],
                "days_in_stage": st["days_in_stage"],
                "allocated_capital_inr": st["allocated_capital_inr"],
                "day_halted": st["day_halted"],
                "day_halt_reason": st.get("day_halt_reason", ""),
                "next_trade_gated": st["next_trade_gated"],
                "total_live_trades": st["total_live_trades"],
            }
        else:
            report["stage"] = {"note": "No stage manager connected"}

        report["slippage"] = {
            "modeled_bps": 0.0,
            "realized_bps": 0.0,
            "within_model": True,
            "note": "Mock mode — no real fills" if MOCK_MODE else "Live TCA pending",
        }
        return report

    def format_telegram(self, report: dict[str, Any]) -> str:
        pnl = report.get("pnl", {})
        stage = report.get("stage", {})
        date = report.get("trading_date", "")
        lines = [f"📊 *Daily Post-Market Report — {date}*", ""]

        if "current_equity_inr" in pnl:
            lines += [
                f"Equity: ₹{pnl['current_equity_inr']:,.0f}",
                f"Realized P&L: ₹{pnl['total_realized_inr']:+,.0f}",
                f"Unrealized: ₹{pnl['total_unrealized_inr']:+,.0f}",
                f"Trades: {pnl['open_trades']} open | Win rate: {pnl['win_rate_pct']:.0f}%",
                "",
            ]
        if "current" in stage:
            lines.append(
                f"Stage: {stage['current'].upper()} (day {stage['days_in_stage']}) "
                f"| Allocated: ₹{stage.get('allocated_capital_inr', 0):,.0f}"
            )
            if stage.get("day_halted"):
                lines.append("⚠️ DAY HALTED — daily loss limit reached")
            if stage.get("next_trade_gated"):
                lines.append("⚠️ REVIEW GATE active — operator review required")
        return "\n".join(lines)
