"""End-to-end logistic-baseline training over a local snapshot (Phase 8).

Pipeline (all causal, all explicit, single-process):

1. Load a snapshot with :func:`load_snapshot` (checksum/row/dupe verified).
2. Build causal features with :func:`compute_features` (bounded chunks);
   keep numeric finite feature columns only, drop warm-up NaNs with an
   explicit count (never silently).
3. Label with :func:`label_triple_barrier`; keep ``valid_mask()`` rows only;
   verify :func:`assert_no_leakage` on the aligned frame.
4. Chronological train/validation/holdout split with purge (``observable_at``
   past the cutoff is excluded from training) plus embargo bars.
5. Fit :class:`SklearnLogistic` on training rows only (train-only scaler);
   cap training rows at the configured maximum (most-recent, recorded).
6. Report exploratory validation metrics + final holdout metrics with a
   majority-class baseline on identical splits. No thresholds are tuned.
7. Persist via :func:`save_model` (no overwrite), reload via
   :func:`load_model`, verify fitted state, schema, checksum, and
   prediction compatibility.

The fitted scores are UNCALIBRATED ranking scores. Training success says
nothing about strategy profitability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from crypto_engine.training.splits import ChronoSplit, chronological_split

MIN_TRAIN_ROWS = 10
MIN_EVAL_ROWS = 5


@dataclass
class TrainRequest:
    """All inputs to one training run (resolved snapshot or discovery)."""

    snapshot_dir: Path | None = None
    symbol: str | None = None
    timeframe: str | None = None
    storage_root: Path | None = None
    registry_root: Path | None = None
    train_frac: float = 0.6
    valid_frac: float = 0.2
    pt_mult: float | None = None
    sl_mult: float | None = None
    max_horizon_bars: int | None = None
    extra_meta: dict[str, Any] = field(default_factory=dict)


def _resolve_snapshot(request: TrainRequest, default_storage: Path) -> Path:
    """Locate the snapshot dir: explicit path wins, else latest match."""
    from crypto_engine.datasets.snapshots import list_snapshots

    if request.snapshot_dir is not None:
        snap = Path(request.snapshot_dir)
        if not snap.is_dir():
            raise FileNotFoundError(
                f"model-train: snapshot directory not found: {snap}. "
                "Pass --snapshot <dir> pointing at a snapshot directory, or "
                "use --symbol/--timeframe for discovery under the data storage."
            )
        return snap
    storage = Path(request.storage_root) if request.storage_root is not None else default_storage
    manifests = list_snapshots(storage)
    cands = [
        m
        for m in manifests
        if (request.symbol is None or m.symbol == request.symbol)
        and (request.timeframe is None or m.timeframe == request.timeframe)
    ]
    if not cands:
        raise FileNotFoundError(
            "model-train: no snapshot matches "
            f"symbol={request.symbol!r} timeframe={request.timeframe!r} under {storage}. "
            "Fetch one with `sf data fetch` or `sf fetch`, or pass --snapshot <dir>."
        )
    best = max(cands, key=lambda m: (str(m.actual_end or ""), str(m.fetched_at or "")))
    base = storage / "processed" / best.exchange / best.symbol.replace("/", "-") / best.timeframe
    snap_dir = base / best.snapshot_id
    if not snap_dir.is_dir():
        raise FileNotFoundError(
            f"model-train: manifest {best.snapshot_id} has no directory at {snap_dir}."
        )
    return snap_dir


def _prepare_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """UTC-index, chronological, finite-OHLCV frame (never invents rows)."""
    if frame.empty:
        raise ValueError("model-train: snapshot frame is empty.")
    if "timestamp" not in frame.columns:
        raise ValueError("model-train: snapshot frame lacks a 'timestamp' column.")
    work = frame.copy()
    ts = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    if bool(ts.isna().any()):
        n_bad = int(ts.isna().sum())
        raise ValueError(f"model-train: {n_bad} unparseable timestamps (no silent drops).")
    work = work.assign(timestamp=ts).sort_values("timestamp").reset_index(drop=True)
    if bool(work["timestamp"].duplicated().any()):
        raise ValueError("model-train: duplicate timestamps after sorting.")
    for col in ("open", "high", "low", "close"):
        if col not in work.columns:
            raise ValueError(f"model-train: snapshot missing OHLC column {col!r}.")
        vals = pd.to_numeric(work[col], errors="coerce")
        if bool(vals.isna().any()) or not bool(np.isfinite(vals.to_numpy(dtype=float)).all()):
            raise ValueError(f"model-train: column {col!r} has non-finite values.")
    if "volume" in work.columns:
        vals = pd.to_numeric(work["volume"], errors="coerce").fillna(0.0)
        if bool((vals.to_numpy(dtype=float) < 0.0).any()) or not bool(
            np.isfinite(vals.to_numpy(dtype=float)).all()
        ):
            raise ValueError("model-train: column 'volume' has invalid values.")
        work["volume"] = vals
    else:
        work["volume"] = 0.0
    return work.set_index("timestamp")


def _build_matrix_with_config(
    ohlcv: pd.DataFrame,
    chunk_rows: int,
    pt_mult: float | None,
    sl_mult: float | None,
    max_horizon_bars: int | None,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, dict[str, Any], str, dict[str, Any]]:
    """Feature/label matrices + label metadata.

    Returns ``(X, y, observable_at, counts, label_hash, label_config_dict)``.
    """
    from crypto_engine.features.engineering import compute_features
    from crypto_engine.labels.triple_barrier import (
        TripleBarrierConfig,
        assert_no_leakage,
        label_config_hash,
        label_triple_barrier,
        valid_mask,
    )

    feats = compute_features(ohlcv, chunk_rows=chunk_rows)
    numeric_cols = [c for c in feats.columns if pd.api.types.is_numeric_dtype(feats[c])]
    dropped_cols = [c for c in feats.columns if c not in numeric_cols]
    X_full = feats[numeric_cols].copy()
    finite = np.isfinite(X_full.to_numpy(dtype=float)).all(axis=1)
    warmup_dropped = int((~finite).sum())
    X_full = X_full.loc[finite]

    cfg_kwargs: dict[str, Any] = {}
    if pt_mult is not None:
        cfg_kwargs["pt_mult"] = float(pt_mult)
    if sl_mult is not None:
        cfg_kwargs["sl_mult"] = float(sl_mult)
    if max_horizon_bars is not None:
        cfg_kwargs["max_horizon_bars"] = int(max_horizon_bars)
    label_cfg = TripleBarrierConfig(**cfg_kwargs)
    labels = label_triple_barrier(ohlcv)
    mask = valid_mask(labels)
    usable_idx = X_full.index.intersection(labels.index[mask])
    X = X_full.loc[usable_idx].copy()
    y = labels.loc[usable_idx, "label"].astype(int)
    obs = labels.loc[usable_idx, "observable_at"]
    if len(X) == 0:
        raise ValueError(
            "model-train: no usable rows after warm-up + incomplete-label exclusion "
            f"(features had {len(X_full)} finite rows). Need a larger snapshot or a "
            "shorter label horizon."
        )
    assert_no_leakage(X, labels.loc[usable_idx])
    counts = {
        "feature_rows": len(X_full),
        "warmup_dropped": warmup_dropped,
        "dropped_columns": dropped_cols,
        "incomplete_labels_excluded": int((~mask).sum()),
        "usable_rows": len(X),
    }
    obs = labels.loc[usable_idx, "observable_at"]
    return X, y, obs, counts, label_config_hash(label_cfg), label_cfg.model_dump(mode="json")


def _class_counts(y: np.ndarray) -> dict[str, int]:
    uniq, counts = np.unique(np.asarray(y).ravel(), return_counts=True)
    return {str(int(k)): int(v) for k, v in zip(uniq.tolist(), counts.tolist(), strict=True)}


def train_model(request: TrainRequest, settings: Any) -> dict[str, Any]:
    """Run the full training workflow; return a JSON-serializable summary."""
    from crypto_engine.calibration.reliability import classification_report_small
    from crypto_engine.datasets.snapshots import load_snapshot
    from crypto_engine.models.baselines import MajorityBaseline
    from crypto_engine.models.sklearn_wrappers import SklearnLogistic
    from crypto_engine.registry.store import load_model, save_model

    model_cfg = settings.model
    validation_cfg = settings.validation
    chunk_rows = int(model_cfg.chunk_rows)
    embargo_bars = int(validation_cfg.embargo_bars)

    default_storage = Path(settings.data.storage_path)
    snap_dir = _resolve_snapshot(request, default_storage)
    frame, manifest = load_snapshot(snap_dir)
    ohlcv = _prepare_frame(frame)
    n_snapshot = len(ohlcv)

    X, y, obs, counts, lbl_hash, lbl_cfg = _build_matrix_with_config(
        ohlcv, chunk_rows, request.pt_mult, request.sl_mult, request.max_horizon_bars
    )
    feature_names = list(X.columns)
    times = pd.DatetimeIndex(X.index)
    obs = pd.Series(pd.to_datetime(obs, utc=True), index=X.index)

    split: ChronoSplit = chronological_split(
        times,
        obs,
        train_frac=float(request.train_frac),
        valid_frac=float(request.valid_frac),
        embargo_bars=embargo_bars,
        min_train_rows=MIN_TRAIN_ROWS,
        min_eval_rows=MIN_EVAL_ROWS,
    )
    y_all = y.to_numpy(dtype=int)
    X_train_full = X.iloc[split.train_idx]
    y_train_full = y_all[np.asarray(split.train_idx, dtype=int)]

    classes = sorted(int(c) for c in np.unique(y_train_full).tolist())
    if len(classes) < 2:
        raise ValueError(
            "model-train: training labels are single-class "
            f"({(classes[0] if classes else 'none')}); logistic regression needs >= 2 "
            "classes. Use a more volatile window, different barriers, or a "
            "longer snapshot — never relabel to manufacture a second class."
        )

    max_rows = model_cfg.max_rows_train
    rows_capped = 0
    if max_rows is not None and len(X_train_full) > int(max_rows):
        rows_capped = int(len(X_train_full) - int(max_rows))
        X_train = X_train_full.iloc[-int(max_rows) :]
        y_train = y_train_full[-int(max_rows) :]
    else:
        X_train, y_train = X_train_full, y_train_full

    train_start = str(times[split.train_idx[0]].isoformat())
    train_end = str(times[split.train_idx[-1]].isoformat())
    train_cutoff = str(times[min(split.valid_idx[0], len(times) - 1)].isoformat())
    train_obs_max = str(
        pd.to_datetime(obs.iloc[np.asarray(split.train_idx, dtype=int)]).max().isoformat()
    )
    valid_idx = np.asarray(split.valid_idx, dtype=int)
    holdout_idx = np.asarray(split.holdout_idx, dtype=int)

    model = SklearnLogistic(random_state=int(model_cfg.random_state))
    try:
        model.fit(X_train, y_train, feature_names=feature_names)
    except Exception as exc:
        raise ValueError(f"model-train: fitting failed: {exc}") from exc
    scaler_mean = [float(v) for v in model.scaler_stats()["mean"][:4]]

    baseline = MajorityBaseline(random_state=int(model_cfg.random_state))
    baseline.fit(X_train, y_train, feature_names=feature_names)

    def _report(idxs: np.ndarray) -> dict[str, Any]:
        yp = model.predict(X.iloc[idxs])
        yb = baseline.predict(X.iloc[idxs])
        rep = classification_report_small(y_all[idxs], yp)
        brep = classification_report_small(y_all[idxs], yb)
        return {
            "n": len(idxs),
            "model": rep,
            "majority_baseline": {"accuracy": brep["accuracy"], "prior": baseline.prior_},
        }

    valid_report = _report(valid_idx)
    holdout_report = _report(holdout_idx)

    record_meta: dict[str, Any] = {
        "dataset_snapshot_id": manifest.snapshot_id,
        "dataset_checksum": manifest.checksum_sha256,
        "label_config": lbl_cfg,
        "label_hash": lbl_hash,
        "train_start": train_start,
        "train_end": train_end,
        "status": "trained",
        "limitations": (
            "UNCALIBRATED logistic scores (ranking only, not probabilities); "
            "baseline on one snapshot; small-sample evaluation; not profitability evidence."
        ),
        "known_incompatibilities": [],
        "version": "1.0",
    }
    registry_root = (
        Path(request.registry_root)
        if request.registry_root is not None
        else Path(settings.registry.path)
    )
    try:
        model_dir = save_model(model, record_meta, registry_root)
    except FileExistsError as exc:
        raise FileExistsError(
            f"model-train: artifact already registered: {exc}. Refusing to overwrite. "
            "Use a different --registry directory; existing artifacts are never "
            "modified or deleted automatically."
        ) from exc

    reloaded, record = load_model(model_dir)
    if not reloaded.is_fitted:
        raise ValueError("model-train: reloaded artifact reports unfitted (corrupt save).")
    if list(reloaded.feature_names_ or []) != feature_names:
        raise ValueError("model-train: reloaded artifact feature schema mismatch.")
    probe = X.iloc[holdout_idx[: min(5, len(holdout_idx))]]
    if not np.array_equal(np.asarray(model.predict(probe)), np.asarray(reloaded.predict(probe))):
        raise ValueError("model-train: reloaded artifact predictions differ (corrupt save).")

    return {
        "snapshot": {
            "snapshot_id": manifest.snapshot_id,
            "dir": str(snap_dir),
            "symbol": manifest.symbol,
            "timeframe": manifest.timeframe,
            "rows": n_snapshot,
            "range": {"start": manifest.actual_start, "end": manifest.actual_end},
            "checksum_sha256": manifest.checksum_sha256,
        },
        "features": {
            "names": feature_names,
            "n_features": len(feature_names),
            "dropped_columns": counts["dropped_columns"],
            "warmup_dropped": counts["warmup_dropped"],
            "incomplete_labels_excluded": counts["incomplete_labels_excluded"],
            "usable_rows": counts["usable_rows"],
        },
        "labels": {
            "config": lbl_cfg,
            "hash": lbl_hash,
            "class_counts": _class_counts(y_all),
        },
        "split": {
            "train_frac": float(request.train_frac),
            "valid_frac": float(request.valid_frac),
            "train_rows": len(split.train_idx),
            "valid_rows": len(split.valid_idx),
            "holdout_rows": len(split.holdout_idx),
            "train_range": [train_start, train_end],
            "train_cutoff": train_cutoff,
            "train_observable_max": train_obs_max,
            "valid_range": [
                str(times[split.valid_idx[0]].isoformat()),
                str(times[split.valid_idx[-1]].isoformat()),
            ],
            "holdout_range": [
                str(times[split.holdout_idx[0]].isoformat()),
                str(times[split.holdout_idx[-1]].isoformat()),
            ],
            "train_class_counts": _class_counts(y_train_full),
            "purged_rows": split.purged_rows,
            "embargoed_rows": split.embargoed_rows,
            "embargo_bars": embargo_bars,
            "rows_capped_by_max_rows_train": rows_capped,
            "max_rows_train": model_cfg.max_rows_train,
            "notes": list(split.notes),
        },
        "model": {
            "family": model.family,
            "params": model.to_dict().get("params", {}),
            "random_state": int(model_cfg.random_state),
            "scaler_fit_on": "training rows only",
            "scaler_mean_head": scaler_mean[:4],
        },
        "validation_exploratory": valid_report,
        "holdout_final": holdout_report,
        "artifact": {
            "model_dir": str(model_dir),
            "model_id": record.model_id,
            "artifact_sha256": record.artifact_sha256,
            "feature_schema_hash": record.feature_schema_hash,
            "label_hash": record.label_hash,
            "reload_verified": True,
        },
        "calibration": {
            "status": "uncalibrated",
            "note": "Scores are ranking-only, not probabilities.",
        },
        "disclaimer": (
            "Training success does not establish strategy profitability. Raw model "
            "scores are UNCALIBRATED and must not be treated as calibrated "
            "probabilities, win rates, or expected returns."
        ),
    }


__all__ = ["MIN_EVAL_ROWS", "MIN_TRAIN_ROWS", "TrainRequest", "train_model"]
