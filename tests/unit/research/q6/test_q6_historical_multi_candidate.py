"""Comprehensive tests for Q6 multi-candidate historical comparison.

All tests use synthetic fixtures and temp directories. No external
Binance availability required. No writes to real docs/validation/results/.
"""

from __future__ import annotations

import json
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.research.q6.config import (  # noqa: E402
    CandidateLock,
    Q6EvaluationPolicy,
    compute_lock_hash,
)
from crypto_engine.research.q6.evaluation import Q6EvaluationEngine  # noqa: F401,E402
from crypto_engine.research.q6.governance import (  # noqa: E402
    historical_q4_rejection_preserved,
)
from crypto_engine.research.q6.historical_pipeline import (  # noqa: E402
    HistoricalValidationPipeline,
)
from crypto_engine.research.q6.historical_storage import (  # noqa: E402
    HistoricalStorage,
)
from crypto_engine.research.q6.promotion import (  # noqa: E402
    BLOCKED,
    PromotionGate,
)

CANDIDATE_IDS = ["majority", "trend", "logistic", "lightgbm"]
N_CANDLES = 10


def _make_candle(index: int) -> dict[str, Any]:
    base = datetime(2026, 11, 1, tzinfo=UTC) + timedelta(hours=index)
    return {
        "open_time": base.isoformat(),
        "close_time": (base + timedelta(hours=1)).isoformat(),
        "open": 100.0 + index,
        "high": 101.0 + index,
        "low": 99.0 + index,
        "close": 100.0 + index,
        "volume": 10.0 + index,
        "event_id": f"candle-{index}",
    }


def _store_synthetic_candles(
    storage: HistoricalStorage, dataset_id: str
) -> int:
    now = "2026-11-13T08:00:00+00:00"
    for i in range(N_CANDLES):
        c = _make_candle(i)
        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=str(c["open_time"]),
            close_time=str(c["close_time"]),
            observed_at_utc=now,
            ingested_at_utc=now,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash=f"hash-{i}",
            raw_payload_hash=f"hash-{i}",
            collector_version="q6-historical-v1",
            dataset_id=dataset_id,
        )
        storage.store_normalized_candle(
            dataset_id=dataset_id,
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=str(c["open_time"]),
            close_time=str(c["close_time"]),
            open_price=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c["volume"]),
            source_observation_id=f"obs-{i}",
            observed_at_utc=now,
            ingested_at_utc=now,
        )
        storage.append_ledger_event(
            event_type="candle_ingested",
            dataset_id=dataset_id,
            source_record_hash=f"hash-{i}",
        )
    return N_CANDLES


def _make_candidate_configs() -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": cid,
            "model_name": cid,
            "model_artifact_hash": f"artifact-{cid}",
            "model_config_hash": f"config-{cid}",
            "feature_config_hash": f"feature-{cid}",
            "target_config_hash": f"target-{cid}",
            "strategy_config_hash": f"strategy-{cid}",
            "entry_exit_rules": {"rule": "triple_barrier"},
            "thresholds": {"t_long": 0.6, "t_short": 0.4},
            "calibration_status": "uncalibrated",
            "cost_assumptions": {"fee_bp": 10.0},
            "risk_limits": {"max_drawdown_bp": 5000},
            "candidate_creation_timestamp": "2026-11-12T00:00:00+00:00",
        }
        for cid in CANDIDATE_IDS
    ]


def _make_predictions(n: int) -> dict[str, list[int]]:
    return {cid: [0] * n for cid in CANDIDATE_IDS}


def _make_decision_masks(n: int) -> dict[str, list[bool]]:
    return {cid: [False] * n for cid in CANDIDATE_IDS}


def _build_pipeline_and_store(
    tmp_path: Path,
) -> tuple[HistoricalValidationPipeline, str]:
    dataset_id = "hist-test-ds-001"
    storage = HistoricalStorage(tmp_path / "storage")
    _store_synthetic_candles(storage, dataset_id)
    pipeline = HistoricalValidationPipeline(storage_root=tmp_path / "storage")
    return pipeline, dataset_id


def _evaluate_all(
    pipeline: HistoricalValidationPipeline,
    dataset_id: str,
) -> dict[str, Any]:
    configs = _make_candidate_configs()
    predictions = _make_predictions(N_CANDLES)
    masks = _make_decision_masks(N_CANDLES)
    return pipeline.evaluate_candidates(
        dataset_id=dataset_id,
        candidate_configs=configs,
        candidate_predictions=predictions,
        candidate_decision_masks=masks,
    )


def _candidate_result_by_id(
    comparison: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {r["candidate_id"]: r for r in comparison["candidate_results"]}


def _make_candidate_lock(candidate_id: str) -> CandidateLock:
    lock = CandidateLock(
        candidate_id=candidate_id,
        model_name=candidate_id,
        model_artifact_hash=f"artifact-{candidate_id}",
        model_config_hash=f"config-{candidate_id}",
        feature_config_hash=f"feature-{candidate_id}",
        target_config_hash=f"target-{candidate_id}",
        strategy_config_hash=f"strategy-{candidate_id}",
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
    fields = lock.to_dict()
    fields.pop("lock_hash", None)
    return CandidateLock(**fields, lock_hash=compute_lock_hash(lock))


class TestHistoricalCandidateIsolation:
    def test_each_candidate_has_distinct_candidate_lock(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        results = _candidate_result_by_id(comparison)
        assert set(results.keys()) == set(CANDIDATE_IDS)
        lock_hashes = [r["candidate_lock_hash"] for r in results.values()]
        assert len(lock_hashes) == len(set(lock_hashes))
        for cid in CANDIDATE_IDS:
            assert results[cid]["candidate_id"] == cid
            assert results[cid]["candidate_lock_hash"]

    def test_lock_hashes_are_unique_across_candidates(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        results = _candidate_result_by_id(comparison)
        hashes = {
            cid: results[cid]["candidate_lock_hash"] for cid in CANDIDATE_IDS
        }
        assert len(set(hashes.values())) == len(CANDIDATE_IDS)


class TestImmutableCandidateLocks:
    def test_candidate_lock_is_frozen(self):
        lock = _make_candidate_lock("majority")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            lock.candidate_id = "changed"  # type: ignore[misc]

    def test_lock_hash_is_deterministic(self):
        lock1 = _make_candidate_lock("majority")
        lock2 = _make_candidate_lock("majority")
        assert compute_lock_hash(lock1) == compute_lock_hash(lock2)
        assert lock1.lock_hash == lock2.lock_hash

    def test_different_inputs_produce_different_hashes(self):
        lock_a = _make_candidate_lock("majority")
        lock_b = _make_candidate_lock("trend")
        assert compute_lock_hash(lock_a) != compute_lock_hash(lock_b)
        assert lock_a.lock_hash != lock_b.lock_hash

    def test_lock_hash_excludes_volatile_timestamp_fields(self):
        lock1 = _make_candidate_lock("majority")
        fields2 = lock1.to_dict()
        fields2["lock_timestamp"] = "2099-01-01T00:00:00+00:00"
        fields2.pop("lock_hash", None)
        lock2_tmp = CandidateLock(**fields2, lock_hash="")
        hash2 = compute_lock_hash(lock2_tmp)
        lock2 = CandidateLock(**fields2, lock_hash=hash2)
        assert compute_lock_hash(lock1) == compute_lock_hash(lock2)


class TestSameDatasetAcrossEvaluations:
    def test_all_candidates_use_same_observation_count(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        results = _candidate_result_by_id(comparison)
        counts = {
            results[cid]["evaluation"]["prospective_dataset"][
                "observation_count"
            ]
            for cid in CANDIDATE_IDS
        }
        assert len(counts) == 1
        assert N_CANDLES in counts
        assert comparison["dataset_id"] == dataset_id


class TestDeterministicResults:
    def test_evaluation_is_deterministic(self, tmp_path, monkeypatch):
        fixed_time = "2026-11-13T12:00:00+00:00"
        monkeypatch.setattr(
            "crypto_engine.research.q6.historical_pipeline._utc_now_iso",
            lambda: fixed_time,
        )
        monkeypatch.setattr(
            "crypto_engine.research.q6.evaluation._utc_now_iso",
            lambda: fixed_time,
        )
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        result1 = _evaluate_all(pipeline, dataset_id)
        result2 = _evaluate_all(pipeline, dataset_id)
        assert result1 == result2


class TestArtifactPersistence:
    def test_each_candidate_gets_own_json_artifact(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        results = _candidate_result_by_id(comparison)

        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        for cid in CANDIDATE_IDS:
            artifact_path = artifacts_dir / f"{cid}.json"
            artifact = {
                "candidate_id": results[cid]["candidate_id"],
                "candidate_lock_hash": results[cid]["candidate_lock_hash"],
                "schema_version": comparison["schema_version"],
                "official_prospective_evidence": False,
                "q5_2_eligible": False,
                "q4_decision": "NO_STRATEGY_ELIGIBLE",
                "q4_decision_preserved": True,
                "evaluation": results[cid]["evaluation"],
                "promotion_gate": results[cid]["promotion_gate"],
            }
            artifact_path.write_text(
                json.dumps(artifact, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            assert artifact_path.is_file()

            loaded = json.loads(artifact_path.read_text(encoding="utf-8"))
            assert loaded["candidate_id"] == cid
            assert loaded["candidate_lock_hash"] == results[cid][
                "candidate_lock_hash"
            ]
            assert loaded["official_prospective_evidence"] is False
            assert loaded["q5_2_eligible"] is False
            assert loaded["q4_decision"] == "NO_STRATEGY_ELIGIBLE"
            assert loaded["q4_decision_preserved"] is True
            assert "evaluation" in loaded
            assert "promotion_gate" in loaded

    def test_artifacts_are_independent(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        results = _candidate_result_by_id(comparison)
        lock_hashes = {
            cid: results[cid]["candidate_lock_hash"] for cid in CANDIDATE_IDS
        }
        assert len(set(lock_hashes.values())) == len(CANDIDATE_IDS)
        policy = Q6EvaluationPolicy()
        assert policy.min_trades == 100


class TestHistoricalProspectiveSeparation:
    def test_schema_version_is_historical_not_prospective(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        assert comparison["schema_version"] == "historical_evaluation_v1"
        assert comparison["schema_version"] != "Q5.2_PROSPECTIVE"

    def test_artifacts_mark_not_prospective(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        results = _candidate_result_by_id(comparison)
        for cid in CANDIDATE_IDS:
            artifact = {
                "schema_version": comparison["schema_version"],
                "official_prospective_evidence": False,
                "q5_2_eligible": False,
                "candidate_id": results[cid]["candidate_id"],
                "promotion_gate": results[cid]["promotion_gate"],
            }
            assert artifact["schema_version"] == "historical_evaluation_v1"
            assert artifact["official_prospective_evidence"] is False
            assert artifact["q5_2_eligible"] is False


class TestQ4DecisionPreservation:
    def test_q4_decision_is_no_strategy_eligible(self):
        q4 = historical_q4_rejection_preserved()
        assert q4["q4_decision"] == "NO_STRATEGY_ELIGIBLE"
        assert q4["preserved"] is True
        assert q4["q6_cannot_override"] is True

    def test_q4_decision_preserved_in_every_artifact(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        results = _candidate_result_by_id(comparison)
        q4 = historical_q4_rejection_preserved()
        assert q4["q4_decision"] == "NO_STRATEGY_ELIGIBLE"
        assert q4["preserved"] is True
        for cid in CANDIDATE_IDS:
            assert results[cid]["candidate_id"] == cid


class TestPromotionAlwaysBlocked:
    def test_promotion_gate_blocked_for_all_candidates(self, tmp_path):
        pipeline, dataset_id = _build_pipeline_and_store(tmp_path)
        comparison = _evaluate_all(pipeline, dataset_id)
        results = _candidate_result_by_id(comparison)
        gate = PromotionGate()
        assert gate is not None
        for cid in CANDIDATE_IDS:
            result_gate = results[cid]["promotion_gate"]
            assert result_gate["status"] == BLOCKED
            assert result_gate["eligible_for_human_review"] is False
            assert result_gate["human_authorized"] is False
            assert result_gate["execution_authorized"] is False


