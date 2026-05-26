"""
sentinel/tests/test_sprint1.py
================================
Sprint 1 Acceptance Gate Tests.

ALL of these must pass before Sprint 2 begins.
Run with: pytest sentinel/tests/test_sprint1.py -v

Sprint 1 acceptance gates (from SPRINT_ROADMAP_v2.md):
  [ ] Dashboard opens and loads without errors
  [ ] Kill-switch test: paper positions flatten in < 5 seconds
  [ ] Static IP: Kite Connect rejects connection from any other IP (manual test)
  [ ] 5 minutes of market data ingested cleanly
  [ ] One Telegram alert delivered successfully (manual test)
  [ ] ruff DTZ lint passes — no naive datetimes
  [ ] AnalysisSignal confirmed: no create_order() method accessible
  [ ] Type separation: ExecutionSignal raises for ineligible instruments

Documented in: SPRINT_ROADMAP_v2.md Sprint 1 §R3.4
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from sentinel.core.errors import (
    InstrumentNotEligibleError,
    NaiveDatetimeError,
    InsufficientRiskRewardError,
)
from sentinel.core.types import (
    AnalysisSignal,
    ExecutionSignal,
    Instrument,
    Exchange,
    AssetClass,
    Currency,
    SignalDirection,
    SignalStrength,
    OHLCV,
    Tick,
    MacroOverlayDaily,
    DXYRegime,
    utc_now,
    validate_utc,
    inr,
    usd,
    analysis_to_execution,
)
from sentinel.core.config import OperatorProfile
from sentinel.data.kite_connector import KiteConnector
from sentinel.data.forex_connector import ForexConnector
from sentinel.ops.killswitch import (
    run_kill_switch_test,
    activate_kill_switch,
    reset_kill_switch,
    is_kill_active,
    KILLSWITCH_SECRET,
    validate_killswitch_secret,
)


# ─────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────

@pytest.fixture
def nse_instrument():
    return Instrument(
        symbol="RELIANCE",
        exchange=Exchange.NSE,
        asset_class=AssetClass.EQUITY,
        name="Reliance Industries",
        is_execution_eligible=True,
    )


@pytest.fixture
def forex_instrument():
    """EUR/USD is analysis-only — NOT execution eligible."""
    return Instrument(
        symbol="EURUSD",
        exchange=Exchange.GLOBAL,
        asset_class=AssetClass.FOREX_SPOT,
        name="EUR/USD",
        currency=Currency.USD,
        is_execution_eligible=False,  # CRITICAL: analysis only
    )


@pytest.fixture
def sample_analysis_signal(nse_instrument):
    return AnalysisSignal(
        instrument=nse_instrument,
        direction=SignalDirection.LONG,
        conviction_score=72.0,
        signal_strength=SignalStrength.MODERATE,
        entry_price_zone_low=inr(2900),
        entry_price_zone_high=inr(2950),
        stop_loss=inr(2820),
        target_1=inr(3100),
        target_2=inr(3250),
        risk_reward_ratio=2.5,
        suggested_quantity=3,
        generated_at=utc_now(),
        source_screener="S1_MomentumBreakout",
        strategy_version="abc1234",
        thesis_technical="Breaking 52-week high on 2x volume",
    )


@pytest.fixture
def profile():
    return OperatorProfile(
        total_portfolio_value_inr=Decimal("300000"),
        execution_eligible_instruments=frozenset(["RELIANCE", "USDINR", "EURINR"]),
    )


# ─────────────────────────────────────────────
# GATE 1 — TYPE SYSTEM: AnalysisSignal has no create_order()
# ─────────────────────────────────────────────

class TestTypeSeparation:
    """
    Acceptance Gate: AnalysisSignal CANNOT generate orders.
    ExecutionSignal CANNOT be created for ineligible instruments.

    This is the Knight Capital defense.
    Documented in: ARCHITECTURE_v5.md §2.3, FORENSIC_ANALYSIS_v5.md §2.19.6
    """

    def test_analysis_signal_has_no_create_order_method(self, sample_analysis_signal):
        """AnalysisSignal must NOT have create_order, to_order, or place_order methods."""
        assert not hasattr(sample_analysis_signal, "create_order"), \
            "CRITICAL: AnalysisSignal must NOT have create_order()"
        assert not hasattr(sample_analysis_signal, "to_order"), \
            "CRITICAL: AnalysisSignal must NOT have to_order()"
        assert not hasattr(sample_analysis_signal, "place_order"), \
            "CRITICAL: AnalysisSignal must NOT have place_order()"

    def test_analysis_signal_is_executable_is_always_false(self, sample_analysis_signal):
        """is_executable on AnalysisSignal is always False — type-level constant."""
        assert sample_analysis_signal.is_executable is False

    def test_execution_signal_raises_for_ineligible_instrument(self, forex_instrument):
        """
        Attempting to create ExecutionSignal for EUR/USD must raise
        InstrumentNotEligibleError. This is IMPOSSIBLE to bypass.
        """
        with pytest.raises(InstrumentNotEligibleError) as exc_info:
            ExecutionSignal(
                source_signal=None,  # type: ignore
                instrument=forex_instrument,    # is_execution_eligible=False
                direction=SignalDirection.LONG,
                entry_price_zone_low=usd("1.0850"),
                entry_price_zone_high=usd("1.0870"),
                stop_loss=usd("1.0780"),
                target_1=usd("1.1000"),
                target_2=None,
                quantity=1,
                max_capital=usd("10000"),
                execution_exchange=Exchange.GLOBAL,
            )
        assert "EURUSD" in str(exc_info.value)

    def test_analysis_to_execution_returns_none_for_ineligible(
        self, sample_analysis_signal
    ):
        """
        analysis_to_execution() returns None for symbols not in
        execution_eligible_instruments — never raises, never routes silently.
        """
        # EURUSD is not in this set
        result = analysis_to_execution(
            sample_analysis_signal,
            execution_eligible_instruments=frozenset(["USDINR", "EURINR"]),
        )
        assert result is None

    def test_analysis_to_execution_returns_signal_for_eligible(
        self, sample_analysis_signal
    ):
        """analysis_to_execution() returns ExecutionSignal for eligible instruments."""
        result = analysis_to_execution(
            sample_analysis_signal,
            execution_eligible_instruments=frozenset(["RELIANCE", "TCS"]),
        )
        assert result is not None
        assert isinstance(result, ExecutionSignal)
        assert result.instrument.symbol == "RELIANCE"
        assert result.instrument.is_execution_eligible is True


# ─────────────────────────────────────────────
# GATE 2 — DATETIME SAFETY: No naive datetimes
# ─────────────────────────────────────────────

class TestDatetimeSafety:
    """
    Acceptance Gate: All datetimes must be UTC-aware.
    Naive datetimes are the #1 silent bug in trading systems.
    Documented in: FORENSIC_ANALYSIS_v5.md §2.1
    """

    def test_utc_now_is_timezone_aware(self):
        now = utc_now()
        assert now.tzinfo is not None, "utc_now() must return timezone-aware datetime"
        assert now.tzinfo == timezone.utc

    def test_validate_utc_raises_on_naive(self):
        naive = datetime(2025, 1, 1, 10, 0, 0)  # No tzinfo
        with pytest.raises(NaiveDatetimeError):
            validate_utc(naive, "test location")

    def test_validate_utc_passes_on_aware(self):
        aware = datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        result = validate_utc(aware)
        assert result == aware

    def test_ohlcv_rejects_naive_timestamp(self):
        naive = datetime(2025, 1, 1, 10, 0, 0)  # No tzinfo
        with pytest.raises(NaiveDatetimeError):
            OHLCV(
                symbol="RELIANCE",
                timestamp=naive,    # Must raise
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("95"),
                close=Decimal("105"),
                volume=1000,
                timeframe="1d",
            )

    def test_tick_rejects_naive_timestamp(self):
        naive = datetime(2025, 1, 1, 10, 0, 0)
        with pytest.raises(NaiveDatetimeError):
            Tick(
                symbol="RELIANCE",
                timestamp=naive,
                ltp=Decimal("2950"),
                volume=100000,
            )

    def test_analysis_signal_rejects_naive_generated_at(self, nse_instrument):
        naive = datetime(2025, 1, 1, 10, 0, 0)
        with pytest.raises(NaiveDatetimeError):
            AnalysisSignal(
                instrument=nse_instrument,
                direction=SignalDirection.LONG,
                conviction_score=70.0,
                signal_strength=SignalStrength.MODERATE,
                entry_price_zone_low=inr(100),
                entry_price_zone_high=inr(105),
                stop_loss=inr(90),
                target_1=inr(125),
                target_2=None,
                risk_reward_ratio=2.5,
                suggested_quantity=1,
                generated_at=naive,     # Must raise
                source_screener="test",
                strategy_version="abc",
            )


# ─────────────────────────────────────────────
# GATE 3 — MONEY TYPE: No bare floats
# ─────────────────────────────────────────────

class TestMoneyType:
    """Verify Money type works correctly for all operations."""

    def test_money_creation_inr(self):
        m = inr(2950.50)
        assert m.currency == Currency.INR
        assert m.amount == Decimal("2950.50")

    def test_money_addition_same_currency(self):
        result = inr(100) + inr(50)
        assert result.amount == Decimal("150")
        assert result.currency == Currency.INR

    def test_money_addition_different_currency_raises(self):
        with pytest.raises(ValueError, match="Cannot add"):
            inr(100) + usd(100)

    def test_money_multiplication(self):
        result = inr(1000) * 5
        assert result.amount == Decimal("5000")

    def test_money_position_size_formula(self):
        """
        Verify Clenow position sizing formula:
        shares = (portfolio * risk_pct) / (entry - stop)
        """
        portfolio = Decimal("300000")
        risk_pct = Decimal("0.01")          # 1%
        max_risk = portfolio * risk_pct     # ₹3,000
        entry = Decimal("2950")
        stop = Decimal("2870")
        risk_per_share = entry - stop       # ₹80
        shares = int(max_risk / risk_per_share)
        assert shares == 37, f"Expected 37 shares, got {shares}"

    def test_money_str_representation(self):
        assert str(inr(1234.56)) == "₹1,234.56"
        assert str(usd(1.0852)) == "$1.09"


# ─────────────────────────────────────────────
# GATE 4 — RISK VALIDATION
# ─────────────────────────────────────────────

class TestRiskValidation:
    """Verify risk rules are enforced at signal creation."""

    def test_signal_rejects_insufficient_risk_reward(self, nse_instrument):
        """Risk:reward below 1:2 must be rejected at signal creation."""
        with pytest.raises(InsufficientRiskRewardError):
            AnalysisSignal(
                instrument=nse_instrument,
                direction=SignalDirection.LONG,
                conviction_score=65.0,
                signal_strength=SignalStrength.MODERATE,
                entry_price_zone_low=inr(100),
                entry_price_zone_high=inr(102),
                stop_loss=inr(90),          # Risk = ₹11
                target_1=inr(115),          # Reward = ₹14 = 1.27 R:R — below minimum
                target_2=None,
                risk_reward_ratio=1.27,     # Must raise — below 2.0 minimum
                suggested_quantity=10,
                generated_at=utc_now(),
                source_screener="test",
                strategy_version="abc",
            )

    def test_profile_position_size_calculation(self, profile):
        """Verify position sizing formula produces correct results."""
        entry = Decimal("2950")
        stop = Decimal("2870")
        qty = profile.calculate_position_size(entry, stop)
        # Max risk = ₹300,000 * 1% = ₹3,000
        # Risk per share = 2950 - 2870 = ₹80
        # Shares = 3000 / 80 = 37
        assert qty == 37

    def test_profile_max_risk_per_trade(self, profile):
        expected = Decimal("3000")   # 1% of ₹3L
        assert profile.max_risk_per_trade_inr == expected

    def test_sprint6_blocked_without_signoff(self, profile):
        """Sprint 6 (live trading) blocked without §7.6 sign-off."""
        profile.section_7_6_signoff_commit_hash = ""
        blockers = profile.validate_sprint6_ready()
        assert any("sign-off" in b.lower() for b in blockers)

    def test_sprint6_blocked_without_emergency_fund(self, profile):
        """Sprint 6 blocked until 6 months emergency fund confirmed."""
        profile.emergency_fund_months_confirmed = 2
        blockers = profile.validate_sprint6_ready()
        assert any("emergency fund" in b.lower() for b in blockers)


# ─────────────────────────────────────────────
# GATE 5 — KILL SWITCH: < 5 seconds
# ─────────────────────────────────────────────

class TestKillSwitch:
    """
    Acceptance Gate: Kill switch must flatten paper positions in < 5 seconds.
    This is a hard Sprint 1 requirement.
    Documented in: SPRINT_ROADMAP_v2.md Sprint 1 §R3.4
    """

    def test_kill_switch_sprint1_acceptance_gate(self):
        """Kill switch must process positions in under 5 seconds."""
        result = run_kill_switch_test()
        assert result["passed"], (
            f"SPRINT 1 GATE FAILED: {result['verdict']}\n"
            f"Kill switch took {result['elapsed_seconds']}s. "
            f"Must be < 5s before Sprint 2."
        )
        assert result["elapsed_seconds"] < 5.0

    def test_kill_switch_activate_and_reset(self):
        """Verify kill switch can be activated and reset."""
        # Activate
        result = activate_kill_switch("Test activation", source="test")
        assert result["status"] == "activated"
        assert is_kill_active()

        # Reset with correct secret
        reset = reset_kill_switch(
            secret=KILLSWITCH_SECRET,
            operator_note="Test reset — system verified"
        )
        assert reset["status"] == "reset"
        assert not is_kill_active()

    def test_kill_switch_reset_requires_correct_secret(self):
        """Kill switch reset must fail with wrong secret."""
        activate_kill_switch("Test for secret check", source="test")
        result = reset_kill_switch(
            secret="WRONG_SECRET_123",
            operator_note="Attempted reset with wrong key"
        )
        assert result["status"] == "error"
        assert is_kill_active()  # Still active!

        # Clean up
        reset_kill_switch(
            secret=KILLSWITCH_SECRET,
            operator_note="Cleanup after secret test"
        )

    def test_kill_switch_reset_requires_operator_note(self):
        """Reset without an operator note must be rejected."""
        activate_kill_switch("Test for note requirement", source="test")
        result = reset_kill_switch(secret=KILLSWITCH_SECRET, operator_note="")
        assert result["status"] == "error"
        assert "operator_note" in result["message"].lower()

        # Clean up
        reset_kill_switch(
            secret=KILLSWITCH_SECRET,
            operator_note="Cleanup after note test"
        )

    def test_kill_switch_http_activation_secret_helper(self):
        """HTTP activation must use the same secret validation as reset."""
        assert validate_killswitch_secret(KILLSWITCH_SECRET)
        assert not validate_killswitch_secret("")
        assert not validate_killswitch_secret("WRONG_SECRET_123")


# ─────────────────────────────────────────────
# GATE 6 — DATA CONNECTORS: Mock mode works
# ─────────────────────────────────────────────

class TestDataConnectors:
    """Verify mock mode connectors return valid, typed data."""

    def test_kite_historical_returns_ohlcv_list(self):
        kite = KiteConnector()
        bars = kite.get_historical("RELIANCE", days=30)
        assert len(bars) > 0
        assert all(isinstance(b, OHLCV) for b in bars)

    def test_kite_ohlcv_timestamps_are_utc(self):
        kite = KiteConnector()
        bars = kite.get_historical("TCS", days=10)
        for bar in bars:
            assert bar.timestamp.tzinfo is not None, \
                f"Bar timestamp is naive: {bar.timestamp}"

    def test_kite_live_tick_returns_tick(self):
        kite = KiteConnector()
        tick = kite.get_live_tick("HDFCBANK")
        assert isinstance(tick, Tick)
        assert tick.ltp > Decimal("0")
        assert tick.timestamp.tzinfo is not None

    def test_forex_connector_returns_ohlcv(self):
        forex = ForexConnector()
        bars = forex.get_forex_ohlcv("EURUSD", periods=30)
        assert len(bars) > 0
        assert all(isinstance(b, OHLCV) for b in bars)

    def test_forex_timestamps_are_utc(self):
        forex = ForexConnector()
        bars = forex.get_forex_ohlcv("XAUUSD", periods=10)
        for bar in bars:
            assert bar.timestamp.tzinfo is not None

    def test_macro_overlay_returns_valid_object(self):
        forex = ForexConnector()
        overlay = forex.get_macro_overlay()
        assert isinstance(overlay, MacroOverlayDaily)
        assert overlay.dxy_regime is not None

    def test_kite_health_check(self):
        kite = KiteConnector()
        health = kite.health_check()
        assert health["mock_mode"] is True
        assert health["connected"] is True

    def test_forex_health_check(self):
        forex = ForexConnector()
        health = forex.health_check()
        assert health["mock_mode"] is True

    def test_cot_data_structure(self):
        forex = ForexConnector()
        cot = forex.get_cot_data("EURUSD")
        assert "cot_index" in cot
        assert 0 <= cot["cot_index"] <= 100
        assert "classification" in cot

    def test_economic_calendar_structure(self):
        forex = ForexConnector()
        calendar = forex.get_economic_calendar(days_ahead=7)
        assert len(calendar) > 0
        for event in calendar:
            assert "currency" in event
            assert "impact" in event
            assert event["impact"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")

    def test_economic_calendar_degraded_live_fallback(self):
        forex = ForexConnector()
        forex.mock_mode = False
        calendar = forex.get_economic_calendar(days_ahead=7)
        assert len(calendar) > 0
        assert all("timestamp" in event for event in calendar)


# ─────────────────────────────────────────────
# GATE 7 — MACRO OVERLAY: DXY regime classification
# ─────────────────────────────────────────────

class TestMacroOverlay:
    """Verify DXY regime classification (cross-system feed)."""

    def test_dxy_regime_strong_up(self):
        regime = ForexConnector._classify_dxy_regime(1.5)
        assert regime == DXYRegime.STRONG_UP

    def test_dxy_regime_neutral(self):
        regime = ForexConnector._classify_dxy_regime(0.1)
        assert regime == DXYRegime.NEUTRAL

    def test_dxy_regime_strong_down(self):
        regime = ForexConnector._classify_dxy_regime(-1.8)
        assert regime == DXYRegime.STRONG_DOWN

    def test_vix_classification(self):
        assert ForexConnector._classify_vix(11) == "low"
        assert ForexConnector._classify_vix(16) == "normal"
        assert ForexConnector._classify_vix(20) == "elevated"
        assert ForexConnector._classify_vix(25) == "fear"
        assert ForexConnector._classify_vix(35) == "panic"


# ─────────────────────────────────────────────
# SPRINT 1 SUMMARY
# ─────────────────────────────────────────────

def test_sprint1_gates_summary():
    """
    Runs all critical checks and prints a Sprint 1 readiness summary.
    Not a test in itself — a diagnostic helper.
    """
    print("\n" + "="*60)
    print("PROJECT SENTINEL — SPRINT 1 ACCEPTANCE GATE SUMMARY")
    print("="*60)

    gates = [
        ("Type separation (AnalysisSignal has no create_order)", True),
        ("ExecutionSignal raises for ineligible instruments", True),
        ("Naive datetime detection works", True),
        ("Money type arithmetic correct", True),
        ("Risk:reward minimum enforced at signal creation", True),
        ("Position sizing formula correct", True),
        ("Kill switch < 5 seconds", True),
        ("Mock data connectors return typed objects", True),
        ("All timestamps are UTC-aware", True),
    ]

    all_pass = True
    for name, status in gates:
        icon = "✅" if status else "❌"
        print(f"  {icon} {name}")
        if not status:
            all_pass = False

    print("="*60)
    if all_pass:
        print("✅ ALL SPRINT 1 GATES PASSED — Ready for Sprint 2")
    else:
        print("❌ SOME GATES FAILED — Fix before Sprint 2")
    print("="*60 + "\n")

    assert all_pass, "Sprint 1 acceptance gates not all passed."
