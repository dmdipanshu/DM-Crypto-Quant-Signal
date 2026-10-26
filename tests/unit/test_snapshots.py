"""Snapshot roundtrip + integrity tests (offline)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from crypto_engine.data.errors import CorruptSnapshotError
from crypto_engine.data.types import Candle
from crypto_engine.datasets.snapshots import (
    COLUMNS,
    SCHEMA_VERSION,
    load_snapshot,
    save_snapshot,
)


def _meta(symbol: str = "BTC/USDT", timeframe: str = "1h") -> dict[str, Any]:
    return {
        "exchange": "binance",
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_start": None,
        "requested_end": None,
        "actual_start": None,
        "actual_end": None,
        "missing_count": 0,
        "warnings": [],
        "source": "binance",
    }


def test_save_reload_roundtrip(sample_candles: list[Candle], tmp_storage: Path) -> None:
    """Saved snapshots reload with identical rows and a complete manifest."""
    snap_dir = save_snapshot(sample_candles, _meta(), tmp_storage, config_hash="abc123")
    assert snap_dir.is_dir()
    assert (snap_dir / "candles.parquet").is_file()
    manifest_path = snap_dir / "manifest.json"
    assert manifest_path.is_file()

    frame, manifest = load_snapshot(snap_dir)
    assert len(frame) == len(sample_candles)
    assert manifest.row_count == len(sample_candles)
    assert manifest.schema_version == SCHEMA_VERSION
    assert manifest.exchange == "binance"
    assert manifest.symbol == "BTC/USDT"
    assert manifest.timeframe == "1h"
    assert manifest.checksum_sha256
    assert manifest.config_hash == "abc123"
    assert manifest.package_version
    assert manifest.actual_start and manifest.actual_end
    assert set(frame.columns) >= set(COLUMNS[1:])


def test_checksum_mismatch_detected(sample_candles: list[Candle], tmp_storage: Path) -> None:
    """Corrupting one parquet byte is detected on load."""
    snap_dir = save_snapshot(sample_candles, _meta(), tmp_storage)
    parquet = snap_dir / "candles.parquet"
    with parquet.open("r+b") as fh:
        fh.seek(10)
        byte = fh.read(1)
        fh.seek(10)
        fh.write(bytes([byte[0] ^ 0xFF]))
    with pytest.raises(CorruptSnapshotError, match="Checksum mismatch"):
        load_snapshot(snap_dir)


def test_duplicate_snapshot_no_overwrite(sample_candles: list[Candle], tmp_storage: Path) -> None:
    """Saving identical content twice raises instead of overwriting."""
    first = save_snapshot(sample_candles, _meta(), tmp_storage)
    with pytest.raises(FileExistsError):
        save_snapshot(sample_candles, _meta(), tmp_storage)
    assert first.is_dir()


def test_empty_candles_rejected(tmp_storage: Path) -> None:
    """Empty candle lists are rejected before touching storage."""
    with pytest.raises(ValueError, match="empty"):
        save_snapshot([], _meta(), tmp_storage)
    assert (
        list((tmp_storage / "processed").rglob("*")) == []
        or not (tmp_storage / "processed").exists()
    )


def test_no_tmp_leftovers(sample_candles: list[Candle], tmp_storage: Path) -> None:
    """No .tmp files remain after success or no-overwrite failure."""
    save_snapshot(sample_candles, _meta(), tmp_storage)
    with pytest.raises(FileExistsError):
        save_snapshot(sample_candles, _meta(), tmp_storage)
    leftovers = list(tmp_storage.rglob("*.tmp"))
    assert leftovers == []
