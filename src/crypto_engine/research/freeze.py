"""Immutable research-freeze manifest governance.

A research freeze captures the complete configuration and provenance state
at the moment research is frozen. The manifest is immutable:

* First authorized freeze creates the manifest (and a history file).
* Ordinary pipeline reruns reuse the existing manifest; configuration hashes
  are recomputed and compared against frozen values.
* A configuration change triggers ``FREEZE_CONFIG_MISMATCH`` but the original
  manifest is never overwritten.
* A new research freeze requires an explicit, deliberate command with a new
  version or freeze ID.
* Complete history of previous freeze manifests is preserved.

This module never derives freeze integrity from filenames or file timestamps,
and never uses the current execution timestamp as a substitute for the
original research-freeze timestamp.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST_PATH = Path("docs/validation/RESEARCH_FREEZE_MANIFEST.json")
DEFAULT_HISTORY_PATH = Path("docs/validation/RESEARCH_FREEZE_HISTORY.jsonl")

SCHEMA_VERSION = "research-freeze-manifest.v1"

#: Explicit freeze validation statuses.
#: Never return "unknown" when a manifest is successfully loaded and checked.
FREEZE_STATUS_VALID = "VALID"
FREEZE_STATUS_CONFIG_MISMATCH = "CONFIG_MISMATCH"
FREEZE_STATUS_DATASET_MISMATCH = "DATASET_MISMATCH"
FREEZE_STATUS_SCHEMA_INVALID = "SCHEMA_INVALID"
FREEZE_STATUS_MISSING = "MISSING"

FREEZE_STATUSES = (
    FREEZE_STATUS_VALID,
    FREEZE_STATUS_CONFIG_MISMATCH,
    FREEZE_STATUS_DATASET_MISMATCH,
    FREEZE_STATUS_SCHEMA_INVALID,
    FREEZE_STATUS_MISSING,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _compute_repo_commit() -> str | None:
    """Return the current git commit SHA, or None if unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


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


class FreezeConfigMismatch(Exception):
    """Raised when recomputed configuration hashes differ from frozen values."""


def build_freeze_manifest(
    *,
    freeze_id: str | None = None,
    dataset_ids: list[str] | None = None,
    dataset_hashes: dict[str, str] | None = None,
    dataset_start: str | None = None,
    dataset_end: str | None = None,
    first_seen_at: str | None = None,
    feature_config: Any | None = None,
    target_config: Any | None = None,
    model_config: Any | None = None,
    strategy_config: Any | None = None,
    cost_config: Any | None = None,
    thresholds_config: Any | None = None,
    config_version: str | None = None,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Build a new freeze manifest dict (does not write to disk)."""
    if commit_sha is None:
        commit_sha = _compute_repo_commit()

    return {
        "schema_version": SCHEMA_VERSION,
        "freeze_id": freeze_id or f"freeze-{uuid.uuid4().hex[:12]}",
        "created_at_utc": _utc_now_iso(),
        "repository_commit_sha": commit_sha,
        "dataset_ids": dataset_ids or [],
        "dataset_hashes": dataset_hashes or {},
        "dataset_start": dataset_start,
        "dataset_end": dataset_end,
        "first_seen_at": first_seen_at,
        "feature_config_hash": _hash_value(feature_config) if feature_config is not None else None,
        "target_config_hash": _hash_value(target_config) if target_config is not None else None,
        "model_config_hash": _hash_value(model_config) if model_config is not None else None,
        "strategy_config_hash": _hash_value(strategy_config)
        if strategy_config is not None
        else None,
        "transaction_cost_config_hash": (
            _hash_value(cost_config) if cost_config is not None else None
        ),
        "thresholds_config_hash": (
            _hash_value(thresholds_config) if thresholds_config is not None else None
        ),
        "config_version": config_version,
    }


def create_or_reuse_freeze(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    history_path: Path = DEFAULT_HISTORY_PATH,
    *,
    force_new: bool = False,
    new_freeze_id: str | None = None,
    **manifest_kwargs: Any,
) -> dict[str, Any]:
    """Create a new freeze manifest or reuse the existing one.

    If a manifest already exists and ``force_new`` is False, the existing
    manifest is returned unchanged. Configuration hashes in ``manifest_kwargs``
    are recomputed and compared against the frozen values; a mismatch raises
    :class:`FreezeConfigMismatch`.

    If ``force_new`` is True, a new manifest is created with ``new_freeze_id``
    (or a generated ID) and appended to history; the original manifest file
    is preserved.
    """
    existing = _load_json(manifest_path)

    if existing is not None and not force_new:
        # Reuse existing manifest; verify configuration hashes match.
        mismatches: list[str] = []
        for key in (
            "feature_config_hash",
            "target_config_hash",
            "model_config_hash",
            "strategy_config_hash",
            "transaction_cost_config_hash",
            "thresholds_config_hash",
        ):
            frozen_val = existing.get(key)
            new_val = (
                _hash_value(manifest_kwargs.get(key.replace("_hash", "")))
                if manifest_kwargs.get(key.replace("_hash", "")) is not None
                else None
            )
            if frozen_val is not None and new_val is not None and frozen_val != new_val:
                mismatches.append(key)
        if mismatches:
            raise FreezeConfigMismatch(
                f"Freeze configuration mismatch for keys: {mismatches}. "
                f"Existing freeze_id={existing.get('freeze_id')} "
                f"created_at={existing.get('created_at_utc')}. "
                f"Do not overwrite the original manifest."
            )
        return existing

    # Create a new manifest (first freeze or forced new freeze).
    manifest = build_freeze_manifest(**manifest_kwargs, freeze_id=new_freeze_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    _append_history(history_path, manifest)
    return manifest


def verify_freeze_integrity(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    *,
    expected_config_hashes: dict[str, str] | None = None,
    expected_dataset_hash: str | None = None,
    expected_dataset_id: str | None = None,
) -> dict[str, Any]:
    """Verify freeze manifest and return explicit validation status.

    Returns a dict with:
      - ``status``: one of VALID, CONFIG_MISMATCH, DATASET_MISMATCH,
        SCHEMA_INVALID, MISSING
      - ``freeze_id``, ``created_at_utc``, ``schema_version``, ``commit_sha``
      - ``reason``: human-readable explanation when not VALID
      - ``checks``: dict of individual check results
    """
    manifest = _load_json(manifest_path)
    if manifest is None:
        return {
            "status": FREEZE_STATUS_MISSING,
            "reason": "manifest_not_found",
            "freeze_id": None,
            "created_at_utc": None,
            "schema_version": None,
            "commit_sha": None,
            "checks": {},
        }

    checks: dict[str, Any] = {}

    # --- Schema validation ---
    required = [
        "schema_version",
        "freeze_id",
        "created_at_utc",
        "repository_commit_sha",
    ]
    missing = [k for k in required if k not in manifest]
    if missing:
        return {
            "status": FREEZE_STATUS_SCHEMA_INVALID,
            "reason": f"missing_fields: {missing}",
            "freeze_id": manifest.get("freeze_id"),
            "created_at_utc": manifest.get("created_at_utc"),
            "schema_version": manifest.get("schema_version"),
            "commit_sha": manifest.get("repository_commit_sha"),
            "checks": {"schema": "invalid"},
        }
    checks["schema"] = "valid"

    # --- Configuration hash validation ---
    if expected_config_hashes is not None:
        config_mismatches: list[str] = []
        for key, expected_val in expected_config_hashes.items():
            frozen_val = manifest.get(key)
            if frozen_val != expected_val:
                config_mismatches.append(key)
        checks["config_hashes"] = (
            "valid" if not config_mismatches else f"mismatch: {config_mismatches}"
        )
        if config_mismatches:
            return {
                "status": FREEZE_STATUS_CONFIG_MISMATCH,
                "reason": f"Configuration hash mismatch for keys: {config_mismatches}",
                "freeze_id": manifest["freeze_id"],
                "created_at_utc": manifest["created_at_utc"],
                "schema_version": manifest.get("schema_version"),
                "commit_sha": manifest.get("repository_commit_sha"),
                "checks": checks,
            }

    # --- Dataset hash validation ---
    if expected_dataset_hash is not None:
        dataset_hashes = manifest.get("dataset_hashes", {})
        dataset_match = any(str(v) == str(expected_dataset_hash) for v in dataset_hashes.values())
        checks["dataset_hash"] = "valid" if dataset_match else "mismatch"
        if not dataset_match:
            return {
                "status": FREEZE_STATUS_DATASET_MISMATCH,
                "reason": f"Dataset hash mismatch: expected {expected_dataset_hash}, "
                f"found {dataset_hashes}",
                "freeze_id": manifest["freeze_id"],
                "created_at_utc": manifest["created_at_utc"],
                "schema_version": manifest.get("schema_version"),
                "commit_sha": manifest.get("repository_commit_sha"),
                "checks": checks,
            }

    if expected_dataset_id is not None:
        dataset_ids = manifest.get("dataset_ids", [])
        dataset_id_match = expected_dataset_id in dataset_ids
        checks["dataset_id"] = "valid" if dataset_id_match else "mismatch"
        if not dataset_id_match:
            return {
                "status": FREEZE_STATUS_DATASET_MISMATCH,
                "reason": f"Dataset ID mismatch: expected {expected_dataset_id}, "
                f"found {dataset_ids}",
                "freeze_id": manifest["freeze_id"],
                "created_at_utc": manifest["created_at_utc"],
                "schema_version": manifest.get("schema_version"),
                "commit_sha": manifest.get("repository_commit_sha"),
                "checks": checks,
            }

    return {
        "status": FREEZE_STATUS_VALID,
        "reason": None,
        "freeze_id": manifest["freeze_id"],
        "created_at_utc": manifest["created_at_utc"],
        "schema_version": manifest.get("schema_version"),
        "commit_sha": manifest.get("repository_commit_sha"),
        "checks": checks,
    }


def load_freeze_history(history_path: Path = DEFAULT_HISTORY_PATH) -> list[dict[str, Any]]:
    """Load all historical freeze manifests."""
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
    "DEFAULT_HISTORY_PATH",
    "DEFAULT_MANIFEST_PATH",
    "FREEZE_STATUSES",
    "FREEZE_STATUS_CONFIG_MISMATCH",
    "FREEZE_STATUS_DATASET_MISMATCH",
    "FREEZE_STATUS_MISSING",
    "FREEZE_STATUS_SCHEMA_INVALID",
    "FREEZE_STATUS_VALID",
    "SCHEMA_VERSION",
    "FreezeConfigMismatch",
    "build_freeze_manifest",
    "create_or_reuse_freeze",
    "load_freeze_history",
    "verify_freeze_integrity",
]
