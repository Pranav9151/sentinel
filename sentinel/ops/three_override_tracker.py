"""
sentinel/ops/three_override_tracker.py
========================================
Three-Override Rule Tracker — Sprint 6.

Bridges Sprint 4's guardrails override log with Sprint 6's StageManager.

Rule (SPRINT_ROADMAP_v2.md §R8.3 — NON-NEGOTIABLE):
  3 guardrail overrides in any rolling 30-day window
  → automatic demotion to QUARANTINE for 14 days
  → no human discretion; no override of the override rule

Call check_and_demote():
  - After every guardrail override is logged
  - Daily at 07:00 IST via scheduler (daily_check)

Documented in: SPRINT_ROADMAP_v2.md §R8.3, ARCHITECTURE_v5.md §23.9
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

from sentinel.core.guardrails import (
    get_override_count_rolling,
    get_recent_overrides,
    OVERRIDE_THRESHOLD,
    OVERRIDE_WINDOW_DAYS,
    PAPER_MODE_DAYS,
)
from sentinel.core.types import utc_now

logger = logging.getLogger(__name__)
MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


class ThreeOverrideTracker:
    """
    Monitors the rolling 30-day guardrail override count and
    auto-demotes to QUARANTINE when threshold is reached.
    """

    def __init__(self, stage_manager: Any = None) -> None:
        """
        Args:
            stage_manager: StageManager instance. Injected for testability.
                           Lazily imported if None.
        """
        self._sm = stage_manager
        logger.info(
            f"[ThreeOverrideTracker] threshold={OVERRIDE_THRESHOLD} "
            f"window={OVERRIDE_WINDOW_DAYS}d paper_days={PAPER_MODE_DAYS}"
        )

    def _get_sm(self) -> Any:
        if self._sm is not None:
            return self._sm
        from sentinel.live.stage_manager import StageManager
        self._sm = StageManager()
        return self._sm

    def check_and_demote(self) -> dict[str, Any]:
        """
        Check override count. Demote if threshold reached.

        Returns:
          override_count, threshold, demoted (bool), warning (bool), message
        """
        count = get_override_count_rolling(OVERRIDE_WINDOW_DAYS)
        recent = get_recent_overrides(OVERRIDE_WINDOW_DAYS)

        result: dict[str, Any] = {
            "override_count": count,
            "threshold": OVERRIDE_THRESHOLD,
            "window_days": OVERRIDE_WINDOW_DAYS,
            "demoted": False,
            "warning": False,
            "message": "",
            "recent_overrides": recent[-3:],
            "checked_at": utc_now().isoformat(),
        }

        if count >= OVERRIDE_THRESHOLD:
            sm = self._get_sm()
            from sentinel.live.stage_manager import Stage
            if sm.stage != Stage.QUARANTINE:
                last = recent[-1].get("guardrail", "unknown") if recent else "unknown"
                reason = (
                    f"Three-Override Rule: {count} overrides in {OVERRIDE_WINDOW_DAYS}d. "
                    f"Demoted for {PAPER_MODE_DAYS} days. "
                    f"Last override: {last}."
                )
                sm.demote_to_quarantine(reason)
                result["demoted"] = True
                result["message"] = reason
                logger.critical(f"[ThreeOverrideTracker] DEMOTION: {reason}")
            else:
                result["message"] = (
                    f"Already in QUARANTINE ({count} overrides). No further demotion."
                )
        elif count == OVERRIDE_THRESHOLD - 1:
            result["warning"] = True
            result["message"] = (
                f"WARNING: {count}/{OVERRIDE_THRESHOLD} overrides in {OVERRIDE_WINDOW_DAYS}d. "
                f"One more triggers QUARANTINE."
            )
            logger.warning("[ThreeOverrideTracker] One override from demotion!")
        else:
            result["message"] = (
                f"{count}/{OVERRIDE_THRESHOLD} overrides in {OVERRIDE_WINDOW_DAYS}d. OK."
            )

        return result

    def daily_check(self) -> dict[str, Any]:
        """Scheduled daily check (07:00 IST). Same as check_and_demote + summary."""
        result = self.check_and_demote()
        recent = get_recent_overrides(OVERRIDE_WINDOW_DAYS)
        result["daily_summary"] = {
            "total_30d": result["override_count"],
            "days_until_oldest_expires": self._days_until_oldest_expires(recent),
            "guardrails_seen": list({r.get("guardrail", "") for r in recent}),
        }
        return result

    def _days_until_oldest_expires(self, overrides: list[dict]) -> int:
        if not overrides:
            return 0
        try:
            oldest = min(
                datetime.fromisoformat(r["timestamp"]) for r in overrides
            )
            return max(0, (oldest + timedelta(days=OVERRIDE_WINDOW_DAYS) - utc_now()).days)
        except Exception:
            return 0

    def get_status(self) -> dict[str, Any]:
        count = get_override_count_rolling(OVERRIDE_WINDOW_DAYS)
        recent = get_recent_overrides(OVERRIDE_WINDOW_DAYS)
        return {
            "override_count_30d": count,
            "threshold": OVERRIDE_THRESHOLD,
            "window_days": OVERRIDE_WINDOW_DAYS,
            "paper_mode_days_on_breach": PAPER_MODE_DAYS,
            "at_threshold": count >= OVERRIDE_THRESHOLD,
            "warning": count == OVERRIDE_THRESHOLD - 1,
            "recent_overrides": recent[-5:],
            "snapshot_at": utc_now().isoformat(),
        }
