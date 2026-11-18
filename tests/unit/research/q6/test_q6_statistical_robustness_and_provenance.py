"""Unit tests for Q6 statistical robustness, provenance audit, dataset expansion, and governance verification.

All tests use synthetic fixtures and temp directories. No network access,
no real Binance API calls, no writes to docs/ or data/.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.research.q6.dataset_expansion import (  # noqa: E402
    DEFAULT_12M_STORAGE_ROOT,
    DatasetExpansionResult,
    TwelveMonthDatasetExpander,
)
from crypto_engine.research.q6.governance_verification import (  # noqa: E402
    REQUIRED_GOVERNANCE_FIELDS,
    GovernanceCheckResult,
    GovernanceVerifier,
    NamespaceCheckResult,
)
from crypto_engine.research.q6.statistical_robustness import (  # noqa: E402
    BLOCK_SIZE,
    BOOTSTRAP_SEED,
    COST_MULTIPLIERS,
    N_BOOTSTRAP_REPLICATES,
    N_PERMUTATION_REPLICATES,
    PERMUTATION_SEED,
    PT_MULT,
    SL_MULT,
    BenchmarkComparisonResult,
    BootstrapCIResult,
    CostSensitivityResult,
    PermutationTestResult,
    RegimeAnalysisResult,
    StatisticalRobustnessAnalyzer,
    StatisticalRobustnessResult,
    _block_bootstrap_ci,
    _buy_and_hold_return,
    _compute_realized_vol,
    _compute_trade_returns,
    _permutation_test,
    _sha256_json,
)
from crypto_engine.research.q6.temporal_provenance_audit import (  # noqa: E402
    ARTIFACTS_ROOT,
    TEMPORAL_PROVENANCE_UNVERIFIED,
    _audit_artifact,
    _parse_iso,
    audit_candidate_provenance,
    run_temporal_provenance_audit,
    write_audit_report,
)
from crypto_engine.research.q6.temporal_provenance_audit import (  # noqa: E402
    _sha256_json as tpa_sha256_json,
)

# ---- Fixtures ----


@pytest.fixture
def synthetic_candles():
    """100 synthetic hourly candles."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    price = 50000.0
    for i in range(100):
        t = base + timedelta(hours=i)
        price *= 1.0 + (0.001 if i % 2 == 0 else -0.001)
        candles.append(
            {
                "open_time": t.isoformat(),
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 10.0,
                "dataset_id": "test-dataset",
            }
        )
    return candles


@pytest.fixture
def synthetic_predictions():
    """Alternating long/short/abstain predictions."""
    return [1, -1, 0, 1, -1, 0] * 20  # 120 predictions


@pytest.fixture
def synthetic_decision_mask():
    """First 60 eligible, rest abstain."""
    return [True] * 60 + [False] * 60


# =====================================================================
# temporal_provenance_audit tests
# =====================================================================


def test_sha256_json_deterministic():
    assert _sha256_json({"a": 1}) == _sha256_json({"a": 1})
    assert _sha256_json({"a": 1}) != _sha256_json({"a": 2})


def test_tpa_sha256_json_deterministic():
    assert tpa_sha256_json({"x": 1}) == tpa_sha256_json({"x": 1})


def test_parse_iso_valid():
    dt = _parse_iso("2026-01-01T00:00:00+00:00")
    assert dt is not None
    assert dt.year == 2026


def test_parse_iso_invalid():
    assert _parse_iso("not-a-date") is None
    assert _parse_iso(None) is None


def test_audit_artifact_missing_dir(tmp_path):
    missing = tmp_path / "nonexistent"
    result = _audit_artifact(missing)
    assert result.artifact_exists is False
    assert len(result.errors) > 0


def test_audit_candidate_provenance_unverified_no_artifact(tmp_path):
    from crypto_engine.research.q6.temporal_provenance_audit import ArtifactAuditResult

    audit = ArtifactAuditResult(
        artifact_id="test",
        artifact_dir=str(tmp_path / "test"),
        artifact_exists=False,
        artifact_file=None,
        record_file=None,
        artifact_sha256_expected="",
        artifact_sha256_actual="",
        artifact_hash_match=False,
        record_exists=False,
        record_parsed=False,
        record=None,
        train_start=None,
        train_end=None,
        feature_names=[],
        feature_schema_hash="",
        label_hash="",
        dataset_snapshot_id="",
        dataset_checksum="",
        created_at=None,
        errors=["no artifact"],
    )
    spec = {
        "candidate_id": "test-cand",
        "model_name": "test",
        "feature_config_hash": "feat-hash",
        "target_config_hash": "tgt-hash",
    }
    result = audit_candidate_provenance(
        candidate_id="test-cand",
        model_name="test",
        spec=spec,
        artifact_audit=audit,
        eval_dataset_start_utc="2026-11-06T00:00:00+00:00",
        eval_dataset_end_utc="2026-11-13T00:00:00+00:00",
    )
    assert result.temporal_provenance_status == TEMPORAL_PROVENANCE_UNVERIFIED
    assert result.artifact_recoverable is False


def test_run_temporal_provenance_audit_synthetic():
    candidates = {
        "logistic": {
            "candidate_id": "logistic",
            "model_name": "logistic",
            "model_artifact_hash": "logistic-artifact",
            "model_config_hash": "cfg",
            "feature_config_hash": "feat",
            "target_config_hash": "tgt",
            "strategy_config_hash": "strat",
            "artifact_dir": str(ARTIFACTS_ROOT / "nonexistent"),
        },
    }
    report = run_temporal_provenance_audit(
        candidates=candidates,
        eval_dataset_start_utc="2026-11-06T00:00:00+00:00",
        eval_dataset_end_utc="2026-11-13T00:00:00+00:00",
    )
    assert report.n_candidates == 1
    assert report.overall_summary["n_unverified"] == 1
    d = report.to_dict()
    assert d["schema_version"] == "temporal_provenance_audit.v1"


def test_write_audit_report(tmp_path):
    candidates = {
        "logistic": {
            "candidate_id": "logistic",
            "model_name": "logistic",
            "model_artifact_hash": "x",
            "model_config_hash": "x",
            "feature_config_hash": "x",
            "target_config_hash": "x",
            "strategy_config_hash": "x",
        },
    }
    report = run_temporal_provenance_audit(candidates=candidates)
    out = tmp_path / "audit.json"
    write_audit_report(report, out)
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == "temporal_provenance_audit.v1"


# =====================================================================
# dataset_expansion tests
# =====================================================================


def test_dataset_expansion_constants():
    assert Path("data/historical_accelerated_12m") == DEFAULT_12M_STORAGE_ROOT
    assert COST_MULTIPLIERS == (1.0, 1.5, 2.0)


def test_dataset_expansion_result_to_dict():
    r = DatasetExpansionResult(
        dataset_id="test",
        storage_root=Path("/tmp/test"),
        start_utc="2026-01-01",
        end_utc="2026-12-31",
        months_requested=12,
        observation_count=8760,
        expected_intervals=8760,
        coverage_fraction=1.0,
        duplicate_count=0,
        conflicting_count=0,
        missing_interval_count=0,
        dataset_hash="abc",
        actual_first_candle_utc="2026-01-01",
        actual_last_candle_utc="2026-12-31",
        retrieval_timestamp="2026-01-02",
        manifest_written=True,
        errors=[],
        warnings=[],
        candidate_valid_windows={},
    )
    d = r.to_dict()
    assert d["dataset_id"] == "test"
    assert d["observation_count"] == 8760


def test_twelve_month_dataset_expander_init(tmp_path):
    expander = TwelveMonthDatasetExpander(storage_root=tmp_path / "12m")
    assert expander.storage_root == tmp_path / "12m"


# =====================================================================
# statistical_robustness tests
# =====================================================================


def test_sha256_json_deterministic_sr():
    assert _sha256_json({"a": 1}) == _sha256_json({"a": 1})


def test_compute_realized_vol_insufficient_data(synthetic_candles):
    closes = np.array([float(c["close"]) for c in synthetic_candles])
    vol = _compute_realized_vol(closes, idx=5, window=20)
    assert vol == 1e-8


def test_compute_trade_returns_basic(
    synthetic_candles, synthetic_predictions, synthetic_decision_mask
):
    returns = _compute_trade_returns(
        synthetic_candles,
        synthetic_predictions,
        synthetic_decision_mask,
        fee_bp=10.0,
        spread_bp=5.0,
        slippage_bp=5.0,
    )
    assert len(returns) == 40  # only non-zero predictions generate trades
    assert all(isinstance(r, float) for r in returns)


def test_block_bootstrap_ci_empty():
    result = _block_bootstrap_ci([], seed=42, n_replicates=10, block_size=5)
    assert result.n_replicates == 10
    assert result.block_size == 5
    assert result.p_value_zero == 1.0


def test_block_bootstrap_ci_nonempty():
    returns = [1.0, -2.0, 3.0, -1.0, 0.5] * 10
    result = _block_bootstrap_ci(returns, seed=42, n_replicates=50, block_size=5)
    assert isinstance(result.mean_return_bp, float)
    assert result.ci_lower_bp <= result.ci_upper_bp
    assert 0.0 <= result.p_value_zero <= 1.0


def test_permutation_test_empty():
    result = _permutation_test([], seed=42, n_replicates=10)
    assert result.p_value == 1.0


def test_permutation_test_nonempty():
    returns = [1.0, -2.0, 3.0, -1.0, 0.5] * 10
    result = _permutation_test(returns, seed=42, n_replicates=50)
    assert isinstance(result.observed_mean_bp, float)
    assert 0.0 <= result.p_value <= 1.0


def test_buy_and_hold_return(synthetic_candles):
    ret = _buy_and_hold_return(synthetic_candles)
    assert isinstance(ret, float)


def test_buy_and_hold_return_single():
    candles = [{"close": 100.0}]
    assert _buy_and_hold_return(candles) == 0.0


def test_bootstrap_ci_result_to_dict():
    r = BootstrapCIResult(
        mean_return_bp=-10.0,
        ci_lower_bp=-20.0,
        ci_upper_bp=0.0,
        n_replicates=100,
        block_size=24,
        seed=42,
        p_value_zero=0.5,
    )
    d = r.to_dict()
    assert d["mean_return_bp"] == -10.0
    assert d["ci_lower_bp"] == -20.0


def test_permutation_result_to_dict():
    r = PermutationTestResult(
        observed_mean_bp=5.0,
        p_value=0.03,
        n_replicates=100,
        seed=42,
        null_mean_bp=0.0,
    )
    d = r.to_dict()
    assert d["observed_mean_bp"] == 5.0
    assert d["p_value"] == 0.03


def test_regime_result_to_dict():
    r = RegimeAnalysisResult(
        regime_periods=[],
        regime_robustness=0.0,
    )
    d = r.to_dict()
    assert d["regime_periods"] == []
    assert d["regime_robustness"] == 0.0


def test_cost_sensitivity_result_to_dict():
    r = CostSensitivityResult(
        cost_multiplier=1.0,
        net_return_bp=-100.0,
        trade_count=10,
        profitable=False,
    )
    d = r.to_dict()
    assert d["cost_multiplier"] == 1.0
    assert d["profitable"] is False


def test_benchmark_result_to_dict():
    r = BenchmarkComparisonResult(
        candidate_net_return_bp=-500.0,
        buy_hold_net_return_bp=-3000.0,
        outperforms_buy_hold=True,
        outperforms_cash=False,
    )
    d = r.to_dict()
    assert d["outperforms_buy_hold"] is True
    assert d["outperforms_cash"] is False


def test_statistical_robustness_result_to_dict():
    r = StatisticalRobustnessResult(
        candidate_id="test",
        model_name="test",
        temporal_provenance_status=TEMPORAL_PROVENANCE_UNVERIFIED,
        status="SKIPPED_UNVERIFIED",
        bootstrap_ci=None,
        permutation_test=None,
        regime_analysis=None,
        cost_sensitivity=[],
        benchmark_comparison=None,
    )
    d = r.to_dict()
    assert d["status"] == "SKIPPED_UNVERIFIED"
    assert d["bootstrap_ci"] is None


def test_analyze_candidate_unverified_skipped(
    synthetic_candles, synthetic_predictions, synthetic_decision_mask
):
    analyzer = StatisticalRobustnessAnalyzer(seed=42)
    result = analyzer.analyze_candidate(
        candidate_id="test",
        model_name="test",
        predictions=synthetic_predictions,
        decision_mask=synthetic_decision_mask,
        candles=synthetic_candles,
        temporal_provenance_status=TEMPORAL_PROVENANCE_UNVERIFIED,
    )
    assert result.status == "SKIPPED_UNVERIFIED"
    assert result.bootstrap_ci is None
    assert result.permutation_test is None
    assert result.regime_analysis is None
    assert result.cost_sensitivity == []


def test_constants_values():
    assert PT_MULT == 2.0
    assert SL_MULT == 1.0
    assert BLOCK_SIZE == 24
    assert N_BOOTSTRAP_REPLICATES == 1000
    assert N_PERMUTATION_REPLICATES == 1000
    assert BOOTSTRAP_SEED == 42
    assert PERMUTATION_SEED == 42


# =====================================================================
# governance_verification tests
# =====================================================================


def test_required_governance_fields():
    assert REQUIRED_GOVERNANCE_FIELDS["official_prospective_evidence"] is False
    assert REQUIRED_GOVERNANCE_FIELDS["q5_2_eligible"] is False
    assert REQUIRED_GOVERNANCE_FIELDS["promotion_eligible"] is False
    assert REQUIRED_GOVERNANCE_FIELDS["q4_decision_preserved"] is True
    assert REQUIRED_GOVERNANCE_FIELDS["q4_decision"] == "NO_STRATEGY_ELIGIBLE"
    assert REQUIRED_GOVERNANCE_FIELDS["promotion_gate"] == "BLOCKED"


def test_governance_check_result_to_dict():
    r = GovernanceCheckResult(
        file_path="test.json",
        compliant=True,
        missing_fields=[],
        mismatched_fields={},
        extra_fields=[],
        details={},
    )
    d = r.to_dict()
    assert d["file_path"] == "test.json"
    assert d["compliant"] is True


def test_namespace_check_result_to_dict():
    r = NamespaceCheckResult(
        namespace="historical_accelerated",
        isolated=True,
        violations=[],
        details={},
    )
    d = r.to_dict()
    assert d["namespace"] == "historical_accelerated"
    assert d["isolated"] is True


def test_governance_verifier_init(tmp_path):
    verifier = GovernanceVerifier(results_root=tmp_path / "results")
    assert verifier.results_root == tmp_path / "results"
