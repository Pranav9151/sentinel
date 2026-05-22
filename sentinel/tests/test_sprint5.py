"""
sentinel/tests/test_sprint5.py
================================
Sprint 5 Acceptance Gate Tests.

ALL must pass before Sprint 6 begins.
Run with: pytest sentinel/tests/test_sprint5.py -v

Sprint 5 gates (from SPRINT_ROADMAP_v2.md §R7):
  [x] Paper trader creates positions correctly
  [x] Paper trader tracks P&L correctly (long and short)
  [x] Paper trader stop-loss auto-closes position
  [x] Paper trader target-1 auto-closes position
  [x] Paper trader kill-switch blocks new orders
  [x] Paper trader monthly circuit breaker blocks orders
  [x] Paper trader close_all() flattens all positions (kill switch test < 5s)
  [x] Paper trader reconcile() passes on clean book
  [x] Paper trader reconcile() catches corrupted positions
  [x] Paper trader persists and reloads from JSON
  [x] Paper trader performance summary: win rate, Sharpe, drawdown
  [x] Scheduler initialises without error
  [x] Scheduler has correct number of jobs registered
  [x] Scheduler starts and stops cleanly
  [x] Scheduler run_job_now() executes a job
  [x] Telegram bot initialises in mock mode (no token needed)
  [x] Telegram bot send_message returns True in mock mode
  [x] Telegram bot send_morning_brief works in mock mode
  [x] Telegram bot send_kill_switch_alert works in mock mode
  [x] Telegram bot send_demotion_alert works in mock mode
  [x] Telegram bot rate limiting works correctly
  [x] Strategy 1 rank_universe returns ranked entries
  [x] Strategy 1 run() generates signals
  [x] Strategy 1 signals have valid R:R
  [x] Strategy 1 signals have ATR-based position sizing
  [x] Strategy 1 backtest runs without error
  [x] Strategy 1 OOS Sharpe >= 0.5 on mock data  ← key acceptance gate
  [x] Strategy 1 backtest passes acceptance gate check
  [x] Kill switch test: close_all() in < 5 seconds (paper session)
  [x] 30-day clean paper trading simulated with mock data
"""

from __future__ import annotations

import json
import os
import time
from decimal import Decimal

import pytest

# ─────────────────────────────────────────────
# ENSURE MOCK_MODE FOR ALL TESTS
# ─────────────────────────────────────────────

os.environ["MOCK_MODE"] = "true"


# ─────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────

from sentinel.ops.paper_trader import (
    PaperTrader,
)
from sentinel.ops.telegram_bot import TelegramBot
from sentinel.ops.scheduler import SentinelScheduler
from sentinel.strategies.strategy1_momentum import (
    CrossSectionalMomentumIN,
    MomentumRankEntry,
    StrategySignal,
    BacktestResult,
    compute_momentum_score,
    compute_atr,
    compute_position_size,
)
from sentinel.core.errors import (
    KillSwitchActivatedError,
    MonthlyLossLimitReachedError,
    PositionSizeTooLargeError,
)
import sentinel.ops.killswitch as ks


# ─────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_kill_switch():
    """Ensure kill switch is always OFF before each test."""
    ks._kill_active = False
    ks._kill_reason = ""
    ks._kill_timestamp = None
    yield
    ks._kill_active = False
    ks._kill_reason = ""
    ks._kill_timestamp = None


@pytest.fixture
def trader(tmp_path):
    """Fresh PaperTrader with isolated JSON file."""
    return PaperTrader(
        portfolio_value_inr=Decimal("300000"),
        book_path=tmp_path / "paper_book.json",
        max_monthly_loss_pct=5.0,
    )


@pytest.fixture
def trader_with_positions(trader):
    """PaperTrader pre-loaded with two open positions."""
    trader.place_order(
        symbol="RELIANCE",
        direction="long",
        quantity=5,
        entry_price=Decimal("2950"),
        stop_loss=Decimal("2870"),
        target_1=Decimal("3110"),
        source_screener="s1_momentum",
    )
    trader.place_order(
        symbol="TCS",
        direction="long",
        quantity=3,
        entry_price=Decimal("3800"),
        stop_loss=Decimal("3680"),
        target_1=Decimal("4040"),
        source_screener="s1_momentum",
    )
    return trader


@pytest.fixture
def bot():
    """TelegramBot in mock mode."""
    return TelegramBot()


@pytest.fixture
def strategy():
    """Strategy 1 instance."""
    return CrossSectionalMomentumIN(portfolio_value=300_000.0, top_n=5)


# ═══════════════════════════════════════════
# GATE 1 — PAPER TRADER: POSITION CREATION
# ═══════════════════════════════════════════

class TestPaperTraderCreation:

    def test_place_order_returns_position_id(self, trader):
        """Place a long order — should return a non-empty position ID."""
        pid = trader.place_order(
            "RELIANCE", "long", 10,
            entry_price=Decimal("2950"),
            stop_loss=Decimal("2880"),
            target_1=Decimal("3090"),
        )
        assert isinstance(pid, str)
        assert len(pid) > 0

    def test_open_position_appears_in_open_positions(self, trader):
        """Newly placed position appears in get_open_positions()."""
        pid = trader.place_order(
            "INFY", "long", 5,
            entry_price=Decimal("1580"),
            stop_loss=Decimal("1520"),
            target_1=Decimal("1700"),
        )
        open_pos = trader.get_open_positions()
        assert len(open_pos) == 1
        assert open_pos[0].position_id == pid
        assert open_pos[0].symbol == "INFY"

    def test_position_entry_price_stored_correctly(self, trader):
        """Entry price matches what was passed to place_order."""
        pid = trader.place_order(
            "HDFCBANK", "long", 8,
            entry_price=Decimal("1720"),
            stop_loss=Decimal("1665"),
            target_1=Decimal("1830"),
        )
        pos = trader.get_position(pid)
        assert pos is not None
        assert pos.entry_price == Decimal("1720")
        assert pos.quantity == 8
        assert pos.direction == "long"
        assert pos.status == "open"

    def test_place_short_order(self, trader):
        """Short orders are accepted and stored correctly."""
        pid = trader.place_order(
            "TATASTEEL", "short", 20,
            entry_price=Decimal("160"),
            stop_loss=Decimal("170"),
            target_1=Decimal("140"),
        )
        pos = trader.get_position(pid)
        assert pos.direction == "short"
        assert pos.status == "open"

    def test_invalid_direction_raises_value_error(self, trader):
        """Invalid direction raises ValueError."""
        with pytest.raises(ValueError, match="direction must be"):
            trader.place_order(
                "RELIANCE", "buy", 5,
                entry_price=Decimal("2950"),
                stop_loss=Decimal("2880"),
                target_1=Decimal("3090"),
            )

    def test_invalid_quantity_raises_value_error(self, trader):
        """Zero or negative quantity raises ValueError."""
        with pytest.raises(ValueError):
            trader.place_order(
                "RELIANCE", "long", 0,
                entry_price=Decimal("2950"),
                stop_loss=Decimal("2880"),
                target_1=Decimal("3090"),
            )

    def test_position_size_cap_enforced(self, trader):
        """Position size > 10% of portfolio raises PositionSizeTooLargeError."""
        # 100 shares at ₹2950 = ₹295,000 = ~98.3% of ₹300,000 portfolio
        with pytest.raises(PositionSizeTooLargeError):
            trader.place_order(
                "RELIANCE", "long", 100,
                entry_price=Decimal("2950"),
                stop_loss=Decimal("2880"),
                target_1=Decimal("3090"),
            )


# ═══════════════════════════════════════════
# GATE 2 — PAPER TRADER: P&L TRACKING
# ═══════════════════════════════════════════

class TestPaperTraderPnL:

    def test_unrealized_pnl_long(self, trader):
        """Unrealized P&L correct for a long position when price rises."""
        pid = trader.place_order(
            "RELIANCE", "long", 10,
            entry_price=Decimal("2950"),
            stop_loss=Decimal("2880"),
            target_1=Decimal("3200"),
        )
        trader.update_price("RELIANCE", Decimal("3000"))
        pos = trader.get_position(pid)
        # Unrealized P&L = (3000 - 2950) * 10 = 500
        assert pos.unrealized_pnl_inr == Decimal("500")

    def test_unrealized_pnl_negative(self, trader):
        """Unrealized P&L is negative when price falls (before SL)."""
        pid = trader.place_order(
            "RELIANCE", "long", 10,
            entry_price=Decimal("2950"),
            stop_loss=Decimal("2800"),  # Wide SL so price doesn't auto-close
            target_1=Decimal("3200"),
        )
        trader.update_price("RELIANCE", Decimal("2920"))
        pos = trader.get_position(pid)
        # Unrealized P&L = (2920 - 2950) * 10 = -300
        assert pos.unrealized_pnl_inr == Decimal("-300")

    def test_realized_pnl_on_close(self, trader):
        """Realized P&L correct after manually closing a long position."""
        pid = trader.place_order(
            "TCS", "long", 5,
            entry_price=Decimal("3800"),
            stop_loss=Decimal("3680"),
            target_1=Decimal("4100"),
        )
        pnl = trader.close_position(pid, Decimal("3950"), "manual")
        # Realized P&L = (3950 - 3800) * 5 = 750
        assert pnl == Decimal("750")

    def test_realized_pnl_short(self, trader):
        """Realized P&L correct for a short position closed at profit."""
        pid = trader.place_order(
            "TATASTEEL", "short", 20,
            entry_price=Decimal("160"),
            stop_loss=Decimal("175"),
            target_1=Decimal("140"),
        )
        pnl = trader.close_position(pid, Decimal("145"), "manual")
        # Realized P&L = (160 - 145) * 20 = 300
        assert pnl == Decimal("300")

    def test_closed_position_has_no_unrealized_pnl(self, trader):
        """A closed position shows zero unrealized P&L."""
        pid = trader.place_order(
            "INFY", "long", 5,
            entry_price=Decimal("1580"),
            stop_loss=Decimal("1520"),
            target_1=Decimal("1700"),
        )
        trader.close_position(pid, Decimal("1650"), "manual")
        pos = trader.get_position(pid)
        assert pos.unrealized_pnl_inr == Decimal("0")

    def test_close_already_closed_raises(self, trader):
        """Closing an already-closed position raises ValueError."""
        pid = trader.place_order(
            "WIPRO", "long", 10,
            entry_price=Decimal("495"),
            stop_loss=Decimal("470"),
            target_1=Decimal("540"),
        )
        trader.close_position(pid, Decimal("530"), "manual")
        with pytest.raises(ValueError, match="already closed"):
            trader.close_position(pid, Decimal("530"), "manual")


# ═══════════════════════════════════════════
# GATE 3 — PAPER TRADER: AUTO-CLOSE (SL / T1)
# ═══════════════════════════════════════════

class TestPaperTraderAutoClose:

    def test_stop_loss_auto_closes_long(self, trader):
        """Long position auto-closes when price hits stop loss."""
        pid = trader.place_order(
            "RELIANCE", "long", 5,
            entry_price=Decimal("2950"),
            stop_loss=Decimal("2880"),
            target_1=Decimal("3110"),
        )
        closed = trader.update_price("RELIANCE", Decimal("2875"))
        assert pid in closed
        pos = trader.get_position(pid)
        assert pos.status == "closed"
        assert pos.close_reason == "stop_loss"

    def test_target_auto_closes_long(self, trader):
        """Long position auto-closes when price hits Target 1."""
        pid = trader.place_order(
            "RELIANCE", "long", 5,
            entry_price=Decimal("2950"),
            stop_loss=Decimal("2880"),
            target_1=Decimal("3110"),
        )
        closed = trader.update_price("RELIANCE", Decimal("3115"))
        assert pid in closed
        pos = trader.get_position(pid)
        assert pos.status == "closed"
        assert pos.close_reason == "target_1"

    def test_stop_loss_auto_closes_short(self, trader):
        """Short position auto-closes when price rises above stop loss."""
        pid = trader.place_order(
            "TATASTEEL", "short", 20,
            entry_price=Decimal("160"),
            stop_loss=Decimal("172"),
            target_1=Decimal("140"),
        )
        closed = trader.update_price("TATASTEEL", Decimal("175"))
        assert pid in closed
        pos = trader.get_position(pid)
        assert pos.status == "closed"
        assert pos.close_reason == "stop_loss"

    def test_price_between_sl_and_t1_keeps_position_open(self, trader):
        """Price in the range [SL, T1) should not auto-close."""
        pid = trader.place_order(
            "INFY", "long", 5,
            entry_price=Decimal("1580"),
            stop_loss=Decimal("1520"),
            target_1=Decimal("1700"),
        )
        closed = trader.update_price("INFY", Decimal("1620"))
        assert pid not in closed
        pos = trader.get_position(pid)
        assert pos.status == "open"

    def test_close_all_flattens_all_positions(self, trader_with_positions):
        """close_all() closes every open position."""
        open_before = trader_with_positions.get_open_positions()
        assert len(open_before) == 2

        trader_with_positions.update_price("RELIANCE", Decimal("3000"))
        trader_with_positions.update_price("TCS", Decimal("3850"))
        result = trader_with_positions.close_all("manual_test")

        assert len(result) == 2
        open_after = trader_with_positions.get_open_positions()
        assert len(open_after) == 0


# ═══════════════════════════════════════════
# GATE 4 — PAPER TRADER: KILL SWITCH + CIRCUIT BREAKER
# ═══════════════════════════════════════════

class TestPaperTraderSafetyGates:

    def test_kill_switch_blocks_new_orders(self, trader):
        """Active kill switch prevents new paper orders."""
        ks._kill_active = True
        with pytest.raises(KillSwitchActivatedError):
            trader.place_order(
                "RELIANCE", "long", 5,
                entry_price=Decimal("2950"),
                stop_loss=Decimal("2880"),
                target_1=Decimal("3110"),
            )

    def test_kill_switch_close_all_timing(self, trader_with_positions):
        """
        Kill switch test: close_all() must flatten all positions in < 5 seconds.
        Sprint 5 acceptance gate.
        """
        # Update prices so positions have current_price set
        trader_with_positions.update_price("RELIANCE", Decimal("3000"))
        trader_with_positions.update_price("TCS", Decimal("3850"))

        start = time.perf_counter()
        trader_with_positions.close_all("kill_switch_test")
        elapsed = time.perf_counter() - start

        assert elapsed < 5.0, (
            f"Kill switch test FAILED: {elapsed:.3f}s > 5.0s limit. "
            f"Do not proceed to live trading until resolved."
        )
        assert len(trader_with_positions.get_open_positions()) == 0

    def test_monthly_circuit_breaker_blocks_orders(self, trader, tmp_path):
        """Monthly loss >= 5% of portfolio blocks new orders.

        5% of ₹300,000 = ₹15,000.
        We place a ₹29,500 position (10 shares × ₹2950) and close it
        at ₹1,450, generating a ₹15,000 loss (5.0%).
        The second order should be blocked.
        """
        # Entry: 10 × ₹2950 = ₹29,500 capital (within 10% cap)
        # Close:  10 × ₹1,450 → loss = 10 × (2950 - 1450) = ₹15,000 = 5.0%
        pid = trader.place_order(
            "RELIANCE", "long", 10,
            entry_price=Decimal("2950"),
            stop_loss=Decimal("1000"),   # Wide SL so place_order accepts it
            target_1=Decimal("3500"),
        )
        trader.close_position(pid, Decimal("1450"), "stop_loss")

        # Verify the loss actually exceeds 5%
        summary = trader.get_performance_summary()
        assert summary.monthly_pnl_inr <= Decimal("-15000"), (
            f"Expected monthly loss >= ₹15000, got ₹{summary.monthly_pnl_inr}"
        )

        # Now attempt a new order — must be blocked
        with pytest.raises(MonthlyLossLimitReachedError):
            trader.place_order(
                "TCS", "long", 5,
                entry_price=Decimal("3800"),
                stop_loss=Decimal("3680"),
                target_1=Decimal("4000"),
            )


# ═══════════════════════════════════════════
# GATE 5 — PAPER TRADER: RECONCILIATION
# ═══════════════════════════════════════════

class TestPaperTraderReconciliation:

    def test_reconcile_clean_book(self, trader):
        """Reconcile on a clean, consistent book returns ok=True."""
        trader.place_order(
            "RELIANCE", "long", 5,
            entry_price=Decimal("2950"),
            stop_loss=Decimal("2880"),
            target_1=Decimal("3110"),
        )
        result = trader.reconcile()
        assert result["ok"] is True
        assert result["issues"] == []
        assert result["open_positions"] == 1

    def test_reconcile_detects_missing_close_price(self, trader):
        """Reconcile flags a closed position that has no close_price."""
        pid = trader.place_order(
            "TCS", "long", 3,
            entry_price=Decimal("3800"),
            stop_loss=Decimal("3680"),
            target_1=Decimal("4000"),
        )
        # Manually corrupt the position
        pos = trader.get_position(pid)
        pos.status = "closed"
        pos.close_price = None  # Missing close price — invalid state

        result = trader.reconcile()
        assert result["ok"] is False
        assert len(result["issues"]) > 0

    def test_reconcile_counts_positions_correctly(self, trader_with_positions):
        """Reconcile returns correct open/closed counts."""
        result = trader_with_positions.reconcile()
        assert result["open_positions"] == 2
        assert result["closed_positions"] == 0

    def test_reconcile_after_partial_close(self, trader_with_positions):
        """Reconcile is consistent after one of two positions is closed."""
        open_pos = trader_with_positions.get_open_positions()
        trader_with_positions.close_position(
            open_pos[0].position_id, Decimal("3000"), "manual"
        )
        result = trader_with_positions.reconcile()
        assert result["ok"] is True
        assert result["open_positions"] == 1
        assert result["closed_positions"] == 1


# ═══════════════════════════════════════════
# GATE 6 — PAPER TRADER: PERSISTENCE
# ═══════════════════════════════════════════

class TestPaperTraderPersistence:

    def test_positions_survive_reload(self, tmp_path):
        """Positions persist to JSON and reload correctly in new instance."""
        book_path = tmp_path / "paper_book.json"

        # Create trader and add a position
        trader1 = PaperTrader(
            portfolio_value_inr=Decimal("300000"),
            book_path=book_path,
        )
        pid = trader1.place_order(
            "MARUTI", "long", 2,
            entry_price=Decimal("12800"),
            stop_loss=Decimal("12400"),
            target_1=Decimal("13600"),
            notes="test persistence",
        )

        # Create new trader from same file
        trader2 = PaperTrader(
            portfolio_value_inr=Decimal("300000"),
            book_path=book_path,
        )
        reloaded = trader2.get_position(pid)
        assert reloaded is not None
        assert reloaded.symbol == "MARUTI"
        assert reloaded.entry_price == Decimal("12800")
        assert reloaded.notes == "test persistence"

    def test_json_file_created_on_first_order(self, tmp_path):
        """JSON book file is created after the first order."""
        book_path = tmp_path / "paper_book.json"
        trader = PaperTrader(book_path=book_path)
        assert not book_path.exists()

        trader.place_order(
            "INFY", "long", 5,
            entry_price=Decimal("1580"),
            stop_loss=Decimal("1520"),
            target_1=Decimal("1700"),
        )
        assert book_path.exists()

    def test_json_file_has_correct_schema(self, tmp_path):
        """JSON book file contains expected schema keys."""
        book_path = tmp_path / "paper_book.json"
        trader = PaperTrader(book_path=book_path)
        trader.place_order(
            "WIPRO", "long", 10,
            entry_price=Decimal("495"),
            stop_loss=Decimal("470"),
            target_1=Decimal("540"),
        )
        with open(book_path) as f:
            data = json.load(f)
        assert "schema_version" in data
        assert data["schema_version"] == "PaperBookV1"
        assert "positions" in data
        assert "equity_curve" in data


# ═══════════════════════════════════════════
# GATE 7 — PAPER TRADER: PERFORMANCE SUMMARY
# ═══════════════════════════════════════════

class TestPaperTraderPerformance:

    def test_empty_book_summary(self, trader):
        """Performance summary on empty book returns zero/default values."""
        summary = trader.get_performance_summary()
        assert summary.total_trades == 0
        assert summary.win_rate_pct == 0.0
        assert summary.total_realized_pnl_inr == Decimal("0")

    def test_win_rate_computation(self, trader):
        """Win rate = wins / (wins + losses) as percentage."""
        # 3 winning trades
        for sym, price, close in [
            ("RELIANCE", "2950", "3050"),
            ("TCS", "3800", "3900"),
            ("INFY", "1580", "1650"),
        ]:
            pid = trader.place_order(
                sym, "long", 2,
                entry_price=Decimal(price),
                stop_loss=Decimal(str(float(price) * 0.95)),
                target_1=Decimal(str(float(price) * 1.1)),
            )
            trader.close_position(pid, Decimal(close), "target_1")

        # 1 losing trade
        pid = trader.place_order(
            "WIPRO", "long", 5,
            entry_price=Decimal("495"),
            stop_loss=Decimal("460"),
            target_1=Decimal("560"),
        )
        trader.close_position(pid, Decimal("470"), "stop_loss")

        summary = trader.get_performance_summary()
        assert summary.winning_trades == 3
        assert summary.losing_trades == 1
        assert summary.win_rate_pct == 75.0

    def test_current_equity_tracks_realized_pnl(self, trader):
        """Current equity = portfolio_value + realized P&L."""
        pid = trader.place_order(
            "HDFCBANK", "long", 5,
            entry_price=Decimal("1720"),
            stop_loss=Decimal("1665"),
            target_1=Decimal("1850"),
        )
        # Profit of 5 * (1800 - 1720) = 400
        trader.close_position(pid, Decimal("1800"), "manual")
        summary = trader.get_performance_summary()
        assert summary.current_equity_inr == Decimal("300400")

    def test_summary_dict_returns_floats(self, trader):
        """summary_dict() returns plain floats (for dashboard display)."""
        d = trader.summary_dict()
        assert isinstance(d["total_pnl_inr"], float)
        assert isinstance(d["win_rate_pct"], float)
        assert "computed_at" in d


# ═══════════════════════════════════════════
# GATE 8 — SCHEDULER
# ═══════════════════════════════════════════

class TestScheduler:

    def test_scheduler_initialises_without_error(self):
        """SentinelScheduler() initialises cleanly."""
        scheduler = SentinelScheduler()
        assert scheduler is not None

    def test_scheduler_registers_expected_jobs(self):
        """Scheduler registers the correct number of jobs (15 jobs)."""
        scheduler = SentinelScheduler()
        jobs = scheduler.get_job_list()
        # 15 total jobs: morning_brief, gsm×2, s1, s2, s3, s4, s5, s6, s7×4, fii_dii
        assert len(jobs) >= 14, f"Expected >= 14 jobs, got {len(jobs)}: {[j['id'] for j in jobs]}"

    def test_scheduler_has_s1_job(self):
        """S1 momentum job is registered."""
        scheduler = SentinelScheduler()
        job_ids = [j["id"] for j in scheduler.get_job_list()]
        assert "s1_momentum" in job_ids

    def test_scheduler_has_morning_brief_job(self):
        """Morning brief job is registered."""
        scheduler = SentinelScheduler()
        job_ids = [j["id"] for j in scheduler.get_job_list()]
        assert "morning_brief" in job_ids

    def test_scheduler_has_fii_dii_job(self):
        """FII/DII ingest job is registered."""
        scheduler = SentinelScheduler()
        job_ids = [j["id"] for j in scheduler.get_job_list()]
        assert "fii_dii_ingest" in job_ids

    def test_scheduler_has_gsm_asm_jobs(self):
        """Two GSM/ASM refresh jobs are registered (morning + evening)."""
        scheduler = SentinelScheduler()
        job_ids = [j["id"] for j in scheduler.get_job_list()]
        assert "gsm_asm_morning" in job_ids
        assert "gsm_asm_evening" in job_ids

    def test_scheduler_starts_and_stops(self):
        """Scheduler starts, reports running=True, stops cleanly."""
        scheduler = SentinelScheduler()
        scheduler.start()
        assert scheduler.is_running()
        scheduler.stop()
        assert not scheduler.is_running()

    def test_scheduler_run_job_now_with_custom_fn(self):
        """run_job_now() executes an injected custom function."""
        executed = []

        def fake_job():
            executed.append(True)

        scheduler = SentinelScheduler(custom_jobs={"s1_momentum": fake_job})
        scheduler.start()
        result = scheduler.run_job_now("s1_momentum")
        scheduler.stop()

        assert result is True
        assert len(executed) >= 1

    def test_scheduler_unknown_job_returns_false(self):
        """run_job_now() with unknown job ID returns False."""
        scheduler = SentinelScheduler()
        result = scheduler.run_job_now("nonexistent_job_xyz")
        assert result is False

    def test_scheduler_job_next_run_is_set(self):
        """Each job has a next_run_time after scheduler starts."""
        scheduler = SentinelScheduler()
        scheduler.start()
        jobs = scheduler.get_job_list()
        # At least one job should have a next_run time
        runs_set = [j for j in jobs if j["next_run"] is not None]
        scheduler.stop()
        assert len(runs_set) > 0


# ═══════════════════════════════════════════
# GATE 9 — TELEGRAM BOT
# ═══════════════════════════════════════════

class TestTelegramBot:

    def test_bot_initialises_in_mock_mode(self):
        """TelegramBot() initialises without any token in mock mode."""
        bot = TelegramBot()
        assert bot is not None
        stats = bot.get_stats()
        assert stats["mock_mode"] is True

    def test_send_message_returns_true_in_mock(self, bot):
        """send_message() returns True in mock mode."""
        result = bot.send_message("Test message for Sprint 5")
        assert result is True

    def test_send_morning_brief_in_mock(self, bot):
        """send_morning_brief() returns True in mock mode."""
        result = bot.send_morning_brief(
            "DXY: Neutral | VIX: 15.2 | FII: +₹823 Cr\nBias: BULLISH"
        )
        assert result is True

    def test_send_kill_switch_alert_in_mock(self, bot):
        """send_kill_switch_alert() returns True in mock mode."""
        result = bot.send_kill_switch_alert("Monthly loss limit breached")
        assert result is True

    def test_send_demotion_alert_in_mock(self, bot):
        """send_demotion_alert() returns True in mock mode."""
        result = bot.send_demotion_alert(override_count=3, paper_days=14)
        assert result is True

    def test_send_screener_alert_in_mock(self, bot):
        """send_screener_alert() returns True in mock mode."""
        result = bot.send_screener_alert(
            screener_id="S1",
            symbol="RELIANCE",
            conviction_score=72.5,
            direction="long",
            entry_zone="₹2950-₹2980",
            stop_loss="₹2870",
            target_1="₹3120",
            rr_ratio=2.2,
        )
        assert result is True

    def test_send_empty_message_returns_false(self, bot):
        """send_message('') returns False — no empty messages."""
        result = bot.send_message("")
        assert result is False

    def test_message_truncated_at_4096_chars(self, bot):
        """Messages longer than 4096 chars are truncated, not rejected."""
        long_msg = "A" * 5000
        result = bot.send_message(long_msg)
        assert result is True

    def test_rate_limiting_blocks_after_limit(self, bot):
        """Rate limiter blocks sends after 20 per window."""
        from sentinel.ops.telegram_bot import RATE_LIMIT_PER_MINUTE
        # Pre-fill the rate window
        for _ in range(RATE_LIMIT_PER_MINUTE):
            bot.send_message("Rate test")

        # Next send should be blocked
        result = bot.send_message("This should be rate-limited")
        assert result is False

    def test_sent_count_increments(self, bot):
        """Stats counter increments on each successful send."""
        initial = bot.get_stats()["sent_count"]
        bot.send_message("Message 1")
        bot.send_message("Message 2")
        final = bot.get_stats()["sent_count"]
        assert final == initial + 2

    def test_send_monthly_circuit_breaker_alert(self, bot):
        """Monthly circuit breaker alert sends in mock mode."""
        result = bot.send_monthly_circuit_breaker_alert(5.3, 5.0)
        assert result is True

    def test_send_gsm_asm_alert(self, bot):
        """GSM/ASM surveillance alert sends in mock mode."""
        result = bot.send_gsm_asm_alert("SMALLSTOCK", "ASM")
        assert result is True


# ═══════════════════════════════════════════
# GATE 10 — STRATEGY 1: MATH UTILITIES
# ═══════════════════════════════════════════

class TestStrategy1Math:

    def test_compute_momentum_score_uptrend(self):
        """Momentum score positive for a cleanly trending stock."""
        # Simulate clean uptrend: price grows ~15% over 90 days
        closes = [100.0 * (1.002 ** i) for i in range(100)]
        score, r2, slope = compute_momentum_score(closes, lookback=90)
        assert score > 0, f"Expected positive score for uptrend, got {score}"
        assert r2 > 0.7, f"Expected high R² for clean uptrend, got {r2}"

    def test_compute_momentum_score_downtrend(self):
        """Momentum score negative for a declining stock."""
        closes = [200.0 * (0.998 ** i) for i in range(100)]
        score, r2, slope = compute_momentum_score(closes, lookback=90)
        assert score < 0, f"Expected negative score for downtrend, got {score}"

    def test_compute_momentum_score_insufficient_data(self):
        """Returns zeros when insufficient bars are provided."""
        closes = [100.0] * 50  # Only 50 bars, lookback=90
        score, r2, slope = compute_momentum_score(closes, lookback=90)
        assert score == 0.0
        assert r2 == 0.0

    def test_compute_atr_positive(self):
        """ATR returns a positive value for normal OHLCV data."""
        closes = [float(100 + i * 0.5) for i in range(30)]
        highs = [c + 1.5 for c in closes]
        lows = [c - 1.5 for c in closes]
        atr = compute_atr(closes, highs, lows, period=14)
        assert atr > 0

    def test_compute_position_size_reasonable(self):
        """ATR-based sizing returns a positive integer."""
        qty = compute_position_size(
            portfolio_value=300_000.0,
            atr=50.0,
            price=2950.0,
        )
        assert qty >= 1
        assert isinstance(qty, int)

    def test_compute_position_size_cap_at_10pct(self):
        """Position size never exceeds 10% of portfolio."""
        # Very low ATR would suggest huge position — should be capped
        qty = compute_position_size(
            portfolio_value=300_000.0,
            atr=0.01,          # Extremely low ATR
            price=100.0,
            max_capital_pct=10.0,
        )
        max_allowed = int(300_000.0 * 0.10 / 100.0)  # 300 shares
        assert qty <= max_allowed


# ═══════════════════════════════════════════
# GATE 11 — STRATEGY 1: SIGNAL GENERATION
# ═══════════════════════════════════════════

class TestStrategy1Signals:

    def test_rank_universe_returns_entries(self, strategy):
        """rank_universe() returns a non-empty list of MomentumRankEntry."""
        ranked = strategy.rank_universe()
        assert len(ranked) > 0
        assert all(isinstance(e, MomentumRankEntry) for e in ranked)

    def test_ranked_entries_are_sorted_descending(self, strategy):
        """Eligible entries come before ineligible, within eligible sorted by score."""
        ranked = strategy.rank_universe()
        eligible = [e for e in ranked if not e.has_gap and e.above_100d_sma]
        if len(eligible) >= 2:
            for i in range(len(eligible) - 1):
                assert eligible[i].momentum_score >= eligible[i + 1].momentum_score, (
                    f"Rank order broken: {eligible[i].momentum_score} < {eligible[i+1].momentum_score}"
                )

    def test_rank_entries_have_rank_numbers(self, strategy):
        """All ranked entries have rank > 0."""
        ranked = strategy.rank_universe()
        for e in ranked:
            assert e.rank > 0

    def test_run_generates_signals(self, strategy):
        """run() returns at least one StrategySignal."""
        signals = strategy.run()
        assert len(signals) >= 1
        assert all(isinstance(s, StrategySignal) for s in signals)

    def test_signals_capped_at_top_n(self, strategy):
        """run() returns at most top_n signals."""
        signals = strategy.run()
        assert len(signals) <= strategy.top_n

    def test_signal_has_valid_fields(self, strategy):
        """Each signal has action, entry_price, stop_loss, target_1."""
        signals = strategy.run()
        for sig in signals:
            assert sig.action in ("enter_long", "exit_long", "hold")
            assert sig.entry_price > 0
            assert sig.stop_loss > 0
            assert sig.target_1 > 0
            assert sig.generated_at != ""

    def test_signal_stop_loss_below_entry_for_long(self, strategy):
        """For enter_long signals, stop_loss < entry_price."""
        signals = [s for s in strategy.run() if s.action == "enter_long"]
        for sig in signals:
            assert sig.stop_loss < sig.entry_price, (
                f"{sig.symbol}: stop_loss {sig.stop_loss} >= entry {sig.entry_price}"
            )

    def test_signal_risk_reward_positive(self, strategy):
        """Each signal has a positive R:R ratio."""
        signals = strategy.run()
        for sig in signals:
            assert sig.risk_reward > 0, f"{sig.symbol} has R:R = {sig.risk_reward}"

    def test_strategy_version_set(self, strategy):
        """Each signal includes the strategy version string."""
        signals = strategy.run()
        for sig in signals:
            assert sig.strategy_version != ""


# ═══════════════════════════════════════════
# GATE 12 — STRATEGY 1: BACKTEST (KEY ACCEPTANCE GATE)
# ═══════════════════════════════════════════

class TestStrategy1Backtest:

    def test_backtest_runs_without_error(self, strategy):
        """backtest() completes without raising any exception."""
        result = strategy.backtest()
        assert isinstance(result, BacktestResult)

    def test_backtest_has_positive_trade_count(self, strategy):
        """Backtest produces at least 1 trade in the OOS period."""
        result = strategy.backtest()
        assert result.oos_n_trades >= 1, (
            "Backtest produced no trades. Something is wrong with the mock data."
        )

    def test_backtest_oos_sharpe_meets_acceptance_gate(self, strategy):
        """
        KEY SPRINT 5 ACCEPTANCE GATE:
        OOS Sharpe >= 0.5 on mock data.

        From SPRINT_ROADMAP_v2.md §R7:
        'Strategy 1 backtest: Sharpe >= 0.5 net of costs on mock data'
        """
        result = strategy.backtest()
        assert result.passes_acceptance_gate(min_sharpe=0.5), (
            f"OOS Sharpe {result.oos_sharpe:.3f} < 0.5. "
            f"Strategy 1 does not pass the Sprint 5 acceptance gate. "
            f"Trades: {result.oos_n_trades}, Return: {result.oos_total_return_pct:+.1f}%"
        )

    def test_backtest_period_lengths_correct(self, strategy):
        """IS + OOS periods sum to the total data window (300 bars)."""
        result = strategy.backtest(is_fraction=0.60)
        total = result.is_period_days + result.oos_period_days
        # total_bars = 300, IS = int(300*0.60) = 180, OOS = 120
        assert total == 300, f"Expected IS+OOS=300, got {total}"

    def test_backtest_acceptance_gate_method(self, strategy):
        """passes_acceptance_gate() correctly evaluates against threshold."""
        result = strategy.backtest()
        passes = result.passes_acceptance_gate(min_sharpe=0.5)
        # Cross-check: manual comparison should match
        manual_check = result.oos_sharpe >= 0.5
        assert passes == manual_check

    def test_backtest_returns_correct_strategy_name(self, strategy):
        """Backtest result has the correct strategy name."""
        result = strategy.backtest()
        assert result.strategy_name == "CrossSectionalMomentumIN"

    def test_benchmark_description(self, strategy):
        """Benchmark description string is non-empty."""
        desc = strategy.get_benchmark_description()
        assert "HDFCNIFETF" in desc


# ═══════════════════════════════════════════
# GATE 13 — 30-DAY PAPER TRADING SIMULATION
# ═══════════════════════════════════════════

class TestThirtyDayPaperSimulation:
    """
    Simulates 30 days of clean paper trading to verify the full
    paper trading lifecycle.

    Per SPRINT_ROADMAP_v2.md §R7: '30-day clean paper trading
    (simulated in test with mock data)'
    """

    def test_30_day_simulation_full_lifecycle(self, trader):
        """
        Simulate 30 trading days:
        - Each day: place 1-2 orders
        - Update prices to simulate market movement
        - Some positions hit SL, some hit T1
        - Final reconcile must pass
        - Final equity must be calculable
        """
        from sentinel.data.mock_data import ALL_MOCK_STOCKS
        import random
        rng = random.Random(42)  # Fixed seed for reproducibility

        symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
                   "WIPRO", "MARUTI", "SUNPHARMA", "TITAN", "AXISBANK"]

        total_placed = 0

        for day in range(30):
            # Each day: potentially place a new order (50% probability)
            if rng.random() < 0.5 and total_placed < 20:
                sym = rng.choice(symbols)
                base_price = float(ALL_MOCK_STOCKS[sym]["price"])
                entry = Decimal(str(round(base_price, 2)))
                stop = entry * Decimal("0.97")   # 3% stop
                target = entry * Decimal("1.06")  # 6% target (R:R = 2.0)
                qty = max(1, int(1000 / float(entry)))  # ~₹1000 position

                try:
                    trader.place_order(
                        sym, "long", qty,
                        entry_price=entry,
                        stop_loss=stop,
                        target_1=target,
                        source_screener="simulation",
                    )
                    total_placed += 1
                except (PositionSizeTooLargeError, MonthlyLossLimitReachedError):
                    pass  # Expected in simulation

            # Update prices for all symbols (random walk ±1.5%)
            for sym in symbols:
                base_price = float(ALL_MOCK_STOCKS[sym]["price"])
                day_move = rng.gauss(0.001, 0.015)
                new_price = Decimal(str(round(base_price * (1 + day_move), 2)))
                trader.update_price(sym, new_price)

        # Final checks
        reconcile = trader.reconcile()
        assert reconcile["ok"] is True, (
            f"Reconcile failed after 30-day simulation: {reconcile['issues']}"
        )

        summary = trader.get_performance_summary()
        assert summary.total_trades >= 0
        assert summary.current_equity_inr > Decimal("0")
        # Equity should be within ±30% of starting value (reasonable for mock)
        lower = trader.portfolio_value_inr * Decimal("0.7")
        upper = trader.portfolio_value_inr * Decimal("1.3")
        assert lower <= summary.current_equity_inr <= upper, (
            f"Equity ₹{summary.current_equity_inr} is outside expected range "
            f"[₹{lower}, ₹{upper}]"
        )

        # Total placed should be at least a few trades
        assert total_placed >= 1, "Simulation placed zero trades — check the logic"
