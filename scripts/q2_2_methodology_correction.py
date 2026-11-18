"""Q2.2 — corrected quantitative model diagnostics.

Methodology corrections relative to Q2.1 (see docs/validation/QUANTITATIVE_METHODOLOGY_CORRECTION_Q2_2.md):

1. **Execution-aligned target** (:mod:`crypto_engine.research.targets`): the
   training label and the Q3 simulator share one barrier geometry — entry at
   the next-bar open, causal volatility at the decision bar, side-aware
   profit-target / stop-loss levels, conservative same-bar collision handling,
   and a max holding-period exit. Labels are ``+1`` (upper barrier first,
   LONG profits), ``-1`` (lower barrier first, SHORT profits), ``0`` (neutral
   timeout/ambiguous).

2. **Class-weighted training (training fold only)**: logistic regression and
   LightGBM receive ``class_weight="balanced"`` computed from the training
   fold's class frequencies — never from validation or holdout data.

3. **Actual-duration purging/embargo**: purge intervals are built from each
   label's *actual* observable bar (``observable_at``), not a conservative
   fixed horizon.

4. **Collapse-aware evaluation**: per-class precision/recall/F1, confusion
   matrix, balanced accuracy, macro-F1, MCC, multiclass ROC-AUC / PR-AUC
   (one-vs-rest), prediction-distribution collapse detection, and fold-by-fold
   stability.

5. **Holdout integrity**: the historical holdout is assessed as
   ``contaminated`` / ``not_yet_available`` — never pristine. No prospective
   holdout exists yet, so ``holdout_evaluated = false`` and promotion is
   blocked.

This script is diagnostic only. It does not open the holdout, select a model,
or grant execution authority.

Run:
    uv run python scripts/q2_2_methodology_correction.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.features.engineering import compute_features  # noqa: E402
from crypto_engine.labels.triple_barrier import (  # noqa: E402
    assert_no_leakage,
    valid_mask,
)
from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline  # noqa: E402
from crypto_engine.models.sklearn_wrappers import (  # noqa: E402
    LightGBMModel,
    SklearnLogistic,
)
from crypto_engine.research.governance import (  # noqa: E402
    assess_holdout_integrity,
    assess_prospective_holdout,
)
from crypto_engine.research.modeling import (  # noqa: E402
    detect_prediction_collapse,
    evaluate_classifier,
    evaluate_fold_predictive_metrics,
    fold_stability,
)
from crypto_engine.research.targets import (  # noqa: E402
    TargetDefinition,
    label_execution_aligned,
    target_metadata,
)
from crypto_engine.validation.folds import label_intervals, make_folds  # noqa: E402
from crypto_engine.validation.holdout import split_holdout  # noqa: E402

DATA_PATH = ROOT / "data/canonical/binance/BTC-USDT/1h/candles.parquet"
OUT_JSON = ROOT / "docs/validation/results/quantitative_methodology_correction_q2_2.json"
OUT_MD = ROOT / "docs/validation/QUANTITATIVE_METHODOLOGY_CORRECTION_Q2_2.md"
SEED = 42
HORIZON = 24
HOLDOUT_FRACTION = 0.15
FOLD_COUNT = 5
EMBARGO_BARS = 24
TRAIN_WINDOW_CAP = 6_000
MIN_CAL_SAMPLES = 20
POSITION_PCT = 1.0
STARTING_EQUITY = 10_000.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_frame() -> pd.DataFrame:
    if not DATA_PATH.is_file():
        raise FileNotFoundError(f"Canonical dataset not found: {DATA_PATH}")
    frame = pd.read_parquet(DATA_PATH)
    for candidate in ("timestamp", "index"):
        if candidate in frame.columns:
            frame = frame.set_index(candidate)
            break
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.DatetimeIndex(frame.index)
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    frame = frame.sort_index()
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("Canonical dataset timestamps must be unique and increasing.")
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Canonical dataset missing OHLCV columns: {missing}")
    return frame


def _model_factories() -> dict[str, Any]:
    """Class-weighted factories; LightGBM used only when installed."""
    factories: dict[str, Any] = {
        "majority": lambda: MajorityBaseline(random_state=SEED),
        "trend": lambda: TrendBaseline(random_state=SEED, ratio_col="trend_ratio_20_50"),
        "logistic": lambda: SklearnLogistic(
            random_state=SEED, C=1.0, max_iter=300, class_weight="balanced"
        ),
    }
    try:
        import lightgbm  # noqa: F401

        factories["lightgbm"] = lambda: LightGBMModel(
            random_state=SEED,
            n_estimators=75,
            num_leaves=15,
            min_child_samples=30,
            class_weight="balanced",
        )
    except ImportError:
        pass  # reported as skipped
    return factories


def _fold_plan(n_rows: int, observable_positions: np.ndarray):
    val_window = max(250, min(800, n_rows // 25))
    val_window = min(val_window, max(1, (n_rows - 100) // FOLD_COUNT))
    train_window = min(TRAIN_WINDOW_CAP, n_rows - FOLD_COUNT * val_window - HORIZON)
    if train_window <= HORIZON + 100:
        raise ValueError(f"Insufficient research rows for Q2.2 folds (n={n_rows}).")
    purge_intervals = label_intervals(observable_positions, HORIZON)
    return make_folds(
        n=n_rows,
        train_window=train_window,
        val_window=val_window,
        n_folds=FOLD_COUNT,
        embargo_bars=max(EMBARGO_BARS, HORIZON),
        purge_intervals=purge_intervals,
        min_samples=100,
    )


def _evaluate_one_model(
    name: str,
    factory: Any,
    X: pd.DataFrame,
    y: pd.Series,
    folds: list[Any],
    y_score_factory: Any | None = None,
) -> dict[str, Any]:
    """Out-of-fold classification evaluation with collapse-aware metrics.

    Each successfully evaluated fold yields per-fold predictive metrics
    (accuracy, balanced_accuracy, macro_f1, MCC, per-class, ROC-AUC,
    collapse status). Aggregate stability is computed only over folds that
    were actually evaluated; failed/skipped folds are never counted.
    """
    fold_rows: list[dict[str, Any]] = []
    fold_metrics: list[dict[str, Any]] = []
    all_y: list[int] = []
    all_pred: list[int] = []
    all_score: list[float] = []
    y_values = y.to_numpy(dtype=int)

    for fold in folds:
        train_idx = np.asarray(fold.train_idx, dtype=int)
        val_idx = np.asarray(fold.val_idx, dtype=int)
        try:
            estimator = factory()
            estimator.fit(X.iloc[train_idx], y_values[train_idx])
            predictions = np.asarray(estimator.predict(X.iloc[val_idx]), dtype=int).reshape(-1)
            scores = None
            try:
                raw_scores = np.asarray(estimator.predict_score(X.iloc[val_idx])).ravel()
                scores = raw_scores.tolist()
            except Exception:
                scores = None
            if predictions.size != val_idx.size:
                raise ValueError("prediction length differs from validation window")
            fold_metric = evaluate_fold_predictive_metrics(
                y_values[val_idx], predictions, np.asarray(scores) if scores else None
            )
            fold_rows.append(
                {
                    "fold": int(fold.fold_id),
                    "train_rows": int(train_idx.size),
                    "validation_rows": int(val_idx.size),
                    "train_start": str(X.index[train_idx[0]]),
                    "train_end": str(X.index[train_idx[-1]]),
                    "validation_start": str(X.index[val_idx[0]]),
                    "validation_end": str(X.index[val_idx[-1]]),
                    "purged_rows": int(fold.exclusion_record()["n_purged"]),
                    "embargo_rows": int(fold.exclusion_record()["n_embargo"]),
                    "status": "ok",
                    **{
                        k: fold_metric[k]
                        for k in (
                            "accuracy",
                            "balanced_accuracy",
                            "macro_precision",
                            "macro_recall",
                            "macro_f1",
                            "mcc",
                            "prediction_collapse_status",
                        )
                        if k in fold_metric
                    },
                    "per_class": fold_metric.get("per_class", {}),
                    "roc_auc_ovr": fold_metric.get("roc_auc_ovr"),
                    "class_distribution": fold_metric.get("class_distribution", {}),
                    "prediction_distribution": {
                        str(int(c)): int((predictions == int(c)).sum()) for c in (-1, 0, 1)
                    },
                }
            )
            fold_metrics.append(fold_metric)
            all_y.extend(y_values[val_idx].tolist())
            all_pred.extend(predictions.tolist())
            if scores is not None:
                all_score.extend(scores)
        except Exception as exc:
            fold_rows.append(
                {
                    "fold": int(fold.fold_id),
                    "train_rows": len(fold.train_idx),
                    "validation_rows": len(fold.val_idx),
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            )

    n_evaluated = sum(1 for r in fold_rows if r.get("status") == "ok")

    result: dict[str, Any] = {
        "model": name,
        "family": {
            "majority": "majority",
            "trend": "trend",
            "logistic": "logistic",
            "lightgbm": "lightgbm",
        }[name],
        "n_folds": n_evaluated,
        "configured_folds": FOLD_COUNT,
        "train_window": TRAIN_WINDOW_CAP,
        "val_window": int(folds[0].n_val) if folds else 0,
        "embargo_bars": max(EMBARGO_BARS, HORIZON),
        "total_val_rows": len(all_y),
        "fold_results": fold_rows,
        "status": "ok"
        if n_evaluated == len(folds)
        else ("partial" if n_evaluated > 0 else "failed"),
    }
    if not all_y:
        result.update(
            {
                "aggregate": {
                    "n": 0,
                    "accuracy": None,
                    "balanced_accuracy": None,
                    "macro_f1": None,
                    "mcc": None,
                },
                "per_class": {},
                "confusion_matrix": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
                "confusion_labels": [-1, 0, 1],
                "collapse": {"collapsed": None},
                "stability": {
                    "balanced_accuracy": {
                        "key": "balanced_accuracy",
                        "n_folds": 0,
                        "mean": None,
                        "std": None,
                        "min": None,
                        "max": None,
                        "median": None,
                        "range": None,
                        "coefficient_of_variation": None,
                    },
                    "accuracy": {
                        "key": "accuracy",
                        "n_folds": 0,
                        "mean": None,
                        "std": None,
                        "min": None,
                        "max": None,
                        "median": None,
                        "range": None,
                        "coefficient_of_variation": None,
                    },
                    "macro_f1": {
                        "key": "macro_f1",
                        "n_folds": 0,
                        "mean": None,
                        "std": None,
                        "min": None,
                        "max": None,
                        "median": None,
                        "range": None,
                        "coefficient_of_variation": None,
                    },
                },
                "oos_scores": [],
                "oos_labels": [],
                "oos_predictions": [],
            }
        )
        return result

    yt = np.asarray(all_y, dtype=int)
    yp = np.asarray(all_pred, dtype=int)
    metrics = evaluate_classifier(yt, yp, np.asarray(all_score) if all_score else None)
    result["aggregate"] = {
        "n": metrics["total"],
        "accuracy": metrics["accuracy"],
        "balanced_accuracy": metrics["balanced_accuracy"],
        "macro_f1": metrics["macro_f1"],
        "mcc": metrics["mcc"],
    }
    result["per_class"] = metrics["per_class"]
    result["confusion_matrix"] = metrics["confusion_matrix"]
    result["confusion_labels"] = metrics["confusion_labels"]
    result["roc_auc_ovr"] = metrics.get("roc_auc_ovr")
    result["pr_auc_ovr"] = metrics.get("pr_auc_ovr")
    result["collapse"] = detect_prediction_collapse(yp, yt)
    # Stability computed only over successfully evaluated folds.
    result["stability"] = {
        "balanced_accuracy": fold_stability(fold_metrics, "balanced_accuracy"),
        "accuracy": fold_stability(fold_metrics, "accuracy"),
        "macro_f1": fold_stability(fold_metrics, "macro_f1"),
    }
    result["oos_scores"] = all_score
    result["oos_labels"] = yt.tolist()
    result["oos_predictions"] = yp.tolist()
    return result

    yt = np.asarray(all_y, dtype=int)
    yp = np.asarray(all_pred, dtype=int)
    metrics = evaluate_classifier(yt, yp, np.asarray(all_score) if all_score else None)
    result["aggregate"] = {
        "n": metrics["total"],
        "accuracy": metrics["accuracy"],
        "balanced_accuracy": metrics["balanced_accuracy"],
        "macro_f1": metrics["macro_f1"],
        "mcc": metrics["mcc"],
    }
    result["per_class"] = metrics["per_class"]
    result["confusion_matrix"] = metrics["confusion_matrix"]
    result["confusion_labels"] = metrics["confusion_labels"]
    result["roc_auc_ovr"] = metrics.get("roc_auc_ovr")
    result["pr_auc_ovr"] = metrics.get("pr_auc_ovr")
    result["collapse"] = detect_prediction_collapse(yp, yt)
    result["stability"] = {
        "balanced_accuracy": fold_stability(fold_rows, "balanced_accuracy"),
        "accuracy": fold_stability(fold_rows, "accuracy"),
        "macro_f1": fold_stability(fold_rows, "macro_f1"),
    }
    result["oos_scores"] = all_score
    result["oos_labels"] = yt.tolist()
    result["oos_predictions"] = yp.tolist()
    return result


def val_window_for(folds: list[Any]) -> int:
    if not folds:
        return 0
    return int(folds[0].n_val)


def run() -> dict[str, Any]:
    frame = _load_frame()
    file_hash = _sha256(DATA_PATH)

    # --- Research head / holdout split (holdout NEVER touched) ---
    research_raw_idx, holdout_raw_idx = split_holdout(len(frame), HOLDOUT_FRACTION)
    research_frame = frame.iloc[research_raw_idx].copy()

    # --- Features (research head only) ---
    raw_features = compute_features(research_frame, chunk_rows=5_000)
    numeric_cols = [
        c for c in raw_features.columns if pd.api.types.is_numeric_dtype(raw_features[c])
    ]
    finite_mask = np.isfinite(raw_features[numeric_cols].to_numpy(dtype=float)).all(axis=1)
    features = raw_features.loc[finite_mask, numeric_cols]
    research_index = features.index

    # --- Execution-aligned labels (research head only) ---
    definition = TargetDefinition(
        pt_mult=2.0,
        sl_mult=1.0,
        max_horizon_bars=HORIZON,
        vol_window=20,
    )
    labels_full = label_execution_aligned(research_frame, definition)
    complete = valid_mask(labels_full)
    aligned = features.index.intersection(labels_full.index[complete])
    aligned = aligned.intersection(research_index)
    X = features.loc[aligned].copy()
    y = labels_full.loc[aligned, "label"].astype(int)
    obs = labels_full.loc[aligned]
    assert_no_leakage(X, obs)

    # Actual-duration purge positions: position of each row's observable bar.
    observable_ts = pd.to_datetime(obs["observable_at"], utc=True)
    observable_positions = X.index.get_indexer(observable_ts).astype(float)
    observable_positions = np.where(
        labels_full.loc[aligned, "barrier_hit"] == "incomplete", np.nan, observable_positions
    )
    folds = _fold_plan(len(X), observable_positions)

    factories = _model_factories()
    model_results = {
        name: _evaluate_one_model(name, factory, X, y, folds) for name, factory in factories.items()
    }
    # --- Holdout integrity (contaminated / not_yet_available, never pristine) ---
    holdout_start = str(frame.index[holdout_raw_idx[0]]) if len(holdout_raw_idx) else None
    holdout_end = str(frame.index[holdout_raw_idx[-1]]) if len(holdout_raw_idx) else None
    # Production model training cutoff (known from the logistic artifact build).
    training_cutoff = "2026-10-01T00:00:00+00:00"
    # Research freeze: Q2.2 methods are frozen now; no post-freeze data exists yet.
    research_freeze = _utc_now()
    holdout_integrity = assess_holdout_integrity(
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        dataset_start=str(frame.index[0]),
        dataset_end=str(frame.index[-1]),
        training_cutoff=training_cutoff,
        research_freeze_timestamp=research_freeze,
        access_count=1,
        model_frozen=True,
        parameters_frozen=True,
        previously_inspected=True,
        influenced_feature_or_threshold_selection=True,
    )
    prospective = assess_prospective_holdout(
        dataset_end=str(frame.index[-1]),
        research_freeze_timestamp=research_freeze,
        dataset_start=str(frame.index[0]),
    )

    result = {
        "schema": "signalforge.quantitative-methodology-correction.q2-2.v1",
        "phase": "Q2.2",
        "generated_at": _utc_now(),
        "dataset": {
            "path": str(DATA_PATH),
            "sha256": file_hash,
            "rows": len(frame),
            "start": str(frame.index[0]),
            "end": str(frame.index[-1]),
            "research_rows": len(research_frame),
            "usable_rows": len(X),
            "feature_count": len(numeric_cols),
            "warmup_dropped": int((~finite_mask).sum()),
        },
        "target": target_metadata(definition, labels_full, file_hash),
        "holdout": {
            "rows_reserved": len(holdout_raw_idx),
            "start": holdout_start,
            "end": holdout_end,
            "evaluated": False,
            "status": holdout_integrity["status"],
            "integrity": holdout_integrity,
            "prospective": prospective,
            "note": (
                "Historical holdout overlaps the production-model training period "
                "(cutoff 2026-10-01) and has been inspected; it is contaminated and "
                "not eligible for prospective validation. No post-freeze data exists "
                "yet, so holdout_evaluated is false."
            ),
        },
        "protocol": {
            "horizon_bars": HORIZON,
            "pt_mult": definition.pt_mult,
            "sl_mult": definition.sl_mult,
            "vol_window": definition.vol_window,
            "entry_convention": definition.entry_convention,
            "fold_count": FOLD_COUNT,
            "train_window_cap": TRAIN_WINDOW_CAP,
            "embargo_bars": max(EMBARGO_BARS, HORIZON),
            "purge_basis": "actual observable bar (label_intervals from observable_at)",
            "class_weight": "balanced (training fold only)",
        },
        "models_skipped": [name for name in ("lightgbm",) if name not in factories],
        "models": model_results,
        "conclusion": "DIAGNOSTIC_ONLY — no strategy or model promotion",
        "limitations": [
            "All evaluation is on the research head; the historical holdout is "
            "contaminated and the prospective holdout does not yet exist.",
            "Scores are UNCALIBRATED rankings, not probabilities.",
            "Class weighting is computed per training fold only.",
        ],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")

    lines = [
        "# Quantitative Methodology Correction — Q2.2",
        "",
        f"Generated: {result['generated_at']}",
        "",
        "## Purpose",
        "",
        "Correct the Q2.1/Q3 methodology defects identified in the evidence audit: "
        "align the training target with the simulated strategy, apply class-weighted "
        "training per fold, purge by actual event duration, and report collapse-aware, "
        "per-class metrics. Diagnostic only — no model promotion.",
        "",
        "## Target definition (execution-aligned)",
        "",
        "- Entry: open of bar ``t+1`` (decision at close of ``t``)",
        f"- Profit target: entry x (1 + {definition.pt_mult} x causal vol)",
        f"- Stop loss: entry x (1 - {definition.sl_mult} x causal vol)",
        f"- Max horizon: {HORIZON} bars",
        "- Same-bar collision: conservative (neutral label / stop-first execution)",
        f"- Definition hash: {definition.definition_hash()[:16]}",
        "",
        "## Dataset",
        "",
        f"- Usable research rows: {len(X)}",
        f"- Features: {len(numeric_cols)}",
        f"- Class counts: {result['target']['diagnostics']['class_counts']}",
        f"- Neutral (0) fraction: {result['target']['diagnostics']['neutral_fraction']}",
        "",
        "## Model comparison (walk-forward aggregate)",
        "",
        "| Model | Accuracy | Bal. Acc. | Macro-F1 | MCC | Collapsed | Val Rows |",
        "|-------|----------|-----------|----------|-----|-----------|----------|",
    ]
    for name in ("majority", "trend", "logistic", "lightgbm"):
        res = model_results.get(name, {})
        agg = res.get("aggregate", {})
        collapse = res.get("collapse", {})
        lines.append(
            f"| {name} | {agg.get('accuracy', 'n/a')} | {agg.get('balanced_accuracy', 'n/a')} | "
            f"{agg.get('macro_f1', 'n/a')} | {agg.get('mcc', 'n/a')} | "
            f"{collapse.get('collapsed', 'n/a')} | {res.get('total_val_rows', 'n/a')} |"
        )
    lines.append("")
    lines.append("## Holdout integrity")
    lines.append("")
    lines.append(f"- Status: {holdout_integrity['status']}")
    lines.append(f"- Evaluation status: {holdout_integrity['evaluation_status']}")
    lines.append(
        f"- Prospective validated: {prospective.get('eligible_for_prospective_validation', False)}"
    )
    lines.append(f"- Reasons: {'; '.join(holdout_integrity.get('reasons', [])) or 'none'}")
    lines.append("")
    lines.append("## Decision")
    lines.append("")
    lines.append(
        "**No model is promoted by Q2.2.** Diagnostic evidence only. "
        "The corrected target shows whether any model family has genuine "
        "directional edge under execution-aligned semantics. Economic "
        "validation requires Q3's barrier-aligned simulator with a "
        "genuinely prospective holdout, which does not yet exist."
    )
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")
    return result


def main() -> int:
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
