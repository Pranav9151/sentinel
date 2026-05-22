"""
sentinel/tests/test_sprint4.py
================================
Sprint 4 Acceptance Gate Tests.

ALL must pass before Sprint 5 begins.
Run with: pytest sentinel/tests/test_sprint4.py -v

Sprint 4 gates:
  [ ] All 7 guardrails trigger correctly
  [ ] GSM/ASM hard block cannot be overridden via normal config
  [ ] Three-override rule logged and counted correctly
  [ ] Pre-mortem journal creates, saves, retrieves entries
  [ ] Low-quality answers flagged correctly
  [ ] Analytics computed after closed trades
  [ ] MF advisor recommends funds for all risk appetites
  [ ] SIP scenario engine handles all 5 scenarios
  [ ] Fund quality scoring 0-100
  [ ] Tax alert identifies LTCG opportunities
"""

import pytest

from sentinel.core.guardrails import (
    GuardrailEngine, GuardrailStatus,
    check_recency_bias, check_loss_aversion, check_fomo_entry,
    check_overtrading, check_position_averaging,
    check_tip_driven_trading, check_gsm_asm,
    log_override, get_override_count_rolling,
)
from sentinel.core.premortem import PreMortemJournal
from sentinel.data.mf_advisor import MFAdvisor


# ─────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_files(tmp_path, monkeypatch):
    """Use temp files so tests don't pollute each other."""
    override_log = tmp_path / "override_log.json"
    journal_file = tmp_path / "premortem_journal.json"
    monkeypatch.setattr("sentinel.core.guardrails.OVERRIDE_LOG_PATH", override_log)
    monkeypatch.setattr("sentinel.core.premortem.JOURNAL_PATH",        journal_file)
    yield


@pytest.fixture
def engine():
    return GuardrailEngine()


@pytest.fixture
def journal(tmp_path):
    return PreMortemJournal(path=tmp_path / "premortem_journal.json")


@pytest.fixture
def advisor():
    return MFAdvisor()


GOOD_ANSWERS = {
    "q1_thesis": "S1 screener: RELIANCE breaking 52-week high with 2.3x volume confirmation and MA stack aligned bullish.",
    "q2_wrong":  "False breakout if broad market sells off on RBI policy. Also risk of profit-booking at round number.",
    "q3_exit":   "Exit if price closes below ₹2870 (original stop) or if thesis changes due to new negative news.",
    "q4_bias":   "This is a system signal from S1 screener — not a tip. Pre-market plan included this stock.",
    "q5_size":   "Conviction score 72, so 1% risk = ₹3000 = 37 shares at ₹80 risk per share.",
}


# ─────────────────────────────────────────────
# GATE 1 — INDIVIDUAL GUARDRAIL CHECKS
# ─────────────────────────────────────────────

class TestIndividualGuardrails:

    def test_recency_bias_triggers_on_winning_streak(self):
        result = check_recency_bias(
            recent_wins=4,
            proposed_position_pct=2.5,
            standard_position_pct=1.0,
        )
        assert result.triggered
        assert result.status == GuardrailStatus.WARNING

    def test_recency_bias_passes_normal_size(self):
        result = check_recency_bias(
            recent_wins=4,
            proposed_position_pct=1.0,
            standard_position_pct=1.0,
        )
        assert not result.triggered

    def test_recency_bias_passes_few_wins(self):
        result = check_recency_bias(
            recent_wins=2,
            proposed_position_pct=2.0,
            standard_position_pct=1.0,
        )
        assert not result.triggered

    def test_loss_aversion_blocked_on_stop_widening(self):
        result = check_loss_aversion(
            symbol="RELIANCE",
            original_stop=2870.0,
            proposed_stop=2800.0,   # Moving stop DOWN = widening for a long
            current_price=2900.0,
        )
        assert result.triggered
        assert result.status == GuardrailStatus.BLOCKED

    def test_loss_aversion_passes_stop_tightening(self):
        result = check_loss_aversion(
            symbol="RELIANCE",
            original_stop=2870.0,
            proposed_stop=2920.0,   # Trailing stop UP = tightening = good
            current_price=2950.0,
        )
        assert not result.triggered

    def test_fomo_triggers_on_big_move_no_plan(self):
        result = check_fomo_entry(
            symbol="TCS",
            current_price=4000.0,
            price_5d_ago=3700.0,    # 8.1% move
            has_premarket_plan=False,
            rr_ratio=1.5,           # Below minimum
        )
        assert result.triggered
        assert result.status == GuardrailStatus.WARNING

    def test_fomo_passes_with_premarket_plan(self):
        result = check_fomo_entry(
            symbol="TCS",
            current_price=4000.0,
            price_5d_ago=3700.0,
            has_premarket_plan=True,   # Had a plan
            rr_ratio=2.5,
        )
        assert not result.triggered

    def test_overtrading_triggers_at_limit(self):
        result = check_overtrading(trades_this_week=5, max_trades_per_week=5)
        assert result.triggered
        assert result.status == GuardrailStatus.WARNING

    def test_overtrading_passes_below_limit(self):
        result = check_overtrading(trades_this_week=3, max_trades_per_week=5)
        assert not result.triggered

    def test_position_averaging_blocked_no_catalyst(self):
        result = check_position_averaging(
            symbol="INFY",
            original_entry=1600.0,
            current_price=1450.0,   # 9.4% loss
            is_long=True,
            has_new_catalyst=False,
        )
        assert result.triggered
        assert result.status in (GuardrailStatus.BLOCKED, GuardrailStatus.WARNING)

    def test_position_averaging_warning_with_catalyst(self):
        result = check_position_averaging(
            symbol="INFY",
            original_entry=1600.0,
            current_price=1450.0,
            is_long=True,
            has_new_catalyst=True,   # Catalyst exists
        )
        assert result.triggered
        assert result.status == GuardrailStatus.WARNING

    def test_position_averaging_passes_on_winner(self):
        result = check_position_averaging(
            symbol="INFY",
            original_entry=1600.0,
            current_price=1750.0,   # Profitable
            is_long=True,
            has_new_catalyst=False,
        )
        assert not result.triggered

    def test_tip_driven_blocked_without_card(self):
        result = check_tip_driven_trading(
            symbol="RANDOMTIP",
            has_screener_card=False,
            tip_source="WhatsApp group",
        )
        assert result.triggered
        assert result.status == GuardrailStatus.BLOCKED

    def test_tip_driven_passes_with_card(self):
        result = check_tip_driven_trading(
            symbol="RELIANCE",
            has_screener_card=True,
        )
        assert not result.triggered

    def test_gsm_asm_hard_block(self):
        result = check_gsm_asm(symbol="BADSTOCK", is_on_surveillance=True)
        assert result.triggered
        assert result.status == GuardrailStatus.BLOCKED
        assert result.can_override is False    # HARD BLOCK

    def test_gsm_asm_passes_clean_stock(self):
        result = check_gsm_asm(symbol="RELIANCE", is_on_surveillance=False)
        assert not result.triggered


# ─────────────────────────────────────────────
# GATE 2 — GUARDRAIL ENGINE
# ─────────────────────────────────────────────

class TestGuardrailEngine:

    def test_clear_trade_all_pass(self, engine):
        result = engine.check_trade(
            symbol="RELIANCE",
            is_on_surveillance=False,
            has_screener_card=True,
            proposed_position_pct=1.0,
            standard_position_pct=1.0,
            recent_wins=1,
            trades_this_week=2,
        )
        assert result["clear"] is True
        assert result["blocked"] is False
        assert result["has_warnings"] is False

    def test_gsm_block_propagates(self, engine):
        result = engine.check_trade(
            symbol="MOCKGSM1",
            is_on_surveillance=True,
            has_screener_card=True,
        )
        assert result["blocked"] is True
        hard = result["hard_blocks"]
        assert len(hard) >= 1
        assert any(b["guardrail_name"] == "GSMASMRejection" for b in hard)

    def test_tip_block_propagates(self, engine):
        result = engine.check_trade(
            symbol="RANDOMTIP",
            is_on_surveillance=False,
            has_screener_card=False,
            tip_source="YouTube",
        )
        assert result["blocked"] is True

    def test_multiple_warnings(self, engine):
        result = engine.check_trade(
            symbol="TCS",
            is_on_surveillance=False,
            has_screener_card=True,
            recent_wins=5,
            proposed_position_pct=3.0,
            standard_position_pct=1.0,
            trades_this_week=6,
            max_trades_per_week=5,
        )
        assert result["has_warnings"] is True
        assert len(result["warnings"]) >= 2


# ─────────────────────────────────────────────
# GATE 3 — THREE-OVERRIDE RULE
# ─────────────────────────────────────────────

class TestThreeOverrideRule:

    def test_override_logged(self, monkeypatch, tmp_path):
        log_path = tmp_path / "override_log.json"
        monkeypatch.setattr("sentinel.core.guardrails.OVERRIDE_LOG_PATH", log_path)

        result = log_override("Overtrading", "RELIANCE", "High conviction setup")
        assert result["overrides_in_window"] >= 1
        assert not result["demotion_triggered"]

    def test_three_overrides_triggers_demotion(self, monkeypatch, tmp_path):
        log_path = tmp_path / "override_log.json"
        monkeypatch.setattr("sentinel.core.guardrails.OVERRIDE_LOG_PATH", log_path)

        log_override("Overtrading", "RELIANCE", "Reason 1")
        log_override("FOMOEntry",   "TCS",      "Reason 2")
        result = log_override("RecencyBias","INFY", "Reason 3")

        assert result["demotion_triggered"] is True
        assert "PAPER MODE" in result["demotion_message"]

    def test_override_count_rolling(self, monkeypatch, tmp_path):
        log_path = tmp_path / "override_log.json"
        monkeypatch.setattr("sentinel.core.guardrails.OVERRIDE_LOG_PATH", log_path)

        log_override("Overtrading", "RELIANCE", "Test 1")
        log_override("Overtrading", "TCS",      "Test 2")

        count = get_override_count_rolling(days=30)
        assert count == 2

    def test_gsm_hard_block_cannot_override(self):
        """GSM/ASM block cannot be overridden — can_override must be False."""
        result = check_gsm_asm("BADSTOCK", is_on_surveillance=True)
        assert result.can_override is False


# ─────────────────────────────────────────────
# GATE 4 — PRE-MORTEM JOURNAL
# ─────────────────────────────────────────────

class TestPreMortemJournal:

    def test_create_entry_returns_id(self, journal):
        entry_id = journal.create_entry(
            symbol="RELIANCE",
            screener="s1_momentum",
            answers=GOOD_ANSWERS,
            conviction_score=72.0,
            entry_price=2950.0,
            stop_loss=2870.0,
            target_1=3100.0,
        )
        assert entry_id.startswith("pm_")

    def test_entry_saved_to_journal(self, journal):
        journal.create_entry(
            symbol="TCS",
            screener="s2_value",
            answers=GOOD_ANSWERS,
            conviction_score=65.0,
            entry_price=3800.0,
            stop_loss=3700.0,
            target_1=4000.0,
        )
        entries = journal.get_all()
        assert len(entries) == 1
        assert entries[0]["symbol"] == "TCS"

    def test_open_and_closed_entries(self, journal):
        eid = journal.create_entry(
            symbol="HDFCBANK",
            screener="s1_momentum",
            answers=GOOD_ANSWERS,
            conviction_score=70.0,
            entry_price=1700.0,
            stop_loss=1640.0,
            target_1=1820.0,
        )
        assert len(journal.get_open()) == 1
        assert len(journal.get_closed()) == 0

        journal.update_outcome(eid, "win", exit_price=1810.0, lesson="Held to target")
        assert len(journal.get_open()) == 0
        assert len(journal.get_closed()) == 1

    def test_outcome_pnl_calculated(self, journal):
        eid = journal.create_entry(
            symbol="INFY",
            screener="s1_momentum",
            answers=GOOD_ANSWERS,
            conviction_score=68.0,
            entry_price=1600.0,
            stop_loss=1540.0,
            target_1=1720.0,
        )
        journal.update_outcome(eid, "win", exit_price=1710.0)
        closed = journal.get_closed()
        pnl = closed[0]["pnl_pct"]
        expected = (1710 - 1600) / 1600 * 100
        assert abs(pnl - expected) < 0.1

    def test_low_quality_answer_flagged(self, journal):
        bad_answers = {
            "q1_thesis": "looks good",          # Too short + templated
            "q2_wrong":  "might not work",
            "q3_exit":   "when it drops",
            "q4_bias":   "system signal",
        }
        entry_id = journal.create_entry(
            symbol="WIPRO",
            screener="s1_momentum",
            answers=bad_answers,
            conviction_score=60.0,
            entry_price=500.0,
            stop_loss=480.0,
            target_1=540.0,
        )
        entries = journal.get_all()
        entry = next(e for e in entries if e["entry_id"] == entry_id)
        assert entry["low_quality"] is True

    def test_required_question_missing_raises(self, journal):
        incomplete = {
            "q1_thesis": "S1 screener breakout signal with volume and MA stack confirmed.",
            # q2_wrong missing — required
            "q3_exit":   "Exit if price closes below original stop of ₹480.",
            "q4_bias":   "System signal from screener, not a tip.",
        }
        with pytest.raises(ValueError, match="q2_wrong"):
            journal.create_entry(
                symbol="WIPRO",
                screener="s1_momentum",
                answers=incomplete,
                conviction_score=60.0,
                entry_price=500.0,
                stop_loss=480.0,
                target_1=540.0,
            )

    def test_analytics_after_closed_trades(self, tmp_path):
        # Create journal with explicit tmp path for isolation
        j = PreMortemJournal(path=tmp_path / "premortem_analytics.json")
        for sym, ep, ex, outcome in [
            ("RELIANCE", 2900, 3100, "win"),
            ("TCS",      3800, 3650, "loss"),
            ("HDFCBANK", 1700, 1820, "win"),
        ]:
            eid = j.create_entry(
                symbol=sym, screener="s1_momentum",
                answers=GOOD_ANSWERS, conviction_score=70.0,
                entry_price=ep, stop_loss=ep*0.97, target_1=ep*1.07,
            )
            j.update_outcome(eid, outcome, exit_price=ex)

        analytics = j.get_analytics()
        assert analytics["entries"] == 3
        assert 0 <= analytics["win_rate_pct"] <= 100
        assert "expectancy" in analytics
        assert "screener_performance" in analytics


# ─────────────────────────────────────────────
# GATE 5 — MF ADVISOR
# ─────────────────────────────────────────────

class TestMFAdvisor:

    def test_recommend_sip_conservative(self, advisor):
        rec = advisor.recommend_sip(
            monthly_budget=3000,
            risk_appetite="conservative",
            time_horizon_years=10,
        )
        assert len(rec["recommendations"]) > 0
        total = sum(r["amount"] for r in rec["recommendations"])
        assert abs(total - 3000) <= 500    # Allow rounding

    def test_recommend_sip_moderate(self, advisor):
        rec = advisor.recommend_sip(3000, "moderate", 10)
        assert rec["risk_appetite"] == "moderate"
        assert rec["total_monthly"] == 3000
        assert rec["projected_value"] > 0

    def test_recommend_sip_aggressive(self, advisor):
        rec = advisor.recommend_sip(3000, "aggressive", 10)
        assert len(rec["recommendations"]) > 0

    def test_projected_value_greater_than_contributions(self, advisor):
        rec = advisor.recommend_sip(3000, "moderate", 10)
        total_contributions = 3000 * 12 * 10
        assert rec["projected_value"] > total_contributions

    def test_fund_quality_score_range(self, advisor):
        for key in advisor.funds:
            score_data = advisor.score_fund(key)
            assert "score" in score_data
            assert 0 <= score_data["score"] <= 100, \
                f"Fund {key} score {score_data['score']} out of range"

    def test_score_all_funds_sorted(self, advisor):
        scores = advisor.score_all_funds()
        assert len(scores) == len(advisor.funds)
        for i in range(len(scores) - 1):
            assert scores[i]["score"] >= scores[i+1]["score"], \
                "Funds should be sorted by score descending"

    def test_scenario_market_crash(self, advisor):
        result = advisor.sip_scenario("market_crash", nifty_drop_pct=38)
        assert result["action"] == "CONTINUE SIP — Do NOT pause"
        assert "action_items" in result
        assert len(result["action_items"]) > 0

    def test_scenario_all_time_high(self, advisor):
        result = advisor.sip_scenario("all_time_high", nifty_pe=26)
        assert "action_items" in result

    def test_scenario_underperform(self, advisor):
        result = advisor.sip_scenario(
            "fund_underperform",
            fund_name="Test Fund",
            underperform_years=3,
            underperform_pct=4
        )
        assert "decision_tree" in result

    def test_scenario_manager_change(self, advisor):
        result = advisor.sip_scenario("fund_manager_change", fund_name="Test Fund")
        assert "action_items" in result

    def test_scenario_short_horizon(self, advisor):
        result = advisor.sip_scenario("need_money_soon", years_to_goal=2)
        assert "recommended_switch" in result
        assert result["action"] == "SWITCH OUT OF EQUITY immediately"

    def test_scenario_invalid_raises_error(self, advisor):
        result = advisor.sip_scenario("nonexistent_scenario")
        assert "error" in result
        assert "valid_scenarios" in result

    def test_tax_alert_structure(self, advisor):
        holdings = [
            {
                "fund": "PPFAS_FLEXI",
                "purchase_date": "2024-01-15",
                "units": 100,
                "buy_nav": 60.0,
                "current_nav": 75.0,
            }
        ]
        result = advisor.tax_alert(holdings, ltcg_used_so_far=0)
        assert "ltcg_exemption" in result
        assert result["ltcg_exemption"] == 125000.0
        assert "alerts" in result
        assert isinstance(result["alerts"], list)

    def test_tax_alert_remaining_exemption(self, advisor):
        result = advisor.tax_alert([], ltcg_used_so_far=50000)
        assert result["ltcg_remaining"] == 75000.0


# ─────────────────────────────────────────────
# SPRINT 4 SUMMARY
# ─────────────────────────────────────────────

def test_sprint4_gates_summary():
    print("\n" + "="*60)
    print("PROJECT SENTINEL — SPRINT 4 ACCEPTANCE GATE SUMMARY")
    print("="*60)
    gates = [
        "Guardrail #1 RecencyBias triggers on winning streak",
        "Guardrail #1 RecencyBias passes normal size",
        "Guardrail #2 LossAversion blocks stop widening",
        "Guardrail #2 LossAversion passes stop tightening",
        "Guardrail #3 FOMOEntry triggers on big move, no plan",
        "Guardrail #3 FOMOEntry passes with premarket plan",
        "Guardrail #4 Overtrading triggers at weekly limit",
        "Guardrail #5 PositionAveraging blocked no catalyst",
        "Guardrail #5 PositionAveraging warning with catalyst",
        "Guardrail #6 TipDriven blocked without screener card",
        "Guardrail #7 GSM/ASM hard block (can_override=False)",
        "GuardrailEngine clear trade passes all checks",
        "GuardrailEngine GSM block propagates to result",
        "Three-override rule: 3 overrides triggers demotion",
        "GSM hard block cannot be overridden",
        "PreMortem: create entry returns ID",
        "PreMortem: entry saved and retrievable",
        "PreMortem: open vs closed tracking",
        "PreMortem: P&L calculated correctly",
        "PreMortem: low-quality answers flagged",
        "PreMortem: missing required question raises ValueError",
        "PreMortem: analytics after closed trades",
        "MFAdvisor: SIP recommendation all 3 risk levels",
        "MFAdvisor: projected value > total contributions",
        "MFAdvisor: fund scores 0-100",
        "MFAdvisor: all 5 scenarios handled",
        "MFAdvisor: invalid scenario returns error dict",
        "MFAdvisor: tax alert structure correct",
    ]
    for g in gates:
        print(f"  ✅ {g}")
    print("\n" + "="*60)
    print("✅ ALL SPRINT 4 GATES — Run full pytest to verify")
    print("   Next: Sprint 5 — Paper Trading + Full Dashboard")
    print("="*60 + "\n")
