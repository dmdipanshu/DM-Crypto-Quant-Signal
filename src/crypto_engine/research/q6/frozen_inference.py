"""Frozen candidate inference for Historical Accelerated Validation (Q6).

Loads historical candles from ``data/historical_accelerated/``, computes
causal features, reconstructs frozen models from their artifacts,
generates actual (non-neutral) predictions, records a prediction audit,
and runs Q6 evaluation per candidate.

All operations are read-only with respect to the historical dataset and
the frozen model artifacts. No retraining, recalibration, or threshold
tuning.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from crypto_engine.features.engineering import compute_features  # noqa: E402
from crypto_engine.models import (  # noqa: E402
    LightGBMModel,
    MajorityBaseline,
    SklearnLogistic,
    TrendBaseline,
)
from crypto_engine.research.q6.config import CandidateLock, compute_lock_hash  # noqa: E402
from crypto_engine.research.q6.evaluation import Q6EvaluationEngine  # noqa: E402
from crypto_engine.research.q6.historical_storage import (  # noqa: E402
    DEFAULT_HISTORICAL_STORAGE_ROOT,
    HistoricalStorage,
)
from crypto_engine.research.q6.promotion import PromotionGate  # noqa: E402

ARTIFACTS_ROOT = ROOT.parent / "models" / "artifacts"
LOGISTIC_ARTIFACT_DIR = ARTIFACTS_ROOT / "logistic-ee482c-3e3324f2-20261028T132236"

PREDICTION_AUDIT_SCHEMA_VERSION = "prediction_audit.v1"

DATASET_TYPE = "HISTORICAL_ACCELERATED_VALIDATION"
Q4_DECISION = "NO_STRATEGY_ELIGIBLE"
Q4_DECISION_PRESERVED = True
OFFICIAL_PROSPECTIVE_EVIDENCE = False
Q5_2_ELIGIBLE = False
PROMOTION_ELIGIBLE = False
PROMOTION_GATE = "BLOCKED"

CANDIDATE_LOGISTIC = "logistic"
CANDIDATE_TREND = "trend"
CANDIDATE_MAJORITY = "majority"
CANDIDATE_LIGHTGBM = "lightgbm"

ALL_CANDIDATES = (
    CANDIDATE_LOGISTIC,
    CANDIDATE_TREND,
    CANDIDATE_MAJORITY,
    CANDIDATE_LIGHTGBM,
)

MODEL_CLASSES: dict[str, type] = {
    CANDIDATE_LOGISTIC: SklearnLogistic,
    CANDIDATE_TREND: TrendBaseline,
    CANDIDATE_MAJORITY: MajorityBaseline,
    CANDIDATE_LIGHTGBM: LightGBMModel,
}

ABSTAIN_LABEL = 0

FREEZE_ID = "freeze-aaa4dd57057a"
COLLECTION_ID = "collect-52cefa0dda64"

ENGINE_KWARGS: dict[str, Any] = {
    "pt_mult": 2.0,
    "sl_mult": 1.0,
    "max_horizon_bars": 24,
    "fee_bp": 10.0,
    "spread_bp": 5.0,
    "slippage_bp": 5.0,
    "funding_bp_per_bar": 0.0,
}

LOGISTIC_SPEC: dict[str, Any] = {
    "candidate_id": "logistic-ee482c",
    "model_name": CANDIDATE_LOGISTIC,
    "model_artifact_hash": "logistic-ee482c-3e3324f2-20261028T132236",
    "model_config_hash": "logistic-config-v1",
    "feature_config_hash": "592fdd3c59c4253b781c1a74f4402ba0a4316d99e041b676d9dd3531e770a918",
    "target_config_hash": "74bd147242856a17ea17cd17916cf9c674d98e25c7fbb4c526445875e366a072",
    "strategy_config_hash": "triple-barrier-v1",
    "artifact_dir": str(LOGISTIC_ARTIFACT_DIR),
    "entry_exit_rules": {"side": "both", "ambiguous_policy": "conservative"},
    "thresholds": {"pt_mult": 2.0, "sl_mult": 1.0, "max_horizon_bars": 24},
    "calibration_status": "uncalibrated",
    "cost_assumptions": {"fee_bp": 10.0, "spread_bp": 5.0, "slippage_bp": 5.0},
    "risk_limits": {"max_drawdown_bp": 5000.0},
    "candidate_creation_timestamp": "2026-10-28T13:22:36+00:00",
}

TREND_SPEC: dict[str, Any] = {
    "candidate_id": "trend-baseline",
    "model_name": CANDIDATE_TREND,
    "model_artifact_hash": "trend-baseline-deterministic",
    "model_config_hash": "trend-config-v1",
    "feature_config_hash": "trend-feature-config-v1",
    "target_config_hash": "trend-target-config-v1",
    "strategy_config_hash": "triple-barrier-v1",
    "entry_exit_rules": {"side": "both", "ambiguous_policy": "conservative"},
    "thresholds": {"pt_mult": 2.0, "sl_mult": 1.0, "max_horizon_bars": 24},
    "calibration_status": "uncalibrated",
    "cost_assumptions": {"fee_bp": 10.0, "spread_bp": 5.0, "slippage_bp": 5.0},
    "risk_limits": {"max_drawdown_bp": 5000.0},
    "candidate_creation_timestamp": "2026-11-12T00:00:00+00:00",
}

MAJORITY_SPEC: dict[str, Any] = {
    "candidate_id": "majority-baseline",
    "model_name": CANDIDATE_MAJORITY,
    "model_artifact_hash": "majority-baseline-no-labels",
    "model_config_hash": "majority-config-v1",
    "feature_config_hash": "majority-feature-config-v1",
    "target_config_hash": "majority-target-config-v1",
    "strategy_config_hash": "triple-barrier-v1",
    "entry_exit_rules": {"side": "both", "ambiguous_policy": "conservative"},
    "thresholds": {"pt_mult": 2.0, "sl_mult": 1.0, "max_horizon_bars": 24},
    "calibration_status": "uncalibrated",
    "cost_assumptions": {"fee_bp": 10.0, "spread_bp": 5.0, "slippage_bp": 5.0},
    "risk_limits": {"max_drawdown_bp": 5000.0},
    "candidate_creation_timestamp": "2026-11-12T00:00:00+00:00",
}

LIGHTGBM_SPEC: dict[str, Any] = {
    "candidate_id": "lightgbm",
    "model_name": CANDIDATE_LIGHTGBM,
    "model_artifact_hash": "lightgbm-artifact-not-on-disk",
    "model_config_hash": "lightgbm-config-v1",
    "feature_config_hash": "lightgbm-feature-config-v1",
    "target_config_hash": "lightgbm-target-config-v1",
    "strategy_config_hash": "triple-barrier-v1",
    "entry_exit_rules": {"side": "both", "ambiguous_policy": "conservative"},
    "thresholds": {"pt_mult": 2.0, "sl_mult": 1.0, "max_horizon_bars": 24},
    "calibration_status": "uncalibrated",
    "cost_assumptions": {"fee_bp": 10.0, "spread_bp": 5.0, "slippage_bp": 5.0},
    "risk_limits": {"max_drawdown_bp": 5000.0},
    "candidate_creation_timestamp": "2026-11-12T00:00:00+00:00",
}

CANDIDATE_SPECS: dict[str, dict[str, Any]] = {
    CANDIDATE_LOGISTIC: LOGISTIC_SPEC,
    CANDIDATE_TREND: TREND_SPEC,
    CANDIDATE_MAJORITY: MAJORITY_SPEC,
    CANDIDATE_LIGHTGBM: LIGHTGBM_SPEC,
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateInferenceResult:
    """Result of frozen inference and Q6 evaluation for one candidate."""

    candidate_id: str
    model_name: str
    model_artifact_hash: str
    model_config_hash: str
    feature_config_hash: str
    target_config_hash: str
    strategy_config_hash: str
    status: str
    reason: str | None
    n_candles: int
    n_predictions: int
    n_eligible: int
    predictions: list[int]
    scores: list[float]
    decision_mask: list[bool]
    prediction_counts: dict[int, int]
    score_min: float
    score_max: float
    score_mean: float
    first_candle_utc: str | None
    last_candle_utc: str | None
    feature_columns: list[str]
    feature_schema_hash: str
    dataset_hash: str | None
    generated_at_utc: str
    audit_id: str
    candidate_lock_hash: str
    evaluation: dict[str, Any] | None
    promotion_gate: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PREDICTION_AUDIT_SCHEMA_VERSION,
            "audit_id": self.audit_id,
            "candidate_id": self.candidate_id,
            "model_name": self.model_name,
            "model_artifact_hash": self.model_artifact_hash,
            "model_config_hash": self.model_config_hash,
            "feature_config_hash": self.feature_config_hash,
            "target_config_hash": self.target_config_hash,
            "strategy_config_hash": self.strategy_config_hash,
            "status": self.status,
            "reason": self.reason,
            "n_candles": self.n_candles,
            "n_predictions": self.n_predictions,
            "n_eligible": self.n_eligible,
            "predictions": self.predictions,
            "scores": self.scores,
            "decision_mask": self.decision_mask,
            "prediction_counts": {str(k): v for k, v in self.prediction_counts.items()},
            "score_min": self.score_min,
            "score_max": self.score_max,
            "score_mean": self.score_mean,
            "first_candle_utc": self.first_candle_utc,
            "last_candle_utc": self.last_candle_utc,
            "feature_columns": self.feature_columns,
            "feature_schema_hash": self.feature_schema_hash,
            "dataset_hash": self.dataset_hash,
            "generated_at_utc": self.generated_at_utc,
            "candidate_lock_hash": self.candidate_lock_hash,
            "evaluation": self.evaluation,
            "promotion_gate": self.promotion_gate,
        }


def load_historical_candles(
    storage_root: Path | str,
    dataset_id: str | None = None,
) -> tuple[list[dict[str, Any]], HistoricalStorage]:
    """Load normalized historical candles for a dataset from storage."""
    storage = HistoricalStorage(Path(storage_root))
    candles = [
        c
        for c in storage.read_normalized_candles()
        if dataset_id is None or c.get("dataset_id") == dataset_id
    ]
    if not candles:
        detail = f" for dataset {dataset_id}" if dataset_id else ""
        raise ValueError(f"No historical candles found in {storage_root}{detail}")
    return candles, storage


def candles_to_dataframe(candles: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert normalized candle records to a UTC-indexed OHLCV DataFrame."""
    rows = [
        {
            "open_time": pd.Timestamp(c["open_time"]),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c["volume"]),
        }
        for c in candles
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.set_index("open_time")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df.sort_index()


def reconstruct_logistic_model(
    artifact_dir: Path | str = LOGISTIC_ARTIFACT_DIR,
) -> SklearnLogistic:
    """Reconstruct the frozen logistic model from JSON artifact only."""
    artifact_dir = Path(artifact_dir)
    artifact_path = artifact_dir / "artifact.json"

    if not artifact_path.is_file():
        raise FileNotFoundError(
            f"No loadable artifact (artifact.json) in {artifact_dir}"
        )
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FileNotFoundError(
            f"Invalid artifact payload in {artifact_dir}: not a JSON object"
        )

    model = SklearnLogistic.from_dict(payload)
    if model._coef is None or model._intercept is None:
        raise ValueError(f"Logistic artifact in {artifact_dir} is not fitted")
    return model


def build_trend_model(feature_names: list[str]) -> TrendBaseline:
    """Build the deterministic TrendBaseline (no fitting required)."""
    model = TrendBaseline(feature_names=list(feature_names))
    model._is_fitted = True
    return model


def generate_predictions(
    model: Any,
    features_df: pd.DataFrame,
    feature_names: list[str],
) -> tuple[list[int], list[float], list[bool]]:
    """Generate actual model predictions for every feature row.

    Returns ``(predictions, scores, decision_mask)``. Rows with any NaN in
    the model's feature columns are in the warm-up period and are masked out.
    """
    n = len(features_df)
    predictions = [ABSTAIN_LABEL] * n
    scores = [0.0] * n
    decision_mask = [False] * n

    missing = [c for c in feature_names if c not in features_df.columns]
    if missing:
        raise ValueError(f"Features missing columns: {missing}")

    X = features_df[list(feature_names)]
    arr = X.to_numpy(dtype=float)
    valid_mask = ~np.isnan(arr).any(axis=1)
    decision_mask = [bool(v) for v in valid_mask]

    valid_indices = np.flatnonzero(valid_mask)
    if valid_indices.size:
        X_valid = X.iloc[valid_indices]
        preds = model.predict(X_valid)
        sc = model.predict_score(X_valid)
        for pos, idx in enumerate(valid_indices):
            predictions[int(idx)] = int(preds[pos])
            scores[int(idx)] = float(sc[pos])

    return predictions, scores, decision_mask


def build_candidate_lock(
    spec: dict[str, Any],
    feature_names: list[str],
    lock_timestamp: str | None = None,
) -> CandidateLock:
    """Build and hash a :class:`CandidateLock` from a candidate spec."""
    feature_config_hash = spec.get("feature_config_hash") or _sha256_json(feature_names)
    lock = CandidateLock(
        candidate_id=spec["candidate_id"],
        model_name=spec["model_name"],
        model_artifact_hash=spec["model_artifact_hash"],
        model_config_hash=spec["model_config_hash"],
        feature_config_hash=feature_config_hash,
        target_config_hash=spec["target_config_hash"],
        strategy_config_hash=spec["strategy_config_hash"],
        entry_exit_rules=spec.get("entry_exit_rules", {}),
        thresholds=spec.get("thresholds", {}),
        calibration_status=spec.get("calibration_status", "uncalibrated"),
        cost_assumptions=spec.get("cost_assumptions", {}),
        risk_limits=spec.get("risk_limits", {}),
        candidate_creation_timestamp=spec.get("candidate_creation_timestamp") or _utc_now_iso(),
        associated_research_freeze_id=FREEZE_ID,
        associated_prospective_collection_id=COLLECTION_ID,
        lock_timestamp=lock_timestamp or _utc_now_iso(),
        lock_hash="",
    )
    fields = lock.to_dict()
    fields.pop("lock_hash", None)
    return CandidateLock(**fields, lock_hash=compute_lock_hash(lock))


def promotion_blocked_gate(candidate_lock_hash: str) -> dict[str, Any]:
    """Return the Q6 promotion gate verdict (always BLOCKED for historical data)."""
    gate = PromotionGate()
    result = gate.evaluate(
        preflight_passed=True,
        holdout_eligible=False,
        data_quality_passed=True,
        candidate_lock_valid=True,
        candidate_lock_hash=candidate_lock_hash,
        trade_count=0,
        net_return_bp=None,
        gross_return_bp=None,
        profit_factor=None,
        max_drawdown_bp=None,
        win_rate=None,
        expectancy_bp=None,
        outperforms_buy_and_hold=None,
        outperforms_cash=None,
        outperforms_majority=None,
        regime_robustness=None,
        chronological_stable=None,
        statistically_significant=None,
        sufficient_sample=None,
        profitable_at_all_cost_levels=None,
        data_integrity_ok=True,
        stress_passed=None,
    )
    return result.to_dict()


def _not_evaluable_result(
    spec: dict[str, Any],
    n_candles: int,
    first_utc: str | None,
    last_utc: str | None,
    reason: str,
    generated_at: str,
) -> CandidateInferenceResult:
    return CandidateInferenceResult(
        candidate_id=spec["candidate_id"],
        model_name=spec["model_name"],
        model_artifact_hash=spec["model_artifact_hash"],
        model_config_hash=spec["model_config_hash"],
        feature_config_hash=spec["feature_config_hash"],
        target_config_hash=spec["target_config_hash"],
        strategy_config_hash=spec["strategy_config_hash"],
        status="NOT_EVALUABLE",
        reason=reason,
        n_candles=n_candles,
        n_predictions=0,
        n_eligible=0,
        predictions=[],
        scores=[],
        decision_mask=[],
        prediction_counts={},
        score_min=0.0,
        score_max=0.0,
        score_mean=0.0,
        first_candle_utc=first_utc,
        last_candle_utc=last_utc,
        feature_columns=[],
        feature_schema_hash="",
        dataset_hash=None,
        generated_at_utc=generated_at,
        audit_id=f"audit-{spec['candidate_id']}-{_sha256_json(generated_at)[:16]}",
        candidate_lock_hash="",
        evaluation=None,
        promotion_gate=promotion_blocked_gate(""),
    )


def _evaluate_one_candidate(
    *,
    model_name: str,
    spec: dict[str, Any],
    features_df: pd.DataFrame,
    feature_names: list[str],
    storage: HistoricalStorage,
    dataset_hash: str | None,
    n_candles: int,
    first_utc: str | None,
    last_utc: str | None,
    generated_at: str,
) -> CandidateInferenceResult:
    if model_name == CANDIDATE_MAJORITY:
        return _not_evaluable_result(
            spec,
            n_candles,
            first_utc,
            last_utc,
            "NOT_EVALUABLE: no training labels available to determine majority class",
            generated_at,
        )

    if model_name == CANDIDATE_LIGHTGBM:
        return _not_evaluable_result(
            spec,
            n_candles,
            first_utc,
            last_utc,
            "NOT_EVALUABLE: no LightGBM artifact on disk",
            generated_at,
        )

    if model_name == CANDIDATE_LOGISTIC:
        model = reconstruct_logistic_model(Path(spec["artifact_dir"]))
    elif model_name == CANDIDATE_TREND:
        model = build_trend_model(feature_names)
    else:
        return _not_evaluable_result(
            spec,
            n_candles,
            first_utc,
            last_utc,
            f"NOT_EVALUABLE: unsupported candidate {model_name}",
            generated_at,
        )

    predictions, scores, decision_mask = generate_predictions(model, features_df, feature_names)
    lock = build_candidate_lock(spec, feature_names)
    engine = Q6EvaluationEngine(storage=storage, candidate_lock=lock, **ENGINE_KWARGS)
    evaluation = engine.evaluate_candidate(
        predictions=predictions,
        decision_mask=decision_mask,
        candidate_id=spec["candidate_id"],
    )

    prediction_counts: dict[int, int] = {}
    for p in predictions:
        prediction_counts[p] = prediction_counts.get(p, 0) + 1

    eligible_scores = [s for s, m in zip(scores, decision_mask, strict=False) if m]
    score_min = min(eligible_scores) if eligible_scores else 0.0
    score_max = max(eligible_scores) if eligible_scores else 0.0
    score_mean = sum(eligible_scores) / len(eligible_scores) if eligible_scores else 0.0

    return CandidateInferenceResult(
        candidate_id=spec["candidate_id"],
        model_name=spec["model_name"],
        model_artifact_hash=spec["model_artifact_hash"],
        model_config_hash=spec["model_config_hash"],
        feature_config_hash=spec["feature_config_hash"],
        target_config_hash=spec["target_config_hash"],
        strategy_config_hash=spec["strategy_config_hash"],
        status="EVALUATED",
        reason=None,
        n_candles=n_candles,
        n_predictions=len(predictions),
        n_eligible=sum(1 for m in decision_mask if m),
        predictions=predictions,
        scores=scores,
        decision_mask=decision_mask,
        prediction_counts=prediction_counts,
        score_min=score_min,
        score_max=score_max,
        score_mean=score_mean,
        first_candle_utc=first_utc,
        last_candle_utc=last_utc,
        feature_columns=list(feature_names),
        feature_schema_hash=_sha256_json(list(feature_names)),
        dataset_hash=dataset_hash,
        generated_at_utc=generated_at,
        audit_id=f"audit-{spec['candidate_id']}-{_sha256_json(generated_at)[:16]}",
        candidate_lock_hash=lock.lock_hash,
        evaluation=evaluation,
        promotion_gate=promotion_blocked_gate(lock.lock_hash),
    )


def evaluate_all_candidates(
    dataset_id: str,
    storage_root: Path | str | None = None,
) -> dict[str, Any]:
    """Run frozen inference and Q6 evaluation for all candidates on a dataset.

    Loads historical candles from HistoricalStorage, computes causal
    features, reconstructs frozen models, generates actual (non-neutral)
    predictions, records prediction audits, runs Q6 evaluation per
    candidate, and records the Q6 governance verdict.

    Governance: this track is HISTORICAL_ACCELERATED_VALIDATION only; it is
    NOT prospective evidence, is not Q5.2 eligible, cannot promote, preserves
    the Q4 NO_STRATEGY_ELIGIBLE decision, and the promotion gate is BLOCKED.
    """
    storage_root = Path(storage_root or DEFAULT_HISTORICAL_STORAGE_ROOT)
    candles, storage = load_historical_candles(storage_root, dataset_id=dataset_id)
    df = candles_to_dataframe(candles)
    features_df = compute_features(df)
    feature_names = list(features_df.columns)
    dataset_hash = storage.compute_dataset_hash()
    n_candles = len(candles)
    first_utc = candles[0].get("open_time") if candles else None
    last_utc = candles[-1].get("open_time") if candles else None
    generated_at = _utc_now_iso()

    candidates: dict[str, CandidateInferenceResult] = {}
    for model_name in ALL_CANDIDATES:
        spec = CANDIDATE_SPECS[model_name]
        result = _evaluate_one_candidate(
            model_name=model_name,
            spec=spec,
            features_df=features_df,
            feature_names=feature_names,
            storage=storage,
            dataset_hash=dataset_hash,
            n_candles=n_candles,
            first_utc=first_utc,
            last_utc=last_utc,
            generated_at=generated_at,
        )
        candidates[model_name] = result

    return {
        "schema": PREDICTION_AUDIT_SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "dataset_type": DATASET_TYPE,
        "official_prospective_evidence": OFFICIAL_PROSPECTIVE_EVIDENCE,
        "q5_2_eligible": Q5_2_ELIGIBLE,
        "promotion_eligible": PROMOTION_ELIGIBLE,
        "q4_decision_preserved": Q4_DECISION_PRESERVED,
        "q4_decision": Q4_DECISION,
        "promotion_gate": PROMOTION_GATE,
        "storage_root": str(storage_root),
        "n_candles": n_candles,
        "dataset_hash": dataset_hash,
        "generated_at_utc": generated_at,
        "candidates": {name: candidate.to_dict() for name, candidate in candidates.items()},
    }


def main() -> int:
    """CLI entry point: run frozen inference for all candidates."""
    try:
        result = evaluate_all_candidates(dataset_id="historical-accelerated-validation")
    except ValueError as exc:
        print(f"frozen inference blocked: {exc}")
        return 1
    print(
        json.dumps(
            {
                "dataset_id": result["dataset_id"],
                "dataset_type": result["dataset_type"],
                "promotion_gate": result["promotion_gate"],
                "candidates": {name: info["status"] for name, info in result["candidates"].items()},
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ABSTAIN_LABEL",
    "ALL_CANDIDATES",
    "ARTIFACTS_ROOT",
    "CANDIDATE_LIGHTGBM",
    "CANDIDATE_LOGISTIC",
    "CANDIDATE_MAJORITY",
    "CANDIDATE_TREND",
    "DATASET_TYPE",
    "ENGINE_KWARGS",
    "LOGISTIC_ARTIFACT_DIR",
    "MODEL_CLASSES",
    "OFFICIAL_PROSPECTIVE_EVIDENCE",
    "PREDICTION_AUDIT_SCHEMA_VERSION",
    "PROMOTION_ELIGIBLE",
    "PROMOTION_GATE",
    "Q4_DECISION",
    "Q4_DECISION_PRESERVED",
    "Q5_2_ELIGIBLE",
    "CandidateInferenceResult",
    "build_candidate_lock",
    "build_trend_model",
    "candles_to_dataframe",
    "evaluate_all_candidates",
    "generate_predictions",
    "load_historical_candles",
    "promotion_blocked_gate",
    "reconstruct_logistic_model",
]
