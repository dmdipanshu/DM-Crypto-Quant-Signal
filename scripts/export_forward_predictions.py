"""Export genuine frozen-model class probabilities for the calibration lab.

What this does
--------------
Loads the registered, frozen logistic artifact read-only, replays the *existing*
Phase 10 forward-evaluation pipeline on the existing forward snapshot, and writes
the artifact's own per-class probabilities next to the realised triple-barrier
labels::

    python scripts/export_forward_predictions.py
    -> reports/generated/signalforge_forward_predictions.csv
    -> reports/generated/signalforge_forward_predictions.manifest.json

Nothing is fitted here: no scaler, no threshold, no calibration, no feature
selection, no model choice. The artifact is opened by
``validation.forward.verify_model_artifact`` (which verifies the SHA-256,
the fitted state, and the recorded feature schema) and never written to.

Why the probability matrix needs a private accessor
---------------------------------------------------
``BaseModel`` exposes only ``predict`` (argmax) and ``predict_score`` (the
marginal maximum). The per-class probabilities live in the wrapper's
``_decision`` path, which is exactly scikit-learn's ``predict_proba``:
multinomial softmax over ``_classes`` order (or ``expit`` for binary). Rather
than re-derive that maths - which would duplicate the model - this script calls
``_decision`` and then *proves* it is the artifact's own output by checking that
``classes_[argmax(proba)] == model.predict(X)`` for every row. Any disagreement
aborts the export.

Class order is read from the artifact's recorded ``_classes``, never assumed.
The export also refuses to run unless that order is exactly ``[-1, 0, 1]``,
because the required CSV schema (``p_neg1, p_0, p_1``) has no column for a
missing class and inventing a zero would be a fabricated probability.

Evidence status
---------------
``--eval-start`` defaults to ``2026-10-01T00:00:00Z``, the exclusive end of the
historical training snapshot. The forward snapshot starts 2026-09-25, so the two
overlap; the cutoff excludes every row that existed at training time. October
2026 was already inspected during the Phase 10 forward check, so these rows are
*exploratory*, not fresh confirmatory evidence. The manifest says so explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_engine.datasets.snapshots import (  # noqa: E402  (needs the path bootstrap)
    list_snapshots,
    symbol_to_dir,
)
from crypto_engine.labels.triple_barrier import (  # noqa: E402
    TripleBarrierConfig,
    label_config_hash,
)
from crypto_engine.validation.forward import (  # noqa: E402
    build_usable_matrix,
    load_eval_frame,
    select_eval_rows,
    verify_model_artifact,
)

MODEL_ID = "logistic-ee482c-3e3324f2-20261028T132236"
ARTIFACT_SHA256 = "42a8ea4bafd248c727538e233e5335337917bcd1664d28421daa70331eccf4db"

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"

# Required CSV schema. The keys are *class labels*, not column positions.
CLASSES: tuple[int, ...] = (-1, 0, 1)
PROB_COLUMN = {-1: "p_neg1", 0: "p_0", 1: "p_1"}
CSV_COLUMNS = ["timestamp", "y_true", "p_neg1", "p_0", "p_1"]

# Exclusive end of the historical training snapshot (see module docstring).
DEFAULT_EVAL_START = "2026-10-01T00:00:00Z"
PROB_SUM_TOL = 1e-9
MIN_ROWS = 100

CSV_NAME = "signalforge_forward_predictions.csv"
MANIFEST_NAME = "signalforge_forward_predictions.manifest.json"


class ExportBlocked(RuntimeError):
    """A blocker that must stop the export instead of producing bad data."""


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _rel(path: Path) -> str:
    """Repository-relative path for portable provenance (never an absolute path)."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #


def resolve_model_dir(explicit: Path | None) -> Path:
    path = explicit if explicit is not None else ROOT / "models" / "artifacts" / MODEL_ID
    if not path.is_dir():
        raise ExportBlocked(f"model directory not found: {path}")
    return path


def resolve_forward_snapshot(explicit: Path | None) -> tuple[Path, Any]:
    """Return the snapshot directory and manifest, discovering the newest one."""
    storage = ROOT / "data"
    manifests = [
        m
        for m in list_snapshots(storage)
        if m.symbol == SYMBOL and m.timeframe == TIMEFRAME and m.actual_end is not None
    ]
    if not manifests:
        raise ExportBlocked(
            f"no {SYMBOL} {TIMEFRAME} snapshots found under {storage / 'processed'}"
        )

    if explicit is not None:
        chosen = next((m for m in manifests if m.snapshot_id == explicit.name), None)
        if chosen is None:
            raise ExportBlocked(f"{explicit} is not a registered snapshot with a manifest")
        path = explicit
    else:
        chosen = max(manifests, key=lambda m: (m.actual_end or "", m.snapshot_id))
        path = (
            storage
            / "processed"
            / chosen.exchange
            / symbol_to_dir(chosen.symbol)
            / chosen.timeframe
            / chosen.snapshot_id
        )

    if not (path / "manifest.json").is_file():
        raise ExportBlocked(f"snapshot manifest missing: {path / 'manifest.json'}")
    return path, chosen


def parse_cutoff(value: str) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ExportBlocked(f"eval-start is not an ISO timestamp: {value!r}") from exc
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


# --------------------------------------------------------------------------- #
# model -> probability matrix
# --------------------------------------------------------------------------- #


def artifact_class_order(model: Any) -> list[int]:
    classes = getattr(model, "_classes", None)
    if not classes:
        raise ExportBlocked(
            "artifact records no class order (no '_classes' on the loaded model); "
            "refusing to guess the probability column mapping"
        )
    return [int(c) for c in classes]


def predict_proba_matrix(model: Any, X: pd.DataFrame, classes: list[int]) -> np.ndarray:
    """The frozen artifact's own per-class probabilities, shape ``(n, len(classes))``."""
    decision = getattr(model, "_decision", None)
    if decision is None:
        raise ExportBlocked(
            "artifact exposes no per-class probability path; only predict/predict_score "
            "are public, and predict_score is a marginal maximum, not a distribution"
        )
    raw = np.asarray(decision(X), dtype=float)
    if raw.ndim == 1:
        if len(classes) != 2:
            raise ExportBlocked(
                f"probability vector is 1-D but the artifact has {len(classes)} classes"
            )
        raw = np.column_stack([1.0 - raw, raw])
    if raw.shape != (len(X), len(classes)):
        raise ExportBlocked(f"probability shape {raw.shape} != ({len(X)}, {len(classes)})")
    return raw


def verify_probabilities(
    model: Any, X: pd.DataFrame, classes: list[int], proba: np.ndarray
) -> dict[str, float]:
    """Reject anything that is not a valid distribution from this exact model."""
    if not np.isfinite(proba).all():
        raise ExportBlocked("probability matrix contains non-finite values")
    if float(proba.min()) < 0.0 or float(proba.max()) > 1.0:
        raise ExportBlocked(f"probability outside [0, 1]: min={proba.min()!r} max={proba.max()!r}")

    sums = proba.sum(axis=1)
    max_dev = float(np.abs(sums - 1.0).max())
    if max_dev > PROB_SUM_TOL:
        raise ExportBlocked(
            f"probabilities do not sum to 1: max |sum-1| = {max_dev:.3e} > {PROB_SUM_TOL:.0e}"
        )

    mapped = np.asarray(classes, dtype=np.int64)[np.argmax(proba, axis=1)]
    predicted = np.asarray(model.predict(X)).ravel()
    disagreements = int((mapped != predicted).sum())
    if disagreements:
        raise ExportBlocked(
            f"argmax(probability) disagrees with model.predict on {disagreements}/{len(X)} rows; "
            "class order or probability path is wrong"
        )

    return {
        "prob_sum_min": float(sums.min()),
        "prob_sum_max": float(sums.max()),
        "prob_sum_max_abs_deviation": max_dev,
        "argmax_matches_predict": True,
    }


# --------------------------------------------------------------------------- #
# frame assembly
# --------------------------------------------------------------------------- #


def build_export_frame(
    index: pd.DatetimeIndex, y_true: np.ndarray, proba: np.ndarray, classes: list[int]
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "timestamp": index.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "y_true": np.asarray(y_true, dtype=np.int64),
        }
    )
    for position, label in enumerate(classes):
        frame[PROB_COLUMN[label]] = proba[:, position]
    return frame[CSV_COLUMNS]


def validate_export_frame(
    frame: pd.DataFrame, cutoff: pd.Timestamp, train_end: pd.Timestamp
) -> dict[str, Any]:
    if len(frame) < MIN_ROWS:
        raise ExportBlocked(
            f"only {len(frame)} rows exported (< minimum {MIN_ROWS}); refusing a thin export"
        )

    stamps = pd.DatetimeIndex(pd.to_datetime(frame["timestamp"], utc=True, errors="coerce"))
    if bool(stamps.isna().any()):
        raise ExportBlocked("exported timestamps are not parseable")
    if bool(stamps.duplicated().any()):
        duplicates = int(stamps.duplicated().sum())
        raise ExportBlocked(f"{duplicates} duplicate timestamps in the export")
    if not bool(stamps.is_monotonic_increasing):
        raise ExportBlocked("exported timestamps are not strictly increasing")
    if bool((stamps <= cutoff).any()):
        raise ExportBlocked(f"rows at or before the cutoff {cutoff.isoformat()} were not excluded")
    if bool((stamps <= train_end).any()):
        raise ExportBlocked(
            f"rows at or before train_end {train_end.isoformat()} were not excluded"
        )
    if bool(((stamps.minute != 0) | (stamps.second != 0) | (stamps.microsecond != 0)).any()):
        raise ExportBlocked("timestamps are not aligned to whole hours; refusing to truncate them")

    unknown = sorted(set(frame["y_true"].astype(int)) - set(CLASSES))
    if unknown:
        raise ExportBlocked(f"realised labels outside {CLASSES}: {unknown}")

    proba = frame[["p_neg1", "p_0", "p_1"]].to_numpy(dtype=float)
    if not np.isfinite(proba).all():
        raise ExportBlocked("exported probabilities contain non-finite values")
    if float(proba.min()) < 0.0 or float(proba.max()) > 1.0:
        raise ExportBlocked("exported probabilities fall outside [0, 1]")
    deviation = float(np.abs(proba.sum(axis=1) - 1.0).max())
    if deviation > PROB_SUM_TOL:
        raise ExportBlocked(f"exported rows do not sum to 1: max |sum-1| = {deviation:.3e}")

    counts = {
        str(int(k)): int(v)
        for k, v in zip(*np.unique(frame["y_true"], return_counts=True), strict=True)
    }
    return {
        "rows": len(frame),
        "first_ts": stamps[0].isoformat(),
        "last_ts": stamps[-1].isoformat(),
        "class_counts": counts,
        "prob_sum_max_abs_deviation": deviation,
    }


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #


def write_atomic(target: Path, payload: bytes, *, force: bool) -> str:
    """Write without ever silently replacing a different existing file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() == payload:
            return "unchanged"
        if not force:
            raise ExportBlocked(
                f"{target} already exists with different content; pass --force to replace it"
            )
    descriptor, temporary = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        Path(temporary).replace(target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return "written"


def phase10_cross_check(
    reports_dir: Path,
    snapshot_id: str,
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> dict[str, Any] | None:
    """Compare this export with the committed Phase 10 forward report, if present.

    This is the strongest available proof that the probabilities are aligned with
    the right rows: the report recorded the model's own argmax predictions and
    realised labels for the same window.
    """
    reports = sorted(reports_dir.glob(f"forward_validation_{snapshot_id}_*.json"))
    if not reports:
        return None
    report = json.loads(reports[-1].read_text(encoding="utf-8"))
    metrics = report.get("metrics_forward", {})

    realized = {
        str(int(k)): int(v) for k, v in zip(*np.unique(y_true, return_counts=True), strict=True)
    }
    predicted = {
        str(int(k)): int(v) for k, v in zip(*np.unique(y_pred, return_counts=True), strict=True)
    }
    accuracy = float((y_pred == y_true).mean())

    checks = {
        "n": metrics.get("n") == len(y_true),
        "accuracy": bool(
            np.isclose(accuracy, float(metrics.get("accuracy", -1.0)), rtol=0, atol=1e-12)
        ),
        "realized_dist": metrics.get("realized_dist") == realized,
        "predicted_dist": metrics.get("predicted_dist") == predicted,
    }
    result = {
        "reference_report": reports[-1].name,
        "n": len(y_true),
        "accuracy": accuracy,
        "reference_accuracy": metrics.get("accuracy"),
        "realized_dist": realized,
        "predicted_dist": predicted,
        "checks": checks,
        "passed": all(checks.values()),
    }
    if not result["passed"]:
        raise ExportBlocked(
            "export does not reproduce the Phase 10 forward report "
            f"({reports[-1].name}); failed checks: "
            + ", ".join(name for name, ok in checks.items() if not ok)
        )
    return result


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export frozen-model forward probabilities for the calibration lab.",
    )
    parser.add_argument("--model-dir", type=Path, default=None, help="default: the frozen artifact")
    parser.add_argument(
        "--forward-snapshot", type=Path, default=None, help="default: newest 1h snapshot"
    )
    parser.add_argument(
        "--eval-start",
        default=DEFAULT_EVAL_START,
        help=f"exclusive UTC cutoff (default: {DEFAULT_EVAL_START})",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help=f"default: reports/generated/{CSV_NAME}"
    )
    parser.add_argument("--chunk-rows", type=int, default=5000, help="feature chunks (4 GB bound)")
    parser.add_argument(
        "--force", action="store_true", help="replace an existing, different export"
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    cutoff = parse_cutoff(args.eval_start)
    model_dir = resolve_model_dir(args.model_dir)
    snapshot_dir, snapshot_manifest = resolve_forward_snapshot(args.forward_snapshot)

    # 1. Frozen artifact: checksum, fitted state, recorded feature schema.
    model, record = verify_model_artifact(model_dir)
    if record.model_id != MODEL_ID:
        raise ExportBlocked(f"artifact model_id {record.model_id!r} != expected {MODEL_ID!r}")
    if record.artifact_sha256 != ARTIFACT_SHA256:
        raise ExportBlocked(
            f"artifact SHA-256 {record.artifact_sha256} != expected {ARTIFACT_SHA256}"
        )
    if record.status != "trained":
        raise ExportBlocked(f"artifact status is {record.status!r}, expected 'trained'")

    classes = artifact_class_order(model)
    if sorted(classes) != list(CLASSES):
        raise ExportBlocked(
            f"artifact class order {classes} cannot fill the required CSV schema {CSV_COLUMNS}"
        )
    feature_names = list(model.feature_names_ or [])
    if not feature_names:
        raise ExportBlocked("artifact records no feature schema")

    label_config = TripleBarrierConfig.model_validate(dict(record.label_config))
    if label_config_hash(label_config) != (record.label_hash or ""):
        raise ExportBlocked("label config hash does not match the recorded label_hash")

    # 2. Snapshot (checksum verified inside load_eval_frame / load_snapshot).
    ohlcv, manifest = load_eval_frame(snapshot_dir)
    if manifest.snapshot_id != snapshot_manifest.snapshot_id:
        raise ExportBlocked("snapshot manifest mismatch between listing and load")
    if manifest.symbol != SYMBOL or manifest.timeframe != TIMEFRAME:
        raise ExportBlocked(
            f"snapshot is {manifest.symbol} {manifest.timeframe}, expected {SYMBOL} {TIMEFRAME}"
        )
    if manifest.checksum_sha256 != snapshot_manifest.checksum_sha256:
        raise ExportBlocked("snapshot checksum differs from the manifest on disk")

    # 3. Existing pipeline: causal features + complete labels, inner-joined.
    X_all, y_all, observable, counts = build_usable_matrix(
        ohlcv, label_config, chunk_rows=args.chunk_rows
    )
    missing = [column for column in feature_names if column not in X_all.columns]
    if missing:
        raise ExportBlocked(f"artifact feature schema mismatch; missing {missing}")
    extra = [column for column in X_all.columns if column not in feature_names]
    if extra:
        raise ExportBlocked(f"snapshot yields unexpected features {extra}; schema drift")
    X_all = X_all[feature_names]

    # 4. Strictly-after-cutoff selection (same rule as the Phase 10 evaluator).
    positions, excluded_before, selection = select_eval_rows(
        pd.DatetimeIndex(X_all.index), args.eval_start
    )
    X_eval = X_all.iloc[positions]
    y_eval = np.asarray(y_all)[positions]
    index = pd.DatetimeIndex(X_eval.index)

    train_end = pd.Timestamp(record.train_end).tz_convert("UTC")
    if bool((index <= train_end).any()):
        raise ExportBlocked("selection leaked rows at or before the recorded train_end")

    observed = pd.DatetimeIndex(observable.iloc[positions])
    if bool((observed < index).any()):
        raise ExportBlocked("a label is observable before its decision timestamp")

    # 5. The artifact's own probabilities, proven against its own predict().
    proba = predict_proba_matrix(model, X_eval, classes)
    probability_checks = verify_probabilities(model, X_eval, classes, proba)
    y_pred = np.asarray(classes, dtype=np.int64)[np.argmax(proba, axis=1)]

    # 6. Frame + validation.
    frame = build_export_frame(index, y_eval, proba, classes)
    summary = validate_export_frame(frame, cutoff, train_end)

    # 7. Write CSV, then re-read it and re-validate the bytes that landed on disk.
    out_path = args.out if args.out is not None else ROOT / "reports" / "generated" / CSV_NAME
    csv_text = frame.to_csv(index=False, float_format="%.12g", lineterminator="\n")
    csv_payload = csv_text.encode("utf-8")
    csv_status = write_atomic(out_path, csv_payload, force=args.force)

    reread = pd.read_csv(out_path)
    if list(reread.columns) != CSV_COLUMNS:
        raise ExportBlocked(f"re-read column mismatch: {list(reread.columns)}")
    if len(reread) != len(frame):
        raise ExportBlocked(f"re-read row count {len(reread)} != {len(frame)}")
    reread_proba = reread[["p_neg1", "p_0", "p_1"]].to_numpy(dtype=float)
    worst = float(np.abs(reread_proba.sum(axis=1) - 1.0).max())
    if worst > PROB_SUM_TOL:
        raise ExportBlocked(f"re-read rows do not sum to 1: max |sum-1| = {worst:.3e}")
    if not np.allclose(reread_proba, proba, rtol=0, atol=1e-12):
        raise ExportBlocked("re-read probabilities differ from the exported matrix")

    # 8. Cross-check against the committed Phase 10 forward report.
    cross_check = phase10_cross_check(
        ROOT / "reports" / "generated", manifest.snapshot_id, y_eval, y_pred
    )

    # 9. Manifest.
    historical = record.dataset_snapshot_id
    manifest_payload = {
        "schema_version": "1.0-predictions",
        "generated_at": _utcnow_iso(),
        "exporter": {
            "script": "scripts/export_forward_predictions.py",
            "command": "python scripts/export_forward_predictions.py",
            "frozen_pipeline_reused": [
                "crypto_engine.validation.forward.verify_model_artifact",
                "crypto_engine.validation.forward.load_eval_frame",
                "crypto_engine.validation.forward.build_usable_matrix",
                "crypto_engine.validation.forward.select_eval_rows",
                "crypto_engine.datasets.snapshots.load_snapshot (checksum verified)",
            ],
            "probability_source": (
                "artifact _decision path (multinomial softmax / expit); verified row-by-row "
                "against model.predict"
            ),
        },
        "csv": {
            "path": _rel(out_path),
            "sha256": _sha256_bytes(csv_payload),
            "row_count": len(frame),
            "columns": CSV_COLUMNS,
        },
        "model": {
            "model_id": record.model_id,
            "artifact_sha256": record.artifact_sha256,
            "family": record.family,
            "version": record.version,
            "status": record.status,
            "calibration_status": "uncalibrated",
            "train_start": record.train_start,
            "train_end": record.train_end,
            "random_state": record.random_state,
            "sklearn_version": record.sklearn_version,
            "interface": record.interface,
        },
        "class_order": classes,
        "probability_columns": {str(label): PROB_COLUMN[label] for label in classes},
        "labels": {
            "config": label_config.model_dump(mode="json"),
            "hash": label_config_hash(label_config),
            "recorded_hash": record.label_hash,
            "convention": "long_up",
            "side": label_config.side,
            "warning": (
                "-1 records that the long geometry's lower barrier printed first; "
                "it is not evidence that a short trade would have been profitable."
            ),
            "incomplete_horizons_excluded": int(counts["incomplete_excluded"]),
            "warmup_rows_dropped": int(counts["warmup_dropped"]),
        },
        "data": {
            "forward_snapshot_id": manifest.snapshot_id,
            "forward_snapshot_checksum_sha256": manifest.checksum_sha256,
            "forward_snapshot_range": [manifest.actual_start, manifest.actual_end],
            "forward_snapshot_rows": int(manifest.row_count),
            "historical_snapshot_id": historical,
            "historical_snapshot_checksum_sha256": record.dataset_checksum,
            "snapshot_path": _rel(snapshot_dir),
        },
        "evaluation_window": {
            "eval_start_exclusive": cutoff.isoformat(),
            "first_ts": summary["first_ts"],
            "last_ts": summary["last_ts"],
            "n_rows": summary["rows"],
            "n_excluded_at_or_before_cutoff": int(excluded_before),
            "n_eligible": int(selection["n_eligible"]),
            "train_end_excluded_strictly_after": record.train_end,
        },
        "validation": {
            "rows": summary["rows"],
            "class_counts": summary["class_counts"],
            "probability_sum_max_abs_deviation": probability_checks["prob_sum_max_abs_deviation"],
            "argmax_matches_model_predict": probability_checks["argmax_matches_predict"],
            "duplicates": 0,
            "non_finite_values": 0,
            "chronological_order": True,
            "re_read_from_disk_verified": True,
            "re_read_probability_sum_max_abs_deviation": worst,
        },
        "features": {
            "count": len(feature_names),
            "names": feature_names,
            "schema_hash": record.feature_schema_hash,
        },
        "evidence_status": {
            "model_frozen": True,
            "retrained": False,
            "tuned": False,
            "calibrated": False,
            "threshold_selected": False,
            "period_previously_evaluated": True,
            "exploratory": True,
            "confirmatory": False,
            "note": (
                "October 2026 was already inspected in the Phase 10 forward validation "
                "(n=624). These rows are exploratory and must not be presented as fresh "
                "confirmatory evidence of edge."
            ),
        },
        "cross_check_phase10": cross_check,
        "limitations": [
            "Model is UNCALIBRATED; these are raw logistic scores, not calibrated probabilities.",
            "The evaluated window has been inspected before; it cannot confirm an edge.",
            "Classification probabilities are not profitability evidence.",
            "Features are recomputed inside the forward snapshot window, exactly as the "
            "Phase 10 evaluator does; the first rows of the snapshot are warm-up.",
        ],
    }
    manifest_text = json.dumps(manifest_payload, indent=2) + "\n"
    manifest_path = out_path.with_name(out_path.stem + ".manifest.json")
    manifest_status = write_atomic(manifest_path, manifest_text.encode("utf-8"), force=True)

    # 10. Report actual content (never a claim).
    print(f"csv            : {_rel(out_path)}  ({csv_status})")
    print(f"manifest       : {_rel(manifest_path)}  ({manifest_status})")
    print(f"sha256 (csv)   : {_sha256_bytes(csv_payload)}")
    print(f"model          : {record.model_id}")
    print(f"artifact sha   : {record.artifact_sha256}")
    print(f"snapshot       : {manifest.snapshot_id}")
    print(f"snapshot sha   : {manifest.checksum_sha256}")
    print(f"window         : {summary['first_ts']} .. {summary['last_ts']}")
    print(f"rows           : {summary['rows']}")
    print(f"class counts   : {summary['class_counts']}")
    print(
        "prob sums      : "
        f"min={probability_checks['prob_sum_min']:.12f} "
        f"max={probability_checks['prob_sum_max']:.12f} "
        f"max|sum-1|={probability_checks['prob_sum_max_abs_deviation']:.3e}"
    )
    print(
        f"predicted dist : {json.dumps({str(int(k)): int(v) for k, v in zip(*np.unique(y_pred, return_counts=True), strict=True)})}"
    )
    if cross_check is not None:
        print(
            "cross-check    : PASS vs "
            f"{cross_check['reference_report']} "
            f"(n={cross_check['n']}, accuracy={cross_check['accuracy']:.12f})"
        )
    else:
        print("cross-check    : skipped (no Phase 10 forward report for this snapshot)")
    print("sample (head)  :")
    print(frame.head(5).to_string(index=False))
    print("sample (tail)  :")
    print(frame.tail(3).to_string(index=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return run(args)
    except ExportBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError, OSError, KeyError) as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
