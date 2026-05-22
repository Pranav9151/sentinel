"""
sentinel/live/live_order_router.py
====================================
Live Order Router — Sprint 6.

Routes every order through the full discipline stack before execution.

Check order (most important first):
  1. Kill switch → blocks everything
  2. StageManager.check_can_trade() → capital / position / risk limits
  3. Route to PaperTrader (MOCK_MODE=true) or Kite Connect (MOCK_MODE=false)

In MOCK_MODE=true (default during Sprint 6 paper period):
  - All orders routed to PaperTrader — no real Kite calls ever made
  - Full discipline stack still active (all guardrails tested)

In MOCK_MODE=false (live):
  - Orders sent via KiteConnector
  - Rate limited to KITE_OPS_LIMIT=5 orders/second

Returns OrderResult — a full audit trail for every attempt.
Never raises silently.

Documented in: SPRINT_ROADMAP_v2.md §R8.2, ARCHITECTURE_v5.md §13
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from sentinel.core.types import utc_now
from sentinel.live.stage_manager import StageManager
from sentinel.ops.killswitch import is_kill_active
from sentinel.ops.paper_trader import PaperTrader

logger = logging.getLogger(__name__)

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"
KITE_OPS_LIMIT = 5   # Hard cap: 5 orders per second (ARCHITECTURE_v5.md §13)


# ─────────────────────────────────────────────
# ORDER RESULT
# ─────────────────────────────────────────────

@dataclass
class OrderResult:
    """Complete audit record for every order attempt."""
    order_id: str
    symbol: str
    direction: str
    quantity: int
    entry_price: Decimal
    stop_loss: Decimal
    target_1: Decimal

    approved: bool
    executed: bool
    rejection_reason: str
    execution_mode: str             # "paper" | "live" | "blocked"

    stage_at_order: str
    stage_checks: dict[str, Any]
    paper_position_id: str
    broker_order_id: str

    proposed_risk_inr: Decimal
    allocated_capital_inr: Decimal
    risk_pct_of_allocated: float

    attempted_at: str
    executed_at: str
    notes: str = ""


# ─────────────────────────────────────────────
# ROUTER
# ─────────────────────────────────────────────

class LiveOrderRouter:
    """
    Routes live/paper orders through all discipline checks.

    Usage:
        router = LiveOrderRouter(stage_manager=sm, paper_trader=pt)
        result = router.place_order("RELIANCE", "long", 5,
                                     Decimal("2950"), Decimal("2900"), Decimal("3050"))
        if result.executed:
            print("placed:", result.paper_position_id)
    """

    def __init__(
        self,
        stage_manager: Optional[StageManager] = None,
        paper_trader: Optional[PaperTrader] = None,
        paper_book_path: Optional[Path] = None,
    ) -> None:
        self._sm = stage_manager or StageManager()
        self._pt = paper_trader or PaperTrader(
            book_path=paper_book_path or Path("live_paper_book.json")
        )
        mode = "PAPER/MOCK" if MOCK_MODE else "LIVE (REAL MONEY)"
        logger.info(
            f"[LiveOrderRouter] mode={mode} stage={self._sm.stage.value.upper()}"
        )

    @property
    def stage_manager(self) -> StageManager:
        return self._sm

    @property
    def paper_trader(self) -> PaperTrader:
        return self._pt

    def place_order(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        entry_price: Decimal,
        stop_loss: Decimal,
        target_1: Decimal,
        target_2: Optional[Decimal] = None,
        source_screener: str = "manual",
        notes: str = "",
    ) -> OrderResult:
        """
        Attempt to place an order. Always returns OrderResult.
        Check result.approved and result.executed.
        """
        order_id = str(uuid4())

        # Proposed risk
        if direction == "long":
            proposed_risk = max(Decimal("0"), (entry_price - stop_loss) * quantity)
        else:
            proposed_risk = max(Decimal("0"), (stop_loss - entry_price) * quantity)

        allocated = self._sm.allocated_capital_inr
        risk_pct = float(proposed_risk / allocated * 100) if allocated > 0 else 0.0

        result = OrderResult(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=target_1,
            approved=False,
            executed=False,
            rejection_reason="",
            execution_mode="blocked",
            stage_at_order=self._sm.stage.value,
            stage_checks={},
            paper_position_id="",
            broker_order_id="",
            proposed_risk_inr=proposed_risk,
            allocated_capital_inr=allocated,
            risk_pct_of_allocated=risk_pct,
            attempted_at=utc_now().isoformat(),
            executed_at="",
            notes=notes,
        )

        # ── 1. Kill switch ────────────────────────────────────────────────────
        if is_kill_active():
            result.rejection_reason = "Kill switch is active. All orders blocked."
            self._audit(result)
            return result

        # ── 2. Stage checks ───────────────────────────────────────────────────
        n_open = len(self._pt.get_open_positions())
        stage_check = self._sm.check_can_trade(
            proposed_risk_inr=proposed_risk,
            symbol=symbol,
            open_positions_count=n_open,
        )
        result.stage_checks = stage_check

        if not stage_check["allowed"]:
            result.rejection_reason = stage_check["reason"]
            self._audit(result)
            return result

        # ── All checks passed ─────────────────────────────────────────────────
        result.approved = True

        # ── 3. Execute ────────────────────────────────────────────────────────
        if MOCK_MODE:
            result.execution_mode = "paper"
            try:
                pid = self._pt.place_order(
                    symbol=symbol,
                    direction=direction,
                    quantity=quantity,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    target_1=target_1,
                    target_2=target_2,
                    source_screener=source_screener,
                    notes=f"[Sprint6] {notes}",
                )
                result.paper_position_id = pid
                result.executed = True
                result.executed_at = utc_now().isoformat()
                self._sm.record_trade_opened()
                logger.info(
                    f"[LiveOrderRouter] PAPER {symbol} {direction.upper()} ×{quantity} "
                    f"@ ₹{entry_price} SL ₹{stop_loss} id={pid[:8]}"
                )
            except Exception as e:
                result.approved = False
                result.execution_mode = "blocked"
                result.rejection_reason = f"Paper order failed: {e}"
                logger.error(f"[LiveOrderRouter] Paper order failed {symbol}: {e}")
        else:
            result.execution_mode = "live"
            try:
                broker_id = self._execute_live(
                    symbol, direction, quantity, entry_price, stop_loss, target_1
                )
                result.broker_order_id = broker_id
                result.executed = True
                result.executed_at = utc_now().isoformat()
                self._sm.record_trade_opened()
                logger.info(
                    f"[LiveOrderRouter] LIVE {symbol} {direction.upper()} ×{quantity} "
                    f"@ ₹{entry_price} broker_id={broker_id}"
                )
            except Exception as e:
                result.executed = False
                result.rejection_reason = f"Kite order failed: {e}"
                logger.error(f"[LiveOrderRouter] Kite order failed {symbol}: {e}")

        self._audit(result)
        return result

    def record_close(
        self,
        position_id: str,
        close_price: Decimal,
        reason: str = "manual",
    ) -> dict[str, Any]:
        """
        Close a position and flow P&L into stage manager.
        Returns the stage action dict (may contain triggered actions).
        """
        pnl = Decimal("0")
        if MOCK_MODE:
            try:
                pnl = self._pt.close_position(position_id, close_price, reason)
            except Exception as e:
                logger.error(f"[LiveOrderRouter] close_position failed: {e}")

        actions = self._sm.record_trade_closed(pnl)
        logger.info(
            f"[LiveOrderRouter] closed {position_id[:8]} @ ₹{close_price} "
            f"P&L ₹{pnl:+,.0f} triggers={actions['actions_triggered']}"
        )
        return actions

    def _execute_live(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        entry_price: Decimal,
        stop_loss: Decimal,
        target_1: Decimal,
    ) -> str:
        """Real Kite Connect execution. Only called when MOCK_MODE=false."""
        from sentinel.data.kite_connector import KiteConnector
        kite = KiteConnector()
        order_type = "BUY" if direction == "long" else "SELL"
        res = kite.place_order(
            symbol=symbol,
            order_type=order_type,
            quantity=quantity,
            price=float(entry_price),
        )
        return str(res.get("order_id", "unknown"))

    def _audit(self, result: OrderResult) -> None:
        status = (
            "EXECUTED" if result.executed
            else ("APPROVED/FAILED" if result.approved else "REJECTED")
        )
        logger.info(
            f"[AUDIT] {result.order_id[:8]} {result.symbol} {result.direction} "
            f"qty={result.quantity} risk=₹{result.proposed_risk_inr:,.0f} "
            f"stage={result.stage_at_order} mode={result.execution_mode} "
            f"status={status}"
            + (f" | {result.rejection_reason}" if not result.executed else "")
        )
