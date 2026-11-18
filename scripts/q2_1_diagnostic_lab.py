"""Q2.1 — targeted diagnostic research; no holdout reads or model promotion.

Evaluates a predeclared triple-barrier horizon grid on the research head only.
Each horizon gets purged/embargoed walk-forward folds and the same model
families. No hyperparameter search is performed. Optional LightGBM is used only
when its optional dependency is installed.

Run from the repository root:
    uv run python scripts/q2_1_diagnostic_lab.py
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
    TripleBarrierConfig,
    assert_no_leakage,
    label_triple_barrier,
    valid_mask,
)
from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline  # noqa: E402
from crypto_engine.models.sklearn_wrappers import SklearnLogistic  # noqa: E402
from crypto_engine.research.governance import assess_holdout_integrity  # noqa: E402
from crypto_engine.validation.folds import label_intervals, make_folds  # noqa: E402
from crypto_engine.validation.holdout import split_holdout  # noqa: E402

DATA_PATH = ROOT / "data/canonical/binance/BTC-USDT/1h/candles.parquet"
OUT_JSON = ROOT / "docs/validation/results/quantitative_diagnostics_q2_1.json"
OUT_MD = ROOT / "docs/validation/QUANTITATIVE_DIAGNOSTICS_Q2_1.md"
HORIZONS = (6, 12, 24, 48, 72)
HOLDOUT_FRACTION = 0.15
FOLD_COUNT = 5
EMBARGO_BARS = 24
TRAIN_WINDOW_CAP = 6_000
SEED = 42


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_data() -> pd.DataFrame:
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


def _models() -> tuple[dict[str, Any], list[str]]:
    from crypto_engine.models.sklearn_wrappers import LightGBMModel

    available: dict[str, Any] = {
        "majority": lambda: MajorityBaseline(random_state=SEED),
        "trend": lambda: TrendBaseline(
            random_state=SEED, ratio_col="trend_ratio_20_50"
        ),
        "logistic": lambda: SklearnLogistic(random_state=SEED, C=1.0, max_iter=300),
    }
    skipped: list[str] = []
    try:
        import lightgbm  # noqa: F401

        available["lightgbm"] = lambda: LightGBMModel(
            random_state=SEED,
            n_estimators=75,
            num_leaves=15,
            min_child_samples=30,
        )
    except ImportError:
        skipped.append("lightgbm: optional dependency not installed")
    return available, skipped


def _class_counts(labels: pd.Series) -> dict[str, Any]:
    counts = labels.value_counts().reindex([-1, 0, 1], fill_value=0)
    total = int(counts.sum())
    return {
        "total": total,
        "counts": {str(int(k)): int(v) for k, v in counts.items()},
        "fractions": {
            str(int(k)): (float(v / total) if total else None) for k, v in counts.items()
        },
        "majority_fraction": (float(counts.max() / total) if total else None),
        "class_count": int((counts > 0).sum()),
    }


def _fold_plan(n: int, horizon: int, valid_mask: np.ndarray):
    val_window = max(250, min(800, n // 25))
    val_window = min(val_window, max(1, (n - 100) // FOLD_COUNT))
    train_window = min(TRAIN_WINDOW_CAP, n - FOLD_COUNT * val_window - horizon)
    if train_window <= horizon + 100:
        raise ValueError(
            f"Insufficient research rows for Q2.1 folds (n={n}, val={val_window}, horizon={horizon})."
        )
    return make_folds(
        n=n,
        train_window=train_window,
        val_window=val_window,
        n_folds=FOLD_COUNT,
        embargo_bars=max(EMBARGO_BARS, horizon),
        purge_intervals=label_intervals(None, horizon),
        min_samples=100,
        valid_idx=valid_mask,
    )


def _evaluate_one_model(
    name: str,
    factory: Any,
    X: pd.DataFrame,
    y: pd.Series,
    folds: list[Any],
) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

    fold_rows: list[dict[str, Any]] = []
    all_y: list[int] = []
    all_pred: list[int] = []
    values = y.to_numpy(dtype=int)
    for fold in folds:
        try:
            estimator = factory()
            train_idx = np.asarray(fold.train_idx, dtype=int)
            val_idx = np.asarray(fold.val_idx, dtype=int)
            estimator.fit(X.iloc[train_idx], values[train_idx])
            predictions = np.asarray(estimator.predict(X.iloc[val_idx]), dtype=int).reshape(-1)
            if predictions.shape != values[val_idx].shape:
                raise ValueError("prediction and validation lengths differ")
            actual = values[val_idx]
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
                    "accuracy": float(accuracy_score(actual, predictions)),
                    "balanced_accuracy": float(
                        balanced_accuracy_score(actual, predictions)
                    ),
                    "macro_f1": float(
                        f1_score(actual, predictions, labels=[-1, 0, 1], average="macro", zero_division=0)
                    ),
                    "actual_distribution": _class_counts(pd.Series(actual)),
                    "predicted_distribution": _class_counts(pd.Series(predictions)),
                    "status": "ok",
                }
            )
            all_y.extend(actual.tolist())
            all_pred.extend(predictions.tolist())
        except Exception as exc:  # record failure; never silently drop a fold
            fold_rows.append(
                {
                    "fold": int(fold.fold_id),
                    "train_rows": len(fold.train_idx),
                    "validation_rows": len(fold.val_idx),
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            )
    if not all_y:
        return {"model": name, "status": "all_folds_failed", "folds": fold_rows}
    actual = np.asarray(all_y, dtype=int)
    predicted = np.asarray(all_pred, dtype=int)
    return {
        "model": name,
        "status": "ok" if all(r["status"] == "ok" for r in fold_rows) else "partial",
        "validation_rows": int(actual.size),
        "aggregate": {
            "accuracy": float(accuracy_score(actual, predicted)),
            "balanced_accuracy": float(balanced_accuracy_score(actual, predicted)),
            "macro_f1": float(
                f1_score(actual, predicted, labels=[-1, 0, 1], average="macro", zero_division=0)
            ),
            "actual_distribution": _class_counts(pd.Series(actual)),
            "predicted_distribution": _class_counts(pd.Series(predicted)),
        },
        "folds": fold_rows,
    }


def _label_config(horizon: int) -> TripleBarrierConfig:
    """Build a validated label config using the canonical horizon field."""
    return TripleBarrierConfig(
        pt_mult=2.0,
        sl_mult=1.0,
        max_horizon_bars=horizon,
        vol_window=20,
        cost_bp=0,
        side="both",
    )


def run() -> dict[str, Any]:
    frame = _load_data()
    file_hash = _sha256(DATA_PATH)
    # Split the raw chronological bars first. Do not compute features or labels
    # on the reserved tail; masking those results after the fact is not isolation.
    research_raw_idx, holdout_raw_idx = split_holdout(len(frame), HOLDOUT_FRACTION)
    research_frame = frame.iloc[research_raw_idx].copy()
    raw_features = compute_features(research_frame, chunk_rows=5_000)
    numeric_cols = [
        col for col in raw_features.columns if pd.api.types.is_numeric_dtype(raw_features[col])
    ]
    finite_mask = np.isfinite(raw_features[numeric_cols].to_numpy(dtype=float)).all(axis=1)
    features = raw_features.loc[finite_mask, numeric_cols]
    research_index = features.index
    factories, skipped_models = _models()

    horizon_results: list[dict[str, Any]] = []
    for horizon in HORIZONS:
        config = _label_config(horizon)
        labeled = label_triple_barrier(research_frame, config)
        label_mask = valid_mask(labeled)
        aligned_idx = features.index.intersection(labeled.index[label_mask])
        aligned_idx = aligned_idx.intersection(research_index)
        X = features.loc[aligned_idx]
        y = labeled.loc[aligned_idx, "label"].astype(int)
        observable = pd.to_datetime(labeled.loc[aligned_idx, "observable_at"], utc=True)
        assert_no_leakage(X, labeled.loc[aligned_idx])
        counts = _class_counts(y)
        folds = _fold_plan(len(X), horizon, observable.notna().to_numpy())
        model_results = [
            _evaluate_one_model(name, factory, X, y, folds)
            for name, factory in factories.items()
        ]
        valid_models = [row for row in model_results if row.get("aggregate")]
        horizon_results.append(
            {
                "horizon_bars": horizon,
                "label_config": {"pt_mult": 2.0, "sl_mult": 1.0, "side": "both"},
                "research_rows": len(X),
                "research_start": str(X.index[0]),
                "research_end": str(X.index[-1]),
                "label_distribution": counts,
                "fold_plan": {
                    "fold_count": FOLD_COUNT,
                    "train_window": int(folds[0].n_train),
                    "validation_rows_per_fold": int(folds[0].n_val),
                    "embargo_bars": max(EMBARGO_BARS, horizon),
                },
                "models": model_results,
                "best_validation_balanced_accuracy": max(
                    (row["aggregate"]["balanced_accuracy"] for row in valid_models),
                    default=None,
                ),
                "best_validation_macro_f1": max(
                    (row["aggregate"]["macro_f1"] for row in valid_models), default=None
                ),
                "promotion": "NOT_EVALUATED — diagnostics only; no production authority",
            }
        )
        print(
            f"horizon={horizon:>2} rows={len(X):>5} "
            f"class_balance={counts['fractions']} "
            f"models_ok={sum(row.get('status') == 'ok' for row in model_results)}"
        )

    reserved_tail_integrity = assess_holdout_integrity(
        holdout_start=str(frame.index[holdout_raw_idx[0]]) if len(holdout_raw_idx) else None,
        training_end="2026-10-01T00:00:00+00:00",
        access_count=1,
        model_frozen=True,
        parameters_frozen=True,
    )
    result = {
        "schema": "signalforge.quantitative-diagnostics.q2-1.v1",
        "phase": "Q2.1",
        "generated_at": _utc_now(),
        "dataset": {
            "path": "data/canonical/binance/BTC-USDT/1h/candles.parquet",
            "sha256": file_hash,
            "rows": len(frame),
            "start": str(frame.index[0]),
            "end": str(frame.index[-1]),
            "research_raw_rows": len(research_frame),
            "feature_count": len(numeric_cols),
        },
        "holdout": {
            "rows_reserved": len(holdout_raw_idx),
            "start": str(frame.index[holdout_raw_idx[0]]) if len(holdout_raw_idx) else None,
            "end": str(frame.index[holdout_raw_idx[-1]]) if len(holdout_raw_idx) else None,
            "evaluated": False,
            "status": "NOT_OPENED_BY_Q2_1_BUT_PREVIOUSLY_CONTAMINATED",
            "integrity": reserved_tail_integrity,
            "note": (
                "Q2.1 does not calculate features, labels or predictions on this tail. "
                "It overlaps the previously accessed Q2 holdout and production-model "
                "training period, so it is not pristine or promotion evidence."
            ),
        },
        "models_skipped": skipped_models,
        "horizons": horizon_results,
        "conclusion": "DIAGNOSTIC_ONLY — no strategy or model promotion",
        "limitations": [
            "The raw time split is applied before feature/label generation; no features, labels, or predictions are computed on the reserved tail.",
            "Raw model outputs are uncalibrated and used only for class predictions.",
            "Model comparisons do not establish economic edge; Q3 uses actual OHLC execution prices and costs.",
            "A genuinely prospective holdout must be collected after the frozen model/strategy timestamp.",
        ],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")

    lines = [
        "# Quantitative Diagnostics — Q2.1",
        "",
        f"Generated: {result['generated_at']}",
        "",
        "## Purpose",
        "",
        "Diagnose label horizon and class imbalance with repeated, purged, chronological "
        "walk-forward evaluation. This phase does not tune toward a target metric, open the "
        "historical holdout, or select a production model.",
        "",
        "## Dataset",
        "",
        f"- Rows: {len(frame)}",
        f"- Period: {frame.index[0]} to {frame.index[-1]}",
        f"- SHA-256: `{file_hash}`",
        "- Reserved tail: not evaluated by Q2.1; previously consumed/contaminated in Q2",
        "",
    ]
    for horizon_row in horizon_results:
        lines.extend(
            [
                f"## Horizon {horizon_row['horizon_bars']} bars",
                "",
                f"- Research rows: {horizon_row['research_rows']}",
                f"- Research period: {horizon_row['research_start']} to {horizon_row['research_end']}",
                f"- Class fractions: `{horizon_row['label_distribution']['fractions']}`",
                "",
                "| Model | Accuracy | Balanced accuracy | Macro-F1 | Status |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for model_row in horizon_row["models"]:
            agg = model_row.get("aggregate", {})
            lines.append(
                f"| {model_row['model']} | {agg.get('accuracy', 'n/a')} | "
                f"{agg.get('balanced_accuracy', 'n/a')} | {agg.get('macro_f1', 'n/a')} | "
                f"{model_row['status']} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Decision",
            "",
            "**No model is promoted by Q2.1.** This is diagnostic evidence only. "
            "Economic validation uses the Q3 next-open simulator with explicit cost stress; "
            "a new prospective holdout remains mandatory before any human promotion review.",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")
    return result


def main() -> int:
    result = run()
    print(
        f"Q2.1 complete: {len(result['horizons'])} horizons; "
        f"holdout evaluated={result['holdout']['evaluated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
