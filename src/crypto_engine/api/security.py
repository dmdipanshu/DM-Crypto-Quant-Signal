"""API security primitives (Phase 7, read-only).

Boundaries enforced here (and covered by tests):

* ID validation: resource IDs match ``^[A-Za-z0-9_.: Fantasy/-]{1,128}$``
  (symbol slashes allowed, e.g. ``BTC/USDT``); anything else is rejected
  before touching the filesystem.
* Path confinement: every store path is resolved under its configured root;
  absolute escapes and ``..`` traversals are rejected.
* Secret hygiene: responses never carry env vars, keys, prompts, or secrets;
  internal errors are sanitized (no tracebacks to clients).
* Methods: only GET is ever registered (the app asserts this at build time).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
SECRET_KEY_HINTS = ("api_key", "apikey", "secret", "token", "password", "private")


class ApiError(Exception):
    """Typed API failure with an HTTP status and a sanitized public message."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def validate_id(value: str, field: str = "id") -> str:
    """Return ``value`` if it is a safe resource identifier, else raise 400."""
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        raise ApiError(400, "invalid_id", f"Malformed {field}; check the value and retry.")
    if ".." in value or value.startswith("/") or value.startswith("\\"):
        raise ApiError(400, "invalid_id", f"Malformed {field}; check the value and retry.")
    return value


def confine(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base``; reject escapes (400 on violation)."""
    root = base.resolve()
    for part in parts:
        if not isinstance(part, str) or not part:
            raise ApiError(400, "invalid_path", "Malformed path component.")
    candidate = (root.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ApiError(400, "invalid_path", "Path escapes the configured data root.") from None
    return candidate


def sanitize_error(exc: BaseException) -> str:
    """Render an internal error safe for clients (no tracebacks, no paths)."""
    name = type(exc).__name__
    return f"{name}: request could not be completed; see server logs for details."


def scan_for_secrets(payload: Any, trail: str = "$") -> str | None:
    """Return the offending key path if a secret-like key is present, else None."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(hint in lowered for hint in SECRET_KEY_HINTS):
                return f"{trail}.{key}"
            hit = scan_for_secrets(value, f"{trail}.{key}" if trail else str(key))
            if hit is not None:
                return hit
        return None
    if isinstance(payload, list):
        for i, value in enumerate(payload):
            hit = scan_for_secrets(value, f"{trail}[{i}]")
            if hit is not None:
                return hit
        return None
    return None


def env_secret_values() -> list[str]:
    """Non-empty environment values whose names look secret-like (for tests)."""
    values: list[str] = []
    for key, value in os.environ.items():
        lowered = key.lower()
        if any(hint in lowered for hint in SECRET_KEY_HINTS) and value:
            values.append(value)
    return values


__all__ = [
    "ID_PATTERN",
    "SECRET_KEY_HINTS",
    "ApiError",
    "confine",
    "env_secret_values",
    "sanitize_error",
    "scan_for_secrets",
    "validate_id",
]
