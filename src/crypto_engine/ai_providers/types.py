"""Provider-independent AI request/response contracts (Phase 4 Track A).

API docs verified 2026-10-27 (verify-against-docs; adapter modules carry
per-provider endpoint notes). These types are transport-agnostic: payloads
are validated downstream by analyst agents (Track B), never here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

AiStatus = Literal[
    "ok",
    "invalid_output",
    "timeout",
    "auth_failure",
    "rate_limited",
    "provider_outage",
    "unsupported",
    "budget_exhausted",
    "disabled",
]

#: All terminal failure statuses that must never be retried.
NON_RETRYABLE = frozenset(
    {"auth_failure", "invalid_output", "unsupported", "disabled", "budget_exhausted"}
)

#: Failures worth one bounded retry (subject to ``max_retries``).
RETRYABLE = frozenset({"timeout", "rate_limited", "provider_outage"})

MAX_EVIDENCE_KEYS = 64
MAX_EVIDENCE_STR_CHARS = 8000


class AiRequest(BaseModel):
    """A single structured-output AI request."""

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    evidence: dict[str, Any] = Field(default_factory=dict)
    schema_name: str = ""
    timeout_s: float = Field(default=30.0, ge=1.0, le=300.0)
    max_retries: int = Field(default=2, ge=0, le=3)
    correlation_id: str = ""

    @field_validator("evidence")
    @classmethod
    def _bound_evidence(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > MAX_EVIDENCE_KEYS:
            raise ValueError(
                f"evidence has {len(v)} keys, limit is {MAX_EVIDENCE_KEYS}; "
                "narrow the evidence selection instead of widening the request."
            )
        for key, value in v.items():
            if isinstance(value, str) and len(value) > MAX_EVIDENCE_STR_CHARS:
                raise ValueError(
                    f"evidence[{key!r}] is {len(value)} chars, limit is "
                    f"{MAX_EVIDENCE_STR_CHARS}; truncate before requesting."
                )
        return v


class AiResponse(BaseModel):
    """A typed provider outcome. ``ok`` is only set on real parsed success."""

    request_id: str
    correlation_id: str = ""
    provider_id: str
    model_id: str
    status: AiStatus
    payload: dict[str, Any] | None = None
    raw_excerpt: str = Field(default="", max_length=500)
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float | None = None
    retries: int = 0
    fallback_chain: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds")
    )


__all__ = [
    "MAX_EVIDENCE_KEYS",
    "MAX_EVIDENCE_STR_CHARS",
    "NON_RETRYABLE",
    "RETRYABLE",
    "AiRequest",
    "AiResponse",
    "AiStatus",
]
