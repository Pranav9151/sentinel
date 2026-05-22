"""
sentinel/core/guardrails.py
=============================
Behavioral Guardrail Engine — all 7 guardrails.

These are not suggestions. They interrupt operator actions
and require explicit logged overrides to bypass.

Guardrail #1  — Recency Bias (winning streak inflating size)
Guardrail #2  — Loss Aversion (widening stops on losers)
Guardrail #3  — FOMO Entry (chasing moves after breakout)
Guardrail #4  — Overtrading (too many trades per week)
Guardrail #5  — Position Averaging (buying falling positions)
Guardrail #6  — Tip-Driven Trading (acting on unresearched tips)
Guardrail #7  — GSM/ASM Hard Reject (surveillance-listed stocks)

Three-Override Rule (Guardrail #9 in ARCHITECTURE_v5.md §23):
  3 overrides in any rolling 30-day window → paper mode for 14 days.
  Tracked in override_log.json. Cannot be bypassed via config.

Documented in: ARCHITECTURE_v5.md §23, GLOBAL_FAILURES_PLAYBOOK.md §5
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from sentinel.core.types import utc_now

logger = logging.getLogger(__name__)

OVERRIDE_LOG_PATH  = Path("override_log.json")
OVERRIDE_THRESHOLD = 3          # overrides in rolling window before demotion
OVERRIDE_WINDOW_DAYS = 30       # rolling window in days
PAPER_MODE_DAYS    = 14         # demotion duration


# ─────────────────────────────────────────────
# ENUMS AND RESULT TYPES
# ─────────────────────────────────────────────

class GuardrailStatus(Enum):
    PASSED  = "passed"    # No issue detected
    WARNING = "warning"   # Issue detected but operator can override
    BLOCKED = "blocked"   # Hard block — cannot be overridden


@dataclass
class GuardrailResult:
    """Result of a single guardrail check."""
    guardrail_id:   int
    guardrail_name: str
    status:         GuardrailStatus
    message:        str
    can_override:   bool        = True
    evidence:       dict        = field(default_factory=dict)
    recommended_action: str     = ""

    @property
    def triggered(self) -> bool:
        return self.status != GuardrailStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "guardrail_id":   self.guardrail_id,
            "guardrail_name": self.guardrail_name,
            "status":         self.status.value,
            "message":        self.message,
            "can_override":   self.can_override,
            "evidence":       self.evidence,
            "recommended_action": self.recommended_action,
        }


# ─────────────────────────────────────────────
# OVERRIDE LOG
# ─────────────────────────────────────────────

def _load_override_log() -> list[dict]:
    """Load the override log from disk."""
    if not OVERRIDE_LOG_PATH.exists():
        return []
    try:
        with open(OVERRIDE_LOG_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _save_override_log(log: list[dict]) -> None:
    """Save the override log to disk."""
    try:
        with open(OVERRIDE_LOG_PATH, "w") as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save override log: {e}")


def log_override(
    guardrail_name: str,
    symbol: str,
    operator_reason: str,
) -> dict[str, Any]:
    """
    Log a guardrail override. Returns override record.
    Triggers three-override demotion if threshold reached.
    """
    log = _load_override_log()
    now = utc_now()

    record = {
        "timestamp":      now.isoformat(),
        "guardrail":      guardrail_name,
        "symbol":         symbol,
        "operator_reason":operator_reason,
    }
    log.append(record)
    _save_override_log(log)

    # Count overrides in rolling window
    window_start = now - timedelta(days=OVERRIDE_WINDOW_DAYS)
    recent = [
        r for r in log
        if datetime.fromisoformat(r["timestamp"]) >= window_start
    ]

    logger.warning(
        f"Guardrail override logged: {guardrail_name} | {symbol} | "
        f"Reason: {operator_reason} | "
        f"Overrides in {OVERRIDE_WINDOW_DAYS}d window: {len(recent)}"
    )

    result = {
        "record":          record,
        "overrides_in_window": len(recent),
        "demotion_triggered": False,
        "demotion_message":   "",
    }

    if len(recent) >= OVERRIDE_THRESHOLD:
        result["demotion_triggered"] = True
        result["demotion_message"] = (
            f"THREE-OVERRIDE RULE TRIGGERED: {len(recent)} overrides "
            f"in the last {OVERRIDE_WINDOW_DAYS} days. "
            f"System demoted to PAPER MODE for {PAPER_MODE_DAYS} days. "
            f"Resume live trading only after {PAPER_MODE_DAYS} days "
            f"AND a fresh §7.6 sign-off commit."
        )
        logger.critical(result["demotion_message"])

    return result


def get_override_count_rolling(days: int = 30) -> int:
    """Count overrides in the last N days."""
    log      = _load_override_log()
    cutoff   = utc_now() - timedelta(days=days)
    return sum(
        1 for r in log
        if datetime.fromisoformat(r["timestamp"]) >= cutoff
    )


def get_recent_overrides(days: int = 30) -> list[dict]:
    """Return all override records in the last N days."""
    log    = _load_override_log()
    cutoff = utc_now() - timedelta(days=days)
    return [
        r for r in log
        if datetime.fromisoformat(r["timestamp"]) >= cutoff
    ]


# ─────────────────────────────────────────────
# INDIVIDUAL GUARDRAIL CHECKS
# ─────────────────────────────────────────────

def check_recency_bias(
    recent_wins: int,
    proposed_position_pct: float,
    standard_position_pct: float,
) -> GuardrailResult:
    """
    Guardrail #1 — Recency Bias
    Winning streak causes operator to inflate position sizes.
    Barber-Odean (2000): overconfidence after wins costs 7.1pp/year.

    Triggers when:
    - 3+ consecutive wins AND proposed size > standard size
    """
    if recent_wins >= 3 and proposed_position_pct > standard_position_pct * 1.1:
        return GuardrailResult(
            guardrail_id=1,
            guardrail_name="RecencyBias",
            status=GuardrailStatus.WARNING,
            message=(
                f"You've had {recent_wins} recent winning trades. "
                f"The proposed position ({proposed_position_pct:.1f}% of portfolio) "
                f"exceeds your standard size ({standard_position_pct:.1f}%). "
                "This is recency bias — recent wins do not change edge."
            ),
            evidence={
                "recent_wins": recent_wins,
                "proposed_pct": proposed_position_pct,
                "standard_pct": standard_position_pct,
            },
            recommended_action=(
                f"Use standard position size of {standard_position_pct:.1f}%. "
                "Your edge does not change because of recent wins."
            ),
        )
    return GuardrailResult(
        guardrail_id=1, guardrail_name="RecencyBias",
        status=GuardrailStatus.PASSED, message="No recency bias detected.",
    )


def check_loss_aversion(
    symbol: str,
    original_stop: float,
    proposed_stop: float,
    current_price: float,
    original_thesis: str = "",
) -> GuardrailResult:
    """
    Guardrail #2 — Loss Aversion (Stop Widening)
    Operators widen stops when a trade goes against them.
    This converts small losses into large losses.

    Triggers when proposed_stop is worse than original_stop.
    """
    is_long  = original_stop < current_price
    widening = (
        (is_long  and proposed_stop < original_stop) or
        (not is_long and proposed_stop > original_stop)
    )

    if widening:
        return GuardrailResult(
            guardrail_id=2,
            guardrail_name="LossAversion",
            status=GuardrailStatus.BLOCKED,
            message=(
                f"STOP WIDENING BLOCKED for {symbol}. "
                f"Original stop: ₹{original_stop:,.2f}. "
                f"Proposed stop: ₹{proposed_stop:,.2f}. "
                "Moving a stop against yourself converts small losses "
                "into large losses. This is the most dangerous habit in trading."
            ),
            can_override=True,
            evidence={
                "symbol": symbol,
                "original_stop": original_stop,
                "proposed_stop": proposed_stop,
                "current_price": current_price,
                "is_long": is_long,
            },
            recommended_action=(
                "If the original thesis is intact → hold with original stop. "
                "If the thesis is broken → exit at market now. "
                "Never move the stop against yourself."
            ),
        )
    return GuardrailResult(
        guardrail_id=2, guardrail_name="LossAversion",
        status=GuardrailStatus.PASSED, message="Stop movement is acceptable.",
    )


def check_fomo_entry(
    symbol: str,
    current_price: float,
    price_5d_ago: float,
    has_premarket_plan: bool,
    rr_ratio: float,
    min_rr: float = 2.0,
) -> GuardrailResult:
    """
    Guardrail #3 — FOMO Entry
    Entering a stock after a large move without a pre-market plan.
    Documented in: GLOBAL_FAILURES_PLAYBOOK.md §5.3

    Triggers when:
    - Stock moved >5% in 5 days AND no pre-market plan AND R:R < minimum
    """
    move_pct = (current_price - price_5d_ago) / price_5d_ago * 100

    if move_pct > 5 and not has_premarket_plan and rr_ratio < min_rr:
        return GuardrailResult(
            guardrail_id=3,
            guardrail_name="FOMOEntry",
            status=GuardrailStatus.WARNING,
            message=(
                f"{symbol} has risen {move_pct:.1f}% in 5 days. "
                f"R:R from current level is 1:{rr_ratio:.1f} — "
                f"below minimum 1:{min_rr:.0f}. "
                "You did not have this in your pre-market plan. "
                "Chasing a stock after a 5%+ move has poor base-rate outcomes."
            ),
            evidence={
                "symbol": symbol,
                "move_5d_pct": round(move_pct, 2),
                "rr_ratio": rr_ratio,
                "has_premarket_plan": has_premarket_plan,
            },
            recommended_action=(
                "Watch only. If it consolidates for 2-3 days, "
                "reassess from the new base. Do not chase."
            ),
        )
    return GuardrailResult(
        guardrail_id=3, guardrail_name="FOMOEntry",
        status=GuardrailStatus.PASSED, message="Entry conditions acceptable.",
    )


def check_overtrading(
    trades_this_week: int,
    max_trades_per_week: int = 5,
) -> GuardrailResult:
    """
    Guardrail #4 — Overtrading
    Barber-Odean (2000): most active 20% of retail traders earned
    11.4% vs 18.5% for least active — 7.1pp gap, entirely from costs.

    Triggers when weekly trade count exceeds limit.
    """
    if trades_this_week >= max_trades_per_week:
        return GuardrailResult(
            guardrail_id=4,
            guardrail_name="Overtrading",
            status=GuardrailStatus.WARNING,
            message=(
                f"Weekly trade limit reached: {trades_this_week} trades "
                f"(limit: {max_trades_per_week}). "
                "Barber-Odean (2000): the most active retail traders "
                "earned 7.1pp less per year than the least active. "
                "Each additional trade costs you on average."
            ),
            evidence={
                "trades_this_week": trades_this_week,
                "limit": max_trades_per_week,
                "barber_odean_cost_pp": 7.1,
            },
            recommended_action=(
                "Quality over quantity. "
                "Only enter if conviction score >= 70 and R:R >= 2.5. "
                "Write a pre-mortem before overriding."
            ),
        )
    return GuardrailResult(
        guardrail_id=4, guardrail_name="Overtrading",
        status=GuardrailStatus.PASSED,
        message=f"Trade count this week: {trades_this_week}/{max_trades_per_week}.",
    )


def check_position_averaging(
    symbol: str,
    original_entry: float,
    current_price: float,
    is_long: bool,
    has_new_catalyst: bool,
    recent_losses: int = 0,
) -> GuardrailResult:
    """
    Guardrail #5 — Position Averaging on Losers
    Buying more of a losing position without a new catalyst.
    Heimer (2016): past losses amplify disposition effect by ~10%.

    Hard block if no new catalyst. Warning if catalyst exists.
    """
    if is_long:
        loss_pct = (original_entry - current_price) / original_entry * 100
        is_losing = current_price < original_entry
    else:
        loss_pct = (current_price - original_entry) / original_entry * 100
        is_losing = current_price > original_entry

    if is_losing and not has_new_catalyst:
        # Harder block if operator also has recent losses (Heimer effect)
        status = GuardrailStatus.BLOCKED if recent_losses >= 2 else GuardrailStatus.WARNING
        return GuardrailResult(
            guardrail_id=5,
            guardrail_name="PositionAveraging",
            status=status,
            message=(
                f"AVERAGING DOWN BLOCKED for {symbol}. "
                f"Position is {loss_pct:.1f}% against you with no new catalyst. "
                "Averaging a loser without a catalyst is how small losses "
                "become account-destroying losses."
            ),
            can_override=True,
            evidence={
                "symbol":         symbol,
                "original_entry": original_entry,
                "current_price":  current_price,
                "loss_pct":       round(loss_pct, 2),
                "has_new_catalyst": has_new_catalyst,
                "recent_losses":  recent_losses,
            },
            recommended_action=(
                "If the thesis is intact → hold with original stop, no add. "
                "If thesis is broken → exit now. "
                "If you have a genuine NEW catalyst → override with written justification."
            ),
        )

    if is_losing and has_new_catalyst:
        return GuardrailResult(
            guardrail_id=5,
            guardrail_name="PositionAveraging",
            status=GuardrailStatus.WARNING,
            message=(
                f"{symbol} is {loss_pct:.1f}% against you. "
                "You have indicated a new catalyst exists. "
                "Adding to a loser always increases risk — size carefully."
            ),
            evidence={"symbol": symbol, "loss_pct": round(loss_pct, 2)},
            recommended_action="Add maximum 50% of original size. Tighten stop.",
        )

    return GuardrailResult(
        guardrail_id=5, guardrail_name="PositionAveraging",
        status=GuardrailStatus.PASSED, message="Position is profitable — averaging allowed.",
    )


def check_tip_driven_trading(
    symbol: str,
    has_screener_card: bool,
    tip_source: Optional[str] = None,
) -> GuardrailResult:
    """
    Guardrail #6 — Tip-Driven Trading
    Indian-specific: WhatsApp groups, YouTube channels, Twitter tips.
    Any trade without a Sentinel screener card is tip-driven.

    Hard block — all trades must originate from a screener card.
    """
    if not has_screener_card:
        source_msg = f" (source: {tip_source})" if tip_source else ""
        return GuardrailResult(
            guardrail_id=6,
            guardrail_name="TipDrivenTrading",
            status=GuardrailStatus.BLOCKED,
            message=(
                f"NO SCREENER CARD for {symbol}{source_msg}. "
                "Trades without a Sentinel-generated card are tip-driven. "
                "Tips from WhatsApp, YouTube, Twitter, and friends "
                "have no documented edge and no risk management."
            ),
            can_override=True,
            evidence={
                "symbol": symbol,
                "has_screener_card": False,
                "tip_source": tip_source,
            },
            recommended_action=(
                "Run the relevant screener first. "
                "If the stock qualifies, a Card will be generated. "
                "Trade from the Card, not the tip."
            ),
        )
    return GuardrailResult(
        guardrail_id=6, guardrail_name="TipDrivenTrading",
        status=GuardrailStatus.PASSED,
        message=f"Screener card exists for {symbol}. Trade is system-driven.",
    )


def check_gsm_asm(
    symbol: str,
    is_on_surveillance: bool,
) -> GuardrailResult:
    """
    Guardrail #7 — GSM/ASM Hard Rejection
    Surveillance-listed stocks are NEVER traded.
    Cannot be overridden — this is a physical block.

    Documented in: GLOBAL_FAILURES_PLAYBOOK.md §1.16
    """
    if is_on_surveillance:
        return GuardrailResult(
            guardrail_id=7,
            guardrail_name="GSMASMRejection",
            status=GuardrailStatus.BLOCKED,
            message=(
                f"HARD REJECTION: {symbol} is on the NSE/BSE "
                "GSM/ASM surveillance list. "
                "This instrument is blocked for all trading activity. "
                "No override is possible."
            ),
            can_override=False,    # HARD BLOCK — cannot be overridden
            evidence={"symbol": symbol, "on_surveillance": True},
            recommended_action="Find an alternative instrument.",
        )
    return GuardrailResult(
        guardrail_id=7, guardrail_name="GSMASMRejection",
        status=GuardrailStatus.PASSED,
        message=f"{symbol} is not on surveillance list.",
    )


# ─────────────────────────────────────────────
# GUARDRAIL ENGINE — runs all checks
# ─────────────────────────────────────────────

class GuardrailEngine:
    """
    Runs all applicable guardrails for a proposed trade.

    Usage:
        engine = GuardrailEngine()
        results = engine.check_trade(
            symbol="RELIANCE",
            is_on_surveillance=False,
            has_screener_card=True,
            proposed_position_pct=2.0,
            standard_position_pct=1.0,
            recent_wins=4,
            trades_this_week=3,
        )
        if results["blocked"]:
            show_block_message(results["hard_blocks"])
        elif results["warnings"]:
            show_warning_message(results["warnings"])
    """

    def check_trade(
        self,
        symbol: str,
        is_on_surveillance: bool = False,
        has_screener_card: bool = True,
        proposed_position_pct: float = 1.0,
        standard_position_pct: float = 1.0,
        recent_wins: int = 0,
        trades_this_week: int = 0,
        max_trades_per_week: int = 5,
        tip_source: Optional[str] = None,
        # For stop-widening check
        check_stop: bool = False,
        original_stop: float = 0,
        proposed_stop: float = 0,
        current_price: float = 0,
        # For FOMO check
        price_5d_ago: float = 0,
        has_premarket_plan: bool = True,
        rr_ratio: float = 3.0,
        # For position averaging check
        check_averaging: bool = False,
        original_entry: float = 0,
        is_long: bool = True,
        has_new_catalyst: bool = False,
        recent_losses: int = 0,
    ) -> dict[str, Any]:
        """Run all applicable guardrails. Returns consolidated result."""
        results: list[GuardrailResult] = []

        # Always run these
        results.append(check_gsm_asm(symbol, is_on_surveillance))
        results.append(check_tip_driven_trading(symbol, has_screener_card, tip_source))
        results.append(check_recency_bias(
            recent_wins, proposed_position_pct, standard_position_pct
        ))
        results.append(check_overtrading(trades_this_week, max_trades_per_week))

        # Conditional checks
        if price_5d_ago > 0 and current_price > 0:
            results.append(check_fomo_entry(
                symbol, current_price, price_5d_ago,
                has_premarket_plan, rr_ratio
            ))

        if check_stop and original_stop > 0 and proposed_stop > 0:
            results.append(check_loss_aversion(
                symbol, original_stop, proposed_stop, current_price
            ))

        if check_averaging and original_entry > 0:
            results.append(check_position_averaging(
                symbol, original_entry, current_price, is_long,
                has_new_catalyst, recent_losses
            ))

        triggered   = [r for r in results if r.triggered]
        # BLOCKED status always counts as a block — even if can_override=True
        # (operator can override but system still marks it as blocked)
        hard_blocks = [r for r in triggered
                       if r.status == GuardrailStatus.BLOCKED]
        warnings    = [r for r in triggered
                       if r.status == GuardrailStatus.WARNING]

        # Three-override count
        override_count = get_override_count_rolling(OVERRIDE_WINDOW_DAYS)
        three_override_warning = override_count >= (OVERRIDE_THRESHOLD - 1)

        return {
            "symbol":        symbol,
            "all_results":   [r.to_dict() for r in results],
            "triggered":     [r.to_dict() for r in triggered],
            "hard_blocks":   [r.to_dict() for r in hard_blocks],
            "warnings":      [r.to_dict() for r in warnings],
            "blocked":       len(hard_blocks) > 0,
            "has_warnings":  len(warnings) > 0,
            "clear":         len(triggered) == 0,
            "override_count_30d": override_count,
            "three_override_warning": three_override_warning,
            "three_override_demote": override_count >= OVERRIDE_THRESHOLD,
        }

    def get_dashboard_summary(self) -> dict[str, Any]:
        """Summary for the dashboard guardrails panel."""
        overrides = get_recent_overrides(30)
        count     = len(overrides)
        remaining = max(0, OVERRIDE_THRESHOLD - count)

        return {
            "overrides_30d":        count,
            "overrides_remaining":  remaining,
            "threshold":            OVERRIDE_THRESHOLD,
            "demotion_triggered":   count >= OVERRIDE_THRESHOLD,
            "recent_overrides":     overrides[-5:],   # Last 5
            "status": (
                "🔴 DEMOTED TO PAPER MODE" if count >= OVERRIDE_THRESHOLD else
                f"🟠 WARNING: {remaining} overrides remaining before demotion"
                if count >= OVERRIDE_THRESHOLD - 1 else
                f"🟢 {count} overrides used of {OVERRIDE_THRESHOLD} allowed (30d)"
            ),
        }
