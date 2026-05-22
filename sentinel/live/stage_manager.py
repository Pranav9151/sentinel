"""
sentinel/live/stage_manager.py
================================
Live Trading Stage Manager — Sprint 6.

Manages the four deployment stages of live trading:

  TOE_DIP    → 10% capital deployed, max 1 trade open, max 0.5% risk/trade
  PILOT      → 25% capital deployed, max 3 trades open, max 1.0% risk/trade
  QUARANTINE → Forced return on any rule breach (min 14 days)
  PRODUCTION → 100% planned allocation, full position sizing

Stage transitions:
  - Demotions are ALWAYS automatic on trigger events
  - Promotions require explicit operator sign-off (commit hash)
  - Cannot skip stages in sequence

Discipline rules enforced (SPRINT_ROADMAP_v2.md §R8.3 — NON-NEGOTIABLE):
  - Capital allocation cap enforced per stage
  - Max open positions enforced per stage
  - Max risk-per-trade enforced per stage
  - Single trade loss > 3% of allocated → review gate before next trade
  - Daily intraday DD > 2% of allocated → halt rest of day
  - 3 guardrail overrides in rolling 30 days → QUARANTINE (via ThreeOverrideTracker)

State persisted to JSON at state_path (inject in tests via constructor).

Documented in: SPRINT_ROADMAP_v2.md §R8.2, §R8.3
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from sentinel.core.types import utc_now

logger = logging.getLogger(__name__)

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
DEFAULT_STATE_PATH = Path("live_stage_state.json")


# ─────────────────────────────────────────────
# STAGE ENUM + CONFIG
# ─────────────────────────────────────────────

class Stage(str, Enum):
    TOE_DIP    = "toe_dip"
    PILOT      = "pilot"
    QUARANTINE = "quarantine"
    PRODUCTION = "production"


@dataclass
class StageConfig:
    """Risk parameters for each deployment stage."""
    stage:                  Stage
    capital_pct:            float   # % of total portfolio deployed
    max_open_positions:     int
    max_risk_per_trade_pct: float   # % of allocated capital
    max_single_loss_pct:    float   # % of allocated → triggers review gate
    max_daily_dd_pct:       float   # % of allocated → halts day
    min_days_before_promote: int


STAGE_CONFIGS: dict[Stage, StageConfig] = {
    Stage.TOE_DIP: StageConfig(
        stage=Stage.TOE_DIP,
        capital_pct=10.0,
        max_open_positions=1,
        max_risk_per_trade_pct=0.5,
        max_single_loss_pct=3.0,
        max_daily_dd_pct=2.0,
        min_days_before_promote=30,
    ),
    Stage.PILOT: StageConfig(
        stage=Stage.PILOT,
        capital_pct=25.0,
        max_open_positions=3,
        max_risk_per_trade_pct=1.0,
        max_single_loss_pct=3.0,
        max_daily_dd_pct=2.0,
        min_days_before_promote=60,
    ),
    Stage.QUARANTINE: StageConfig(
        stage=Stage.QUARANTINE,
        capital_pct=10.0,
        max_open_positions=1,
        max_risk_per_trade_pct=0.5,
        max_single_loss_pct=3.0,
        max_daily_dd_pct=2.0,
        min_days_before_promote=14,
    ),
    Stage.PRODUCTION: StageConfig(
        stage=Stage.PRODUCTION,
        capital_pct=100.0,
        max_open_positions=10,
        max_risk_per_trade_pct=1.0,
        max_single_loss_pct=5.0,
        max_daily_dd_pct=3.0,
        min_days_before_promote=999,
    ),
}

# Valid promotion path (no skipping)
_NEXT_STAGE: dict[Stage, Stage] = {
    Stage.TOE_DIP:    Stage.PILOT,
    Stage.PILOT:      Stage.PRODUCTION,
    Stage.QUARANTINE: Stage.TOE_DIP,
}


# ─────────────────────────────────────────────
# STATE DATACLASS
# ─────────────────────────────────────────────

@dataclass
class StageState:
    current_stage:          str
    entered_at:             str
    demotion_reason:        str
    next_trade_gated:       bool
    next_trade_gate_reason: str
    day_halted:             bool
    day_halt_reason:        str
    day_halt_date:          str
    promotion_signoff:      str
    open_positions_count:   int
    daily_pnl_inr:          float
    daily_pnl_date:         str
    total_live_trades:      int
    created_at:             str

    @property
    def stage(self) -> Stage:
        return Stage(self.current_stage)

    @property
    def config(self) -> StageConfig:
        return STAGE_CONFIGS[self.stage]

    @property
    def days_in_stage(self) -> int:
        try:
            entered = datetime.fromisoformat(self.entered_at)
            return max(0, (utc_now() - entered).days)
        except Exception:
            return 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StageState":
        fields = {k for k in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in fields})


# ─────────────────────────────────────────────
# STAGE MANAGER
# ─────────────────────────────────────────────

class StageManager:
    """
    Controls which deployment stage the live trading system is in
    and enforces the discipline rules for that stage.

    Every live order MUST pass check_can_trade() before execution.
    """

    def __init__(
        self,
        total_portfolio_inr: Decimal = Decimal("300000"),
        state_path: Optional[Path] = None,
    ) -> None:
        self.total_portfolio_inr = total_portfolio_inr
        self.state_path = state_path or DEFAULT_STATE_PATH
        self._state = self._load_or_create()
        logger.info(
            f"[StageManager] stage={self._state.current_stage.upper()} "
            f"portfolio=₹{total_portfolio_inr:,.0f} mock={MOCK_MODE}"
        )

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_or_create(self) -> StageState:
        if self.state_path.exists():
            try:
                with open(self.state_path) as f:
                    return StageState.from_dict(json.load(f))
            except Exception as e:
                logger.warning(f"[StageManager] Load failed ({e}), creating fresh state")
        return self._fresh()

    def _fresh(self) -> StageState:
        now = utc_now().isoformat()
        today = utc_now().strftime("%Y-%m-%d")
        return StageState(
            current_stage=Stage.TOE_DIP.value,
            entered_at=now,
            demotion_reason="",
            next_trade_gated=False,
            next_trade_gate_reason="",
            day_halted=False,
            day_halt_reason="",
            day_halt_date="",
            promotion_signoff="",
            open_positions_count=0,
            daily_pnl_inr=0.0,
            daily_pnl_date=today,
            total_live_trades=0,
            created_at=now,
        )

    def _save(self) -> None:
        try:
            with open(self.state_path, "w") as f:
                json.dump(self._state.to_dict(), f, indent=2)
        except Exception as e:
            logger.error(f"[StageManager] Save failed: {e}")

    # ── Computed properties ───────────────────────────────────────────────────

    @property
    def stage(self) -> Stage:
        return self._state.stage

    @property
    def config(self) -> StageConfig:
        return self._state.config

    @property
    def allocated_capital_inr(self) -> Decimal:
        pct = Decimal(str(self.config.capital_pct))
        return (self.total_portfolio_inr * pct / Decimal("100")).quantize(Decimal("1"))

    @property
    def max_risk_per_trade_inr(self) -> Decimal:
        pct = Decimal(str(self.config.max_risk_per_trade_pct))
        return (self.allocated_capital_inr * pct / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def max_single_loss_inr(self) -> Decimal:
        pct = Decimal(str(self.config.max_single_loss_pct))
        return (self.allocated_capital_inr * pct / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def max_daily_dd_inr(self) -> Decimal:
        pct = Decimal(str(self.config.max_daily_dd_pct))
        return (self.allocated_capital_inr * pct / Decimal("100")).quantize(Decimal("0.01"))

    # ── Core gate ─────────────────────────────────────────────────────────────

    def check_can_trade(
        self,
        proposed_risk_inr: Decimal,
        symbol: str = "",
        open_positions_count: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Master pre-trade check. Returns {"allowed": bool, "reason": str, ...}.
        Call this before EVERY order.
        """
        if open_positions_count is not None:
            self._state.open_positions_count = open_positions_count

        self._refresh_daily()
        blocks: list[str] = []
        checks: dict[str, Any] = {}

        # 1. Day halt
        if self._state.day_halted:
            blocks.append(f"Day halted: {self._state.day_halt_reason}")
            checks["day_halt"] = False
        else:
            checks["day_halt"] = True

        # 2. Review gate
        if self._state.next_trade_gated:
            blocks.append(f"Review gate: {self._state.next_trade_gate_reason}")
            checks["review_gate"] = False
        else:
            checks["review_gate"] = True

        # 3. Max open positions
        max_pos = self.config.max_open_positions
        cur_pos = self._state.open_positions_count
        if cur_pos >= max_pos:
            blocks.append(
                f"Max open positions for {self._state.current_stage}: {cur_pos}/{max_pos}"
            )
            checks["max_positions"] = False
        else:
            checks["max_positions"] = True

        # 4. Risk per trade
        max_risk = self.max_risk_per_trade_inr
        if proposed_risk_inr > max_risk:
            blocks.append(
                f"Proposed risk ₹{proposed_risk_inr:,.0f} exceeds stage limit "
                f"₹{max_risk:,.0f} ({self.config.max_risk_per_trade_pct}% of allocated)"
            )
            checks["risk_per_trade"] = False
        else:
            checks["risk_per_trade"] = True

        if self.stage == Stage.QUARANTINE:
            checks["quarantine_notice"] = (
                f"QUARANTINE active ({self._state.demotion_reason}). "
                f"Days remaining: "
                f"{max(0, self.config.min_days_before_promote - self._state.days_in_stage)}"
            )

        allowed = len(blocks) == 0
        return {
            "allowed": allowed,
            "reason": " | ".join(blocks) if blocks else "All checks passed.",
            "checks": checks,
            "stage": self._state.current_stage,
            "allocated_capital_inr": float(self.allocated_capital_inr),
            "max_risk_per_trade_inr": float(max_risk),
            "checked_at": utc_now().isoformat(),
        }

    # ── Trade recording ───────────────────────────────────────────────────────

    def record_trade_opened(self) -> None:
        self._state.open_positions_count = max(0, self._state.open_positions_count + 1)
        self._state.total_live_trades += 1
        self._save()

    def record_trade_closed(self, pnl_inr: Decimal) -> dict[str, Any]:
        """Record a closed trade. May trigger review gate or day halt."""
        self._state.open_positions_count = max(0, self._state.open_positions_count - 1)
        self._refresh_daily()
        self._state.daily_pnl_inr += float(pnl_inr)
        self._save()

        triggered: list[str] = []

        # Single loss > threshold → review gate
        if pnl_inr < Decimal("0") and abs(pnl_inr) >= self.max_single_loss_inr:
            self._state.next_trade_gated = True
            self._state.next_trade_gate_reason = (
                f"Loss ₹{abs(pnl_inr):,.0f} exceeded "
                f"{self.config.max_single_loss_pct}% threshold "
                f"(₹{self.max_single_loss_inr:,.0f})"
            )
            self._save()
            triggered.append("review_gate_set")
            logger.warning(f"[StageManager] Review gate set: {self._state.next_trade_gate_reason}")

        # Cumulative daily loss > threshold → halt day
        daily_loss = Decimal(str(self._state.daily_pnl_inr))
        if daily_loss < Decimal("0") and abs(daily_loss) >= self.max_daily_dd_inr:
            self._state.day_halted = True
            self._state.day_halt_reason = (
                f"Daily loss ₹{abs(daily_loss):,.0f} exceeded "
                f"{self.config.max_daily_dd_pct}% limit (₹{self.max_daily_dd_inr:,.0f})"
            )
            self._state.day_halt_date = utc_now().strftime("%Y-%m-%d")
            self._save()
            triggered.append("day_halted")
            logger.warning(f"[StageManager] Day halted: {self._state.day_halt_reason}")

        return {"pnl_inr": float(pnl_inr), "actions_triggered": triggered}

    # ── Demotion / Promotion ──────────────────────────────────────────────────

    def demote_to_quarantine(self, reason: str) -> None:
        """Auto-demote to QUARANTINE. Called by ThreeOverrideTracker."""
        prev = self._state.current_stage
        self._state.current_stage = Stage.QUARANTINE.value
        self._state.entered_at = utc_now().isoformat()
        self._state.demotion_reason = reason
        self._save()
        logger.critical(
            f"[StageManager] DEMOTED {prev} → QUARANTINE. Reason: {reason}"
        )

    def promote(self, target_stage: Stage, signoff_commit: str) -> dict[str, Any]:
        """
        Promote to next stage. Requires sign-off commit and minimum days.
        Returns {"allowed": bool, "reason": str}.
        """
        allowed_next = _NEXT_STAGE.get(self.stage)
        if target_stage != allowed_next:
            return {
                "allowed": False,
                "reason": (
                    f"Cannot promote from {self._state.current_stage} to "
                    f"{target_stage.value}. "
                    f"Valid next: {allowed_next.value if allowed_next else 'none'}"
                ),
            }

        days_in = self._state.days_in_stage
        min_days = self.config.min_days_before_promote
        if days_in < min_days:
            return {
                "allowed": False,
                "reason": (
                    f"Insufficient time in {self._state.current_stage}: "
                    f"{days_in} days (need {min_days}). "
                    f"{min_days - days_in} more days required."
                ),
            }

        if self._state.next_trade_gated:
            return {"allowed": False, "reason": "Promotion blocked: review gate is active."}
        if self._state.day_halted:
            return {"allowed": False, "reason": "Promotion blocked: day halt is active."}
        if not signoff_commit:
            return {"allowed": False, "reason": "Promotion requires a non-empty commit hash signoff."}

        prev = self._state.current_stage
        self._state.current_stage = target_stage.value
        self._state.entered_at = utc_now().isoformat()
        self._state.demotion_reason = ""
        self._state.promotion_signoff = signoff_commit
        self._save()
        logger.info(f"[StageManager] PROMOTED {prev} → {target_stage.value} ({signoff_commit})")
        return {"allowed": True, "reason": f"Promoted to {target_stage.value}."}

    # ── Gate management ───────────────────────────────────────────────────────

    def clear_review_gate(self, notes: str = "") -> None:
        self._state.next_trade_gated = False
        self._state.next_trade_gate_reason = ""
        self._save()
        logger.info(f"[StageManager] Review gate cleared. Notes: {notes}")

    def reset_daily_tracking(self) -> None:
        today = utc_now().strftime("%Y-%m-%d")
        self._state.daily_pnl_inr = 0.0
        self._state.daily_pnl_date = today
        if self._state.day_halt_date != today:
            self._state.day_halted = False
            self._state.day_halt_reason = ""
        self._save()

    def _refresh_daily(self) -> None:
        today = utc_now().strftime("%Y-%m-%d")
        if self._state.daily_pnl_date != today:
            self.reset_daily_tracking()

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        return {
            "stage": self._state.current_stage,
            "days_in_stage": self._state.days_in_stage,
            "entered_at": self._state.entered_at,
            "demotion_reason": self._state.demotion_reason,
            "allocated_capital_inr": float(self.allocated_capital_inr),
            "max_open_positions": self.config.max_open_positions,
            "max_risk_per_trade_inr": float(self.max_risk_per_trade_inr),
            "max_single_loss_inr": float(self.max_single_loss_inr),
            "max_daily_dd_inr": float(self.max_daily_dd_inr),
            "open_positions_count": self._state.open_positions_count,
            "daily_pnl_inr": self._state.daily_pnl_inr,
            "next_trade_gated": self._state.next_trade_gated,
            "next_trade_gate_reason": self._state.next_trade_gate_reason,
            "day_halted": self._state.day_halted,
            "day_halt_reason": self._state.day_halt_reason,
            "total_live_trades": self._state.total_live_trades,
            "promotion_signoff": self._state.promotion_signoff,
            "mock_mode": MOCK_MODE,
            "snapshot_at": utc_now().isoformat(),
        }
