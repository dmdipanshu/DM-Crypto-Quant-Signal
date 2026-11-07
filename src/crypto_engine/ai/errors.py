"""Deterministic AI failure states (Stage 0 / A0).

The vocabulary is closed. Member names follow the Stage 0 contract
(``NOT_CONFIGURED`` ... ``INTERNAL_ERROR``); values keep the observable
snake_case strings from ``docs/architecture/AI_GATEWAY.md`` so CLI/API/audit
output stays stable. Two Stage 0 additions are documented here rather than
invented silently:

* ``INVALID_CONFIGURATION`` - registry/provider configuration is structurally
  wrong (distinct from a caller's ``INVALID_REQUEST``).
* ``INVALID_RESPONSE`` and ``UNSUPPORTED_CAPABILITY`` map onto the frozen
  ``invalid_output`` and ``unsupported`` values.

AI failure must never become a hidden trading decision: the deterministic
core stays usable when AI is disabled, unavailable, misconfigured or timed out.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any


class AIStatus(StrEnum):
    """Closed AI status vocabulary (``ok`` plus deterministic failures)."""

    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    INVALID_CONFIGURATION = "invalid_configuration"
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION_ERROR = "auth_failure"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_outage"
    INVALID_RESPONSE = "invalid_output"
    UNSUPPORTED_CAPABILITY = "unsupported"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"

    # Stage 1 canonical aliases. These resolve to the same members as the
    # Stage 0 names above (one vocabulary, no duplicate semantics); they exist
    # so code and docs may use the short names from the Stage 1 contract.
    AUTH_FAILURE = "auth_failure"
    PROVIDER_OUTAGE = "provider_outage"
    INVALID_OUTPUT = "invalid_output"
    UNSUPPORTED = "unsupported"

    @property
    def retryable(self) -> bool:
        """Whether the gateway may spend a bounded retry on this status."""
        return self in RETRYABLE_STATUSES


#: Statuses worth a bounded retry (never unbounded, never non-transient ones).
RETRYABLE_STATUSES: frozenset[AIStatus] = frozenset(
    {AIStatus.RATE_LIMITED, AIStatus.TIMEOUT, AIStatus.PROVIDER_UNAVAILABLE}
)

#: Everything except ``ok`` and the retryable transient statuses.
NON_RETRYABLE_STATUSES: frozenset[AIStatus] = (
    frozenset(AIStatus) - RETRYABLE_STATUSES - {AIStatus.OK}
)

_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{4,}")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|authorization)\b(\s*[=:]\s*)\S+"
)
_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}")


def redact_secrets(text: str) -> str:
    """Strip credential-shaped substrings before a message is observable."""
    cleaned = _BEARER_PATTERN.sub(r"\1 <redacted>", str(text))
    cleaned = _ASSIGNMENT_PATTERN.sub(r"\1\2<redacted>", cleaned)
    return _KEY_PATTERN.sub("<redacted>", cleaned)


class AIError(Exception):
    """A classified AI failure; safe to convert into an ``AIResponse``."""

    def __init__(
        self,
        status: AIStatus | str,
        message: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        retryable: bool | None = None,
        provider_status: int | None = None,
    ) -> None:
        resolved = AIStatus(status)
        if resolved is AIStatus.OK:
            raise ValueError("AIError requires a non-ok status")
        self.status = resolved
        self.message = redact_secrets(message)
        self.provider_id = provider_id
        self.model_id = model_id
        self.provider_status = provider_status
        self.retryable = resolved.retryable if retryable is None else bool(retryable)
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Redacted, JSON-friendly error record (never contains a secret)."""
        return {
            "code": self.status.value,
            "message": self.message,
            "retryable": self.retryable,
            "provider_status": self.provider_status,
        }


def classify_exception(exc: BaseException) -> AIError:
    """Map an adapter-boundary exception into the closed status vocabulary.

    Messages use type names only: an exception string may carry request
    headers or provider payload fragments.
    """
    if isinstance(exc, AIError):
        return exc
    if isinstance(exc, TimeoutError):
        return AIError(AIStatus.TIMEOUT, "AI call exceeded its deadline")
    if isinstance(exc, PermissionError):
        return AIError(AIStatus.AUTHENTICATION_ERROR, "AI credential was rejected")
    return AIError(AIStatus.INTERNAL_ERROR, f"unexpected adapter failure: {type(exc).__name__}")
