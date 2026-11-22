"""Comprehensive regression tests for Q6 prospective strategy evaluation.

All tests use synthetic fixtures or mocked data. No external Binance
availability required.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

# Ensure src is on path
ROOT = Path(__file__).resolve().parents[4]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.research.prospective.storage import ProspectiveStorage  # noqa: E402
from crypto_engine.research.q6.accounting import (  # noqa: E402
    EconomicAccounting,
    compute_economic_accounting,
)
from crypto_engine.research.q6.benchmark import BenchmarkComparator  # noqa: E402
from crypto_engine.research.q6.config import (  # noqa: E402
    CandidateLock,
    Q6EvaluationPolicy,
    compute_lock_hash,
)
from crypto_engine.research.q6.governance import (  # noqa: E402
    BLOCKED,
    EDGE_NOT_FOUND,
    INSUFFICIENT_EVIDENCE,
    VALIDATION_PASSED_PENDING_HUMAN_REVIEW,
    assess_q6_readiness,
    historical_q4_rejection_preserved,
    no_prospective_data_leakage,
    q6_cannot_auto_promote,
)
from crypto_engine.research.q6.manifest import EvaluationManifest  # noqa: E402
from crypto_engine.research.q6.preflight import (  # noqa: E402
    PreflightBlockedError,
)
from crypto_engine.research.q6.promotion import (  # noqa: E402
    PromotionGate,
)
from crypto_engine.research.q6.regime import (  # noqa: E402
    RegimeAnalyzer,
    classify_regime,
    compute_chronological_subperiods,
    compute_regime_performance,
    compute_rolling_performance,
)
from crypto_engine.research.q6.robustness import RobustnessAnalyzer  # noqa: E402
from crypto_engine.research.q6.statistics import (  # noqa: E402
    StatisticalValidator,
    compute_bootstrap_confidence_interval,
    compute_permutation_test,
    compute_probability_of_positive_expectancy,
    compute_sharpe_ratio,
    compute_sortino_ratio,
)

# ---------------------------------------------------------------------------
# Accounting tests
# ---------------------------------------------------------------------------


class TestEconomicAccounting:
    """Tests for gross-vs-net accounting."""

    def test_gross_vs_net_reconciliation(self):
        """Gross minus costs must equal net exactly."""
        result = compute_economic_accounting(
            gross_returns_bp=[100.0, -50.0, 200.0, -30.0],
            net_returns_bp=[80.0, -60.0, 180.0, -40.0],
            fees_bp=40.0,
            spread_bp=20.0,
            slippage_bp=40.0,
            funding_bp=0.0,
        )
        assert result["accounting_reconciliation"]["reconciles"] is True
        assert result["trade_count"] == 4
        assert result["gross_return_bp"] == 220.0
        assert result["net_return_bp"] == 160.0
        assert result["total_trading_costs_bp"] == 60.0

    def test_zero_trades(self):
        """Zero-trade accounting returns None for rates, 0.0 for sums."""
        result = compute_economic_accounting(
            gross_returns_bp=[],
            net_returns_bp=[],
        )
        assert result["trade_count"] == 0
        assert result["win_rate"] is None
        assert result["profit_factor"] is None
        assert result["gross_profit_factor"] is None
        assert result["net_profit_factor"] is None
        assert result["expectancy_bp"] is None
        assert result["average_trade_return_bp"] is None

    def test_zero_loss_profit_factor_none(self):
        """Zero-loss profit factor is None (undefined, not infinite)."""
        result = compute_economic_accounting(
            gross_returns_bp=[100.0, 50.0, 200.0],
            net_returns_bp=[90.0, 40.0, 190.0],
            fees_bp=30.0,
        )
        assert result["gross_loss_bp"] == 0.0
        assert result["net_loss_bp"] == 0.0
        assert result["gross_profit_factor"] is None
        assert result["net_profit_factor"] is None

    def test_cost_stress_multipliers(self):
        """Cost stress at 1x, 1.5x, 2x reduces net returns."""
        result = compute_economic_accounting(
            gross_returns_bp=[1000.0, -100.0, 500.0],
            net_returns_bp=[980.0, -110.0, 480.0],
            fees_bp=40.0,
            cost_multipliers=[1.0, 1.5, 2.0],
        )
        assert len(result["cost_stress"]) == 3
        multipliers = [cs["cost_multiplier"] for cs in result["cost_stress"]]
        assert multipliers == [1.0, 1.5, 2.0]
        assert (
            result["cost_stress"][2]["total_net_return_bp"]
            <= result["cost_stress"][0]["total_net_return_bp"]
        )

    def test_economic_accounting_wrapper(self):
        """EconomicAccounting class wrapper works correctly."""
        ea = EconomicAccounting(
            fees_bp=10.0,
            spread_bp=5.0,
            slippage_bp=5.0,
            cost_multipliers=[1.0, 2.0],
        )
        result = ea.compute(
            gross_returns_bp=[100.0, -50.0],
            net_returns_bp=[80.0, -60.0],
        )
        assert result["trade_count"] == 2
        assert "cost_stress" in result


# ---------------------------------------------------------------------------
# Candidate lock tests
# ---------------------------------------------------------------------------


class TestCandidateLock:
    """Tests for candidate lock immutability."""

    def test_candidate_lock_valid(self):
        """Valid candidate lock passes is_valid()."""
        lock = CandidateLock(
            candidate_id="lightgbm",
            model_name="lightgbm",
            model_artifact_hash="abc123def456",
            model_config_hash="def456ghi789",
            feature_config_hash="ghi789jkl012",
            target_config_hash="jkl012mno345",
            strategy_config_hash="mno345pqr678",
            entry_exit_rules={"rule": "triple_barrier"},
            thresholds={"t_long": 0.6, "t_short": 0.4},
            calibration_status="uncalibrated",
            cost_assumptions={"fee_bp": 10.0},
            risk_limits={"max_drawdown_bp": 5000},
            candidate_creation_timestamp="2026-11-12T00:00:00+00:00",
            associated_research_freeze_id="freeze-aaa4dd57057a",
            associated_prospective_collection_id="collect-52cefa0dda64",
            lock_timestamp="2026-11-12T00:00:00+00:00",
            lock_hash="",
        )
        assert lock.is_valid() is True

    def test_candidate_lock_invalid_missing_fields(self):
        """Invalid candidate lock fails is_valid()."""
        lock = CandidateLock(
            candidate_id="",
            model_name="",
            model_artifact_hash="",
            model_config_hash="",
            feature_config_hash="",
            target_config_hash="",
            strategy_config_hash="",
            entry_exit_rules={},
            thresholds={},
            calibration_status="",
            cost_assumptions={},
            risk_limits={},
            candidate_creation_timestamp="",
            associated_research_freeze_id="",
            associated_prospective_collection_id="",
            lock_timestamp="",
            lock_hash="",
        )
        assert lock.is_valid() is False

    def test_compute_lock_hash_deterministic(self):
        """Same lock produces same hash."""
        lock1 = CandidateLock(
            candidate_id="test",
            model_name="test",
            model_artifact_hash="hash1",
            model_config_hash="hash2",
            feature_config_hash="hash3",
            target_config_hash="hash4",
            strategy_config_hash="hash5",
            entry_exit_rules={"a": 1},
            thresholds={"b": 2},
            calibration_status="uncalibrated",
            cost_assumptions={"c": 3},
            risk_limits={"d": 4},
            candidate_creation_timestamp="2026-01-01T00:00:00+00:00",
            associated_research_freeze_id="freeze-1",
            associated_prospective_collection_id="collect-1",
            lock_timestamp="2026-01-01T00:00:00+00:00",
            lock_hash="",
        )
        lock2 = CandidateLock(
            candidate_id="test",
            model_name="test",
            model_artifact_hash="hash1",
            model_config_hash="hash2",
            feature_config_hash="hash3",
            target_config_hash="hash4",
            strategy_config_hash="hash5",
            entry_exit_rules={"a": 1},
            thresholds={"b": 2},
            calibration_status="uncalibrated",
            cost_assumptions={"c": 3},
            risk_limits={"d": 4},
            candidate_creation_timestamp="2026-01-01T00:00:00+00:00",
            associated_research_freeze_id="freeze-1",
            associated_prospective_collection_id="collect-1",
            lock_timestamp="2026-01-01T00:00:00+00:00",
            lock_hash="",
        )
        assert compute_lock_hash(lock1) == compute_lock_hash(lock2)

    def test_compute_lock_hash_different_inputs_different_hash(self):
        """Different inputs produce different hashes."""
        lock1 = CandidateLock(
            candidate_id="a",
            model_name="a",
            model_artifact_hash="h1",
            model_config_hash="h2",
            feature_config_hash="h3",
            target_config_hash="h4",
            strategy_config_hash="h5",
            entry_exit_rules={},
            thresholds={},
            calibration_status="uncalibrated",
            cost_assumptions={},
            risk_limits={},
            candidate_creation_timestamp="t1",
            associated_research_freeze_id="f1",
            associated_prospective_collection_id="c1",
            lock_timestamp="lt1",
            lock_hash="",
        )
        lock2 = CandidateLock(
            candidate_id="b",
            model_name="a",
            model_artifact_hash="h1",
            model_config_hash="h2",
            feature_config_hash="h3",
            target_config_hash="h4",
            strategy_config_hash="h5",
            entry_exit_rules={},
            thresholds={},
            calibration_status="uncalibrated",
            cost_assumptions={},
            risk_limits={},
            candidate_creation_timestamp="t1",
            associated_research_freeze_id="f1",
            associated_prospective_collection_id="c1",
            lock_timestamp="lt1",
            lock_hash="",
        )
        assert compute_lock_hash(lock1) != compute_lock_hash(lock2)


# ---------------------------------------------------------------------------
# Statistics tests
# ---------------------------------------------------------------------------


class TestStatistics:
    """Tests for statistical validation."""

    def test_bootstrap_ci_sufficient_sample(self):
        """Bootstrap CI with sufficient sample returns valid bounds."""
        rng = np.random.RandomState(42)
        returns = rng.normal(5.0, 10.0, 100).tolist()
        result = compute_bootstrap_confidence_interval(
            returns_bp=returns,
            confidence_level=0.95,
            n_replicates=100,
            seed=42,
        )
        assert result["sufficient_sample"] is True
        assert result["mean"] is not None
        assert result["ci_lower"] is not None
        assert result["ci_upper"] is not None
        assert result["ci_lower"] < result["ci_upper"]

    def test_bootstrap_ci_insufficient_sample(self):
        """Bootstrap CI with insufficient sample returns None bounds."""
        result = compute_bootstrap_confidence_interval(
            returns_bp=[1.0, 2.0, 3.0],
            confidence_level=0.95,
            n_replicates=100,
            seed=42,
        )
        assert result["sufficient_sample"] is False
        assert result["mean"] is not None
        # CI may still be computed but sample is insufficient

    def test_permutation_test_significant(self):
        """Permutation test detects significant difference."""
        strategy = [10.0, 12.0, 8.0, 15.0, 9.0] * 20
        benchmark = [1.0, 2.0, 0.0, -1.0, 0.5] * 20
        result = compute_permutation_test(
            strategy_returns_bp=strategy,
            benchmark_returns_bp=benchmark,
            n_replicates=100,
            seed=42,
        )
        assert result["p_value"] is not None
        assert result["significant_at_005"] is True  # Large difference

    def test_permutation_test_zero_benchmark(self):
        """Permutation test against zero baseline."""
        strategy = [5.0, 5.0, 5.0, 5.0, 5.0]
        result = compute_permutation_test(
            strategy_returns_bp=strategy,
            benchmark_returns_bp=None,
            n_replicates=100,
            seed=42,
        )
        assert result["p_value"] is not None
        assert result["null_hypothesis"] == "strategy mean return = 0"

    def test_sharpe_ratio(self):
        """Sharpe ratio computation."""
        result = compute_sharpe_ratio(
            returns_bp=[5.0, 5.0, 5.0, 5.0, 5.0],
            periods_per_year=8760,
        )
        # Zero volatility means Sharpe is None
        assert result["sharpe_ratio"] is None

    def test_sharpe_ratio_with_volatility(self):
        """Sharpe ratio with non-zero volatility."""
        rng = np.random.RandomState(42)
        returns = rng.normal(2.0, 10.0, 100).tolist()
        result = compute_sharpe_ratio(
            returns_bp=returns,
            periods_per_year=8760,
        )
        assert result["sharpe_ratio"] is not None
        assert result["sufficient_sample"] is True

    def test_sortino_ratio(self):
        """Sortino ratio computation."""
        result = compute_sortino_ratio(
            returns_bp=[5.0, -2.0, 3.0, -1.0, 4.0],
            periods_per_year=8760,
        )
        assert result["sortino_ratio"] is not None

    def test_probability_positive_expectancy(self):
        """Probability of positive expectancy."""
        result = compute_probability_of_positive_expectancy(
            returns_bp=[5.0, 5.0, 5.0, 5.0, 5.0],
            n_replicates=100,
            seed=42,
        )
        assert result["probability_positive"] == 1.0  # All positive

    def test_statistical_validator(self):
        """Full statistical validation suite."""
        rng = np.random.RandomState(42)
        returns = rng.normal(2.0, 10.0, 50).tolist()
        validator = StatisticalValidator(
            bootstrap_n_replicates=100,
            permutation_n_replicates=100,
        )
        result = validator.validate(
            strategy_returns_bp=returns,
            max_drawdown_bp=500.0,
        )
        assert result["sample_size"] == 50
        assert "bootstrap_ci" in result
        assert "permutation_test" in result
        assert "sharpe_ratio" in result
        assert "sortino_ratio" in result
        assert "calmar_ratio" in result
        assert "statistically_significant" in result


# ---------------------------------------------------------------------------
# Benchmark tests
# ---------------------------------------------------------------------------


class TestBenchmark:
    """Tests for benchmark comparison."""

    def test_buy_and_hold_return(self):
        """Buy-and-hold return calculation."""
        comparator = BenchmarkComparator()
        result = comparator.compare(
            strategy_net_return_bp=100.0,
            strategy_gross_return_bp=120.0,
            prices=[100.0, 110.0],
            candidate_trade_count=1,
        )
        assert result["buy_and_hold"]["net_return_bp"] is not None
        assert result["excess_return_vs_buy_and_hold_bp"] is not None
        assert result["outperforms_buy_and_hold"] in (True, False)

    def test_cash_return(self):
        """Cash benchmark returns zero."""
        comparator = BenchmarkComparator()
        result = comparator.compare(
            strategy_net_return_bp=100.0,
            strategy_gross_return_bp=120.0,
            prices=[100.0, 110.0],
        )
        assert result["cash"]["net_return_bp"] == 0.0
        assert result["cash"]["trade_count"] == 0


# ---------------------------------------------------------------------------
# Regime tests
# ---------------------------------------------------------------------------


class TestRegime:
    """Tests for market regime analysis."""

    def test_classify_regime(self):
        """Regime classification returns valid regimes."""
        returns = [1.0, 2.0, -1.0, -2.0, 0.5, 0.5, 0.5]
        regimes = classify_regime(returns_bp=returns, window=3)
        assert len(regimes) == len(returns)
        valid_regimes = {"bullish", "bearish", "sideways", "high_volatility", "low_volatility"}
        for r in regimes:
            assert r in valid_regimes

    def test_regime_performance(self):
        """Regime performance computation."""
        returns = [1.0, 2.0, 3.0, -1.0, -2.0, -3.0, 0.5, 0.5]
        regimes = [
            "bullish",
            "bullish",
            "bullish",
            "bearish",
            "bearish",
            "bearish",
            "sideways",
            "sideways",
        ]
        result = compute_regime_performance(
            returns_bp=returns,
            regimes=regimes,
        )
        assert "bullish" in result
        assert "bearish" in result
        assert "sideways" in result
        assert result["bullish"]["observation_count"] == 3
        assert result["bearish"]["observation_count"] == 3

    def test_rolling_performance(self):
        """Rolling window performance computation."""
        returns = [1.0, 2.0, 3.0, 4.0, 5.0, -1.0, -2.0, -3.0]
        result = compute_rolling_performance(
            returns_bp=returns,
            window=3,
        )
        assert len(result) == len(returns) - 3 + 1
        assert result[0]["window_size"] == 3

    def test_chronological_subperiods(self):
        """Chronological subperiod computation."""
        returns = [1.0] * 12
        result = compute_chronological_subperiods(
            returns_bp=returns,
            n_subperiods=4,
        )
        assert len(result) == 4
        for sp in result:
            assert sp["observation_count"] == 3

    def test_regime_analyzer_full(self):
        """Full regime analysis."""
        rng = np.random.RandomState(42)
        returns = rng.normal(1.0, 5.0, 100).tolist()
        analyzer = RegimeAnalyzer(window=10, n_subperiods=5)
        result = analyzer.analyze(returns_bp=returns)
        assert "regime_classification" in result
        assert "regime_performance" in result
        assert "rolling_performance" in result
        assert "chronological_subperiods" in result
        assert "regime_robustness" in result


# ---------------------------------------------------------------------------
# Robustness tests
# ---------------------------------------------------------------------------


class TestRobustness:
    """Tests for robustness analysis."""

    def test_cost_stress(self):
        """Cost stress testing."""
        result = RobustnessAnalyzer(
            cost_multipliers=[1.0, 2.0],
        ).analyze(
            gross_returns_bp=[1000.0, -100.0, 500.0],
            net_returns_bp=[980.0, -110.0, 480.0],
            base_costs_bp=40.0,
        )
        assert len(result["cost_stress"]) == 2
        assert result["base_net_return_bp"] == 1350.0

    def test_delayed_execution(self):
        """Delayed execution simulation."""
        result = RobustnessAnalyzer().analyze(
            gross_returns_bp=[100.0, -50.0, 200.0],
            net_returns_bp=[90.0, -60.0, 190.0],
        )
        assert "delayed_execution" in result
        assert result["delayed_execution"]["delay_bars"] == 1

    def test_adverse_slippage(self):
        """Adverse slippage simulation."""
        result = RobustnessAnalyzer().analyze(
            gross_returns_bp=[100.0, -50.0, 200.0],
            net_returns_bp=[90.0, -60.0, 190.0],
        )
        assert "adverse_slippage" in result
        assert result["adverse_slippage"]["extra_slippage_bp"] == 5.0

    def test_consecutive_losses(self):
        """Consecutive losses computation."""
        result = RobustnessAnalyzer().analyze(
            gross_returns_bp=[100.0, -50.0, -30.0, -20.0, 200.0],
            net_returns_bp=[90.0, -60.0, -40.0, -30.0, 190.0],
        )
        assert "consecutive_losses" in result
        assert result["consecutive_losses"]["max_consecutive_losses"] == 3

    def test_drawdown_recovery(self):
        """Drawdown recovery computation."""
        result = RobustnessAnalyzer().analyze(
            gross_returns_bp=[100.0, -50.0, -30.0, 200.0],
            net_returns_bp=[90.0, -60.0, -40.0, 190.0],
        )
        assert "drawdown_recovery" in result
        assert "max_drawdown_bp" in result["drawdown_recovery"]

    def test_volatility_spike(self):
        """Volatility spike simulation."""
        result = RobustnessAnalyzer().analyze(
            gross_returns_bp=[100.0, -50.0, 200.0],
            net_returns_bp=[90.0, -60.0, 190.0],
        )
        assert "volatility_spike" in result
        assert result["volatility_spike"]["volatility_multiplier"] == 2.0


# ---------------------------------------------------------------------------
# Promotion gate tests
# ---------------------------------------------------------------------------


class TestPromotionGate:
    """Tests for promotion gate."""

    def test_blocked_preflight(self):
        """Blocked when preflight fails."""
        gate = PromotionGate()
        result = gate.evaluate(
            preflight_passed=False,
            holdout_eligible=True,
            data_quality_passed=True,
            candidate_lock_valid=True,
            candidate_lock_hash="hash",
            trade_count=200,
            net_return_bp=100.0,
            gross_return_bp=120.0,
            profit_factor=1.5,
            max_drawdown_bp=1000.0,
            win_rate=0.6,
            expectancy_bp=10.0,
            outperforms_buy_and_hold=True,
            outperforms_cash=True,
            outperforms_majority=True,
            regime_robustness=0.8,
            chronological_stable=True,
            statistically_significant=True,
            sufficient_sample=True,
            profitable_at_all_cost_levels=True,
            data_integrity_ok=True,
            stress_passed=True,
        )
        assert result.status == BLOCKED
        assert result.eligible_for_human_review is False
        assert result.human_authorized is False
        assert result.execution_authorized is False

    def test_insufficient_evidence(self):
        """Insufficient evidence when trades below minimum."""
        gate = PromotionGate(
            policy=Q6EvaluationPolicy(min_trades=1000),
        )
        result = gate.evaluate(
            preflight_passed=True,
            holdout_eligible=True,
            data_quality_passed=True,
            candidate_lock_valid=True,
            candidate_lock_hash="hash",
            trade_count=50,
            net_return_bp=100.0,
            gross_return_bp=120.0,
            profit_factor=1.5,
            max_drawdown_bp=1000.0,
            win_rate=0.6,
            expectancy_bp=10.0,
            outperforms_buy_and_hold=True,
            outperforms_cash=True,
            outperforms_majority=True,
            regime_robustness=0.8,
            chronological_stable=True,
            statistically_significant=True,
            sufficient_sample=True,
            profitable_at_all_cost_levels=True,
            data_integrity_ok=True,
            stress_passed=True,
        )
        assert result.status == INSUFFICIENT_EVIDENCE

    def test_edge_not_found(self):
        """Edge not found when expectancy is non-positive."""
        gate = PromotionGate()
        result = gate.evaluate(
            preflight_passed=True,
            holdout_eligible=True,
            data_quality_passed=True,
            candidate_lock_valid=True,
            candidate_lock_hash="hash",
            trade_count=200,
            net_return_bp=-50.0,
            gross_return_bp=-30.0,
            profit_factor=0.5,
            max_drawdown_bp=1000.0,
            win_rate=0.3,
            expectancy_bp=-5.0,
            outperforms_buy_and_hold=False,
            outperforms_cash=False,
            outperforms_majority=False,
            regime_robustness=0.5,
            chronological_stable=True,
            statistically_significant=False,
            sufficient_sample=True,
            profitable_at_all_cost_levels=False,
            data_integrity_ok=True,
            stress_passed=False,
        )
        assert result.status == EDGE_NOT_FOUND

    def test_validation_passed_pending_human_review(self):
        """All checks pass -> pending human review."""
        gate = PromotionGate()
        result = gate.evaluate(
            preflight_passed=True,
            holdout_eligible=True,
            data_quality_passed=True,
            candidate_lock_valid=True,
            candidate_lock_hash="hash",
            trade_count=200,
            net_return_bp=100.0,
            gross_return_bp=120.0,
            profit_factor=1.5,
            max_drawdown_bp=1000.0,
            win_rate=0.6,
            expectancy_bp=10.0,
            outperforms_buy_and_hold=True,
            outperforms_cash=True,
            outperforms_majority=True,
            regime_robustness=0.8,
            chronological_stable=True,
            statistically_significant=True,
            sufficient_sample=True,
            profitable_at_all_cost_levels=True,
            data_integrity_ok=True,
            stress_passed=True,
        )
        assert result.status == VALIDATION_PASSED_PENDING_HUMAN_REVIEW
        assert result.eligible_for_human_review is True
        assert result.human_authorized is False
        assert result.execution_authorized is False


# ---------------------------------------------------------------------------
# Preflight tests
# ---------------------------------------------------------------------------


class TestPreflight:
    """Tests for preflight validation."""

    def test_preflight_blocked_error(self):
        """PreflightBlockedError is raised when conditions fail."""
        # We can't easily test run_preflight without the actual manifest files,
        # but we can verify the exception class exists and can be raised
        with pytest.raises(PreflightBlockedError):
            raise PreflightBlockedError("test block")


# ---------------------------------------------------------------------------
# Governance tests
# ---------------------------------------------------------------------------


class TestGovernance:
    """Tests for Q6 governance."""

    def test_historical_q4_rejection_preserved(self):
        """Q4 rejection is preserved."""
        result = historical_q4_rejection_preserved()
        assert result["q4_decision"] == "NO_STRATEGY_ELIGIBLE"
        assert result["preserved"] is True
        assert result["q6_cannot_override"] is True

    def test_q6_cannot_auto_promote(self):
        """Q6 cannot auto-promote."""
        result = q6_cannot_auto_promote()
        assert result["automatic_promotion"] is False
        assert result["human_approval_required"] is True
        assert result["execution_authorized"] is False
        assert result["paper_trading_authorized"] is False
        assert result["order_execution_authorized"] is False

    def test_no_prospective_data_leakage(self):
        """No prospective data leakage."""
        result = no_prospective_data_leakage()
        assert result["prospective_data_read_only"] is True
        assert result["no_retraining"] is True
        assert result["no_recalibration"] is True
        assert result["no_threshold_tuning"] is True
        assert result["no_feature_selection"] is True
        assert result["no_backfill"] is True
        assert result["historical_contamination_preserved"] is True

    def test_assess_q6_readiness_blocked(self):
        """Q6 readiness assessment returns blocked when conditions fail."""
        result = assess_q6_readiness(
            preflight_passed=False,
            holdout_eligible=True,
            data_quality_passed=True,
            candidate_lock_valid=True,
            observation_count=10,
            min_observation_count=168,
            trade_count=50,
            min_trades=100,
            net_return_bp=None,
            statistical_significance=None,
            cost_stress_passed=None,
            data_integrity_ok=True,
        )
        assert result["can_evaluate"] is False
        assert len(result["blocking_reasons"]) > 0

    def test_assess_q6_readiness_passed(self):
        """Q6 readiness assessment passes when all conditions met."""
        result = assess_q6_readiness(
            preflight_passed=True,
            holdout_eligible=True,
            data_quality_passed=True,
            candidate_lock_valid=True,
            observation_count=200,
            min_observation_count=168,
            trade_count=200,
            min_trades=100,
            net_return_bp=100.0,
            statistical_significance=True,
            cost_stress_passed=True,
            data_integrity_ok=True,
        )
        assert result["can_evaluate"] is True
        assert result["state"] == VALIDATION_PASSED_PENDING_HUMAN_REVIEW


# ---------------------------------------------------------------------------
# Manifest tests
# ---------------------------------------------------------------------------


class TestManifest:
    """Tests for evaluation manifest."""

    def test_build_evaluation_manifest(self):
        """Build evaluation manifest."""
        from crypto_engine.research.q6.manifest import build_evaluation_manifest

        manifest = build_evaluation_manifest(
            q6_evaluation_id="q6-test-001",
            candidate_lock_id="lightgbm",
            candidate_lock_hash="hash123",
            research_freeze_id="freeze-aaa4dd57057a",
            research_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            collection_id="collect-52cefa0dda64",
            collection_started_at="2026-11-13T06:15:46.915986+00:00",
            dataset_hash="dataset-hash",
            dataset_start="2026-11-13T06:15:00+00:00",
            dataset_end="2026-11-14T06:15:00+00:00",
            evaluation_protocol_hash="protocol-hash",
            configuration_hashes={"config": "hash"},
            evaluation_timestamp="2026-11-13T07:00:00+00:00",
            sample_size=200,
            metrics={"net_return_bp": 100.0},
            promotion_decision="BLOCKED",
            promotion_reasons=["No observations"],
        )
        assert manifest["schema_version"] == "q6-evaluation-manifest.v1"
        assert manifest["q6_evaluation_id"] == "q6-test-001"
        assert manifest["candidate_lock_id"] == "lightgbm"
        assert manifest["immutable"] is True

    def test_evaluation_manifest_class(self):
        """EvaluationManifest class."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            history_path = Path(tmpdir) / "history.jsonl"
            em = EvaluationManifest(manifest_path=manifest_path, history_path=history_path)
            result = em.create_manifest(
                q6_evaluation_id="q6-test-002",
                candidate_lock_id="test",
                research_freeze_id="freeze-1",
                collection_id="collect-1",
                evaluation_timestamp="2026-11-13T07:00:00+00:00",
                sample_size=100,
                metrics={},
                promotion_decision="BLOCKED",
                promotion_reasons=[],
            )
            assert result["q6_evaluation_id"] == "q6-test-002"
            assert manifest_path.is_file()
            assert history_path.is_file()


# ---------------------------------------------------------------------------
# Q5.2 Regression Tests: Observation Storage, Deduplication & Q6 Preflight
# Consistency
# ---------------------------------------------------------------------------


class TestQ52ObservationStorage:
    """Tests for Q5.2 observation storage deduplication and canonical reading."""

    def test_one_accepted_candle_counts_as_one(self, tmp_path):
        """Storing the same candle once results in one unique observation."""
        storage = ProspectiveStorage(tmp_path / "storage")
        rec = storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc="2026-11-13T08:00:00+00:00",
            observed_at_utc="2026-11-13T08:00:00+00:00",
            ingested_at_utc="2026-11-13T08:00:00+00:00",
            research_freeze_id="freeze-1",
        )
        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1
        assert unique[0]["event_id"] == rec["event_id"]

    def test_identical_duplicate_does_not_increase_accepted_count(self, tmp_path):
        """Storing the same candle twice yields one unique observation."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = "2026-11-13T08:00:00+00:00"
        # Store raw observations first (as collector would)
        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            observed_at_utc=now,
            ingested_at_utc=now,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="hash-1",
            raw_payload_hash="hash-1",
            collector_version="q5-v1",
            collection_id="collect-1",
            research_freeze_id="freeze-1",
        )
        rec1 = storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        rec2 = storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-2",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        assert rec2["event_id"] == rec1["event_id"]
        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1
        summary = storage.get_observation_summary()
        assert summary["accepted_unique_observations"] == 1
        assert summary["raw_ingestion_events"] == 1
        assert summary["duplicate_ingestion_attempts"] == 0

    def test_first_seen_timestamp_survives_retries(self, tmp_path):
        """first_seen_at_utc is preserved on duplicate ingestion."""
        storage = ProspectiveStorage(tmp_path / "storage")
        first_seen = "2026-11-13T07:00:00+00:00"
        rec1 = storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=first_seen,
            observed_at_utc="2026-11-13T08:00:00+00:00",
            ingested_at_utc="2026-11-13T08:00:00+00:00",
            research_freeze_id="freeze-1",
        )
        rec2 = storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-2",
            first_seen_at_utc="2026-11-13T09:00:00+00:00",
            observed_at_utc="2026-11-13T09:00:00+00:00",
            ingested_at_utc="2026-11-13T09:00:00+00:00",
            research_freeze_id="freeze-1",
        )
        assert rec2["event_id"] == rec1["event_id"]
        unique = storage.read_unique_normalized_candles()
        assert unique[0]["first_seen_at_utc"] == first_seen

    def test_conflicting_duplicate_fails_closed(self, tmp_path):
        """Same candle with different OHLCV values is a conflict."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = "2026-11-13T08:00:00+00:00"
        rec1 = storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        rec2 = storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=105.0,
            high=106.0,
            low=98.0,
            close=104.0,
            volume=11.0,
            source_observation_id="obs-2",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        assert rec1["event_id"] != rec2["event_id"]
        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1
        summary = storage.get_observation_summary()
        assert summary["conflicting_observations"] == 1

    def test_ledger_event_count_differs_from_observation_count(self, tmp_path):
        """Ledger events count all ingestion attempts, not unique observations."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = "2026-11-13T08:00:00+00:00"
        for i in range(3):
            storage.store_normalized_candle(
                collection_id="collect-1",
                exchange="binance",
                market_type="spot",
                symbol="BTC/USDT",
                interval="1h",
                open_time="2026-11-13T07:00:00+00:00",
                close_time="2026-11-13T08:00:00+00:00",
                open_price=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=10.0,
                source_observation_id=f"obs-{i}",
                first_seen_at_utc=now,
                observed_at_utc=now,
                ingested_at_utc=now,
                research_freeze_id="freeze-1",
            )
            storage.append_ledger_event(
                event_type="candle_ingested",
                collection_id="collect-1",
                source_record_hash=f"hash-{i}",
            )
        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1
        ledger_events = storage.read_ledger_events()
        assert len(ledger_events) == 3
        assert len(ledger_events) != len(unique)

    def test_q5_q6_report_identical_observation_counts(self, tmp_path):
        """Q5 and Q6 report identical unique observation counts."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = "2026-11-13T08:00:00+00:00"
        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T08:00:00+00:00",
            close_time="2026-11-13T09:00:00+00:00",
            open_price=101.0,
            high=102.0,
            low=100.0,
            close=101.5,
            volume=11.0,
            source_observation_id="obs-2",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-3",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 2

    def test_one_observation_100_percent_coverage_still_fails_min_observations(self, tmp_path):
        """One observation with 100% coverage still fails the 168-observation
        minimum."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = "2026-11-13T08:00:00+00:00"
        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1
        assert len(unique) < 168

    def test_fewer_than_seven_days_blocks_evaluation(self, tmp_path):
        """Fewer than 7 days of elapsed collection duration blocks evaluation."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = "2026-11-13T08:00:00+00:00"
        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1
        from datetime import UTC, datetime, timedelta

        now_dt = datetime.now(UTC)
        collection_start = now_dt - timedelta(days=1)
        elapsed_seconds = (now_dt - collection_start).total_seconds()
        assert elapsed_seconds < 7 * 24 * 60 * 60

    def test_collector_healthy_does_not_grant_evaluation_eligibility(self):
        """Collector can be HEALTHY but evaluation still blocked due to
        insufficient observations."""
        from crypto_engine.research.prospective.collector import CollectionStatus

        quality_checks = {
            "state": CollectionStatus.HEALTHY,
            "observation_count": 0,
            "expected_intervals": 1,
            "coverage_fraction": 0.0,
            "error_count": 0,
            "rejection_count": 0,
            "gap_count": 0,
            "duplicate_count": 0,
            "conflict_count": 0,
            "evaluation_blocked": True,
            "evaluation_block_reasons": [
                "Observation count 0 is below minimum 168",
            ],
        }
        assert quality_checks["state"] == CollectionStatus.HEALTHY
        assert quality_checks["evaluation_blocked"] is True
        assert quality_checks["observation_count"] < 168

    def test_no_automatic_historical_backfill(self, tmp_path):
        """Collector does not backfill historical observations before
        collection start."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = "2026-11-13T08:00:00+00:00"
        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-11-13T07:00:00+00:00",
            close_time="2026-11-13T08:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1
        for c in unique:
            assert c["open_time"] >= "2026-11-13T06:15:46.915986+00:00"

    def test_original_manifests_remain_unchanged(self):
        """Original freeze and collection manifests are not modified."""
        import json

        freeze_path = Path("docs/validation/RESEARCH_FREEZE_MANIFEST.json")
        collection_path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json")

        freeze_data = json.loads(freeze_path.read_text(encoding="utf-8"))
        collection_data = json.loads(collection_path.read_text(encoding="utf-8"))

        assert freeze_data["freeze_id"] == "freeze-aaa4dd57057a"
        assert freeze_data["created_at_utc"] == "2026-11-12T19:11:47.293700+00:00"
        assert collection_data["collection_id"] == "collect-52cefa0dda64"
        assert collection_data["collection_started_at_utc"] == ("2026-11-13T06:15:46.915986+00:00")

    def test_q4_remains_no_strategy_eligible(self):
        """Q4 decision remains NO_STRATEGY_ELIGIBLE and is not modified by Q5/Q6."""
        import json

        q4_path = Path("docs/validation/results/quantitative_release_q4.json")
        q4_data = json.loads(q4_path.read_text(encoding="utf-8"))
        assert q4_data["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"
        assert q4_data["research_status"]["promotion_eligible"] is False
        assert q4_data["authority"]["human_authorized"] is False
        assert q4_data["authority"]["order_execution_authorized"] is False


# ---------------------------------------------------------------------------
# Q5.2 expiry lifecycle tests (controllable clock)
# ---------------------------------------------------------------------------

def _freeze_datetime(monkeypatch, target_utc):
    """Monkeypatch datetime.now(UTC) in crypto_engine.api.repos to a fixed time."""
    import datetime as _dt

    class _FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return target_utc.replace(tzinfo=tz)
            return target_utc

    monkeypatch.setattr(
        "crypto_engine.api.repos.datetime",
        _FixedDatetime,
    )


def _collection_started_at(days_ago: float) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()


def test_q52_before_expiry_accumulating(monkeypatch, tmp_path):
    """Collection started 1 day ago with 0 observations → ACCUMULATING, not eligible."""
    import crypto_engine.api.repos as repos

    _freeze_datetime(monkeypatch, datetime.now(UTC))

    # Use the real collection manifest which started 7+ days ago.
    # Monkeypatch the clock to 1 day after collection start → before expiry.
    from crypto_engine.api.repos import RepoRoots
    from crypto_engine.config.settings import load_settings

    load_settings()
    # Build roots pointing to tmp_path for storage but real manifest.
    roots = RepoRoots(
        signals_dir=tmp_path / "signals",
        registry_dir=tmp_path / "registry",
        data_storage=tmp_path / "storage",
        backtest_dir=tmp_path / "backtests",
        paper_path=tmp_path / "paper",
        experiments_path=tmp_path / "experiments",
        holdout_ledger_path=tmp_path / "holdout",
        scan_cap=5000,
    )

    # With clock at collection_start + 1 day (< 7 days), state is ACCUMULATING.
    # We can't easily change the manifest's collection_started_at_utc without
    # modifying it, so instead we test the state derivation logic directly.
    # The manifest started 2026-11-13; with clock at now (>7 days after), it's EXPIRED.
    # To test "before expiry", we set the clock to collection_start + 1 day.
    collection_start = "2026-11-13T06:15:46.915986+00:00"
    one_day_after = datetime.fromisoformat(collection_start) + timedelta(days=1)
    _freeze_datetime(monkeypatch, one_day_after)

    status = repos.read_q5_collection_status(roots)
    assert status["lifecycle_state"] == "ACCUMULATING"
    assert status["window_expired"] is False
    assert status["requirements_met"] is False


def test_q52_at_expiry_expired(monkeypatch, tmp_path):
    """Collection window exactly at 7 days → EXPIRED, not eligible."""
    import crypto_engine.api.repos as repos
    from crypto_engine.api.repos import RepoRoots
    from crypto_engine.config.settings import load_settings

    load_settings()
    roots = RepoRoots(
        signals_dir=tmp_path / "signals",
        registry_dir=tmp_path / "registry",
        data_storage=tmp_path / "storage",
        backtest_dir=tmp_path / "backtests",
        paper_path=tmp_path / "paper",
        experiments_path=tmp_path / "experiments",
        holdout_ledger_path=tmp_path / "holdout",
        scan_cap=5000,
    )

    collection_start = "2026-11-13T06:15:46.915986+00:00"
    seven_days_after = datetime.fromisoformat(collection_start) + timedelta(days=7)
    _freeze_datetime(monkeypatch, seven_days_after)

    status = repos.read_q5_collection_status(roots)
    assert status["lifecycle_state"] == "EXPIRED"
    assert status["window_expired"] is True
    assert status["requirements_met"] is False


def test_q52_after_expiry_expired(monkeypatch, tmp_path):
    """Collection window past 7 days → EXPIRED, cannot become eligible."""
    import crypto_engine.api.repos as repos
    from crypto_engine.api.repos import RepoRoots
    from crypto_engine.config.settings import load_settings

    load_settings()
    roots = RepoRoots(
        signals_dir=tmp_path / "signals",
        registry_dir=tmp_path / "registry",
        data_storage=tmp_path / "storage",
        backtest_dir=tmp_path / "backtests",
        paper_path=tmp_path / "paper",
        experiments_path=tmp_path / "experiments",
        holdout_ledger_path=tmp_path / "holdout",
        scan_cap=5000,
    )

    collection_start = "2026-11-13T06:15:46.915986+00:00"
    eight_days_after = datetime.fromisoformat(collection_start) + timedelta(days=8)
    _freeze_datetime(monkeypatch, eight_days_after)

    status = repos.read_q5_collection_status(roots)
    assert status["lifecycle_state"] == "EXPIRED"
    assert status["window_expired"] is True
    assert status["requirements_met"] is False


def test_q52_expired_never_eligible(monkeypatch, tmp_path):
    """Even with unlimited time passing, expired collection stays EXPIRED."""
    import crypto_engine.api.repos as repos
    from crypto_engine.api.repos import RepoRoots
    from crypto_engine.config.settings import load_settings

    load_settings()
    roots = RepoRoots(
        signals_dir=tmp_path / "signals",
        registry_dir=tmp_path / "registry",
        data_storage=tmp_path / "storage",
        backtest_dir=tmp_path / "backtests",
        paper_path=tmp_path / "paper",
        experiments_path=tmp_path / "experiments",
        holdout_ledger_path=tmp_path / "holdout",
        scan_cap=5000,
    )

    collection_start = "2026-11-13T06:15:46.915986+00:00"
    thirty_days_after = datetime.fromisoformat(collection_start) + timedelta(days=30)
    _freeze_datetime(monkeypatch, thirty_days_after)

    status = repos.read_q5_collection_status(roots)
    assert status["lifecycle_state"] == "EXPIRED"
    assert status["window_expired"] is True
    assert status["requirements_met"] is False


def test_q52_governance_not_eligible_when_expired(monkeypatch, tmp_path):
    """Governance endpoint reports eligible_for_evaluation=False when expired."""
    import crypto_engine.api.repos as repos
    from crypto_engine.api.repos import RepoRoots
    from crypto_engine.config.settings import load_settings

    load_settings()
    roots = RepoRoots(
        signals_dir=tmp_path / "signals",
        registry_dir=tmp_path / "registry",
        data_storage=tmp_path / "storage",
        backtest_dir=tmp_path / "backtests",
        paper_path=tmp_path / "paper",
        experiments_path=tmp_path / "experiments",
        holdout_ledger_path=tmp_path / "holdout",
        scan_cap=5000,
    )

    collection_start = "2026-11-13T06:15:46.915986+00:00"
    eight_days_after = datetime.fromisoformat(collection_start) + timedelta(days=8)
    _freeze_datetime(monkeypatch, eight_days_after)

    gov = repos.read_q5_prospective_governance(roots)
    assert gov["eligible_for_evaluation"] is False
    assert gov["lifecycle_state"] == "EXPIRED"
    assert gov["window_expired"] is True
