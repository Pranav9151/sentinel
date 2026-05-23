"""Sprint 8 production-grade lifecycle tests."""

from sentinel.lifecycle.lifecycle_gate import (
    LifecycleGateResult,
    StrategyLifecycleEvidence,
    StrategyLifecycleGate,
    StrategyLifecycleStage,
)
from sentinel.ops.deployment_readiness import (
    DeploymentReadinessReport,
    build_deployment_readiness_report,
)
from sentinel.ops.audit import AppendOnlyAuditLog
from sentinel.allocation.correlation_monitor import StrategyReturnSeries
from sentinel.allocation.hrp_allocator import HierarchicalRiskParityAllocator
from sentinel.regime.hmm import RegimeState, classify_regime
from sentinel.strategies.pairs_mean_reversion_in import (
    PairsBacktestResult,
    PairsMeanReversionIN,
    PairSignal,
)


class TestStrategyLifecycleGate:

    def test_research_promotes_to_shadow_when_quant_gates_pass(self):
        result = StrategyLifecycleGate().evaluate(StrategyLifecycleEvidence(
            strategy_id="strategy2",
            current_stage=StrategyLifecycleStage.RESEARCH,
            oos_sharpe=0.8,
            deflated_sharpe_ratio=0.97,
            max_drawdown_pct=6.0,
        ))
        assert isinstance(result, LifecycleGateResult)
        assert result.can_promote
        assert result.recommended_stage == StrategyLifecycleStage.SHADOW

    def test_research_blocks_low_dsr(self):
        result = StrategyLifecycleGate().evaluate(StrategyLifecycleEvidence(
            strategy_id="strategy2",
            current_stage=StrategyLifecycleStage.RESEARCH,
            oos_sharpe=0.8,
            deflated_sharpe_ratio=0.80,
        ))
        assert not result.can_promote
        assert result.recommended_stage == StrategyLifecycleStage.RESEARCH
        assert any("DSR" in blocker for blocker in result.blockers)

    def test_shadow_requires_low_correlation_and_30_days(self):
        result = StrategyLifecycleGate().evaluate(StrategyLifecycleEvidence(
            strategy_id="strategy2",
            current_stage=StrategyLifecycleStage.SHADOW,
            shadow_days=31,
            live_correlation_to_incumbent=0.25,
        ))
        assert result.can_promote
        assert result.recommended_stage == StrategyLifecycleStage.PAPER

    def test_paper_requires_operator_signoff(self):
        result = StrategyLifecycleGate().evaluate(StrategyLifecycleEvidence(
            strategy_id="strategy2",
            current_stage=StrategyLifecycleStage.PAPER,
            paper_days=90,
            operator_signoff_present=False,
        ))
        assert not result.can_promote
        assert any("sign-off" in blocker for blocker in result.blockers)

    def test_quarantine_requires_90_clean_days_and_capital_signoff(self):
        result = StrategyLifecycleGate().evaluate(StrategyLifecycleEvidence(
            strategy_id="strategy2",
            current_stage=StrategyLifecycleStage.QUARANTINE_LIVE,
            quarantine_live_days=90,
            clean_live_days=90,
            capital_expansion_signoff_present=True,
        ))
        assert result.can_promote
        assert result.recommended_stage == StrategyLifecycleStage.PRODUCTION

    def test_operational_breach_blocks_any_promotion(self):
        result = StrategyLifecycleGate().evaluate(StrategyLifecycleEvidence(
            strategy_id="strategy2",
            current_stage=StrategyLifecycleStage.RESEARCH,
            oos_sharpe=1.0,
            deflated_sharpe_ratio=0.99,
            operational_breaches=1,
        ))
        assert not result.can_promote
        assert any("breaches" in blocker for blocker in result.blockers)


class TestRegimePosterior:

    def test_calm_uptrend_has_full_risk_multiplier(self):
        posterior = classify_regime([0.001, 0.002, -0.001, 0.0015] * 25, india_vix=14)
        assert posterior.state in {RegimeState.CALM_UPTREND, RegimeState.CHOPPY_NEUTRAL}
        assert round(sum(posterior.probabilities.values()), 4) == 1.0
        assert not posterior.vix_defensive
        assert posterior.recommended_risk_multiplier in {0.75, 1.0}

    def test_vix_stress_forces_defensive_overlay(self):
        posterior = classify_regime([0.0, 0.001, -0.001] * 30, india_vix=28)
        assert posterior.vix_defensive
        assert posterior.probabilities[RegimeState.STRESS] > 0.3
        assert posterior.recommended_risk_multiplier == 0.5

    def test_empty_history_falls_back_without_crashing(self):
        posterior = classify_regime([], india_vix=18)
        assert posterior.state == RegimeState.CHOPPY_NEUTRAL
        assert posterior.rationale == "Insufficient return history"


class TestHRPAllocator:

    def test_hrp_allocates_three_strategy_streams(self):
        result = HierarchicalRiskParityAllocator().allocate([
            StrategyReturnSeries("s1", [0.01, 0.02, -0.01, 0.015, 0.0]),
            StrategyReturnSeries("s2", [0.008, 0.018, -0.008, 0.012, 0.002]),
            StrategyReturnSeries("s3", [-0.004, 0.003, 0.002, -0.001, 0.004]),
        ])
        assert result.method == "hrp_recursive_bisection"
        assert set(result.weights) == {"s1", "s2", "s3"}
        assert round(sum(result.weights.values()), 4) == 1.0

    def test_hrp_declines_when_fewer_than_three_strategies(self):
        result = HierarchicalRiskParityAllocator().allocate([
            StrategyReturnSeries("s1", [0.01, 0.02]),
            StrategyReturnSeries("s2", [0.01, 0.02]),
        ])
        assert result.weights == {}
        assert result.method == "insufficient_strategies"


class TestPairsMeanReversionStrategy:

    def test_pairs_strategy_backtest_returns_research_result(self):
        result = PairsMeanReversionIN().backtest()
        assert isinstance(result, PairsBacktestResult)
        assert result.strategy_name == "PairsMeanReversionIN"
        assert result.oos_n_trades >= 0
        assert 0 <= result.deflated_sharpe_ratio <= 1

    def test_pairs_strategy_signals_are_research_only_pair_reversion(self):
        signals = PairsMeanReversionIN(entry_z=0.1).run()
        assert all(isinstance(signal, PairSignal) for signal in signals)
        assert all(signal.action == "enter_pair_reversion" for signal in signals)
        assert all(signal.notional_per_leg <= 25_000 for signal in signals)


class TestDeploymentReadiness:

    def test_readiness_report_is_machine_readable_and_blocks_current_config(self):
        report = build_deployment_readiness_report()
        assert isinstance(report, DeploymentReadinessReport)
        assert not report.ready
        assert report.blockers
        data = report.as_dict()
        assert data["ready"] is False
        assert any(check["category"] == "operator_safety" for check in data["checks"])

    def test_readiness_report_contains_strategy_and_security_checks(self):
        report = build_deployment_readiness_report()
        categories = {check.category for check in report.checks}
        assert "strategy" in categories
        assert "security" in categories
        assert any(check.name == "Kill switch secret validation" for check in report.checks)


class TestAppendOnlyAuditLog:

    def test_audit_log_appends_and_verifies_hash_chain(self, tmp_path):
        audit = AppendOnlyAuditLog(tmp_path / "audit.jsonl")
        first = audit.append("deployment_check", {"ready": False}, actor="codex")
        second = audit.append("test_run", {"passed": 331}, actor="codex")

        events = audit.read_all()
        assert [e.event_id for e in events] == [first.event_id, second.event_id]
        assert events[0].previous_hash == "GENESIS"
        assert events[1].previous_hash == first.event_hash

        result = audit.verify()
        assert result.valid
        assert result.event_count == 2

    def test_audit_log_detects_tampering(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        audit = AppendOnlyAuditLog(path)
        audit.append("deployment_check", {"ready": False}, actor="codex")
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("deployment_check", "tampered"), encoding="utf-8")

        result = audit.verify()
        assert not result.valid
        assert "hash mismatch" in result.first_error
