"""API response envelopes (Phase 7, Pydantic, read-only).

Every successful response is ``{"api_version", "request_id", "data_ts",
"data": ..., "meta": ...}``; every handled failure is
``{"api_version", "request_id", "error": {"code", "message"}}``.
No envelope ever carries secrets, prompts, or tracebacks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

API_VERSION = "1.0"


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with milliseconds."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class ApiErrorBody(BaseModel):
    """Sanitized public error (no tracebacks, no paths, no secrets)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class Pagination(BaseModel):
    """Bounded pagination metadata (echoes the enforced caps)."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class Envelope(BaseModel):
    """Success envelope for all v1 responses."""

    model_config = ConfigDict(extra="forbid")

    api_version: str = API_VERSION
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    data_ts: str = Field(default_factory=utc_now_iso)
    data: Any = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """Failure envelope for all handled v1 errors."""

    model_config = ConfigDict(extra="forbid")

    api_version: str = API_VERSION
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    error: ApiErrorBody


def ok(data: Any, request_id: str | None = None, **meta: Any) -> dict[str, Any]:
    """Build a success envelope payload (JSON-serializable dict)."""
    env = Envelope(data=data, meta=dict(meta))
    if request_id:
        env.request_id = request_id
    return env.model_dump(mode="json")


def fail(code: str, message: str, request_id: str | None = None) -> dict[str, Any]:
    """Build a failure envelope payload (JSON-serializable dict)."""
    env = ErrorEnvelope(error=ApiErrorBody(code=code, message=message))
    if request_id:
        env.request_id = request_id
    return env.model_dump(mode="json")


__all__ = [
    "API_VERSION",
    "ApiErrorBody",
    "Envelope",
    "ErrorEnvelope",
    "Pagination",
    "fail",
    "ok",
    "utc_now_iso",
]
