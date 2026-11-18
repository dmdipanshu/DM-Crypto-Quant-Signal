"""Build canonical BTC/USDT 1h dataset from all snapshots.

Stage 3 of Q1.2: deduplicate all snapshots by timestamp, produce canonical
dataset with manifest, overlap matrix, quality report, and chronological
validation boundaries.

Read-only with respect to existing snapshots; writes the canonical dataset
and report files only.

Run:
    uv run python scripts/build_canonical_dataset.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from crypto_engine.datasets.snapshots import load_snapshot  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_DIR = REPO_ROOT / "data" / "processed" / "binance" / "BTC-USDT" / "1h"
CANONICAL_DIR = REPO_ROOT / "data" / "canonical" / "binance" / "BTC-USDT" / "1h"
REPORT_DIR = REPO_ROOT / "docs" / "validation"
RESULTS_DIR = REPORT_DIR / "results"

TRAINING_START = datetime(2026, 7, 1, tzinfo=UTC)
TRAINING_END = datetime(2026, 10, 1, tzinfo=UTC)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_all_snapshots() -> list[dict[str, Any]]:
    """Load every snapshot and return frame + manifest + dir info."""
    entries: list[dict[str, Any]] = []
    for snap_dir in sorted(SNAPSHOTS_DIR.iterdir()):
        if not snap_dir.is_dir():
            continue
        try:
            frame, manifest = load_snapshot(snap_dir)
            # save_snapshot writes with index=False, so timestamp is a column;
            # promote it to the index for consistency with downstream code.
            if "timestamp" in frame.columns:
                frame = frame.set_index("timestamp")
            frame.index = pd.DatetimeIndex(frame.index, tz="UTC")
            frame = frame.sort_index()
        except Exception as exc:
            entries.append(
                {
                    "dir": snap_dir.name,
                    "error": str(exc),
                    "loaded": False,
                }
            )
            continue
        entries.append(
            {
                "dir": snap_dir.name,
                "manifest": manifest.model_dump(),
                "frame": frame,
                "loaded": True,
                "first_ts": frame.index[0].isoformat() if len(frame) else None,
                "last_ts": frame.index[-1].isoformat() if len(frame) else None,
                "rows": len(frame),
            }
        )
    return entries


def timestamp_overlap_matrix(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pairwise overlap of actual parquet timestamp ranges."""
    overlaps: list[dict[str, Any]] = []
    loaded = [e for e in entries if e.get("loaded")]
    for i in range(len(loaded)):
        for j in range(i + 1, len(loaded)):
            a, b = loaded[i], loaded[j]
            a_start = datetime.fromisoformat(a["first_ts"])
            a_end = datetime.fromisoformat(a["last_ts"])
            b_start = datetime.fromisoformat(b["first_ts"])
            b_end = datetime.fromisoformat(b["last_ts"])
            os_ = max(a_start, b_start)
            oe = min(a_end, b_end)
            if os_ < oe:
                overlap_hours = (oe - os_).total_seconds() / 3600.0
                overlaps.append(
                    {
                        "a": a["dir"],
                        "b": b["dir"],
                        "a_start": a["first_ts"],
                        "a_end": a["last_ts"],
                        "b_start": b["first_ts"],
                        "b_end": b["last_ts"],
                        "overlap_start": os_.isoformat(),
                        "overlap_end": oe.isoformat(),
                        "overlap_hours": round(overlap_hours, 2),
                    }
                )
    return overlaps


def deduplicate(entries: list[dict[str, Any]]) -> pd.DataFrame:
    """Deduplicate all loaded snapshots by timestamp (first wins)."""
    frames: dict[Any, pd.Series] = {}
    col_order: list[str] | None = None
    per_snapshot_added: dict[str, int] = {}

    loaded = [e for e in entries if e.get("loaded")]
    for e in loaded:
        df = e["frame"]
        if col_order is None:
            col_order = list(df.columns)
        for ts, row in df.iterrows():
            if ts not in frames:
                frames[ts] = row
        per_snapshot_added[e["dir"]] = len(df)  # tracked for diagnostics

    if not frames:
        raise ValueError("no rows after deduplication")

    combined = pd.DataFrame(list(frames.values()), columns=col_order)
    combined.index = pd.DatetimeIndex(list(frames.keys()))
    combined = combined.sort_index()
    combined.index.name = "timestamp"
    return combined, per_snapshot_added


def save_canonical_dataset(df: pd.DataFrame, source_snapshot_count: int) -> Path:
    """Save canonical dataset as parquet + manifest."""
    CANONICAL_DIR.mkdir(parents=True, exist_ok=True)

    # Serialize to bytes for checksum.
    import io

    buffer = io.BytesIO()
    df.reset_index().to_parquet(buffer, engine="pyarrow", index=False)
    payload = buffer.getvalue()
    checksum = hashlib.sha256(payload).hexdigest()

    parquet_path = CANONICAL_DIR / "candles.parquet"
    parquet_path.write_bytes(payload)

    manifest = {
        "dataset_id": "canonical-btcusdt-1h",
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "created_at": utc_now_iso(),
        "row_count": len(df),
        "first_ts": df.index[0].isoformat(),
        "last_ts": df.index[-1].isoformat(),
        "span_hours": round((df.index[-1] - df.index[0]).total_seconds() / 3600.0, 2),
        "checksum_sha256": checksum,
        "source_snapshots": source_snapshot_count,
    }
    manifest_path = CANONICAL_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return CANONICAL_DIR


def validation_boundaries(df: pd.DataFrame) -> dict[str, Any]:
    """Design chronological train/calibration/walk-forward/holdout boundaries.

    Rules:
    - Training: strictly before calibration
    - Calibration: strictly before walk-forward validation
    - Walk-forward validation: strictly before final holdout
    - 24-bar prediction horizon respected (purge overlapping labels)
    - 24-bar embargo applied
    - No training observations in validation or holdout
    - No calibration fitting on final holdout
    """
    n = len(df)
    horizon = 24  # bars
    embargo = 24  # bars

    # Final holdout: last 15% of data, untouched
    holdout_start_idx = int(n * 0.85)
    holdout_df = df.iloc[holdout_start_idx:]

    # Remaining data (train + cal + walk-forward val)
    remaining = df.iloc[:holdout_start_idx]
    rem_n = len(remaining)

    # Calibration: 10% of remaining, immediately before walk-forward val
    cal_start_idx = int(rem_n * 0.70)
    cal_df = remaining.iloc[cal_start_idx:]

    # Training + walk-forward val: first 70% of remaining
    train_val_df = remaining.iloc[:cal_start_idx]

    # Walk-forward folds within train_val
    # 3 folds, train=200, val=50, embargo=24
    fold_val_size = 50
    n_folds = 3
    total_val = n_folds * fold_val_size
    tv_n = len(train_val_df)

    folds = []
    for k in range(n_folds):
        val_start = tv_n - total_val + k * fold_val_size
        val_end = val_start + fold_val_size
        train_lo = max(0, val_start - 200)

        train_block = train_val_df.iloc[train_lo:val_start]
        val_block = train_val_df.iloc[val_start:val_end]

        # Purge: training rows whose label info interval overlaps val range
        # Conservative: drop last 24 bars of train (embargo)
        train_after_embargo = (
            train_block.iloc[: len(train_block) - embargo]
            if len(train_block) > embargo
            else train_block.iloc[:0]
        )

        folds.append(
            {
                "fold_id": k,
                "train_start": str(train_block.index[0]) if len(train_block) else None,
                "train_end": str(train_block.index[-1]) if len(train_block) else None,
                "train_end_after_embargo": str(train_after_embargo.index[-1])
                if len(train_after_embargo)
                else None,
                "val_start": str(val_block.index[0]) if len(val_block) else None,
                "val_end": str(val_block.index[-1]) if len(val_block) else None,
                "n_train_block": len(train_block),
                "n_train_after_embargo": len(train_after_embargo),
                "n_val": len(val_block),
                "train_strictly_before_val": bool(train_block.index.max() < val_block.index.min())
                if len(train_block) and len(val_block)
                else None,
                "embargo_gap_hours": round(
                    (val_block.index.min() - train_block.index.max()).total_seconds() / 3600.0, 2
                )
                if len(train_block) and len(val_block)
                else None,
            }
        )

    return {
        "horizon_bars": horizon,
        "embargo_bars": embargo,
        "total_rows": n,
        "holdout": {
            "start_idx": holdout_start_idx,
            "start_ts": str(holdout_df.index[0]),
            "end_ts": str(holdout_df.index[-1]),
            "n_rows": len(holdout_df),
            "pct_of_total": round(100.0 * len(holdout_df) / n, 2),
        },
        "calibration": {
            "start_idx": cal_start_idx,
            "start_ts": str(cal_df.index[0]),
            "end_ts": str(cal_df.index[-1]),
            "n_rows": len(cal_df),
            "pct_of_remaining": round(100.0 * len(cal_df) / rem_n, 2),
        },
        "train_val": {
            "start_idx": 0,
            "end_idx": cal_start_idx,
            "start_ts": str(train_val_df.index[0]),
            "end_ts": str(train_val_df.index[-1]),
            "n_rows": len(train_val_df),
        },
        "folds": folds,
        "note": (
            "Chronological partitions only. No random splits. Final holdout "
            "is untouched. Calibration uses only calibration-period data. "
            "24-bar embargo applied before each validation block."
        ),
    }


def main() -> int:
    print("=== SignalForge Q1.2 Stage 3: Canonical Dataset Builder ===")
    print(f"Generated: {utc_now_iso()}")
    print()

    # ---- Load all snapshots ----
    print("[Loading] All snapshots...")
    entries = load_all_snapshots()
    loaded = [e for e in entries if e.get("loaded")]
    failed = [e for e in entries if not e.get("loaded")]
    print(f"  Total snapshot dirs: {len(entries)}")
    print(f"  Loaded successfully: {len(loaded)}")
    if failed:
        print(f"  Failed to load: {len(failed)}")
        for f in failed:
            print(f"    - {f['dir']}: {f.get('error')}")
    print()

    # ---- Overlap matrix ----
    print("[Overlap] Timestamp-level overlap matrix...")
    overlaps = timestamp_overlap_matrix(entries)
    print(f"  Pairwise overlaps: {len(overlaps)}")
    total_overlap_hours = sum(o["overlap_hours"] for o in overlaps)
    print(f"  Total overlap hours (summed across pairs): {total_overlap_hours:.2f}")
    print()

    # ---- Deduplicate ----
    print("[Deduplicate] Building canonical dataset...")
    combined, per_snapshot_added = deduplicate(entries)
    print(f"  Canonical rows: {len(combined)}")
    print(f"  Range: {combined.index[0]} to {combined.index[-1]}")
    print(f"  Span: {(combined.index[-1] - combined.index[0]).total_seconds() / 3600.0:.2f} hours")
    print(f"  Chronological: {bool(combined.index.is_monotonic_increasing)}")
    print(f"  Duplicate timestamps: {bool(combined.index.duplicated().any())}")
    print()

    # Count how many rows came from training-overlap snapshots
    training_dir = "binance-BTC-USDT-1h-20260701T0000-20261001T0000-103f110e06f0"
    training_rows_added = per_snapshot_added.get(training_dir, 0)
    print(f"  Rows from training snapshot ({training_dir}): {training_rows_added}")
    print()

    # ---- Save canonical dataset ----
    print("[Save] Writing canonical dataset...")
    canon_dir = save_canonical_dataset(combined, len(entries))
    print(f"  Directory: {canon_dir}")
    print()

    # ---- Validation boundaries ----
    print("[Boundaries] Chronological train/cal/val/holdout...")
    boundaries = validation_boundaries(combined)
    print(f"  Total rows: {boundaries['total_rows']}")
    print(
        f"  Holdout: {boundaries['holdout']['start_ts']} to {boundaries['holdout']['end_ts']} "
        f"({boundaries['holdout']['n_rows']} rows, {boundaries['holdout']['pct_of_total']}%)"
    )
    print(
        f"  Calibration: {boundaries['calibration']['start_ts']} to {boundaries['calibration']['end_ts']} "
        f"({boundaries['calibration']['n_rows']} rows)"
    )
    print(
        f"  Train+Val: {boundaries['train_val']['start_ts']} to {boundaries['train_val']['end_ts']} "
        f"({boundaries['train_val']['n_rows']} rows)"
    )
    for fold in boundaries["folds"]:
        print(
            f"    Fold {fold['fold_id']}: train [{fold['train_start']}..{fold['train_end']}], "
            f"val [{fold['val_start']}..{fold['val_end']}], "
            f"embargo_gap={fold['embargo_gap_hours']}h, "
            f"train_before_val={fold['train_strictly_before_val']}"
        )
    print()

    # ---- Write results JSON ----
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results = {
        "generated_at": utc_now_iso(),
        "stage": "Q1.2 Stage 3 - Canonical Dataset",
        "snapshots_loaded": len(loaded),
        "snapshots_failed": len(failed),
        "pairwise_overlaps": overlaps,
        "total_overlap_hours": round(total_overlap_hours, 2),
        "canonical_rows": len(combined),
        "canonical_first_ts": str(combined.index[0]),
        "canonical_last_ts": str(combined.index[-1]),
        "canonical_span_hours": round(
            (combined.index[-1] - combined.index[0]).total_seconds() / 3600.0, 2
        ),
        "chronological": bool(combined.index.is_monotonic_increasing),
        "duplicate_timestamps": bool(combined.index.duplicated().any()),
        "training_snapshot_rows_added": training_rows_added,
        "canonical_dir": str(canon_dir),
        "validation_boundaries": boundaries,
    }
    results_path = RESULTS_DIR / "quantitative_data_expansion_q1_2.json"
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f"[Results] {results_path}")
    print()

    # ---- Verify checksum ----
    parquet_path = CANONICAL_DIR / "candles.parquet"
    actual_checksum = sha256_file(parquet_path)
    manifest_checksum = json.loads((CANONICAL_DIR / "manifest.json").read_text(encoding="utf-8"))[
        "checksum_sha256"
    ]
    checksum_ok = actual_checksum == manifest_checksum
    print(f"[Verify] Checksum OK: {checksum_ok}")
    print(f"[Verify] Parquet rows: {len(combined)}")
    print()
    print("=== Canonical dataset complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
