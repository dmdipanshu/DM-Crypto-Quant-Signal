"""Frozen-model forward validation on genuinely new data (Phase 10).

Evaluates an already-trained, UNCALIBRATED classifier on a snapshot whose
rows are strictly newer than a cutoff (normally the end of the historical
training snapshot). Nothing is fitted here: no scalers, no thresholds, no
feature selection, no calibration, no model choice. The majority baseline is
derived from the *historical training split only*.

Read-only with respect to the model artifact and both snapshots; the only
write is the JSON report file (new unique filename per run, never overwrite).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _parse_cutoff(value: str) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"forward: eval_start must be an ISO timestamp, got {value!r}.") from exc
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def load_eval_frame(snapshot_dir: Path | str) -> tuple[pd.DataFrame, Any]:
    """Load + validate a snapshot frame (UTC-indexed, monotonic, finite OHLCV)."""
    from crypto_engine.datasets.snapshots import load_snapshot

    frame, manifest = load_snapshot(Path(snapshot_dir))
    if frame.empty:
        raise ValueError("forward: snapshot frame is empty.")
    if "timestamp" not in frame.columns:
        raise ValueError("forward: snapshot lacks a 'timestamp' column.")
    work = frame.copy()
    ts = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    if bool(ts.isna().any()):
        raise ValueError("forward: snapshot has unparseable timestamps.")
    work = work.assign(timestamp=ts).sort_values("timestamp").reset_index(drop=True)
    if bool(work["timestamp"].duplicated().any()):
        raise ValueError("forward: duplicate timestamps after sorting.")
    for col in ("open", "high", "low", "close"):
        if col not in work.columns:
            raise ValueError(f"forward: snapshot missing OHLC column {col!r}.")
        vals = pd.to_numeric(work[col], errors="coerce")
        if bool(vals.isna().any()) or not bool(np.isfinite(vals.to_numpy(dtype=float)).all()):
            raise ValueError(f"forward: column {col!r} has non-finite values.")
    if "volume" in work.columns:
        vals = pd.to_numeric(work["volume"], errors="coerce").fillna(0.0)
        if bool((vals.to_numpy(dtype=float) < 0.0).any()) or not bool(
            np.isfinite(vals.to_numpy(dtype=float)).all()
        ):
            raise ValueError("forward: column 'volume' has invalid values.")
        work["volume"] = vals
    else:
        work["volume"] = 0.0
    return work.set_index("timestamp").sort_index(), manifest


def build_usable_matrix(
    ohlcv: pd.DataFrame, label_config: Any, chunk_rows: int = 5000
) -> tuple[pd.DataFrame, pd.Series, pd.Series, dict[str, Any]]:
    """Causal features + complete triple-barrier labels, inner-joined.

    Returns ``(X, y, observable_at, counts)``. Raises on empty usable sets.
    Asserts no-leakage invariants on the aligned frame.
    """
    from crypto_engine.features.engineering import compute_features
    from crypto_engine.labels.triple_barrier import (
        TripleBarrierConfig,
        assert_no_leakage,
        valid_mask,
    )

    cfg = (
        label_config
        if isinstance(label_config, TripleBarrierConfig)
        else TripleBarrierConfig.model_validate(label_config or {})
    )
    feats = compute_features(ohlcv, chunk_rows=chunk_rows)
    numeric = [c for c in feats.columns if pd.api.types.is_numeric_dtype(feats[c])]
    finite = np.isfinite(feats[numeric].to_numpy(dtype=float)).all(axis=1)
    warmup_dropped = int((~finite).sum())
    xf = feats[numeric].loc[finite]
    if len(xf) == 0:
        raise ValueError("forward: no finite feature rows (all warm-up/degenerate).")
    from crypto_engine.labels.triple_barrier import label_triple_barrier

    labels = label_triple_barrier(ohlcv, cfg)
    mask = valid_mask(labels)
    incomplete = int((~mask).sum())
    idx = xf.index.intersection(labels.index[mask])
    if len(idx) == 0:
        raise ValueError("forward: no usable rows after label-completeness filtering.")
    X = xf.loc[idx].copy()
    y = labels.loc[idx, "label"].astype(int)
    obs = pd.Series(pd.to_datetime(labels.loc[idx, "observable_at"], utc=True), index=idx)
    assert_no_leakage(X, labels.loc[idx])
    counts = {
        "feature_rows": len(xf),
        "warmup_dropped": warmup_dropped,
        "incomplete_excluded": incomplete,
        "usable_rows": len(X),
    }
    return X, y, obs, counts


def historical_majority(
    snapshot_dir: Path | str,
    label_config: Any,
    train_frac: float = 0.6,
    valid_frac: float = 0.2,
    embargo_bars: int = 24,
    chunk_rows: int = 5000,
    max_rows_train: int | None = None,
) -> tuple[int, dict[str, int], dict[str, Any]]:
    """Majority class of the historical *training* split (deterministic replay).

    Reuses the exact split procedure (purge + embargo). No fitting of any kind.
    """
    from crypto_engine.training.splits import chronological_split

    ohlcv, manifest = load_eval_frame(snapshot_dir)
    X, y, obs, _counts = build_usable_matrix(ohlcv, label_config, chunk_rows=chunk_rows)
    split = chronological_split(
        pd.DatetimeIndex(X.index),
        obs,
        train_frac=train_frac,
        valid_frac=valid_frac,
        embargo_bars=embargo_bars,
    )
    train_idx = np.asarray(split.train_idx, dtype=int)
    if max_rows_train is not None and len(train_idx) > int(max_rows_train):
        train_idx = train_idx[-int(max_rows_train) :]
    ytr = np.asarray(y)[train_idx]
    uniq, counts_arr = np.unique(ytr, return_counts=True)
    majority = int(uniq[int(np.argmax(counts_arr))])
    return (
        majority,
        {str(int(k)): int(v) for k, v in zip(uniq.tolist(), counts_arr.tolist(), strict=True)},
        {
            "n_train": len(train_idx),
            "purged": split.purged_rows,
            "embargoed": split.embargoed_rows,
            # Manifest of the snapshot actually loaded (verified by load_snapshot);
            # used to validate the artifact-recorded provenance before reporting it.
            "snapshot_id": manifest.snapshot_id,
            "checksum_sha256": manifest.checksum_sha256,
        },
    )


def select_eval_rows(
    times: pd.DatetimeIndex, eval_start_iso: str
) -> tuple[np.ndarray, int, dict[str, int]]:
    """Boolean-select rows strictly after ``eval_start`` (never <=).

    Returns ``(positions, n_excluded_before_or_at_cutoff, {"n_eligible": ...})``.
    Raises if nothing is eligible.
    """
    cutoff = _parse_cutoff(eval_start_iso)
    eligible = np.asarray([bool(t > cutoff) for t in times])
    n_excluded = int(len(times) - int(eligible.sum()))
    if int(eligible.sum()) == 0:
        raise ValueError(
            "forward: no evaluation rows strictly after "
            f"{cutoff.isoformat()} (n={len(times)}); fetch a newer snapshot."
        )
    return np.flatnonzero(eligible), n_excluded, {"n_eligible": int(eligible.sum())}


def verify_model_artifact(model_dir: Path | str) -> tuple[Any, Any]:
    """Load the registry artifact, verifying checksum, schema, and fitted state."""
    from crypto_engine.registry.store import load_model

    model, record = load_model(model_dir)
    if not model.is_fitted:
        raise ValueError("forward: registered artifact reports unfitted; refusing to evaluate.")
    feature_names = list(getattr(model, "feature_names_", None) or [])
    if not feature_names:
        raise ValueError("forward: artifact has no recorded feature schema.")
    return model, record


def resolve_historical_snapshot_id(
    recorded_snapshot_id: str | None,
    recorded_checksum: str | None,
    loaded_snapshot_id: str | None,
    loaded_checksum: str | None,
) -> str | None:
    """Return the artifact-recorded historical snapshot id, verified.

    The artifact record is authoritative for which dataset trained the model;
    the manifest of the snapshot actually loaded is the independent check. The
    id is reported only when both agree. When the record carries no id the
    result is ``None`` (explicit unknown, never invented). Any disagreement is
    rejected rather than silently reported.
    """
    recorded_id = (recorded_snapshot_id or "").strip()
    if not recorded_id:
        return None
    loaded_id = (loaded_snapshot_id or "").strip()
    if not loaded_id:
        raise ValueError(
            "forward: artifact records dataset_snapshot_id "
            f"{recorded_id!r} but the loaded historical snapshot manifest has no id; "
            "refusing unverified provenance."
        )
    if loaded_id != recorded_id:
        raise ValueError(
            "forward: historical snapshot provenance mismatch: artifact record "
            f"dataset_snapshot_id={recorded_id!r}, loaded manifest snapshot_id={loaded_id!r}."
        )
    recorded_sum = (recorded_checksum or "").strip()
    loaded_sum = (loaded_checksum or "").strip()
    if recorded_sum and loaded_sum and recorded_sum != loaded_sum:
        raise ValueError(
            "forward: historical snapshot checksum mismatch: artifact record "
            f"dataset_checksum={recorded_sum!r}, loaded manifest "
            f"checksum_sha256={loaded_sum!r}."
        )
    return recorded_id


def summarize_forward(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    majority_class: int,
    *,
    min_rows: int = 5,
) -> dict[str, Any]:
    """Deterministic metric bundle (accuracy, balanced accuracy, macro-F1, ...)."""
    from crypto_engine.calibration.reliability import classification_report_small

    yt = np.asarray(y_true).ravel()
    yp = np.asarray(y_pred).ravel()
    if yt.size == 0:
        raise ValueError("forward: empty evaluation set.")
    if yt.size < min_rows:
        raise ValueError(
            f"forward: only {yt.size} eligible rows (< minimum {min_rows}); "
            "sample too small for a meaningful report."
        )
    rep = classification_report_small(yt, yp)
    labels = [int(c) for c in rep["confusion_labels"]]
    per = rep["per_class"]
    f1s, recs = [], []
    per_out: dict[str, Any] = {}
    for c in labels:
        key = str(c)
        p = float(per[key]["precision"])
        r = float(per[key]["recall"])
        f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0
        f1s.append(f1)
        recs.append(r)
        per_out[key] = {
            "precision": p,
            "recall": r,
            "f1": f1,
            "support": int(per[key]["support"]),
        }
    uniq_t, counts_t = np.unique(yt, return_counts=True)
    uniq_p, counts_p = np.unique(yp, return_counts=True)
    majority_mask = yt == int(majority_class)
    return {
        "n": int(yt.size),
        "accuracy": float(rep["accuracy"]),
        "balanced_accuracy": float(sum(recs) / len(recs)),
        "macro_f1": float(sum(f1s) / len(f1s)),
        "majority_baseline_accuracy": float(majority_mask.mean()),
        "majority_class": int(majority_class),
        "per_class": per_out,
        "confusion_matrix": rep["confusion_matrix"],
        "confusion_labels": [int(c) for c in labels],
        "predicted_dist": {
            str(int(k)): int(v) for k, v in zip(uniq_p.tolist(), counts_p.tolist(), strict=True)
        },
        "realized_dist": {
            str(int(k)): int(v) for k, v in zip(uniq_t.tolist(), counts_t.tolist(), strict=True)
        },
    }


def evaluate_forward(
    historical_snapshot_dir: Path | str,
    forward_snapshot_dir: Path | str,
    model_dir: Path | str,
    eval_start_iso: str,
    *,
    train_frac: float = 0.6,
    valid_frac: float = 0.2,
    embargo_bars: int = 24,
    chunk_rows: int = 5000,
    max_rows_train: int | None = 2000,
    min_rows: int = 5,
) -> dict[str, Any]:
    """Run the full frozen-model forward evaluation; return the report body.

    No fitting, tuning, calibration, or threshold selection anywhere in this
    function. The model artifact is opened read-only (never written).
    """
    from crypto_engine.labels.triple_barrier import TripleBarrierConfig, label_config_hash
    from crypto_engine.models.base import feature_schema_hash

    model, record = verify_model_artifact(model_dir)
    feature_names = list(getattr(model, "feature_names_", None) or [])
    label_cfg = TripleBarrierConfig.model_validate(dict(record.label_config or {}))
    if label_config_hash(label_cfg) != (record.label_hash or ""):
        raise ValueError(
            "forward: label_config hash does not match the registered label_hash; "
            "refusing to evaluate under unverified provenance."
        )

    hist_majority, hist_counts, hist_split = historical_majority(
        historical_snapshot_dir,
        label_cfg,
        train_frac=train_frac,
        valid_frac=valid_frac,
        embargo_bars=embargo_bars,
        chunk_rows=chunk_rows,
        max_rows_train=max_rows_train,
    )
    historical_snapshot_id = resolve_historical_snapshot_id(
        getattr(record, "dataset_snapshot_id", None),
        getattr(record, "dataset_checksum", None),
        hist_split.get("snapshot_id"),
        hist_split.get("checksum_sha256"),
    )

    ohlcv, manifest = load_eval_frame(forward_snapshot_dir)
    X, y_all, _obs, counts = build_usable_matrix(ohlcv, label_cfg, chunk_rows=chunk_rows)
    missing = [c for c in feature_names if c not in X.columns]
    if missing:
        raise ValueError(f"forward: artifact feature schema mismatch; missing {missing}.")
    extra = [c for c in X.columns if c not in feature_names]
    if extra:
        raise ValueError(
            f"forward: snapshot yields unexpected features {extra}; refusing (schema drift)."
        )
    X = X[feature_names]
    if list(X.columns) != feature_names:
        raise ValueError("forward: feature column order/schema mismatch.")

    positions, n_excluded_before, sel = select_eval_rows(pd.DatetimeIndex(X.index), eval_start_iso)
    Xe = X.iloc[positions]
    ye = np.asarray(y_all)[positions]
    yp = np.asarray(model.predict(Xe)).ravel()
    if yp.shape != ye.shape:
        raise ValueError("forward: prediction/label shape mismatch.")
    metrics = summarize_forward(ye, yp, hist_majority, min_rows=min_rows)

    cutoff = _parse_cutoff(eval_start_iso)
    return {
        "schema_version": "1.0-forward",
        "generated_at": _utcnow_iso(),
        "frozen_model": True,
        "model": {
            "model_id": record.model_id,
            "artifact_sha256": record.artifact_sha256,
            "family": record.family,
            "version": record.version,
            "train_start": record.train_start,
            "train_end": record.train_end,
            "calibration_status": "uncalibrated",
        },
        "historical_snapshot": {
            # Verified against the loaded manifest; None when the record has no id.
            "snapshot_id": historical_snapshot_id,
            "majority_class_train": hist_majority,
            "train_class_counts": hist_counts,
            "train_rows": hist_split["n_train"],
        },
        "forward_snapshot": {
            "snapshot_id": manifest.snapshot_id,
            "checksum_sha256": manifest.checksum_sha256,
            "actual_range": [manifest.actual_start, manifest.actual_end],
            "row_count": manifest.row_count,
        },
        "evaluation_window": {
            "eval_start_exclusive": cutoff.isoformat(),
            "eval_first_ts": str(X.index[positions[0]].isoformat()),
            "eval_last_ts": str(X.index[positions[-1]].isoformat()),
            "n_eligible": sel["n_eligible"],
            "n_excluded_at_or_before_cutoff": n_excluded_before,
            "warmup_dropped": counts["warmup_dropped"],
            "incomplete_excluded": counts["incomplete_excluded"],
        },
        "features": {
            "names": feature_names,
            "schema_hash": feature_schema_hash(feature_names),
            "recorded_schema_hash": record.feature_schema_hash,
            "schema_match": feature_schema_hash(feature_names)
            == (record.feature_schema_hash or ""),
        },
        "labels": {
            "config": label_cfg.model_dump(mode="json"),
            "hash": label_config_hash(label_cfg),
            "recorded_hash": record.label_hash,
            "hash_match": True,
        },
        "metrics_forward": metrics,
        "selection_rule": "none (frozen single model; majority baseline is descriptive context, not a selected competitor)",
        "limitations": [
            "Model is UNCALIBRATED; scores are rankings, not probabilities.",
            "Holdout-style comparison only; sample size and class coverage limit inference.",
            "No trading backtest was run; classification metrics are not profitability.",
            "Forward period is fixed; do not extend it to chase significance.",
        ],
        "frozen_statement": (
            "The registered artifact was opened read-only and never modified, "
            "retrained, calibrated, or tuned during this evaluation."
        ),
    }


def write_forward_report(report: dict[str, Any], reports_dir: Path | str) -> Path:
    """Atomically write the report to a unique timestamped file (no overwrite)."""
    import json as _json
    import os as _os
    import tempfile as _tempfile

    base = Path(reports_dir)
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    snap = str(report.get("forward_snapshot", {}).get("snapshot_id", "unknown"))
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in snap)
    target = base / f"forward_validation_{safe}_{stamp}.json"
    if target.exists():
        raise FileExistsError(f"forward: refusing to overwrite existing report {target}.")
    payload = _json.dumps(report, indent=2, default=str).encode("utf-8")
    fd, tmp_name = _tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(base))
    try:
        with _os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        _os.replace(tmp_name, target)
    except BaseException:
        try:
            if Path(tmp_name).exists():
                Path(tmp_name).unlink()
        except OSError:
            pass
        raise
    return target


__all__ = [
    "build_usable_matrix",
    "evaluate_forward",
    "historical_majority",
    "load_eval_frame",
    "resolve_historical_snapshot_id",
    "select_eval_rows",
    "summarize_forward",
    "verify_model_artifact",
    "write_forward_report",
]
