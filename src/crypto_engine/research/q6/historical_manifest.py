"""Historical validation manifest.

Immutable manifest for the HISTORICAL_ACCELERATED_VALIDATION track.
Completely separate from:
- docs/validation/RESEARCH_FREEZE_MANIFEST.json
- docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json

This manifest must never be confused with prospective holdout evidence.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_HISTORICAL_MANIFEST_PATH = Path("docs/validation/HISTORICAL_VALIDATION_MANIFEST.json")
DEFAULT_HISTORICAL_HISTORY_PATH = Path("docs/validation/HISTORICAL_VALIDATION_HISTORY.jsonl")

HISTORICAL_MANIFEST_SCHEMA_VERSION = "historical_accelerated_manifest_v1"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_value(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _append_history(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def build_historical_manifest(
    *,
    dataset_id: str | None = None,
    dataset_started_at_utc: str | None = None,
    provider: str = "binance",
    exchange: str = "binance",
    market_type: str = "spot",
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    requested_start_utc: str | None = None,
    requested_end_utc: str | None = None,
    actual_first_candle_utc: str | None = None,
    actual_last_candle_utc: str | None = None,
    observation_count: int | None = None,
    missing_interval_count: int | None = None,
    duplicate_count: int | None = None,
    conflicting_count: int | None = None,
    dataset_hash: str | None = None,
    retrieval_timestamp: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    normalization_version: str = "v1",
) -> dict[str, Any]:
    """Build a historical validation manifest dict (does not write to disk)."""
    return {
        "schema_version": HISTORICAL_MANIFEST_SCHEMA_VERSION,
        "dataset_id": dataset_id or f"hist-accel-{uuid.uuid4().hex[:12]}",
        "dataset_type": "HISTORICAL_ACCELERATED_VALIDATION",
        "dataset_started_at_utc": dataset_started_at_utc or _utc_now_iso(),
        "provider": provider,
        "exchange": exchange,
        "market_type": market_type,
        "symbol": symbol,
        "timeframe": timeframe,
        "requested_start_utc": requested_start_utc,
        "requested_end_utc": requested_end_utc,
        "actual_first_candle_utc": actual_first_candle_utc,
        "actual_last_candle_utc": actual_last_candle_utc,
        "observation_count": observation_count,
        "missing_interval_count": missing_interval_count,
        "duplicate_count": duplicate_count,
        "conflicting_count": conflicting_count,
        "dataset_hash": dataset_hash,
        "retrieval_timestamp": retrieval_timestamp or _utc_now_iso(),
        "source_metadata": source_metadata or {},
        "normalization_version": normalization_version,
        "is_prospective": False,
        "q5_2_eligible": False,
    }


def create_or_reuse_historical_manifest(
    manifest_path: Path = DEFAULT_HISTORICAL_MANIFEST_PATH,
    history_path: Path = DEFAULT_HISTORICAL_HISTORY_PATH,
    *,
    force_new: bool = False,
    new_dataset_id: str | None = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Create a new historical manifest or reuse the existing one."""
    existing = _load_json(manifest_path)

    if existing is not None and not force_new:
        return existing

    resolved_dataset_id = new_dataset_id or manifest_kwargs.pop("dataset_id", None)
    manifest = build_historical_manifest(**manifest_kwargs, dataset_id=resolved_dataset_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    _append_history(history_path, manifest)
    return manifest


def verify_historical_manifest(
    manifest_path: Path = DEFAULT_HISTORICAL_MANIFEST_PATH,
) -> dict[str, Any]:
    """Verify the historical manifest integrity."""
    manifest = _load_json(manifest_path)
    if manifest is None:
        return {
            "valid": False,
            "reason": "manifest_not_found",
            "dataset_id": None,
            "dataset_started_at_utc": None,
            "checks": {},
        }

    required = [
        "schema_version",
        "dataset_id",
        "dataset_started_at_utc",
        "dataset_type",
        "provider",
        "exchange",
        "market_type",
        "symbol",
        "timeframe",
    ]
    missing = [k for k in required if k not in manifest]
    if missing:
        return {
            "valid": False,
            "reason": f"missing_fields: {missing}",
            "dataset_id": manifest.get("dataset_id"),
            "dataset_started_at_utc": manifest.get("dataset_started_at_utc"),
            "checks": {"schema": "invalid"},
        }

    checks = {
        "schema": "valid",
        "dataset_type": "valid"
        if manifest.get("dataset_type") == "HISTORICAL_ACCELERATED_VALIDATION"
        else "invalid",
        "is_prospective": not manifest.get("is_prospective", False),
        "q5_2_eligible": not manifest.get("q5_2_eligible", False),
    }

    return {
        "valid": all(v is True for v in checks.values() if isinstance(v, bool)),
        "reason": None
        if all(v is True for v in checks.values() if isinstance(v, bool))
        else "schema_or_type_invalid",
        "dataset_id": manifest.get("dataset_id"),
        "dataset_started_at_utc": manifest.get("dataset_started_at_utc"),
        "dataset_type": manifest.get("dataset_type"),
        "checks": checks,
    }


def load_historical_manifest_history(
    history_path: Path = DEFAULT_HISTORICAL_HISTORY_PATH,
) -> list[dict[str, Any]]:
    if not history_path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with history_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


__all__ = [
    "DEFAULT_HISTORICAL_HISTORY_PATH",
    "DEFAULT_HISTORICAL_MANIFEST_PATH",
    "HISTORICAL_MANIFEST_SCHEMA_VERSION",
    "build_historical_manifest",
    "create_or_reuse_historical_manifest",
    "load_historical_manifest_history",
    "verify_historical_manifest",
]
