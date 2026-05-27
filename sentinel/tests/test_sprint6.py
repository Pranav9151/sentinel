"""
sentinel/tests/test_sprint6.py
================================
Sprint 6 Acceptance Gate Tests — 44 tests.

pytest sentinel/tests/test_sprint6.py -v

Gates (SPRINT_ROADMAP_v2.md §R8.3, §R8.5):
  StageManager    — initial state, persistence, trade checks, auto-triggers
  Stage transitions — demotion, promotion rules, sequence enforcement
  ThreeOverrideTracker — warning, demotion, status
  LiveOrderRouter — mock execution, kill switch, stage rejection, audit trail
  Reporting       — post-market, weekly, monthly — generation + formatting
  Integration     — full clean trade and losing-trade workflows
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from decimal import Decimal

import pytest

os.environ["MOCK_MODE"] = "true"

# ── imports ───────────────────────────────────────────────────────────────────
from sentinel.live.stage_manager import (
    StageManager, Stage, STAGE_CONFIGS,
)
from sentinel.live.live_order_router import LiveOrderRouter
from sentinel.ops.three_override_tracker import ThreeOverrideTracker
from sentinel.reporting.daily_postmarket import DailyPostMarketReport
from sentinel.reporting.weekly_review import WeeklyReviewReport
from sentinel.reporting.monthly_letter import MonthlyLetter
from sentinel.ops.paper_trader import PaperTrader
import sentinel.ops.killswitch as ks
import sentinel.live.live_order_router as live_order_router
from sentinel.core.types import utc_now


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_kill_switch():
    ks._kill_active = False
    ks._kill_reason = ""
    ks._kill_timestamp = None
    yield
    ks._kill_active = False
    ks._kill_reason = ""
    ks._kill_timestamp = None


@pytest.fixture
def sm(tmp_path):
    return StageManager(
        total_portfolio_inr=Decimal("300000"),
        state_path=tmp_path / "state.json",
    )


@pytest.fixture
def pt(tmp_path):
    return PaperTrader(
        portfolio_value_inr=Decimal("300000"),
        book_path=tmp_path / "paper.json",
    )


@pytest.fixture
def router(tmp_path):
    _sm = StageManager(
        total_portfolio_inr=Decimal("300000"),
        state_path=tmp_path / "state.json",
    )
    _pt = PaperTrader(
        portfolio_value_inr=Decimal("300000"),
        book_path=tmp_path / "paper.json",
    )
    return LiveOrderRouter(stage_manager=_sm, paper_trader=_pt)


# ═══════════════════════════════════════════
# 1 — STAGE MANAGER: INITIAL STATE
# ═══════════════════════════════════════════

class TestStageManagerInit:

    def test_starts_in_toe_dip(self, sm):
        assert sm.stage == Stage.TOE_DIP

    def test_toe_dip_capital_10pct(self, sm):
        # 10% × ₹300,000 = ₹30,000
        assert sm.allocated_capital_inr == Decimal("30000")

    def test_toe_dip_max_positions_1(self, sm):
        assert sm.config.max_open_positions == 1

    def test_toe_dip_max_risk_150(self, sm):
        # 0.5% × ₹30,000 = ₹150
        assert sm.max_risk_per_trade_inr == Decimal("150")

    def test_toe_dip_max_single_loss_900(self, sm):
        # 3% × ₹30,000 = ₹900
        assert sm.max_single_loss_inr == Decimal("900")

    def test_toe_dip_max_daily_dd_600(self, sm):
        # 2% × ₹30,000 = ₹600
        assert sm.max_daily_dd_inr == Decimal("600")

    def test_all_four_stages_have_configs(self):
        for s in Stage:
            assert s in STAGE_CONFIGS
            cfg = STAGE_CONFIGS[s]
            assert cfg.max_open_positions >= 1
            assert cfg.capital_pct > 0

    def test_get_status_has_required_keys(self, sm):
        st = sm.get_status()
        for key in ("stage", "allocated_capital_inr", "max_risk_per_trade_inr",
                    "days_in_stage", "open_positions_count", "total_live_trades"):
            assert key in st
        assert st["stage"] == Stage.TOE_DIP.value

    def test_pilot_config(self):
        cfg = STAGE_CONFIGS[Stage.PILOT]
        assert cfg.capital_pct == 25.0
        assert cfg.max_open_positions == 3

    def test_production_config_full_allocation(self):
        cfg = STAGE_CONFIGS[Stage.PRODUCTION]
        assert cfg.capital_pct == 100.0


# ═══════════════════════════════════════════
# 2 — PERSISTENCE
# ═══════════════════════════════════════════

class TestStageManagerPersistence:

    def test_state_survives_reload(self, tmp_path):
        path = tmp_path / "state.json"
        sm1 = StageManager(state_path=path)
        sm1.demote_to_quarantine("persistence test")
        sm2 = StageManager(state_path=path)
        assert sm2.stage == Stage.QUARANTINE
        assert "persistence test" in sm2._state.demotion_reason

    def test_json_written_after_demotion(self, tmp_path):
        path = tmp_path / "state.json"
        sm = StageManager(state_path=path)
        sm.demote_to_quarantine("write test")
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        assert data["current_stage"] == Stage.QUARANTINE.value


# ═══════════════════════════════════════════
# 3 — TRADE CHECKS
# ═══════════════════════════════════════════

class TestStageManagerChecks:

    def test_passes_valid_order(self, sm):
        r = sm.check_can_trade(Decimal("100"), open_positions_count=0)
        assert r["allowed"] is True

    def test_blocks_excessive_risk(self, sm):
        # ₹200 > ₹150 TOE_DIP limit
        r = sm.check_can_trade(Decimal("200"), open_positions_count=0)
        assert r["allowed"] is False
        assert "risk" in r["reason"].lower() or "stage limit" in r["reason"].lower()

    def test_blocks_at_max_positions(self, sm):
        # TOE_DIP max=1; passing 1 means already full
        r = sm.check_can_trade(Decimal("50"), open_positions_count=1)
        assert r["allowed"] is False
        assert "position" in r["reason"].lower()

    def test_blocks_when_day_halted(self, sm):
        sm._state.day_halted = True
        sm._state.day_halt_reason = "DD exceeded"
        sm._state.day_halt_date = sm._state.daily_pnl_date
        r = sm.check_can_trade(Decimal("50"), open_positions_count=0)
        assert r["allowed"] is False
        assert "halted" in r["reason"].lower()

    def test_blocks_when_review_gate_active(self, sm):
        sm._state.next_trade_gated = True
        sm._state.next_trade_gate_reason = "Review required"
        r = sm.check_can_trade(Decimal("50"), open_positions_count=0)
        assert r["allowed"] is False
        assert "review" in r["reason"].lower()

    def test_clear_gate_re_enables_trading(self, sm):
        sm._state.next_trade_gated = True
        sm.clear_review_gate("reviewed")
        r = sm.check_can_trade(Decimal("50"), open_positions_count=0)
        assert r["allowed"] is True


# ═══════════════════════════════════════════
# 4 — AUTO-TRIGGERS
# ═══════════════════════════════════════════

class TestStageManagerAutoTriggers:

    def test_large_loss_sets_review_gate(self, sm):
        # ₹901 > ₹900 threshold
        sm.record_trade_closed(Decimal("-901"))
        assert sm._state.next_trade_gated is True

    def test_small_loss_does_not_set_gate(self, sm):
        sm.record_trade_closed(Decimal("-800"))
        assert sm._state.next_trade_gated is False

    def test_daily_dd_breach_halts_day(self, sm):
        sm.record_trade_closed(Decimal("-350"))
        sm.record_trade_closed(Decimal("-300"))  # total -650 > -600 limit
        assert sm._state.day_halted is True

    def test_day_halt_blocks_trading(self, sm):
        sm.record_trade_closed(Decimal("-700"))
        r = sm.check_can_trade(Decimal("50"), open_positions_count=0)
        assert r["allowed"] is False

    def test_trade_opened_increments_count(self, sm):
        sm.record_trade_opened()
        assert sm._state.open_positions_count == 1

    def test_trade_closed_decrements_count(self, sm):
        sm.record_trade_opened()
        sm.record_trade_opened()
        sm.record_trade_closed(Decimal("100"))
        assert sm._state.open_positions_count == 1


# ═══════════════════════════════════════════
# 5 — STAGE TRANSITIONS
# ═══════════════════════════════════════════

class TestStageTransitions:

    def test_demote_to_quarantine(self, sm):
        sm.demote_to_quarantine("test")
        assert sm.stage == Stage.QUARANTINE
        assert "test" in sm._state.demotion_reason

    def test_promote_blocked_before_min_days(self, sm):
        # TOE_DIP needs 30 days; we're at day 0
        r = sm.promote(Stage.PILOT, "abc123")
        assert r["allowed"] is False
        assert "30" in r["reason"] or "Insufficient" in r["reason"]

    def test_promote_requires_signoff(self, sm):
        sm._state.entered_at = (utc_now() - timedelta(days=31)).isoformat()
        r = sm.promote(Stage.PILOT, "")
        assert r["allowed"] is False
        assert "signoff" in r["reason"].lower() or "commit" in r["reason"].lower()

    def test_cannot_skip_to_production(self, sm):
        r = sm.promote(Stage.PRODUCTION, "abc123")
        assert r["allowed"] is False

    def test_quarantine_to_toe_dip_after_14_days(self, sm):
        sm.demote_to_quarantine("test demotion")
        sm._state.entered_at = (utc_now() - timedelta(days=15)).isoformat()
        r = sm.promote(Stage.TOE_DIP, "resume123")
        assert r["allowed"] is True
        assert sm.stage == Stage.TOE_DIP

    def test_toe_dip_to_pilot_after_30_days(self, sm):
        sm._state.entered_at = (utc_now() - timedelta(days=31)).isoformat()
        r = sm.promote(Stage.PILOT, "pilot_signoff")
        assert r["allowed"] is True
        assert sm.stage == Stage.PILOT


# ═══════════════════════════════════════════
# 6 — THREE-OVERRIDE TRACKER
# ═══════════════════════════════════════════

class TestThreeOverrideTracker:

    def test_initialises_without_error(self, sm):
        t = ThreeOverrideTracker(stage_manager=sm)
        assert t is not None

    def test_check_returns_required_keys(self, sm):
        t = ThreeOverrideTracker(stage_manager=sm)
        r = t.check_and_demote()
        for key in ("override_count", "threshold", "demoted", "warning", "message"):
            assert key in r

    def test_no_demotion_with_zero_overrides(self, sm, monkeypatch):
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_override_count_rolling", lambda d: 0
        )
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_recent_overrides", lambda d: []
        )
        t = ThreeOverrideTracker(stage_manager=sm)
        r = t.check_and_demote()
        assert r["demoted"] is False
        assert sm.stage == Stage.TOE_DIP

    def test_warning_at_threshold_minus_1(self, sm, monkeypatch):
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_override_count_rolling", lambda d: 2
        )
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_recent_overrides", lambda d: []
        )
        t = ThreeOverrideTracker(stage_manager=sm)
        r = t.check_and_demote()
        assert r["warning"] is True
        assert r["demoted"] is False

    def test_demotes_at_threshold(self, sm, monkeypatch):
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_override_count_rolling", lambda d: 3
        )
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_recent_overrides",
            lambda d: [{"guardrail": "test", "timestamp": "2026-01-01T00:00:00+00:00"}],
        )
        t = ThreeOverrideTracker(stage_manager=sm)
        r = t.check_and_demote()
        assert r["demoted"] is True
        assert sm.stage == Stage.QUARANTINE

    def test_no_double_demotion_if_already_quarantine(self, sm, monkeypatch):
        sm.demote_to_quarantine("first demotion")
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_override_count_rolling", lambda d: 5
        )
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_recent_overrides", lambda d: []
        )
        t = ThreeOverrideTracker(stage_manager=sm)
        r = t.check_and_demote()
        # Still QUARANTINE but demoted=False (was already there)
        assert sm.stage == Stage.QUARANTINE
        assert r["demoted"] is False

    def test_status_has_required_keys(self, sm):
        t = ThreeOverrideTracker(stage_manager=sm)
        st = t.get_status()
        for key in ("override_count_30d", "threshold", "at_threshold", "warning"):
            assert key in st


# ═══════════════════════════════════════════
# 7 — LIVE ORDER ROUTER
# ═══════════════════════════════════════════

class TestLiveOrderRouter:

    def test_initialises_in_mock_mode(self, router):
        assert router is not None

    def test_valid_order_executes_in_paper(self, router):
        # Risk = (2950 - 2910) × 1 = ₹40 < ₹150 TOE_DIP limit
        r = router.place_order(
            "RELIANCE", "long", 1,
            Decimal("2950"), Decimal("2910"), Decimal("3030"),
        )
        assert r.approved is True
        assert r.executed is True
        assert r.execution_mode == "paper"
        assert r.paper_position_id != ""

    def test_kill_switch_blocks_order(self, router):
        ks._kill_active = True
        r = router.place_order(
            "RELIANCE", "long", 1,
            Decimal("2950"), Decimal("2910"), Decimal("3030"),
        )
        assert r.executed is False
        assert "kill switch" in r.rejection_reason.lower()

    def test_excessive_risk_rejected(self, router):
        # Risk = (2950 - 2650) × 1 = ₹300 > ₹150 limit
        r = router.place_order(
            "TCS", "long", 1,
            Decimal("2950"), Decimal("2650"), Decimal("3250"),
        )
        assert r.approved is False
        assert r.executed is False

    def test_second_order_rejected_max_positions(self, router):
        # First order — within limits
        r1 = router.place_order(
            "RELIANCE", "long", 1,
            Decimal("2950"), Decimal("2910"), Decimal("3030"),
        )
        assert r1.executed is True
        # Second order — TOE_DIP max=1
        r2 = router.place_order(
            "TCS", "long", 1,
            Decimal("3800"), Decimal("3765"), Decimal("3870"),
        )
        assert r2.approved is False
        assert "position" in r2.rejection_reason.lower()

    def test_order_result_has_full_audit_trail(self, router):
        r = router.place_order(
            "INFY", "long", 1,
            Decimal("1580"), Decimal("1555"), Decimal("1630"),
        )
        assert r.order_id != ""
        assert r.stage_at_order == Stage.TOE_DIP.value
        assert r.attempted_at != ""
        assert isinstance(r.stage_checks, dict)
        assert r.proposed_risk_inr > Decimal("0")

    def test_record_close_flows_pnl_to_stage(self, router):
        r = router.place_order(
            "WIPRO", "long", 1,
            Decimal("495"), Decimal("475"), Decimal("535"),
        )
        assert r.executed is True
        actions = router.record_close(r.paper_position_id, Decimal("520"), "manual")
        assert "actions_triggered" in actions

    def test_stage_manager_accessible(self, router):
        assert router.stage_manager is not None
        assert router.stage_manager.stage == Stage.TOE_DIP

    def test_paper_trader_accessible(self, router):
        assert router.paper_trader is not None

    def test_invalid_direction_is_rejected_before_stage_or_broker(self, router):
        r = router.place_order(
            "RELIANCE", "sideways", 1,
            Decimal("2950"), Decimal("2910"), Decimal("3030"),
        )

        assert r.approved is False
        assert r.executed is False
        assert "direction must be long or short" in r.rejection_reason

    def test_invalid_quantity_is_rejected_before_stage_or_broker(self, router):
        r = router.place_order(
            "RELIANCE", "long", 0,
            Decimal("2950"), Decimal("2910"), Decimal("3030"),
        )

        assert r.approved is False
        assert r.executed is False
        assert "quantity" in r.rejection_reason

    def test_invalid_long_price_geometry_is_rejected(self, router):
        r = router.place_order(
            "RELIANCE", "long", 1,
            Decimal("2950"), Decimal("2960"), Decimal("3030"),
        )

        assert r.approved is False
        assert r.executed is False
        assert "long stop_loss must be below entry_price" in r.rejection_reason

    def test_live_mode_blocks_when_deployment_readiness_has_blockers(self, router, monkeypatch):
        class Blocker:
            name = "Operator sign-off"
            detail = "missing sign-off"

        monkeypatch.setattr(live_order_router, "MOCK_MODE", False)
        monkeypatch.setattr(
            live_order_router.LiveOrderRouter,
            "_live_readiness_blocks",
            staticmethod(lambda: [f"{Blocker.name}: {Blocker.detail}"]),
        )

        r = router.place_order(
            "RELIANCE", "long", 1,
            Decimal("2950"), Decimal("2910"), Decimal("3030"),
        )

        assert r.approved is False
        assert r.executed is False
        assert r.execution_mode == "blocked"
        assert "Live readiness blocked" in r.rejection_reason


# ═══════════════════════════════════════════
# 8 — REPORTING
# ═══════════════════════════════════════════

class TestReporting:

    def test_daily_postmarket_generates(self, pt, sm):
        r = DailyPostMarketReport(paper_trader=pt, stage_manager=sm)
        result = r.generate()
        assert result["report_type"] == "daily_postmarket"
        assert "pnl" in result
        assert "stage" in result
        assert "slippage" in result

    def test_daily_postmarket_telegram_format(self, pt, sm):
        rg = DailyPostMarketReport(paper_trader=pt, stage_manager=sm)
        result = rg.generate()
        text = rg.format_telegram(result)
        assert isinstance(text, str)
        assert len(text) > 10
        assert "Post-Market" in text

    def test_daily_postmarket_without_dependencies(self):
        r = DailyPostMarketReport()
        result = r.generate()
        assert result["report_type"] == "daily_postmarket"
        assert "note" in result["pnl"]

    def test_weekly_review_generates(self, pt, sm):
        rg = WeeklyReviewReport(paper_trader=pt, stage_manager=sm)
        result = rg.generate()
        assert result["report_type"] == "weekly_review"
        assert "override_analysis" in result
        assert "reflection_prompts" in result

    def test_weekly_review_override_analysis_structure(self, pt, sm):
        rg = WeeklyReviewReport(paper_trader=pt, stage_manager=sm)
        result = rg.generate()
        oa = result["override_analysis"]
        assert "count_7d" in oa
        assert "count_30d" in oa
        assert oa["threshold"] == 3

    def test_weekly_review_telegram_format(self, pt, sm):
        rg = WeeklyReviewReport(paper_trader=pt, stage_manager=sm)
        text = rg.format_telegram(rg.generate())
        assert "Weekly Review" in text

    def test_monthly_letter_generates(self, pt, sm):
        ml = MonthlyLetter(paper_trader=pt, stage_manager=sm)
        result = ml.generate("Stayed disciplined.")
        assert result["report_type"] == "monthly_letter"
        assert "exit_criteria_check" in result
        assert "template_commitments" in result
        assert len(result["template_commitments"]) >= 4

    def test_monthly_letter_exit_criteria(self, pt, sm):
        ml = MonthlyLetter(paper_trader=pt, stage_manager=sm)
        result = ml.generate()
        ec = result["exit_criteria_check"]
        assert "flags" in ec
        assert "any_triggered" in ec
        assert isinstance(ec["flags"], list)

    def test_monthly_letter_format_text(self, pt, sm):
        ml = MonthlyLetter(paper_trader=pt, stage_manager=sm)
        text = ml.format_text(ml.generate("Good month."))
        assert "MONTHLY LETTER" in text
        assert "REFLECTION" in text
        assert "COMMITMENTS" in text
        assert "Good month." in text

    def test_monthly_letter_no_reflection_fallback(self, pt, sm):
        ml = MonthlyLetter(paper_trader=pt, stage_manager=sm)
        text = ml.format_text(ml.generate())
        assert "no reflection written" in text


# ═══════════════════════════════════════════
# 9 — INTEGRATION WORKFLOWS
# ═══════════════════════════════════════════

class TestSprint6Integration:

    def test_clean_trade_full_lifecycle(self, router):
        """Place → close at profit → all reports → clean state."""
        # Place within TOE_DIP limits: risk = (2950-2920)×1 = ₹30
        r = router.place_order(
            "HDFCBANK", "long", 1,
            Decimal("2950"), Decimal("2920"), Decimal("3010"),
            source_screener="s1_momentum",
        )
        assert r.executed is True, f"Order blocked: {r.rejection_reason}"

        # Close at profit
        actions = router.record_close(r.paper_position_id, Decimal("2990"), "target_1")
        assert "actions_triggered" in actions

        # No gates or halts on a winning trade
        assert router.stage_manager._state.next_trade_gated is False
        assert router.stage_manager._state.day_halted is False

        # All reports generate without error
        pt, sm = router.paper_trader, router.stage_manager
        d = DailyPostMarketReport(paper_trader=pt, stage_manager=sm).generate()
        assert d["report_type"] == "daily_postmarket"

        w = WeeklyReviewReport(paper_trader=pt, stage_manager=sm).generate()
        assert w["report_type"] == "weekly_review"

        m = MonthlyLetter(paper_trader=pt, stage_manager=sm).generate("Sprint 6 working.")
        assert m["report_type"] == "monthly_letter"

        # Reconcile clean
        rec = pt.reconcile()
        assert rec["ok"] is True

    def test_losing_trade_triggers_review_gate(self, router):
        """Realized loss > 3% of allocated triggers review gate."""
        r = router.place_order(
            "TATASTEEL", "long", 1,
            Decimal("200"), Decimal("170"), Decimal("240"),
            # Risk = (200-170)×1 = ₹30 — approved by router
        )
        assert r.executed is True

        # Close at ₹170 (stop hit) — realized loss ₹30 via paper trader
        # Then separately push a ₹951 loss through stage_manager directly
        # to trigger the review gate (simulating a gap-down)
        router.stage_manager.record_trade_closed(Decimal("-951"))

        assert router.stage_manager._state.next_trade_gated is True

    def test_three_override_tracker_integrates_with_router(self, router, monkeypatch):
        """ThreeOverrideTracker demotes stage used by router."""
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_override_count_rolling", lambda d: 3
        )
        monkeypatch.setattr(
            "sentinel.ops.three_override_tracker.get_recent_overrides",
            lambda d: [{"guardrail": "test", "timestamp": "2026-01-01T00:00:00+00:00"}],
        )
        tracker = ThreeOverrideTracker(stage_manager=router.stage_manager)
        result = tracker.check_and_demote()
        assert result["demoted"] is True
        # Router's stage_manager is now in QUARANTINE
        assert router.stage_manager.stage == Stage.QUARANTINE
