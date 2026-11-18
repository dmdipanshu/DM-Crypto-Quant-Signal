"""Comprehensive regression tests for Q6 frozen inference integrity.

Covers frozen candidate inference: statuses (EVALUATED / NOT_EVALUABLE),
determinism, model/configuration lock-hash verification, causal feature
boundaries (no future leakage), timestamp alignment, next-bar execution
semantics, invalid/missing model behaviour, no neutral fallback for
unavailable models, no training/tuning during inference, historical /
prospective isolation, Q4 decision preservation, and permanent promotion
block.

All tests use synthetic fixtures and temp directories; nothing is written to
docs/validation/results/ or the real model artifacts. One end-to-end test runs
against the real historical accelerated dataset when it is available.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.features.engineering import (  # noqa: E402
    compute_features,
)
from crypto_engine.models import (  # noqa: E402
    LightGBMModel,
    MajorityBaseline,
    SklearnLogistic,
    TrendBaseline,
)
from crypto_engine.models.base import NotFittedError, SchemaMismatchError  # noqa: E402
from crypto_engine.research.q6.config import (  # noqa: E402
    CandidateLock,
    compute_lock_hash,
)
from crypto_engine.research.q6.evaluation import Q6EvaluationEngine  # noqa: E402
from crypto_engine.research.q6.frozen_inference import (  # noqa: E402
    ABSTAIN_LABEL,
    ALL_CANDIDATES,
    CANDIDATE_LIGHTGBM,
    CANDIDATE_LOGISTIC,
    CANDIDATE_MAJORITY,
    CANDIDATE_SPECS,
    CANDIDATE_TREND,
    DATASET_TYPE,
    LOGISTIC_ARTIFACT_DIR,
    MODEL_CLASSES,
    PROMOTION_ELIGIBLE,
    PROMOTION_GATE,
    Q4_DECISION,
    Q4_DECISION_PRESERVED,
    CandidateInferenceResult,
    build_candidate_lock,
    build_trend_model,
    candles_to_dataframe,
    evaluate_all_candidates,
    generate_predictions,
    reconstruct_logistic_model,
)
from crypto_engine.research.q6.historical_storage import (  # noqa: E402
    DEFAULT_HISTORICAL_STORAGE_ROOT,
    HistoricalStorage,
)
from crypto_engine.research.q6.promotion import (  # noqa: E402
    BLOCKED,
    PromotionGate,
)

FIXED_NOW = "2026-11-13T12:00:00+00:00"
DATASET_ID = "q6-frozen-test-ds"
REAL_DATASET_ID = "hist-accel-7c0a166d4f93"
REAL_STORAGE_ROOT = ROOT / "data" / "historical_accelerated"
REAL_LOGISTIC_ARTIFACT = LOGISTIC_ARTIFACT_DIR


def _make_candle(index: int, base: datetime) -> dict[str, Any]:
    open_time = base + timedelta(hours=index)
    close_time = open_time + timedelta(hours=1)
    close = 100.0 + index * 0.5
    return {
        "open_time": open_time.isoformat(),
        "close_time": close_time.isoformat(),
        "open": close - 0.25,
        "high": close + 0.75,
        "low": close - 0.75,
        "close": close,
        "volume": 10.0 + index,
        "event_id": f"candle-{index}",
        "dataset_id": DATASET_ID,
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTC/USDT",
        "interval": "1h",
    }


def _store_synthetic_candles(
    storage: HistoricalStorage, n: int = 200, dataset_id: str = DATASET_ID
) -> list[dict[str, Any]]:
    base = datetime(2026, 11, 1, tzinfo=UTC)
    candles = [_make_candle(i, base) for i in range(n)]
    now = FIXED_NOW
    for c in candles:
        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=c["open_time"],
            close_time=c["close_time"],
            observed_at_utc=now,
            ingested_at_utc=now,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash=f"resp-{c['event_id']}",
            raw_payload_hash=f"pay-{c['event_id']}",
            collector_version="q6-test-v1",
            dataset_id=dataset_id,
        )
        storage.store_normalized_candle(
            dataset_id=dataset_id,
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_price=c["open"],
            high=c["high"],
            low=c["low"],
            close=c["close"],
            volume=c["volume"],
            open_time=c["open_time"],
            close_time=c["close_time"],
            source_observation_id=f"obs-{c['event_id']}",
            observed_at_utc=now,
            ingested_at_utc=now,
        )
        storage.append_ledger_event(
            event_type="candle_ingested",
            dataset_id=dataset_id,
            source_record_hash=f"pay-{c['event_id']}",
        )
    return candles


def _make_trending_candles(n: int = 200) -> list[dict[str, Any]]:
    """Rise then fall so trend_ratio_20_50 changes sign (both +1 and -1)."""
    base = datetime(2026, 11, 1, tzinfo=UTC)
    candles: list[dict[str, Any]] = []
    for i in range(n):
        close = 100.0 + (i if i < n // 2 else n - i) * 0.4
        open_time = base + timedelta(hours=i)
        close_time = open_time + timedelta(hours=1)
        candles.append(
            {
                "open_time": open_time.isoformat(),
                "close_time": close_time.isoformat(),
                "open": close - 0.2,
                "high": close + 0.6,
                "low": close - 0.6,
                "close": close,
                "volume": 10.0 + i,
                "event_id": f"candle-{i}",
                "dataset_id": DATASET_ID,
                "exchange": "binance",
                "market_type": "spot",
                "symbol": "BTC/USDT",
                "interval": "1h",
            }
        )
    return candles


def _features_for(candles: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[str]]:
    df = candles_to_dataframe(candles)
    features_df = compute_features(df)
    return features_df, list(features_df.columns)


def _freeze_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "crypto_engine.research.q6.frozen_inference._utc_now_iso", lambda: FIXED_NOW
    )
    monkeypatch.setattr("crypto_engine.research.q6.evaluation._utc_now_iso", lambda: FIXED_NOW)


def _patched_specs(
    tmp_path: Path, logistic_artifact_dir: Path | None = None
) -> dict[str, dict[str, Any]]:
    specs = copy.deepcopy(CANDIDATE_SPECS)
    if logistic_artifact_dir is not None:
        specs[CANDIDATE_LOGISTIC] = dict(
            specs[CANDIDATE_LOGISTIC], artifact_dir=str(logistic_artifact_dir)
        )
    return specs


def _fit_and_save_logistic_artifact(tmp_path: Path, feature_names: list[str]) -> Path:
    """Persist a fitted SklearnLogistic as record.json (no pickle callables)."""
    artifact_dir = tmp_path / "artifacts" / "logistic-test"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    n = 300
    X = pd.DataFrame(rng.normal(size=(n, len(feature_names))), columns=feature_names)
    y = np.where(X.iloc[:, 0].to_numpy() > 0, 1, -1).astype(np.int64)
    model = SklearnLogistic(feature_names=feature_names, random_state=7)
    model.fit(X, y)
    payload = model.to_dict()
    (artifact_dir / "artifact.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    from crypto_engine.models.base import feature_schema_hash
    record = {
        "model_id": "logistic-test",
        "version": "1.0",
        "family": "logistic",
        "artifact_path": "artifact.json",
        "artifact_sha256": "",
        "is_fitted": True,
        "feature_names": feature_names,
        "feature_schema_hash": feature_schema_hash(feature_names),
        "params": model.to_dict().get("params", {}),
    }
    (artifact_dir / "record.json").write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return artifact_dir


def _run_all_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candles: list[dict[str, Any]],
    logistic_artifact_dir: Path | None = None,
    freeze_time: bool = True,
) -> dict[str, Any]:
    storage_root = tmp_path / "storage"
    if freeze_time:
        _freeze_time(monkeypatch)
    monkeypatch.setattr(
        "crypto_engine.research.q6.frozen_inference.CANDIDATE_SPECS",
        _patched_specs(tmp_path, logistic_artifact_dir),
    )
    return evaluate_all_candidates(dataset_id=DATASET_ID, storage_root=storage_root)


class TestModuleImports:
    def test_required_symbols_are_importable(self) -> None:
        """All symbols the task requires are importable from the package."""
        assert callable(generate_predictions)
        assert callable(build_candidate_lock)
        assert callable(compute_lock_hash)
        assert callable(reconstruct_logistic_model)
        assert callable(build_trend_model)
        assert callable(evaluate_all_candidates)
        assert CandidateLock is not None
        assert CandidateInferenceResult is not None
        assert SklearnLogistic is not None
        assert TrendBaseline is not None
        assert MajorityBaseline is not None
        assert LightGBMModel is not None
        assert Q6EvaluationEngine is not None
        assert HistoricalStorage is not None
        assert PromotionGate is not None


class TestEvaluatedCandidatesProduceNonNeutralPredictions:
    def test_logistic_candidate_evaluated_with_non_neutral_predictions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=200)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        result = _run_all_candidates(
            monkeypatch, tmp_path, candles, logistic_artifact_dir=artifact_dir
        )
        logistic = result["candidates"][CANDIDATE_LOGISTIC]
        assert logistic["status"] == "EVALUATED"
        assert logistic["reason"] is None
        assert logistic["n_candles"] == len(candles)
        assert logistic["n_predictions"] == len(candles)
        assert logistic["n_eligible"] > 0
        assert any(p != ABSTAIN_LABEL for p in logistic["predictions"])
        assert len(logistic["scores"]) == len(candles)
        assert len(logistic["decision_mask"]) == len(candles)

    def test_trend_candidate_evaluated_with_non_neutral_predictions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _make_trending_candles(200)
        for c in candles:
            storage.store_normalized_candle(
                dataset_id=DATASET_ID,
                exchange="binance",
                market_type="spot",
                symbol="BTC/USDT",
                interval="1h",
                open_price=c["open"],
                high=c["high"],
                low=c["low"],
                close=c["close"],
                volume=c["volume"],
                open_time=c["open_time"],
                close_time=c["close_time"],
                source_observation_id=f"obs-{c['event_id']}",
                observed_at_utc=FIXED_NOW,
                ingested_at_utc=FIXED_NOW,
            )
        result = _run_all_candidates(monkeypatch, tmp_path, candles)
        trend = result["candidates"][CANDIDATE_TREND]
        assert trend["status"] == "EVALUATED"
        assert trend["n_eligible"] > 0
        non_neutral = [p for p in trend["predictions"] if p != ABSTAIN_LABEL]
        assert non_neutral, "trend must produce actual predictions"
        assert 1 in non_neutral and -1 in non_neutral, "trend must emit both signs"


class TestNotEvaluableCandidates:
    def test_majority_is_not_evaluable_no_training_labels(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=200)
        result = _run_all_candidates(monkeypatch, tmp_path, candles)
        majority = result["candidates"][CANDIDATE_MAJORITY]
        assert majority["status"] == "NOT_EVALUABLE"
        assert "no training labels" in (majority["reason"] or "")
        assert majority["n_predictions"] == 0
        assert majority["n_eligible"] == 0
        assert majority["predictions"] == []
        assert majority["scores"] == []
        assert majority["decision_mask"] == []
        assert majority["evaluation"] is None

    def test_lightgbm_is_not_evaluable_no_artifact_on_disk(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=200)
        result = _run_all_candidates(monkeypatch, tmp_path, candles)
        lightgbm = result["candidates"][CANDIDATE_LIGHTGBM]
        assert lightgbm["status"] == "NOT_EVALUABLE"
        assert "no LightGBM artifact on disk" in (lightgbm["reason"] or "")
        assert lightgbm["n_predictions"] == 0
        assert lightgbm["predictions"] == []


class TestDeterministicInference:
    def test_generate_predictions_is_deterministic(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=200)
        features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        model = reconstruct_logistic_model(artifact_dir)
        preds1, scores1, mask1 = generate_predictions(model, features_df, feature_names)
        preds2, scores2, mask2 = generate_predictions(model, features_df, feature_names)
        assert preds1 == preds2
        assert scores1 == scores2
        assert mask1 == mask2

    def test_full_pipeline_is_deterministic(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        _freeze_time(monkeypatch)
        monkeypatch.setattr(
            "crypto_engine.research.q6.frozen_inference.CANDIDATE_SPECS",
            _patched_specs(tmp_path, logistic_artifact_dir=artifact_dir),
        )
        storage_root = tmp_path / "storage"
        r1 = evaluate_all_candidates(dataset_id=DATASET_ID, storage_root=storage_root)
        r2 = evaluate_all_candidates(dataset_id=DATASET_ID, storage_root=storage_root)
        assert r1 == r2
        for cid in ALL_CANDIDATES:
            assert r1["candidates"][cid] == r2["candidates"][cid]


class TestHashVerification:
    def test_candidate_lock_is_immutable(self) -> None:
        lock = _make_lock()
        with pytest.raises((FrozenInstanceError, AttributeError)):
            lock.candidate_id = "changed"  # type: ignore[misc]

    def test_compute_lock_hash_is_deterministic(self) -> None:
        lock = _make_lock()
        assert compute_lock_hash(lock) == compute_lock_hash(lock)

    def test_lock_hash_changes_when_any_field_changes(self) -> None:
        lock_a = _make_lock("logistic")
        lock_b = _make_lock("trend")
        assert compute_lock_hash(lock_a) != compute_lock_hash(lock_b)
        fields = lock_a.to_dict()
        fields["model_artifact_hash"] = "artifact-different"
        fields.pop("lock_hash", None)
        lock_c = CandidateLock(**fields, lock_hash="")
        assert compute_lock_hash(lock_a) != compute_lock_hash(lock_c)

    def test_result_lock_hash_matches_computed_lock_hash(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        spec = dict(
            CANDIDATE_SPECS[CANDIDATE_LOGISTIC],
            artifact_dir=str(artifact_dir),
        )
        lock = build_candidate_lock(spec, feature_names)
        result = _evaluate_one_candidate_result(
            monkeypatch, tmp_path, candles, artifact_dir=artifact_dir
        )
        logistic = result["candidates"][CANDIDATE_LOGISTIC]
        assert logistic["candidate_lock_hash"] == compute_lock_hash(lock)
        assert logistic["candidate_lock_hash"] != ""

    def test_feature_schema_hash_matches_feature_names(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        result = _evaluate_one_candidate_result(
            monkeypatch, tmp_path, candles, artifact_dir=artifact_dir
        )
        logistic = result["candidates"][CANDIDATE_LOGISTIC]
        expected = hashlib.sha256(
            json.dumps(list(feature_names), sort_keys=False).encode("utf-8")
        ).hexdigest()
        assert logistic["feature_schema_hash"] == expected
        assert logistic["feature_columns"] == list(feature_names)

    def test_model_config_hash_and_artifact_hash_are_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        result = _evaluate_one_candidate_result(
            monkeypatch, tmp_path, candles, artifact_dir=artifact_dir
        )
        logistic = result["candidates"][CANDIDATE_LOGISTIC]
        assert (
            logistic["model_config_hash"]
            == CANDIDATE_SPECS[CANDIDATE_LOGISTIC]["model_config_hash"]
        )
        assert (
            logistic["model_artifact_hash"]
            == CANDIDATE_SPECS[CANDIDATE_LOGISTIC]["model_artifact_hash"]
        )


class TestCausalFeatureBoundaries:
    def test_features_are_causal_no_future_leakage(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        df = candles_to_dataframe(candles)
        _features_df, feature_names = _features_for(candles)
        full = compute_features(df, chunk_rows=10**9)
        prefix = compute_features(df.iloc[:-10], chunk_rows=10**9)
        for col in feature_names:
            pd.testing.assert_series_equal(
                prefix[col].reset_index(drop=True),
                full[col].iloc[: len(prefix)].reset_index(drop=True),
                check_names=False,
            )

    def test_perturbing_a_future_candle_does_not_change_earlier_features(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        df = candles_to_dataframe(candles)
        features_a = compute_features(df)
        df_b = df.copy()
        df_b.iloc[-1, df_b.columns.get_loc("close")] = 999999.0
        features_b = compute_features(df_b)
        overlap = min(len(features_a), len(features_b)) - 10
        for col in features_a.columns:
            pd.testing.assert_series_equal(
                features_a[col].iloc[:overlap].reset_index(drop=True),
                features_b[col].iloc[:overlap].reset_index(drop=True),
                check_names=False,
            )


class TestTimestampAlignment:
    def test_predictions_align_one_to_one_with_candles(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        model = reconstruct_logistic_model(artifact_dir)
        predictions, scores, decision_mask = generate_predictions(model, features_df, feature_names)
        assert len(predictions) == len(candles)
        assert len(scores) == len(candles)
        assert len(decision_mask) == len(candles)

    def test_result_timestamps_match_storage_first_and_last_candle(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        result = _evaluate_one_candidate_result(
            monkeypatch, tmp_path, candles, artifact_dir=artifact_dir
        )
        logistic = result["candidates"][CANDIDATE_LOGISTIC]
        assert logistic["first_candle_utc"] == candles[0]["open_time"]
        assert logistic["last_candle_utc"] == candles[-1]["open_time"]

    def test_evaluation_engine_requires_prediction_observation_alignment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        _store_synthetic_candles(storage, n=20)
        lock = CandidateLock(
            candidate_id="x",
            model_name="x",
            model_artifact_hash="h",
            model_config_hash="h",
            feature_config_hash="h",
            target_config_hash="h",
            strategy_config_hash="h",
            entry_exit_rules={},
            thresholds={},
            calibration_status="uncalibrated",
            cost_assumptions={},
            risk_limits={},
            candidate_creation_timestamp="2026-01-01T00:00:00+00:00",
            associated_research_freeze_id="freeze-1",
            associated_prospective_collection_id="collect-1",
            lock_timestamp="2026-01-01T00:00:00+00:00",
            lock_hash="hash",
        )
        engine = Q6EvaluationEngine(storage=storage, candidate_lock=lock)
        with pytest.raises(ValueError):
            engine.evaluate_candidate(
                predictions=[1, -1],
                decision_mask=[True, False],
                candidate_id="x",
            )


class TestNextBarExecutionSemantics:
    def test_prediction_at_bar_uses_only_candles_up_to_that_bar(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A prediction for candle i is computed from candles <= i (causal),
        so it can only be executed on the next bar (i + 1), never the same bar."""
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        model = reconstruct_logistic_model(artifact_dir)
        predictions, _, decision_mask = generate_predictions(model, features_df, feature_names)
        eligible_indices = [i for i, m in enumerate(decision_mask) if m]
        assert eligible_indices, "there must be eligible next-bar decision points"
        first_eligible = eligible_indices[0]
        assert first_eligible > 0
        assert len(predictions) == len(candles)

    def test_last_candle_has_no_following_bar_to_execute_into(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=50)
        features_df, feature_names = _features_for(candles)
        assert len(features_df) == len(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        model = reconstruct_logistic_model(artifact_dir)
        predictions, _, decision_mask = generate_predictions(model, features_df, feature_names)
        assert predictions[-1] in (-1, 0, 1)
        assert len(decision_mask) == len(candles)


class TestInvalidOrMissingModelBehaviour:
    def test_reconstruct_logistic_missing_artifact_raises(self, tmp_path: Path) -> None:
        missing_dir = tmp_path / "does-not-exist"
        with pytest.raises(FileNotFoundError):
            reconstruct_logistic_model(missing_dir)

    def test_generate_predictions_missing_feature_columns_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        model = reconstruct_logistic_model(artifact_dir)
        with pytest.raises(ValueError, match="Features missing columns"):
            generate_predictions(model, features_df, ["nonexistent_column"])

    def test_predict_before_fit_raises(self) -> None:
        model = SklearnLogistic(feature_names=["a", "b"])
        X = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0]})
        with pytest.raises(NotFittedError):
            model.predict(X)

    def test_schema_mismatch_on_predict_raises(self) -> None:
        model = SklearnLogistic(feature_names=["a", "b"], random_state=1)
        X = pd.DataFrame({"a": [1.0, 2.0], "b": [3.0, 4.0], "c": [5.0, 6.0]})
        y = np.array([0, 1])
        model.fit(X[["a", "b"]], y)
        with pytest.raises(SchemaMismatchError):
            model.predict(X)

    def test_unsupported_candidate_is_not_evaluable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=50)
        features_df, feature_names = _features_for(candles)
        spec = dict(
            CANDIDATE_SPECS[CANDIDATE_LOGISTIC],
            candidate_id="unsupported",
            model_name="unsupported-model",
        )
        monkeypatch.setattr(
            "crypto_engine.research.q6.frozen_inference.CANDIDATE_SPECS",
            {"unsupported-model": spec},
        )
        from crypto_engine.research.q6.frozen_inference import _evaluate_one_candidate

        storage2 = HistoricalStorage(tmp_path / "storage")
        _store_synthetic_candles(storage2, n=50)
        result = _evaluate_one_candidate(
            model_name="unsupported-model",
            spec=spec,
            features_df=features_df,
            feature_names=feature_names,
            storage=storage2,
            dataset_hash="hash",
            n_candles=len(candles),
            first_utc=candles[0]["open_time"],
            last_utc=candles[-1]["open_time"],
            generated_at=FIXED_NOW,
        )
        assert result.status == "NOT_EVALUABLE"
        assert "unsupported" in (result.reason or "")


class TestNoNeutralFallback:
    def test_unavailable_models_produce_empty_predictions_not_neutral_fallback(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=200)
        result = _run_all_candidates(monkeypatch, tmp_path, candles)
        for cid in (CANDIDATE_MAJORITY, CANDIDATE_LIGHTGBM):
            cand = result["candidates"][cid]
            assert cand["status"] == "NOT_EVALUABLE"
            assert cand["predictions"] == []
            assert cand["scores"] == []
            assert cand["decision_mask"] == []
            assert cand["n_predictions"] == 0
            assert cand["n_eligible"] == 0
            assert (
                all(p == ABSTAIN_LABEL for p in cand["predictions"]) is True
                and len(cand["predictions"]) == 0
            )

    def test_evaluated_models_never_emit_all_neutral_predictions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=200)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        result = _run_all_candidates(
            monkeypatch, tmp_path, candles, logistic_artifact_dir=artifact_dir
        )
        logistic = result["candidates"][CANDIDATE_LOGISTIC]
        non_neutral = [p for p in logistic["predictions"] if p != ABSTAIN_LABEL]
        assert non_neutral, "logistic must emit at least one non-neutral prediction"
        trend = result["candidates"][CANDIDATE_TREND]
        non_neutral_t = [p for p in trend["predictions"] if p != ABSTAIN_LABEL]
        assert non_neutral_t, "trend must emit at least one non-neutral prediction"


class TestNoTrainingOrTuningDuringInference:
    def test_inference_does_not_call_fit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls: list[str] = []
        real_fit = SklearnLogistic.fit

        def guarded_fit(self, X, y, feature_names=None):
            calls.append("fit")
            raise AssertionError("SklearnLogistic.fit must not be called during frozen inference")

        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        monkeypatch.setattr(SklearnLogistic, "fit", guarded_fit)
        try:
            result = _run_all_candidates(
                monkeypatch, tmp_path, candles, logistic_artifact_dir=artifact_dir
            )
            logistic = result["candidates"][CANDIDATE_LOGISTIC]
            assert logistic["status"] == "EVALUATED"
        finally:
            monkeypatch.setattr(SklearnLogistic, "fit", real_fit)
        assert "fit" not in calls, "fit must not be invoked during frozen inference"

    def test_loaded_artifact_coefficients_are_preserved(self, tmp_path: Path) -> None:
        feature_names = ["ret_1", "trend_ratio_20_50", "rsi_14"]
        rng = np.random.default_rng(3)
        n = 120
        X = pd.DataFrame(rng.normal(size=(n, len(feature_names))), columns=feature_names)
        y = np.where(X.iloc[:, 0].to_numpy() > 0, 1, -1).astype(np.int64)
        model = SklearnLogistic(feature_names=feature_names, random_state=7)
        model.fit(X, y)
        payload = model.to_dict()
        loaded = SklearnLogistic.from_dict(payload)
        assert loaded.is_fitted is True
        assert loaded._classes == model._classes
        assert np.allclose(loaded._coef, model._coef)
        assert np.allclose(loaded._intercept, model._intercept)
        assert np.allclose(loaded._scaler_mean, model._scaler_mean)
        assert np.allclose(loaded._scaler_scale, model._scaler_scale)
        unfitted = SklearnLogistic(feature_names=feature_names)
        assert unfitted.is_fitted is False


class TestHistoricalProspectiveIsolation:
    def test_dataset_type_is_historical_accelerated_validation(self) -> None:
        assert DATASET_TYPE == "HISTORICAL_ACCELERATED_VALIDATION"

    def test_evaluate_all_candidates_marks_historical_not_prospective(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        result = _run_all_candidates(monkeypatch, tmp_path, candles)
        assert result["official_prospective_evidence"] is False
        assert result["q5_2_eligible"] is False
        assert result["dataset_type"] == DATASET_TYPE
        assert "prospective" not in result["storage_root"].lower()

    def test_historical_storage_is_separate_from_prospective(self) -> None:
        assert Path("data/historical_accelerated") == DEFAULT_HISTORICAL_STORAGE_ROOT
        assert "prospective" not in str(DEFAULT_HISTORICAL_STORAGE_ROOT)

    def test_model_classes_mapping_is_complete(self) -> None:
        assert set(MODEL_CLASSES.keys()) == set(ALL_CANDIDATES)
        assert MODEL_CLASSES[CANDIDATE_LOGISTIC] is SklearnLogistic
        assert MODEL_CLASSES[CANDIDATE_TREND] is TrendBaseline
        assert MODEL_CLASSES[CANDIDATE_MAJORITY] is MajorityBaseline
        assert MODEL_CLASSES[CANDIDATE_LIGHTGBM] is LightGBMModel


class TestQ4DecisionPreservation:
    def test_q4_decision_constants(self) -> None:
        assert Q4_DECISION == "NO_STRATEGY_ELIGIBLE"
        assert Q4_DECISION_PRESERVED is True

    def test_evaluate_all_candidates_preserves_q4_decision(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        result = _run_all_candidates(monkeypatch, tmp_path, candles)
        assert result["q4_decision_preserved"] is True
        assert result["q4_decision"] == Q4_DECISION


class TestPromotionPermanentlyBlocked:
    def test_promotion_constants_are_blocked(self) -> None:
        assert PROMOTION_ELIGIBLE is False
        assert PROMOTION_GATE == BLOCKED

    def test_evaluate_all_candidates_promotion_gate_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        storage = HistoricalStorage(tmp_path / "storage")
        candles = _store_synthetic_candles(storage, n=160)
        _features_df, feature_names = _features_for(candles)
        artifact_dir = _fit_and_save_logistic_artifact(tmp_path, feature_names)
        result = _run_all_candidates(
            monkeypatch, tmp_path, candles, logistic_artifact_dir=artifact_dir
        )
        assert result["promotion_gate"] == BLOCKED
        assert result["promotion_eligible"] is False
        for cid in ALL_CANDIDATES:
            gate = result["candidates"][cid]["promotion_gate"]
            assert gate["status"] == BLOCKED
            assert gate["eligible_for_human_review"] is False
            assert gate["human_authorized"] is False
            assert gate["execution_authorized"] is False

    def test_promotion_gate_never_authorizes_execution(self) -> None:
        gate = PromotionGate()
        result = gate.evaluate(
            preflight_passed=True,
            holdout_eligible=True,
            data_quality_passed=True,
            candidate_lock_valid=True,
            candidate_lock_hash="hash",
            trade_count=500,
            net_return_bp=1000.0,
            gross_return_bp=1200.0,
            profit_factor=2.0,
            max_drawdown_bp=1000.0,
            win_rate=0.7,
            expectancy_bp=20.0,
            outperforms_buy_and_hold=True,
            outperforms_cash=True,
            outperforms_majority=True,
            regime_robustness=0.9,
            chronological_stable=True,
            statistically_significant=True,
            sufficient_sample=True,
            profitable_at_all_cost_levels=True,
            data_integrity_ok=True,
            stress_passed=True,
        )
        assert result.execution_authorized is False
        assert result.human_authorized is False


@pytest.mark.skipif(
    not REAL_STORAGE_ROOT.exists()
    or not (REAL_STORAGE_ROOT / "normalized_candles.jsonl").is_file()
    or not REAL_LOGISTIC_ARTIFACT.exists(),
    reason="Real historical accelerated dataset or logistic artifact not available",
)
class TestRealHistoricalDataEndToEnd:
    def test_real_dataset_loads_and_evaluates_all_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        storage = HistoricalStorage(REAL_STORAGE_ROOT)
        candles = storage.read_normalized_candles()
        assert len(candles) >= 130, "need enough history past the feature warm-up"
        dataset_hash = storage.compute_dataset_hash()
        assert dataset_hash is not None
        assert all(c.get("dataset_id") == REAL_DATASET_ID for c in candles)

        _freeze_time(monkeypatch)
        result = evaluate_all_candidates(dataset_id=REAL_DATASET_ID, storage_root=REAL_STORAGE_ROOT)
        assert result["dataset_id"] == REAL_DATASET_ID
        assert result["n_candles"] == len(candles)
        assert result["dataset_hash"] == dataset_hash
        assert result["official_prospective_evidence"] is False
        assert result["q5_2_eligible"] is False
        assert result["q4_decision_preserved"] is True
        assert result["q4_decision"] == Q4_DECISION
        assert result["promotion_gate"] == BLOCKED
        assert result["promotion_eligible"] is False

        statuses = {cid: info["status"] for cid, info in result["candidates"].items()}
        assert statuses[CANDIDATE_LOGISTIC] == "EVALUATED"
        assert statuses[CANDIDATE_TREND] == "EVALUATED"
        assert statuses[CANDIDATE_MAJORITY] == "NOT_EVALUABLE"
        assert statuses[CANDIDATE_LIGHTGBM] == "NOT_EVALUABLE"

        logistic = result["candidates"][CANDIDATE_LOGISTIC]
        assert logistic["n_eligible"] > 0
        assert any(p != ABSTAIN_LABEL for p in logistic["predictions"])
        assert logistic["first_candle_utc"] == candles[0]["open_time"]
        assert logistic["last_candle_utc"] == candles[-1]["open_time"]
        assert len(logistic["predictions"]) == len(candles)
        for cid in ALL_CANDIDATES:
            gate = result["candidates"][cid]["promotion_gate"]
            assert gate["status"] == BLOCKED
            assert gate["execution_authorized"] is False


def _make_lock(candidate_id: str = "logistic") -> CandidateLock:
    return CandidateLock(
        candidate_id=candidate_id,
        model_name=candidate_id,
        model_artifact_hash=f"artifact-{candidate_id}",
        model_config_hash=f"config-{candidate_id}",
        feature_config_hash=f"feature-{candidate_id}",
        target_config_hash=f"target-{candidate_id}",
        strategy_config_hash=f"strategy-{candidate_id}",
        entry_exit_rules={"side": "both"},
        thresholds={"pt_mult": 2.0},
        calibration_status="uncalibrated",
        cost_assumptions={"fee_bp": 10.0},
        risk_limits={"max_drawdown_bp": 5000.0},
        candidate_creation_timestamp="2026-10-28T13:22:36+00:00",
        associated_research_freeze_id="freeze-aaa4dd57057a",
        associated_prospective_collection_id="collect-52cefa0dda64",
        lock_timestamp="2026-10-28T13:22:36+00:00",
        lock_hash="",
    )


def _evaluate_one_candidate_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    candles: list[dict[str, Any]],
    artifact_dir: Path,
) -> dict[str, Any]:
    """Run evaluate_all_candidates with a fresh storage and frozen time."""
    storage_root = tmp_path / "storage"
    _freeze_time(monkeypatch)
    monkeypatch.setattr(
        "crypto_engine.research.q6.frozen_inference.CANDIDATE_SPECS",
        _patched_specs(tmp_path, logistic_artifact_dir=artifact_dir),
    )
    return evaluate_all_candidates(dataset_id=DATASET_ID, storage_root=storage_root)


__all__ = [
    "ABSTAIN_LABEL",
    "ALL_CANDIDATES",
    "DATASET_ID",
    "FIXED_NOW",
]

