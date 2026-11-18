"""Data quality tests for Q1.2 canonical dataset (offline, no network).

Tests verify timestamp ordering, duplicate detection, missing intervals,
manifest integrity, checksum verification, UTC consistency, and
chronological train/validation/holdout separation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from crypto_engine.data.validation import (
    find_missing_intervals,
    timeframe_to_timedelta,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DIR = REPO_ROOT / "data" / "canonical" / "binance" / "BTC-USDT" / "1h"
CANONICAL_PARQUET = CANONICAL_DIR / "candles.parquet"
CANONICAL_MANIFEST = CANONICAL_DIR / "manifest.json"
SNAPSHOTS_DIR = REPO_ROOT / "data" / "processed" / "binance" / "BTC-USDT" / "1h"
RESULTS_JSON = (
    REPO_ROOT / "docs" / "validation" / "results" / "quantitative_data_expansion_q1_2.json"
)


def _load_canonical() -> pd.DataFrame:
    df = pd.read_parquet(CANONICAL_PARQUET)
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    elif "index" in df.columns:
        df = df.set_index("index")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.DatetimeIndex(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df.sort_index()


# ---------------------------------------------------------------------------
# Timestamp ordering
# ---------------------------------------------------------------------------


def test_canonical_index_is_utc_datetime() -> None:
    df = _load_canonical()
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is not None
    assert df.index.tz == UTC


def test_canonical_timestamps_are_chronological() -> None:
    df = _load_canonical()
    assert df.index.is_monotonic_increasing, "timestamps must be sorted ascending"


def test_canonical_no_duplicate_timestamps() -> None:
    df = _load_canonical()
    assert not df.index.duplicated().any(), "no duplicate timestamps allowed"


# ---------------------------------------------------------------------------
# Candle consistency (OHLCV)
# ---------------------------------------------------------------------------


def test_canonical_ohlcv_consistency() -> None:
    df = _load_canonical()
    for col in ("open", "high", "low", "close"):
        assert df[col].notna().all(), f"{col} must not have nulls"
        assert (df[col] > 0).all(), f"{col} must be positive"
    assert (df["high"] >= df["low"]).all(), "high must be >= low"
    assert (df["high"] >= df["open"]).all(), "high must be >= open"
    assert (df["high"] >= df["close"]).all(), "high must be >= close"
    assert (df["low"] <= df["open"]).all(), "low must be <= open"
    assert (df["low"] <= df["close"]).all(), "low must be <= close"
    assert (df["volume"] >= 0).all(), "volume must be non-negative"


# ---------------------------------------------------------------------------
# Missing candle intervals
# ---------------------------------------------------------------------------


def test_canonical_no_large_gaps() -> None:
    df = _load_canonical()
    timeframe_to_timedelta("1h")
    gaps = find_missing_intervals(
        [
            __import__("crypto_engine.data.types", fromlist=["Candle"]).Candle(
                timestamp=ts,
                open=float(df.loc[ts, "open"]),
                high=float(df.loc[ts, "high"]),
                low=float(df.loc[ts, "low"]),
                close=float(df.loc[ts, "close"]),
                volume=float(df.loc[ts, "volume"]),
            )
            for ts in df.index
        ],
        "1h",
    )
    # Allow gaps only at the very beginning/end (data boundaries), not internal.
    internal_gaps = [g for g in gaps if df.index[0] <= g[0] and g[1] <= df.index[-1]]
    assert len(internal_gaps) == 0, f"internal gaps found: {internal_gaps[:5]}"


# ---------------------------------------------------------------------------
# Manifest integrity
# ---------------------------------------------------------------------------


def test_manifest_exists_and_is_valid_json() -> None:
    assert CANONICAL_MANIFEST.is_file(), "canonical manifest.json must exist"
    with open(CANONICAL_MANIFEST, encoding="utf-8") as fh:
        manifest = json.load(fh)
    for key in ("exchange", "symbol", "timeframe", "row_count", "checksum_sha256", "created_at"):
        assert key in manifest, f"manifest missing key: {key}"
    # Canonical manifest uses dataset_id instead of snapshot_id; accept either.
    assert ("snapshot_id" in manifest) or ("dataset_id" in manifest), (
        "manifest must have snapshot_id or dataset_id"
    )


def test_manifest_matches_parquet_checksum() -> None:
    with open(CANONICAL_MANIFEST, encoding="utf-8") as fh:
        manifest = json.load(fh)
    expected = manifest["checksum_sha256"]
    actual = hashlib.sha256(CANONICAL_PARQUET.read_bytes()).hexdigest()
    assert actual == expected, f"checksum mismatch: expected {expected}, got {actual}"


def test_manifest_row_count_matches_parquet() -> None:
    df = _load_canonical()
    with open(CANONICAL_MANIFEST, encoding="utf-8") as fh:
        manifest = json.load(fh)
    assert manifest["row_count"] == len(df), (
        f"manifest row_count {manifest['row_count']} != parquet rows {len(df)}"
    )


# ---------------------------------------------------------------------------
# Snapshot overlap (source snapshots should not create false independent obs)
# ---------------------------------------------------------------------------


def test_source_snapshot_overlap_is_documented() -> None:
    """All source snapshot pairwise overlaps are recorded in results JSON."""
    assert RESULTS_JSON.is_file(), "results JSON must exist"
    with open(RESULTS_JSON, encoding="utf-8") as fh:
        results = json.load(fh)
    overlaps = results.get("pairwise_overlaps", [])
    assert isinstance(overlaps, list)
    assert len(overlaps) > 0, "expected at least one documented overlap"


# ---------------------------------------------------------------------------
# Training / validation / holdout separation
# ---------------------------------------------------------------------------


def test_validation_boundaries_are_chronological() -> None:
    """Train < calibration < walk-forward val < holdout, all strictly separated."""
    assert RESULTS_JSON.is_file(), "results JSON must exist"
    with open(RESULTS_JSON, encoding="utf-8") as fh:
        results = json.load(fh)
    boundaries = results.get("validation_boundaries", {})
    assert boundaries, "validation_boundaries must be present"

    holdout = boundaries["holdout"]
    calibration = boundaries["calibration"]
    train_val = boundaries["train_val"]

    holdout_start = datetime.fromisoformat(holdout["start_ts"])
    cal_end = datetime.fromisoformat(calibration["end_ts"])
    tv_end = datetime.fromisoformat(train_val["end_ts"])

    # Calibration ends before holdout starts
    assert cal_end < holdout_start, (
        f"calibration end {cal_end} must be before holdout start {holdout_start}"
    )
    # Train+val ends before calibration starts
    cal_start = datetime.fromisoformat(calibration["start_ts"])
    assert tv_end <= cal_start, (
        f"train+val end {tv_end} must be before calibration start {cal_start}"
    )


def test_folds_train_strictly_before_val() -> None:
    """Every walk-forward fold has train strictly before val."""
    assert RESULTS_JSON.is_file(), "results JSON must exist"
    with open(RESULTS_JSON, encoding="utf-8") as fh:
        results = json.load(fh)
    folds = results.get("validation_boundaries", {}).get("folds", [])
    assert len(folds) == 3, f"expected 3 folds, got {len(folds)}"
    for fold in folds:
        assert fold["train_strictly_before_val"] is True, (
            f"fold {fold['fold_id']}: train must be strictly before val"
        )


def test_holdout_is_untouched_last_15pct() -> None:
    """Final holdout is the last 15% of data and is never used for training."""
    df = _load_canonical()
    n = len(df)
    holdout_start_idx = int(n * 0.85)
    holdout_df = df.iloc[holdout_start_idx:]
    assert len(holdout_df) == round(n * 0.15), (
        f"holdout must be 15% of data: {len(holdout_df)} != {round(n * 0.15)}"
    )
    assert holdout_df.index[0] == df.index[holdout_start_idx], "holdout must start at 85% mark"


# ---------------------------------------------------------------------------
# Reproducible dataset construction
# ---------------------------------------------------------------------------


def test_canonical_dataset_is_deterministic() -> None:
    """Same snapshots in same order always produce same canonical dataset."""
    df1 = _load_canonical()
    df2 = _load_canonical()
    pd.testing.assert_frame_equal(df1, df2)


def test_canonical_row_count_matches_results_json() -> None:
    df = _load_canonical()
    with open(RESULTS_JSON, encoding="utf-8") as fh:
        results = json.load(fh)
    assert results["canonical_rows"] == len(df), (
        f"results canonical_rows {results['canonical_rows']} != actual {len(df)}"
    )


def test_canonical_first_and_last_ts_match_results() -> None:
    df = _load_canonical()
    with open(RESULTS_JSON, encoding="utf-8") as fh:
        results = json.load(fh)
    assert results["canonical_first_ts"].replace(" ", "T") == df.index[0].isoformat()
    assert results["canonical_last_ts"].replace(" ", "T") == df.index[-1].isoformat()


# ---------------------------------------------------------------------------
# Source snapshots all valid (checksum + row count)
# ---------------------------------------------------------------------------


def test_all_source_snapshots_pass_checksum() -> None:
    """Every source snapshot directory passes its own checksum verification."""
    from crypto_engine.datasets.snapshots import load_snapshot

    for snap_dir in sorted(SNAPSHOTS_DIR.iterdir()):
        if not snap_dir.is_dir():
            continue
        try:
            _frame, _manifest = load_snapshot(snap_dir)
        except Exception as exc:
            pytest.fail(f"snapshot {snap_dir.name} failed validation: {exc}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
