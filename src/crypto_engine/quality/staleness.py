"""Staleness detection — determine if data is too old for reliable use.

Pure stdlib. Accepts a timestamp and max age; returns a staleness verdict
with age in seconds and a boolean ``stale`` flag.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def detect_staleness(
    timestamp: str | datetime | None,
    max_age_seconds: float,
) -> dict[str, Any]:
    """Determine whether ``timestamp`` is stale relative to now.

    Args:
        timestamp: ISO-8601 string or aware datetime. None means no data.
        max_age_seconds: Maximum acceptable age in seconds.

    Returns a dict with:
    - ``stale``: bool — True if data is missing or older than max_age
    - ``age_seconds``: float | None — age in seconds, or None if no data
    - ``timestamp``: str | None — normalized ISO-8601 UTC, or None
    - ``max_age_seconds``: float — the configured threshold
    - ``reason``: str — human-readable explanation
    """
    if timestamp is None:
        return {
            "stale": True,
            "age_seconds": None,
            "timestamp": None,
            "max_age_seconds": max_age_seconds,
            "reason": "no timestamp provided",
        }

    ts = _parse_timestamp(timestamp)
    if ts is None:
        return {
            "stale": True,
            "age_seconds": None,
            "timestamp": str(timestamp),
            "max_age_seconds": max_age_seconds,
            "reason": "unparseable timestamp",
        }

    now = datetime.now(UTC)
    age = (now - ts).total_seconds()
    stale = age > max_age_seconds

    return {
        "stale": stale,
        "age_seconds": age,
        "timestamp": ts.isoformat(),
        "max_age_seconds": max_age_seconds,
        "reason": (
            "data is current"
            if not stale
            else f"data is {age:.0f}s old, exceeding {max_age_seconds}s limit"
        ),
    }


def _parse_timestamp(value: str | datetime) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if dt.tzinfo is None:
            return None
        return dt.astimezone(UTC)
    return None


__all__ = ["detect_staleness"]
