"""
Sprint 7 research-factory tests.

These tests validate research-only modules. They do not assert live promotion;
Sprint 7 live expansion still requires 90 clean live days and operator sign-off.
"""

from sentinel.allocation.correlation_monitor import CorrelationMonitor, StrategyReturnSeries
from sentinel.allocation.meta_allocator import InverseVarianceAllocator, StrategyRiskSnapshot
from sentinel.execution.multi_strategy_arbiter import MultiStrategyArbiter, StrategyIntent
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
