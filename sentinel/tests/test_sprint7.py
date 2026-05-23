"""
Sprint 7 research-factory tests.

These tests validate research-only modules. They do not assert live promotion;
Sprint 7 live expansion still requires 90 clean live days and operator sign-off.
"""

from sentinel.allocation.correlation_monitor import CorrelationMonitor, StrategyReturnSeries
from sentinel.allocation.meta_allocator import InverseVarianceAllocator, StrategyRiskSnapshot
from sentinel.execution.multi_strategy_arbiter import MultiStrategyArbiter, StrategyIntent
from sentinel.fo.covered_call import (
    CoveredCallCandidate,
    CoveredCallPlanner,
    EquityHolding,
    HedgeRejection,
)
from sentinel.fo.greeks_dashboard import OptionContract, calculate_greeks
from sentinel.research.sprint7_factory import (
    Sprint7ResearchSnapshot,
    build_sprint7_research_snapshot,
)
from sentinel.strategies.value_momentum_in import (
    ValueMomentumBacktestResult,
    ValueMomentumIN,
    ValueMomentumRankEntry,
    ValueMomentumSignal,
)


class TestValueMomentumStrategy:

    def test_rank_universe_returns_sorted_entries(self):
        strategy = ValueMomentumIN(top_n=5)
        ranked = strategy.rank_universe()
        assert ranked
        assert all(isinstance(e, ValueMomentumRankEntry) for e in ranked)
        assert all(ranked[i].combined_score >= ranked[i + 1].combined_score
                   for i in range(len(ranked) - 1))

    def test_run_generates_research_signals(self):
        signals = ValueMomentumIN(top_n=5).run()
        assert signals
        assert all(isinstance(s, ValueMomentumSignal) for s in signals)
        assert all(s.action == "enter_long" for s in signals)
        assert all(s.risk_reward >= 2.0 for s in signals)

    def test_backtest_returns_research_result(self):
        result = ValueMomentumIN(top_n=5).backtest()
        assert isinstance(result, ValueMomentumBacktestResult)
        assert result.strategy_name == "ValueMomentumIN"
        assert result.oos_n_trades >= 1
        assert 0 <= result.deflated_sharpe_ratio <= 1


class TestMetaAllocator:

    def test_inverse_variance_weights_sum_to_one(self):
        weights = InverseVarianceAllocator().allocate([
            StrategyRiskSnapshot("strategy1", annualized_vol_pct=12),
            StrategyRiskSnapshot("strategy2", annualized_vol_pct=8),
        ])
        assert round(sum(weights.values()), 4) == 1.0
        assert weights["strategy2"] > weights["strategy1"]

    def test_disabled_strategy_gets_no_weight(self):
        weights = InverseVarianceAllocator().allocate([
            StrategyRiskSnapshot("strategy1", annualized_vol_pct=12),
            StrategyRiskSnapshot("strategy2", annualized_vol_pct=8, enabled=False),
        ])
        assert weights == {"strategy1": 1.0}


class TestCorrelationMonitor:

    def test_matrix_identity_and_pair_detection(self):
        monitor = CorrelationMonitor()
        series = [
            StrategyReturnSeries("s1", [0.01, 0.02, -0.01, 0.03]),
            StrategyReturnSeries("s2", [0.02, 0.04, -0.02, 0.06]),
            StrategyReturnSeries("s3", [-0.01, 0.01, 0.02, -0.02]),
        ]
        mat = monitor.matrix(series)
        assert mat["s1"]["s1"] == 1.0
        pairs = monitor.high_correlation_pairs(series, threshold=0.9)
        assert ("s1", "s2", 1.0) in pairs


class TestMultiStrategyArbiter:

    def test_selects_highest_priority_then_conviction(self):
        decisions = MultiStrategyArbiter().resolve([
            StrategyIntent("s1", "RELIANCE", "enter_long", 80, priority=20),
            StrategyIntent("s2", "RELIANCE", "enter_long", 75, priority=10),
        ])
        assert decisions[0].selected is not None
        assert decisions[0].selected.strategy_id == "s2"
        assert len(decisions[0].rejected) == 1

    def test_conflicting_actions_block_for_review(self):
        decisions = MultiStrategyArbiter().resolve([
            StrategyIntent("s1", "TCS", "enter_long", 80),
            StrategyIntent("s2", "TCS", "exit_long", 75),
        ])
        assert decisions[0].selected is None
        assert "conflicting" in decisions[0].reason


class TestSprint7ResearchFactory:

    def test_research_snapshot_contains_all_operator_visible_sections(self):
        snapshot = build_sprint7_research_snapshot()
        assert isinstance(snapshot, Sprint7ResearchSnapshot)
        assert len(snapshot.strategy_metrics) == 3
        assert set(snapshot.target_weights) == {
            "strategy1_momentum",
            "strategy2_value_momentum",
            "strategy3_pairs_mean_reversion",
        }
        assert round(sum(snapshot.target_weights.values()), 4) == 1.0
        assert "strategy1_momentum" in snapshot.correlation_matrix
        assert "strategy2_value_momentum" in snapshot.correlation_matrix
        assert "strategy3_pairs_mean_reversion" in snapshot.correlation_matrix
        assert snapshot.allocation_method in {"hrp_recursive_bisection", "inverse_variance"}
        assert snapshot.promotion_memo

    def test_research_snapshot_blocks_live_promotion_without_evidence(self):
        snapshot = build_sprint7_research_snapshot()
        assert not snapshot.live_approved
        blocker_names = {gate.name for gate in snapshot.live_blockers}
        assert "90 clean live days" in blocker_names
        assert "Emergency fund gate" in blocker_names
        assert snapshot.promotion_status == "RESEARCH_ONLY"


class TestHedgingOnlyFO:

    def test_greeks_snapshot_contains_all_sensitivities(self):
        contract = OptionContract(
            symbol="RELIANCE24JUN3100CE",
            underlying="RELIANCE",
            option_type="call",
            strike=3100,
            expiry_days=30,
            lot_size=250,
            last_price=42,
            implied_vol_pct=22,
        )
        snapshot = calculate_greeks(contract, underlying_price=2950)
        assert snapshot.theoretical_price >= 0
        assert 0 <= snapshot.greeks.delta <= 1
        assert snapshot.greeks.gamma >= 0
        assert snapshot.greeks.vega >= 0

    def test_valid_covered_call_requires_held_equity(self):
        planner = CoveredCallPlanner(portfolio_value=3_000_000)
        holding = EquityHolding("RELIANCE", quantity=250, average_price=2800, last_price=2950)
        contract = OptionContract(
            symbol="RELIANCE24JUN3100CE",
            underlying="RELIANCE",
            option_type="call",
            strike=3100,
            expiry_days=30,
            lot_size=250,
            last_price=42,
            implied_vol_pct=22,
        )
        result = planner.evaluate(holding, contract)
        assert isinstance(result, CoveredCallCandidate)
        assert result.is_hedging_only
        assert result.premium_income == 10500
        assert result.greeks_snapshot.contract.symbol == contract.symbol

    def test_rejects_naked_call_when_holding_is_too_small(self):
        planner = CoveredCallPlanner(portfolio_value=300_000)
        holding = EquityHolding("RELIANCE", quantity=100, average_price=2800, last_price=2950)
        contract = OptionContract(
            symbol="RELIANCE24JUN3100CE",
            underlying="RELIANCE",
            option_type="call",
            strike=3100,
            expiry_days=30,
            lot_size=250,
            last_price=42,
            implied_vol_pct=22,
        )
        result = planner.evaluate(holding, contract)
        assert isinstance(result, HedgeRejection)
        assert "Insufficient held equity" in result.reason

    def test_rejects_puts_and_weekly_options(self):
        planner = CoveredCallPlanner(portfolio_value=300_000)
        holding = EquityHolding("RELIANCE", quantity=250, average_price=2800, last_price=2950)
        put = OptionContract(
            symbol="RELIANCE24JUN2800PE",
            underlying="RELIANCE",
            option_type="put",
            strike=2800,
            expiry_days=30,
            lot_size=250,
            last_price=30,
            implied_vol_pct=24,
        )
        weekly_call = OptionContract(
            symbol="RELIANCE24W3100CE",
            underlying="RELIANCE",
            option_type="call",
            strike=3100,
            expiry_days=5,
            lot_size=250,
            last_price=15,
            implied_vol_pct=28,
        )
        assert isinstance(planner.evaluate(holding, put), HedgeRejection)
        weekly_result = planner.evaluate(holding, weekly_call)
        assert isinstance(weekly_result, HedgeRejection)
        assert "Weekly" in weekly_result.reason
