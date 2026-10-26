"""Immutable versioned dataset snapshots (Phase 1)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from crypto_engine import __version__ as package_version
from crypto_engine.data.errors import CorruptSnapshotError
from crypto_engine.data.types import Candle, FetchResult
from crypto_engine.data.validation import detect_duplicates, to_frame

SCHEMA_VERSION = "1.0"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
PARQUET_FILENAME = "candles.parquet"
MANIFEST_FILENAME = "manifest.json"


class SnapshotManifest(BaseModel):
    """Provenance + quality manifest stored alongside each snapshot."""

    snapshot_id: str
    exchange: str
    symbol: str
    timeframe: str
    requested_start: str | None = None
    requested_end: str | None = None
    actual_start: str | None = None
    actual_end: str | None = None
    fetched_at: str
    row_count: int
    schema_version: str = SCHEMA_VERSION
    columns: dict[str, str] = Field(default_factory=dict)
    quality: dict[str, Any] = Field(default_factory=dict)
    checksum_sha256: str
    config_hash: str = ""
    package_version: str = package_version


def symbol_to_dir(symbol: str) -> str:
    """Make a symbol filesystem-safe (``BTC/USDT`` -> ``BTC-USDT``)."""
    return symbol.replace("/", "-").replace(":", "-").strip()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    ts = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


def make_snapshot_id(
    exchange: str,
    symbol: str,
    timeframe: str,
    actual_start: str | None,
    actual_end: str | None,
    checksum: str,
) -> str:
    """Deterministic snapshot id from provenance + content checksum."""
    seed = "|".join([exchange, symbol, timeframe, actual_start or "", actual_end or "", checksum])
    short = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]

    def _stamp(iso: str | None) -> str:
        if not iso:
            return "empty"
        try:
            dt = datetime.fromisoformat(iso)
            return dt.strftime("%Y%m%dT%H%M")
        except ValueError:
            return "na"

    return f"{exchange}-{symbol_to_dir(symbol)}-{timeframe}-{_stamp(actual_start)}-{_stamp(actual_end)}-{short}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def save_snapshot(
    candles: Sequence[Candle],
    fetch_meta: FetchResult | Mapping[str, Any],
    storage_root: Path,
    config_hash: str = "",
    fetched_at: datetime | None = None,
    *,
    exchange: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> Path:
    """Persist candles as an immutable parquet snapshot + manifest.

    Args:
        candles: Validated candles (non-empty).
        fetch_meta: FetchResult or mapping with exchange/symbol/timeframe,
            requested/actual bounds, warnings, missing intervals, source.
        storage_root: Base storage dir (``processed/...`` is appended).
        config_hash: Hash of the relevant config for provenance.
        fetched_at: Override fetch timestamp (UTC); defaults to now.

    Returns:
        Path to the created snapshot directory.

    Raises:
        ValueError: If ``candles`` is empty.
        FileExistsError: If the deterministic snapshot id already exists (no overwrite).
    """
    items = list(candles)
    if not items:
        raise ValueError("Cannot snapshot an empty candle list.")

    if isinstance(fetch_meta, FetchResult):
        _ex = exchange or fetch_meta.source or "unknown"
        exchange = _ex
        # FetchResult does not carry symbol/timeframe; caller may pass them
        # as kwargs (CLI fetch path) or they stay empty and fail below.
        _sym = symbol or str(getattr(fetch_meta, "symbol", "") or "")
        _tf = timeframe or str(getattr(fetch_meta, "timeframe", "") or "")
        symbol = _sym
        timeframe = _tf
        requested_start = _iso(fetch_meta.requested_since)
        requested_end = _iso(fetch_meta.requested_until)
        actual_start = _iso(fetch_meta.actual_since)
        actual_end = _iso(fetch_meta.actual_until)
        missing_count = len(fetch_meta.missing_intervals)
        warnings = list(fetch_meta.warnings)
        source = fetch_meta.source
    else:
        exchange = exchange or str(fetch_meta.get("exchange", fetch_meta.get("source", "unknown")))
        symbol = symbol or str(fetch_meta.get("symbol", ""))
        timeframe = timeframe or str(fetch_meta.get("timeframe", ""))
        requested_start = fetch_meta.get("requested_start")
        requested_end = fetch_meta.get("requested_end")
        actual_start = fetch_meta.get("actual_start")
        actual_end = fetch_meta.get("actual_end")
        missing_count = int(fetch_meta.get("missing_count", 0))
        warnings = list(fetch_meta.get("warnings", []))  # type: ignore[arg-type]
        source = str(fetch_meta.get("source", exchange))
        # Accept datetime objects in mapping too.
        for key in ("requested_start", "requested_end", "actual_start", "actual_end"):
            val = fetch_meta.get(key)
            if isinstance(val, datetime):
                if key == "requested_start":
                    requested_start = _iso(val)
                elif key == "requested_end":
                    requested_end = _iso(val)
                elif key == "actual_start":
                    actual_start = _iso(val)
                else:
                    actual_end = _iso(val)

    if not symbol or not timeframe:
        raise ValueError("fetch_meta must include 'symbol' and 'timeframe' (or pass a mapping).")
    _ = source

    dupes = detect_duplicates(items)
    frame = to_frame(items)
    # Serialize parquet to bytes first (deterministic checksum before publish).
    import io

    buffer = io.BytesIO()
    frame.reset_index().to_parquet(buffer, engine="pyarrow", index=False)
    payload = buffer.getvalue()
    checksum = hashlib.sha256(payload).hexdigest()

    if actual_start is None:
        actual_start = _iso(items[0].timestamp)
    if actual_end is None:
        actual_end = _iso(items[-1].timestamp)

    snapshot_id = make_snapshot_id(exchange, symbol, timeframe, actual_start, actual_end, checksum)
    snap_dir = (
        Path(storage_root)
        / "processed"
        / exchange
        / symbol_to_dir(symbol)
        / timeframe
        / snapshot_id
    )
    if snap_dir.exists():
        raise FileExistsError(f"Snapshot already exists (no overwrite): {snap_dir}")

    fetched_at_iso = _iso(fetched_at or datetime.now(UTC))
    assert fetched_at_iso is not None
    manifest = SnapshotManifest(
        snapshot_id=snapshot_id,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        requested_start=requested_start,
        requested_end=requested_end,
        actual_start=actual_start,
        actual_end=actual_end,
        fetched_at=fetched_at_iso,
        row_count=len(items),
        columns={c: ("timestamp[ns, tz=UTC]" if c == "timestamp" else "float64") for c in COLUMNS},
        quality={
            "duplicates": len(dupes),
            "missing_intervals": missing_count,
            "warnings": warnings,
        },
        checksum_sha256=checksum,
        config_hash=config_hash,
    )

    snap_dir.mkdir(parents=True, exist_ok=False)
    try:
        _atomic_write_bytes(snap_dir / PARQUET_FILENAME, payload)
        _atomic_write_bytes(
            snap_dir / MANIFEST_FILENAME,
            manifest.model_dump_json(indent=2).encode("utf-8"),
        )
    except BaseException:
        # Best-effort cleanup of a half-written snapshot; remove tmp leftovers.
        for leftover in snap_dir.glob("*.tmp"):
            with contextlib.suppress(OSError):
                leftover.unlink()
        try:
            parquet = snap_dir / PARQUET_FILENAME
            manifest_path = snap_dir / MANIFEST_FILENAME
            if (manifest_path.exists() and not parquet.exists()) or not any(snap_dir.iterdir()):
                snap_dir.rmdir()
        except OSError:
            pass
        raise
    return snap_dir


def load_snapshot(snapshot_dir: Path) -> tuple[pd.DataFrame, SnapshotManifest]:
    """Load a snapshot, verifying checksum, row count, and duplicates."""
    snap = Path(snapshot_dir)
    parquet_path = snap / PARQUET_FILENAME
    manifest_path = snap / MANIFEST_FILENAME
    if not parquet_path.is_file() or not manifest_path.is_file():
        raise CorruptSnapshotError(f"Incomplete snapshot directory: {snap}")
    manifest = SnapshotManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    actual = _sha256_file(parquet_path)
    if actual != manifest.checksum_sha256:
        raise CorruptSnapshotError(
            f"Checksum mismatch for {parquet_path}: expected {manifest.checksum_sha256}, got {actual}"
        )
    frame = pd.read_parquet(parquet_path, engine="pyarrow")
    if len(frame) != manifest.row_count:
        raise CorruptSnapshotError(
            f"Row count mismatch: manifest={manifest.row_count}, parquet={len(frame)}"
        )
    if "timestamp" in frame.columns and frame["timestamp"].duplicated().any():
        raise CorruptSnapshotError(f"Duplicate timestamps found in {parquet_path}")
    return frame, manifest


def list_snapshots(storage_root: Path) -> list[SnapshotManifest]:
    """List all snapshot manifests under ``storage_root/processed``."""
    base = Path(storage_root) / "processed"
    manifests: list[SnapshotManifest] = []
    if not base.is_dir():
        return manifests
    for manifest_path in sorted(base.rglob(MANIFEST_FILENAME)):
        try:
            manifests.append(
                SnapshotManifest.model_validate(
                    json.loads(manifest_path.read_text(encoding="utf-8"))
                )
            )
        except (OSError, ValueError):
            continue
    return manifests


__all__ = [
    "COLUMNS",
    "MANIFEST_FILENAME",
    "PARQUET_FILENAME",
    "SCHEMA_VERSION",
    "SnapshotManifest",
    "list_snapshots",
    "load_snapshot",
    "make_snapshot_id",
    "save_snapshot",
    "symbol_to_dir",
]
