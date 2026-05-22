"""
sentinel/ops/paper_trader.py
=============================
Paper Trading Engine — Sprint 5.

Simulates order execution against live mock prices.
No real broker connection. No real money ever moves.

Responsibilities:
  - Accept paper orders (ExecutionSignal or direct params)
  - Simulate fill at current mock price (or limit price if better)
  - Track open positions, unrealized P&L, realized P&L
  - Check stop-loss and target levels on every tick update
  - Reconciliation: verify book is internally consistent
  - Performance summary: Sharpe, max drawdown, win rate

Design rules (per ARCHITECTURE_v5.md §11, SPRINT_ROADMAP_v2.md §R7):
  - Kill-switch blocks all new orders immediately when active
  - Monthly loss circuit breaker enforced (5% default)
  - All datetimes UTC-aware (utc_now())
  - All P&L in Money(INR) — never bare floats
  - Positions persisted to JSON file (path injectable for test isolation)
  - MOCK_MODE=true is the only supported mode in Sprint 5

Documented in: SPRINT_ROADMAP_v2.md §R7, ARCHITECTURE_v5.md §11
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from sentinel.core.errors import (
    KillSwitchActivatedError,
    MonthlyLossLimitReachedError,
    PositionSizeTooLargeError,
)
from sentinel.core.types import (
    utc_now,
)
from sentinel.ops.killswitch import is_kill_active

logger = logging.getLogger(__name__)

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"

# ─────────────────────────────────────────────
# DEFAULT FILE PATH  (overridden in tests via constructor)
# ─────────────────────────────────────────────
DEFAULT_PAPER_BOOK_PATH = Path("paper_book.json")


# ─────────────────────────────────────────────
# PAPER POSITION
# ─────────────────────────────────────────────

@dataclass
class PaperPosition:
    """
    A paper trading position.

    Unlike the live Position type, this carries current_price for
    unrealized P&L computation and the paper fill metadata.
    """
    position_id: str
    symbol: str
    direction: str                  # "long" or "short"
    quantity: int
    entry_price: Decimal            # Simulated fill price (INR)
    stop_loss: Decimal              # Price level for stop
    target_1: Decimal               # Price level for T1
    target_2: Optional[Decimal]

    opened_at: str                  # ISO UTC string
    source_screener: str
    notes: str = ""

    # Updated on every tick
    current_price: Decimal = Decimal("0")
    status: str = "open"            # "open" | "closed"
    close_price: Optional[Decimal] = None
    closed_at: Optional[str] = None
    close_reason: str = ""          # "stop_loss" | "target_1" | "target_2" | "manual" | "kill_switch"

    @property
    def unrealized_pnl_inr(self) -> Decimal:
        """Unrealized P&L in INR at current_price."""
        if self.status != "open" or self.current_price == Decimal("0"):
            return Decimal("0")
        if self.direction == "long":
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    @property
    def realized_pnl_inr(self) -> Decimal:
        """Realized P&L when position is closed."""
        if self.status != "closed" or self.close_price is None:
            return Decimal("0")
        if self.direction == "long":
            return (self.close_price - self.entry_price) * self.quantity
        return (self.entry_price - self.close_price) * self.quantity

    @property
    def capital_deployed(self) -> Decimal:
        """INR capital tied up in this position."""
        return self.entry_price * self.quantity

    def to_dict(self) -> dict[str, Any]:
        """Serialise to plain dict for JSON persistence."""
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "quantity": self.quantity,
            "entry_price": str(self.entry_price),
            "stop_loss": str(self.stop_loss),
            "target_1": str(self.target_1),
            "target_2": str(self.target_2) if self.target_2 else None,
            "opened_at": self.opened_at,
            "source_screener": self.source_screener,
            "notes": self.notes,
            "current_price": str(self.current_price),
            "status": self.status,
            "close_price": str(self.close_price) if self.close_price else None,
            "closed_at": self.closed_at,
            "close_reason": self.close_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PaperPosition":
        """Deserialise from dict (loaded from JSON)."""
        return cls(
            position_id=d["position_id"],
            symbol=d["symbol"],
            direction=d["direction"],
            quantity=int(d["quantity"]),
            entry_price=Decimal(d["entry_price"]),
            stop_loss=Decimal(d["stop_loss"]),
            target_1=Decimal(d["target_1"]),
            target_2=Decimal(d["target_2"]) if d.get("target_2") else None,
            opened_at=d["opened_at"],
            source_screener=d.get("source_screener", ""),
            notes=d.get("notes", ""),
            current_price=Decimal(d.get("current_price", "0")),
            status=d.get("status", "open"),
            close_price=Decimal(d["close_price"]) if d.get("close_price") else None,
            closed_at=d.get("closed_at"),
            close_reason=d.get("close_reason", ""),
        )


# ─────────────────────────────────────────────
# PERFORMANCE SNAPSHOT
# ─────────────────────────────────────────────

@dataclass
class PerformanceSummary:
    """Snapshot of paper trading performance metrics."""
    total_trades: int
    open_trades: int
    closed_trades: int
    winning_trades: int
    losing_trades: int

    total_realized_pnl_inr: Decimal
    total_unrealized_pnl_inr: Decimal
    total_pnl_inr: Decimal

    win_rate_pct: float
    avg_win_inr: Decimal
    avg_loss_inr: Decimal
    profit_factor: float            # gross_wins / gross_losses

    max_drawdown_pct: float         # Peak-to-trough on closed equity curve
    sharpe_ratio: float             # Annualized Sharpe of daily P&L series
    monthly_pnl_inr: Decimal        # Current calendar month realized P&L

    portfolio_value_inr: Decimal    # Starting value (from OperatorProfile)
    current_equity_inr: Decimal     # Starting value + realized P&L

    computed_at: str                # UTC ISO string

    def is_monthly_limit_breached(self, max_monthly_loss_pct: float = 5.0) -> bool:
        """True if monthly P&L loss exceeds the circuit-breaker threshold."""
        if self.monthly_pnl_inr >= Decimal("0"):
            return False
        loss_pct = abs(float(self.monthly_pnl_inr)) / float(self.portfolio_value_inr) * 100
        return loss_pct >= max_monthly_loss_pct


# ─────────────────────────────────────────────
# PAPER TRADER
# ─────────────────────────────────────────────

class PaperTrader:
    """
    Paper Trading Engine for Project Sentinel Sprint 5.

    Usage:
        trader = PaperTrader(portfolio_value_inr=Decimal("300000"))
        pos_id = trader.place_order("RELIANCE", "long", 10,
                                     entry_price=Decimal("2950"),
                                     stop_loss=Decimal("2880"),
                                     target_1=Decimal("3090"))
        trader.update_price("RELIANCE", Decimal("3010"))
        trader.close_position(pos_id, Decimal("3010"), reason="manual")
        summary = trader.get_performance_summary()

    All state persisted to JSON at book_path.  Use tmp_path in tests.
    """

    def __init__(
        self,
        portfolio_value_inr: Decimal = Decimal("300000"),
        book_path: Optional[Path] = None,
        max_monthly_loss_pct: float = 5.0,
    ) -> None:
        self.portfolio_value_inr = portfolio_value_inr
        self.book_path = book_path or DEFAULT_PAPER_BOOK_PATH
        self.max_monthly_loss_pct = max_monthly_loss_pct

        # In-memory book: position_id → PaperPosition
        self._positions: dict[str, PaperPosition] = {}
        # Daily equity curve: list of (date_str, equity_inr)
        self._equity_curve: list[dict[str, Any]] = []
        # Load persisted state
        self._load()

        logger.info(
            f"PaperTrader initialised. Portfolio: ₹{portfolio_value_inr:,.0f}. "
            f"Book: {self.book_path}. Positions loaded: {len(self._positions)}"
        )

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load positions and equity curve from JSON file."""
        if not self.book_path.exists():
            return
        try:
            with open(self.book_path) as f:
                data = json.load(f)
            for d in data.get("positions", []):
                pos = PaperPosition.from_dict(d)
                self._positions[pos.position_id] = pos
            self._equity_curve = data.get("equity_curve", [])
        except Exception as e:
            logger.warning(f"Could not load paper book from {self.book_path}: {e}")

    def _save(self) -> None:
        """Persist positions and equity curve to JSON file."""
        try:
            data = {
                "schema_version": "PaperBookV1",
                "portfolio_value_inr": str(self.portfolio_value_inr),
                "positions": [p.to_dict() for p in self._positions.values()],
                "equity_curve": self._equity_curve,
                "saved_at": utc_now().isoformat(),
            }
            with open(self.book_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Could not save paper book: {e}")

    # ── Order Placement ──────────────────────────────────────────────────────

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
    ) -> str:
        """
        Place a paper order and return the new position_id.

        Raises:
            KillSwitchActivatedError — if kill switch is active
            MonthlyLossLimitReachedError — if monthly circuit breaker is hit
            PositionSizeTooLargeError — if capital > 10% of portfolio
            ValueError — if direction/quantity invalid
        """
        # 1. Kill switch check — most important check
        if is_kill_active():
            raise KillSwitchActivatedError(
                "Kill switch is active. Paper orders blocked. "
                "Reset kill switch before placing new orders."
            )

        # 2. Monthly loss circuit breaker
        summary = self.get_performance_summary()
        if summary.is_monthly_limit_breached(self.max_monthly_loss_pct):
            raise MonthlyLossLimitReachedError(
                f"Monthly loss limit reached ({self.max_monthly_loss_pct}%). "
                f"Monthly P&L: ₹{summary.monthly_pnl_inr:,.0f}. "
                f"No new positions until next month or operator review."
            )

        # 3. Validate inputs
        if direction not in ("long", "short"):
            raise ValueError(f"direction must be 'long' or 'short', got '{direction}'")
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")
        if entry_price <= Decimal("0"):
            raise ValueError(f"entry_price must be positive, got {entry_price}")

        # 4. Position size cap: max 10% of portfolio in a single position
        capital = entry_price * quantity
        capital_pct = float(capital) / float(self.portfolio_value_inr) * 100
        if capital_pct > 10.0:
            raise PositionSizeTooLargeError(symbol, capital_pct, 10.0)

        # 5. Create position — fill at entry_price (market order simulation)
        position_id = str(uuid4())
        now_utc = utc_now()

        pos = PaperPosition(
            position_id=position_id,
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            opened_at=now_utc.isoformat(),
            source_screener=source_screener,
            notes=notes,
            current_price=entry_price,
            status="open",
        )

        self._positions[position_id] = pos
        self._save()

        logger.info(
            f"[PAPER ORDER] {symbol} {direction.upper()} ×{quantity} "
            f"@ ₹{entry_price} | SL ₹{stop_loss} | T1 ₹{target_1} | "
            f"Capital ₹{capital:,.0f} ({capital_pct:.1f}%) | id={position_id[:8]}"
        )
        return position_id

    # ── Price Updates ────────────────────────────────────────────────────────

    def update_price(self, symbol: str, current_price: Decimal) -> list[str]:
        """
        Update the current price for a symbol and check SL/T1 triggers.

        Returns list of position_ids that were auto-closed by SL or T1.
        """
        auto_closed: list[str] = []

        for pos in list(self._positions.values()):
            if pos.symbol != symbol or pos.status != "open":
                continue

            # Update current price
            pos.current_price = current_price

            # Check stop-loss trigger
            if self._sl_triggered(pos, current_price):
                self.close_position(pos.position_id, current_price, "stop_loss")
                auto_closed.append(pos.position_id)
                continue

            # Check target 1 trigger
            if self._t1_triggered(pos, current_price):
                self.close_position(pos.position_id, current_price, "target_1")
                auto_closed.append(pos.position_id)
                continue

            # Check target 2 trigger (if set)
            if pos.target_2 and self._t2_triggered(pos, current_price):
                self.close_position(pos.position_id, current_price, "target_2")
                auto_closed.append(pos.position_id)

        self._save()
        return auto_closed

    def _sl_triggered(self, pos: PaperPosition, price: Decimal) -> bool:
        if pos.direction == "long":
            return price <= pos.stop_loss
        return price >= pos.stop_loss

    def _t1_triggered(self, pos: PaperPosition, price: Decimal) -> bool:
        if pos.direction == "long":
            return price >= pos.target_1
        return price <= pos.target_1

    def _t2_triggered(self, pos: PaperPosition, price: Decimal) -> bool:
        if pos.target_2 is None:
            return False
        if pos.direction == "long":
            return price >= pos.target_2
        return price <= pos.target_2

    # ── Position Close ───────────────────────────────────────────────────────

    def close_position(
        self,
        position_id: str,
        close_price: Decimal,
        reason: str = "manual",
    ) -> Decimal:
        """
        Close a paper position at close_price.

        Returns the realized P&L in INR (can be negative).
        Raises KeyError if position not found.
        Raises ValueError if position is already closed.
        """
        pos = self._positions.get(position_id)
        if pos is None:
            raise KeyError(f"Position {position_id} not found in paper book.")
        if pos.status == "closed":
            raise ValueError(f"Position {position_id} is already closed.")

        pos.close_price = close_price
        pos.closed_at = utc_now().isoformat()
        pos.status = "closed"
        pos.close_reason = reason
        pos.current_price = close_price

        pnl = pos.realized_pnl_inr

        # Record equity snapshot
        current_equity = self.portfolio_value_inr + self._total_realized_pnl()
        self._equity_curve.append({
            "ts": pos.closed_at,
            "equity_inr": str(current_equity),
            "pnl_inr": str(pnl),
            "symbol": pos.symbol,
            "reason": reason,
        })

        self._save()

        logger.info(
            f"[PAPER CLOSE] {pos.symbol} @ ₹{close_price} | "
            f"P&L ₹{pnl:+,.0f} | reason={reason} | id={position_id[:8]}"
        )
        return pnl

    def close_all(self, reason: str = "kill_switch") -> dict[str, Decimal]:
        """
        Close all open positions. Returns dict of position_id → pnl.
        Called by kill switch activation.
        """
        closed = {}
        for pos in list(self._positions.values()):
            if pos.status == "open":
                pnl = self.close_position(
                    pos.position_id,
                    pos.current_price if pos.current_price > Decimal("0") else pos.entry_price,
                    reason=reason,
                )
                closed[pos.position_id] = pnl
        logger.info(f"[PAPER CLOSE ALL] {len(closed)} positions closed. Reason: {reason}")
        return closed

    # ── Queries ──────────────────────────────────────────────────────────────

    def get_open_positions(self) -> list[PaperPosition]:
        """Return all currently open paper positions."""
        return [p for p in self._positions.values() if p.status == "open"]

    def get_closed_positions(self) -> list[PaperPosition]:
        """Return all closed paper positions."""
        return [p for p in self._positions.values() if p.status == "closed"]

    def get_position(self, position_id: str) -> Optional[PaperPosition]:
        """Get a specific position by ID."""
        return self._positions.get(position_id)

    # ── Reconciliation ───────────────────────────────────────────────────────

    def reconcile(self) -> dict[str, Any]:
        """
        Verify internal consistency of the paper book.

        Checks:
          - No positions with invalid status
          - Closed positions have close_price and closed_at set
          - Open positions have positive quantity
          - Equity curve is non-empty if any closed trades exist

        Returns dict with 'ok' bool and 'issues' list.
        """
        issues = []

        for pid, pos in self._positions.items():
            if pos.status not in ("open", "closed"):
                issues.append(f"{pid[:8]}: invalid status '{pos.status}'")

            if pos.status == "closed":
                if pos.close_price is None:
                    issues.append(f"{pid[:8]}: closed but no close_price")
                if pos.closed_at is None:
                    issues.append(f"{pid[:8]}: closed but no closed_at")

            if pos.status == "open":
                if pos.quantity <= 0:
                    issues.append(f"{pid[:8]}: open with quantity <= 0")
                if pos.entry_price <= Decimal("0"):
                    issues.append(f"{pid[:8]}: open with entry_price <= 0")

        closed_count = len(self.get_closed_positions())
        equity_count = len(self._equity_curve)
        if closed_count > 0 and equity_count == 0:
            issues.append(f"{closed_count} closed positions but equity_curve is empty")

        result = {
            "ok": len(issues) == 0,
            "issues": issues,
            "open_positions": len(self.get_open_positions()),
            "closed_positions": len(self.get_closed_positions()),
            "equity_snapshots": equity_count,
            "checked_at": utc_now().isoformat(),
        }

        if result["ok"]:
            logger.info("Reconcile: OK")
        else:
            logger.warning(f"Reconcile: {len(issues)} issues found: {issues}")

        return result

    # ── Performance ──────────────────────────────────────────────────────────

    def _total_realized_pnl(self) -> Decimal:
        return sum(
            (p.realized_pnl_inr for p in self._positions.values() if p.status == "closed"),
            Decimal("0"),
        )

    def _total_unrealized_pnl(self) -> Decimal:
        return sum(
            (p.unrealized_pnl_inr for p in self._positions.values() if p.status == "open"),
            Decimal("0"),
        )

    def _monthly_realized_pnl(self) -> Decimal:
        """Realized P&L in the current calendar month."""
        now = utc_now()
        total = Decimal("0")
        for pos in self._positions.values():
            if pos.status == "closed" and pos.closed_at:
                try:
                    closed_dt = datetime.fromisoformat(pos.closed_at)
                    if closed_dt.year == now.year and closed_dt.month == now.month:
                        total += pos.realized_pnl_inr
                except ValueError:
                    pass
        return total

    def _compute_sharpe(self) -> float:
        """
        Compute annualised Sharpe ratio from equity curve.
        Returns 0.0 if fewer than 5 data points.
        """
        if len(self._equity_curve) < 5:
            return 0.0

        try:
            pnls = [float(e["pnl_inr"]) for e in self._equity_curve]
            n = len(pnls)
            mean = sum(pnls) / n
            variance = sum((x - mean) ** 2 for x in pnls) / n
            std = math.sqrt(variance)
            if std == 0:
                return 0.0
            # Annualise: each trade ≈ 5 trading days average
            trades_per_year = 252 / 5
            sharpe = (mean / std) * math.sqrt(trades_per_year)
            return round(sharpe, 3)
        except Exception:
            return 0.0

    def _compute_max_drawdown(self) -> float:
        """
        Max peak-to-trough drawdown on the running equity curve.
        Returns 0.0 if fewer than 2 data points.
        """
        if len(self._equity_curve) < 2:
            return 0.0

        try:
            equities = [float(e["equity_inr"]) for e in self._equity_curve]
            peak = equities[0]
            max_dd = 0.0
            for e in equities[1:]:
                if e > peak:
                    peak = e
                dd = (peak - e) / peak * 100 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)
            return round(max_dd, 2)
        except Exception:
            return 0.0

    def get_performance_summary(self) -> PerformanceSummary:
        """
        Compute and return the full performance summary.
        Call this after any set of tick updates or close operations.
        """
        closed = self.get_closed_positions()
        open_pos = self.get_open_positions()

        wins = [p for p in closed if p.realized_pnl_inr > Decimal("0")]
        losses = [p for p in closed if p.realized_pnl_inr <= Decimal("0")]

        total_realized = self._total_realized_pnl()
        total_unrealized = self._total_unrealized_pnl()

        gross_wins = sum((p.realized_pnl_inr for p in wins), Decimal("0"))
        gross_losses = abs(sum((p.realized_pnl_inr for p in losses), Decimal("0")))

        profit_factor = (
            float(gross_wins) / float(gross_losses)
            if gross_losses > Decimal("0") else
            float("inf") if gross_wins > Decimal("0") else 0.0
        )

        avg_win = gross_wins / len(wins) if wins else Decimal("0")
        avg_loss = gross_losses / len(losses) if losses else Decimal("0")
        win_rate = len(wins) / len(closed) * 100 if closed else 0.0

        current_equity = self.portfolio_value_inr + total_realized

        return PerformanceSummary(
            total_trades=len(self._positions),
            open_trades=len(open_pos),
            closed_trades=len(closed),
            winning_trades=len(wins),
            losing_trades=len(losses),
            total_realized_pnl_inr=total_realized,
            total_unrealized_pnl_inr=total_unrealized,
            total_pnl_inr=total_realized + total_unrealized,
            win_rate_pct=round(win_rate, 1),
            avg_win_inr=avg_win,
            avg_loss_inr=avg_loss,
            profit_factor=round(profit_factor, 2),
            max_drawdown_pct=self._compute_max_drawdown(),
            sharpe_ratio=self._compute_sharpe(),
            monthly_pnl_inr=self._monthly_realized_pnl(),
            portfolio_value_inr=self.portfolio_value_inr,
            current_equity_inr=current_equity,
            computed_at=utc_now().isoformat(),
        )

    def summary_dict(self) -> dict[str, Any]:
        """Return performance summary as a plain dict for dashboard display."""
        s = self.get_performance_summary()
        return {
            "total_trades": s.total_trades,
            "open_trades": s.open_trades,
            "closed_trades": s.closed_trades,
            "win_rate_pct": s.win_rate_pct,
            "total_realized_pnl_inr": float(s.total_realized_pnl_inr),
            "total_unrealized_pnl_inr": float(s.total_unrealized_pnl_inr),
            "total_pnl_inr": float(s.total_pnl_inr),
            "profit_factor": s.profit_factor,
            "max_drawdown_pct": s.max_drawdown_pct,
            "sharpe_ratio": s.sharpe_ratio,
            "monthly_pnl_inr": float(s.monthly_pnl_inr),
            "current_equity_inr": float(s.current_equity_inr),
            "computed_at": s.computed_at,
        }
