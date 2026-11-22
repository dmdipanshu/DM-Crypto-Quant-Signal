"""Prospective collection manifest governance.

A collection manifest captures the immutable collection epoch state.
It is separate from the research freeze manifest and must never modify
or reference it for writes.

* First authorized collection creates the manifest (and history).
* Ordinary collection reruns reuse the existing manifest.
* A new collection epoch requires an explicit administrative action
  with a new collection ID.
* Complete history of collection manifests is preserved.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_COLLECTION_MANIFEST_PATH = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json")
DEFAULT_COLLECTION_HISTORY_PATH = Path("docs/validation/PROSPECTIVE_COLLECTION_HISTORY.jsonl")

SCHEMA_VERSION = "prospective-collection-manifest.v1"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_value(value: Any) -> str:
    """Deterministic sha256 over a JSON-serialisable value."""
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


class CollectionConfigMismatch(Exception):
    """Raised when collection configuration hashes differ from frozen values."""


def build_collection_manifest(
    *,
    collection_id: str | None = None,
    collection_started_at_utc: str | None = None,
    associated_freeze_id: str | None = None,
    associated_freeze_timestamp: str | None = None,
    exchange: str = "binance",
    market_type: str = "spot",
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    collection_config: Any | None = None,
    collector_version: str | None = None,
    data_schema_version: str | None = None,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Build a new collection manifest dict (does not write to disk)."""
    import subprocess

    if commit_sha is None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                commit_sha = result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # collection_started_at_utc must remain null until the first successfully
    # accepted observation establishes an immutable anchor. Callers may pass an
    # explicit timestamp for legacy manifests; default is null (NOT_STARTED).
    return {
        "schema_version": SCHEMA_VERSION,
        "collection_id": collection_id or f"collect-{uuid.uuid4().hex[:12]}",
        "collection_started_at_utc": collection_started_at_utc,
        "associated_freeze_id": associated_freeze_id,
        "associated_freeze_timestamp": associated_freeze_timestamp,
        "exchange": exchange,
        "market_type": market_type,
        "symbols": symbols or ["BTC/USDT"],
        "timeframes": timeframes or ["1h"],
        "collection_config_hash": _hash_value(collection_config)
        if collection_config is not None
        else None,
        "collector_version": collector_version or "q5-v1",
        "data_schema_version": data_schema_version or "ohlcv-v1",
        "repository_commit_sha": commit_sha,
    }


def create_or_reuse_collection(
    manifest_path: Path = DEFAULT_COLLECTION_MANIFEST_PATH,
    history_path: Path = DEFAULT_COLLECTION_HISTORY_PATH,
    *,
    force_new: bool = False,
    new_collection_id: str | None = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Create a new collection manifest or reuse the existing one.

    If a manifest already exists and ``force_new`` is False, the existing
    manifest is returned unchanged. Configuration hashes are recomputed and
    compared against the frozen values; a mismatch raises
    :class:`CollectionConfigMismatch`.
    """
    existing = _load_json(manifest_path)

    if existing is not None and not force_new:
        # Reuse existing manifest; verify configuration hash matches.
        frozen_hash = existing.get("collection_config_hash")
        new_hash = (
            _hash_value(manifest_kwargs.get("collection_config"))
            if manifest_kwargs.get("collection_config") is not None
            else None
        )
        if frozen_hash is not None and new_hash is not None and frozen_hash != new_hash:
            raise CollectionConfigMismatch(
                f"Collection configuration mismatch. "
                f"Existing collection_id={existing.get('collection_id')} "
                f"started_at={existing.get('collection_started_at_utc')}. "
                f"Do not overwrite the original manifest."
            )
        return existing

    # Pop collection_id from kwargs if present to avoid duplicate kwarg
    manifest_kwargs.pop("collection_id", None)
    manifest = build_collection_manifest(**manifest_kwargs, collection_id=new_collection_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    _append_history(history_path, manifest)
    return manifest


def verify_collection_integrity(
    manifest_path: Path = DEFAULT_COLLECTION_MANIFEST_PATH,
    *,
    expected_collection_config_hash: str | None = None,
    expected_exchange: str | None = None,
    expected_symbols: list[str] | None = None,
    expected_timeframes: list[str] | None = None,
    associated_freeze_id: str | None = None,
) -> dict[str, Any]:
    """Verify collection manifest and return explicit validation status.

    Returns a dict with:
      - ``status``: one of VALID, CONFIG_MISMATCH, SCOPE_MISMATCH,
        SCHEMA_INVALID, MISSING
      - ``collection_id``, ``collection_started_at_utc``
      - ``associated_freeze_id``, ``associated_freeze_timestamp``
      - ``reason``: human-readable explanation when not VALID
      - ``checks``: dict of individual check results
    """
    manifest = _load_json(manifest_path)
    if manifest is None:
        return {
            "status": "MISSING",
            "reason": "manifest_not_found",
            "collection_id": None,
            "collection_started_at_utc": None,
            "associated_freeze_id": None,
            "associated_freeze_timestamp": None,
            "checks": {},
        }

    checks: dict[str, Any] = {}

    # --- Schema validation ---
    required = [
        "schema_version",
        "collection_id",
        "collection_started_at_utc",
        "associated_freeze_id",
        "associated_freeze_timestamp",
    ]
    missing = [k for k in required if k not in manifest]
    if missing:
        return {
            "status": "SCHEMA_INVALID",
            "reason": f"missing_fields: {missing}",
            "collection_id": manifest.get("collection_id"),
            "collection_started_at_utc": manifest.get("collection_started_at_utc"),
            "associated_freeze_id": manifest.get("associated_freeze_id"),
            "associated_freeze_timestamp": manifest.get("associated_freeze_timestamp"),
            "checks": {"schema": "invalid"},
        }
    checks["schema"] = "valid"

    # --- Collection config hash validation ---
    if expected_collection_config_hash is not None:
        frozen_hash = manifest.get("collection_config_hash")
        if frozen_hash != expected_collection_config_hash:
            return {
                "status": "CONFIG_MISMATCH",
                "reason": f"Collection config hash mismatch: expected {expected_collection_config_hash}, found {frozen_hash}",
                "collection_id": manifest["collection_id"],
                "collection_started_at_utc": manifest["collection_started_at_utc"],
                "associated_freeze_id": manifest["associated_freeze_id"],
                "associated_freeze_timestamp": manifest["associated_freeze_timestamp"],
                "checks": checks,
            }
        checks["collection_config_hash"] = "valid"

    # --- Scope validation ---
    if expected_exchange is not None and manifest.get("exchange") != expected_exchange:
        return {
            "status": "SCOPE_MISMATCH",
            "reason": f"Exchange mismatch: expected {expected_exchange}, found {manifest.get('exchange')}",
            "collection_id": manifest["collection_id"],
            "collection_started_at_utc": manifest["collection_started_at_utc"],
            "associated_freeze_id": manifest["associated_freeze_id"],
            "associated_freeze_timestamp": manifest["associated_freeze_timestamp"],
            "checks": checks,
        }
    checks["exchange"] = "valid"

    if expected_symbols is not None and manifest.get("symbols") != expected_symbols:
        return {
            "status": "SCOPE_MISMATCH",
            "reason": f"Symbols mismatch: expected {expected_symbols}, found {manifest.get('symbols')}",
            "collection_id": manifest["collection_id"],
            "collection_started_at_utc": manifest["collection_started_at_utc"],
            "associated_freeze_id": manifest["associated_freeze_id"],
            "associated_freeze_timestamp": manifest["associated_freeze_timestamp"],
            "checks": checks,
        }
    checks["symbols"] = "valid"

    if expected_timeframes is not None and manifest.get("timeframes") != expected_timeframes:
        return {
            "status": "SCOPE_MISMATCH",
            "reason": f"Timeframes mismatch: expected {expected_timeframes}, found {manifest.get('timeframes')}",
            "collection_id": manifest["collection_id"],
            "collection_started_at_utc": manifest["collection_started_at_utc"],
            "associated_freeze_id": manifest["associated_freeze_id"],
            "associated_freeze_timestamp": manifest["associated_freeze_timestamp"],
            "checks": checks,
        }
    checks["timeframes"] = "valid"

    # --- Freeze association validation ---
    if (
        associated_freeze_id is not None
        and manifest.get("associated_freeze_id") != associated_freeze_id
    ):
        return {
            "status": "SCOPE_MISMATCH",
            "reason": f"Freeze ID mismatch: expected {associated_freeze_id}, found {manifest.get('associated_freeze_id')}",
            "collection_id": manifest["collection_id"],
            "collection_started_at_utc": manifest["collection_started_at_utc"],
            "associated_freeze_id": manifest["associated_freeze_id"],
            "associated_freeze_timestamp": manifest["associated_freeze_timestamp"],
            "checks": checks,
        }
    checks["freeze_association"] = "valid"

    return {
        "status": "VALID",
        "reason": None,
        "collection_id": manifest["collection_id"],
        "collection_started_at_utc": manifest["collection_started_at_utc"],
        "associated_freeze_id": manifest["associated_freeze_id"],
        "associated_freeze_timestamp": manifest["associated_freeze_timestamp"],
        "checks": checks,
    }


def load_collection_history(
    history_path: Path = DEFAULT_COLLECTION_HISTORY_PATH,
) -> list[dict[str, Any]]:
    """Load all historical collection manifests."""
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
    "DEFAULT_COLLECTION_HISTORY_PATH",
    "DEFAULT_COLLECTION_MANIFEST_PATH",
    "SCHEMA_VERSION",
    "CollectionConfigMismatch",
    "build_collection_manifest",
    "create_or_reuse_collection",
    "load_collection_history",
    "verify_collection_integrity",
]
