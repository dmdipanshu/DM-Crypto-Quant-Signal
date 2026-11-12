"""Integrity audit — read-only verification of stored state.

Pure stdlib. Never mutates; returns a structured result with ok, errors,
and checked_at timestamp.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def audit(state: dict[str, Any]) -> dict[str, Any]:
    """Perform a read-only integrity audit of a state document.

    Checks:
    - required keys present (``schema_version``, ``records``, ``checksums``)
    - every checksum key has a corresponding record key and vice versa
    - no empty record values where a payload is expected

    Returns a dict with ``ok``, ``errors`` (list of str), ``checked`` count,
    and ``checked_at`` (ISO-8601 UTC). Never mutates ``state``.
    """
    errors: list[str] = []
    checked = 0

    if not isinstance(state, dict):
        return {
            "ok": False,
            "errors": ["state must be a JSON object"],
            "checked": 0,
            "checked_at": _now_iso(),
        }

    required = ("schema_version", "records", "checksums")
    for key in required:
        if key not in state:
            errors.append(f"missing required key: {key}")

    records = state.get("records", {})
    checksums = state.get("checksums", {})

    if not isinstance(records, dict):
        errors.append("records must be a JSON object")
        records = {}
    if not isinstance(checksums, dict):
        errors.append("checksums must be a JSON object")
        checksums = {}

    record_keys = set(records.keys()) if isinstance(records, dict) else set()
    checksum_keys = set(checksums.keys()) if isinstance(checksums, dict) else set()

    missing_checksums = record_keys - checksum_keys
    missing_records = checksum_keys - record_keys

    for key in sorted(missing_checksums):
        errors.append(f"record {key!r} has no checksum")
    for key in sorted(missing_records):
        errors.append(f"checksum {key!r} has no record")

    for key, value in records.items():
        checked += 1
        if value is None or value == "":
            errors.append(f"record {key!r} has empty payload")

    ok = len(errors) == 0
    return {
        "ok": ok,
        "errors": errors,
        "checked": checked,
        "checked_at": _now_iso(),
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


__all__ = ["audit"]
