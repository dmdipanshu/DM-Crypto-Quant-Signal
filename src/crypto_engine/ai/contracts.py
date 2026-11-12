"""Provider-neutral AI contracts (Stage 0 / A0).

Imports here are standard library only: no provider SDK, no HTTP client, no
FastAPI, no database driver, no trading/risk module. Concrete adapters live in
infrastructure and are injected into
:class:`crypto_engine.ai.gateway.AIGateway`.

Authority boundary: :class:`AIResponse` is advisory data. It is frozen, marked
``advisory_only`` and deliberately carries no signal direction, deterministic
score, risk limit, position size, approval flag, execution authorization,
circuit-breaker or ledger field. AI output cannot mutate deterministic state
through these contracts.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit
from uuid import uuid4

from crypto_engine.ai.errors import AIError, AIStatus, redact_secrets

#: Hard application workload envelope. This is an architectural operating
#: budget, not an OS-level memory limit; bounded workloads are the mechanism.
WORKLOAD_BUDGET_BYTES = 4 * 1024**3

#: Exactly one AI request in flight (4 GB laptop, single process).
MAX_CONCURRENT_AI_REQUESTS = 1

#: Bounded retry ceiling; the architecture never retries without a bound.
MAX_RETRY_CEILING = 3

DEFAULT_MAX_REQUEST_CHARS = 8_000
DEFAULT_MAX_RESPONSE_CHARS = 4_000
DEFAULT_MAX_OUTPUT_TOKENS = 1_024
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 2
MAX_QUEUE_DEPTH = 64

MAX_EVIDENCE_KEYS = 64
MAX_EVIDENCE_VALUE_CHARS = 8_000

#: Schemas treated as plain text; anything else requires structured output.
TEXT_SCHEMA_IDS = frozenset({"text", "text.v1"})

ACTORS = frozenset({"operator", "cli", "scheduler"})
AUTH_MECHANISMS = frozenset({"bearer", "header", "query", "none"})
STRUCTURED_OUTPUT_LEVELS = ("none", "json_mode", "json_schema")
USAGE_REPORTING_LEVELS = ("exact", "estimated", "none")

_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@\-]{0,127}$")
_ENV_VAR_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class AITask(StrEnum):
    """Task vocabulary used for provider/model routing (config, not code)."""

    SIGNAL_EXPLANATION = "signal_explanation"
    REGIME_EXPLANATION = "regime_explanation"
    RISK_EXPLANATION = "risk_explanation"
    RESEARCH_SUMMARY = "research_summary"
    BACKTEST_EXPLANATION = "backtest_explanation"
    STRATEGY_COMPARISON_SUMMARY = "strategy_comparison_summary"
    ROBUSTNESS_SUMMARY = "robustness_summary"
    FAILURE_ANALYSIS = "failure_analysis"
    MARKET_CONTEXT = "market_context"
    ASSISTANT = "assistant"
    REPORT_GENERATION = "report_generation"
    NEWS_ANALYSIS = "news_analysis"
    MACRO_ANALYSIS = "macro_analysis"


@dataclass(frozen=True, slots=True, repr=False)
class SecretRef:
    """Reference to an environment variable; never holds the secret value."""

    env_var: str

    def __post_init__(self) -> None:
        if not isinstance(self.env_var, str) or not _ENV_VAR_PATTERN.match(self.env_var):
            raise ValueError("secret reference must be an environment variable NAME (A-Z, 0-9, _)")

    def is_set(self, environ: Mapping[str, str] | None = None) -> bool:
        return self.resolve(environ) is not None

    def resolve(self, environ: Mapping[str, str] | None = None) -> str | None:
        source = os.environ if environ is None else environ
        value = source.get(self.env_var)
        return value or None

    def require(self, environ: Mapping[str, str] | None = None) -> str:
        value = self.resolve(environ)
        if value is None:
            raise AIError(
                AIStatus.AUTHENTICATION_ERROR,
                f"credential environment variable '{self.env_var}' is not set",
            )
        return value

    def __str__(self) -> str:
        return f"SecretRef({self.env_var})"

    def __repr__(self) -> str:
        return self.__str__()


@dataclass(frozen=True, slots=True)
class AICapabilities:
    """Declared capabilities; the gateway refuses impossible requests up front."""

    structured_output: str = "none"
    text_generation: bool = True
    max_context_tokens: int = 0
    max_output_tokens: int = 0
    reasoning_metadata: bool = False
    usage_reporting: str = "none"
    modalities: tuple[str, ...] = ("text",)
    tool_calling: bool = False
    streaming: bool = False
    local: bool = False

    def __post_init__(self) -> None:
        if self.structured_output not in STRUCTURED_OUTPUT_LEVELS:
            raise ValueError(f"structured_output must be one of {STRUCTURED_OUTPUT_LEVELS}")
        if self.usage_reporting not in USAGE_REPORTING_LEVELS:
            raise ValueError(f"usage_reporting must be one of {USAGE_REPORTING_LEVELS}")
        if self.tool_calling:
            raise ValueError("tool calling is outside the approved AI boundary")
        if any(modality != "text" for modality in self.modalities):
            raise ValueError("only the text modality is approved")
        if self.max_context_tokens < 0 or self.max_output_tokens < 0:
            raise ValueError("token ceilings must be non-negative")


@dataclass(frozen=True, slots=True)
class AIUsage:
    """Token usage reported by an adapter (``0`` when not reported)."""

    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token usage must be non-negative")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class AIErrorInfo:
    """Redacted failure record carried by a non-ok :class:`AIResponse`."""

    code: AIStatus
    message: str
    retryable: bool = False
    provider_status: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", AIStatus(self.code))
        object.__setattr__(self, "message", redact_secrets(self.message))


@dataclass(frozen=True, slots=True)
class AIRequest:
    """Normalized, provider-neutral request. Callers never pick a model."""

    task: AITask
    task_text: str
    evidence: Mapping[str, str] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    output_schema_id: str = "text.v1"
    related_ids: Mapping[str, str] = field(default_factory=dict)
    max_output_tokens: int | None = None
    timeout_s: float | None = None
    actor: str = "cli"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task", AITask(self.task))
        if not isinstance(self.task_text, str) or not self.task_text.strip():
            raise ValueError("task_text must be a non-empty application-owned instruction")
        if not isinstance(self.output_schema_id, str) or not self.output_schema_id.strip():
            raise ValueError("output_schema_id must be a non-empty string")
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("request_id must be a non-empty string")
        if self.actor not in ACTORS:
            raise ValueError(f"actor must be one of {sorted(ACTORS)}")
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive when set")
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive when set")
        object.__setattr__(self, "evidence", _freeze_str_mapping(self.evidence, "evidence"))
        object.__setattr__(
            self, "related_ids", _freeze_str_mapping(self.related_ids, "related_ids")
        )

    @property
    def char_size(self) -> int:
        """Bounded request size in characters (instruction + evidence keys)."""
        evidence_chars = sum(len(key) + len(value) for key, value in self.evidence.items())
        return len(self.task_text) + len(self.output_schema_id) + evidence_chars


@dataclass(frozen=True, slots=True)
class AIProviderSpec:
    """Provider metadata. ``api_key_env`` is a NAME, never a secret value."""

    provider_id: str
    display_name: str
    dialect: str = "openai_compatible"
    base_url: str | None = None
    api_key_env: str | None = None
    auth_mechanism: str = "bearer"
    capabilities: AICapabilities = field(default_factory=AICapabilities)
    egress_allowlist: tuple[str, ...] = ()
    enabled: bool = False
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_retries: int = DEFAULT_MAX_RETRIES

    def __post_init__(self) -> None:
        if not _PROVIDER_ID_PATTERN.match(self.provider_id):
            raise ValueError("provider_id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("display_name must be a non-empty string")
        if not isinstance(self.dialect, str) or not self.dialect.strip():
            raise ValueError("dialect must be a non-empty string")
        if self.auth_mechanism not in AUTH_MECHANISMS:
            raise ValueError(f"auth_mechanism must be one of {sorted(AUTH_MECHANISMS)}")
        if self.base_url is not None:
            _validate_base_url(self.base_url)
        if self.api_key_env is not None and not _ENV_VAR_PATTERN.match(self.api_key_env):
            raise ValueError("api_key_env must be an environment variable NAME, never a secret")
        if not 0 < self.timeout_s <= 600:
            raise ValueError("timeout_s must be in (0, 600] seconds")
        if not 0 <= self.max_retries <= MAX_RETRY_CEILING:
            raise ValueError(f"max_retries must be in [0, {MAX_RETRY_CEILING}]")
        for host in self.egress_allowlist:
            if not isinstance(host, str) or not host or "://" in host or "/" in host:
                raise ValueError("egress_allowlist entries must be bare hostnames")

    @property
    def secret_ref(self) -> SecretRef | None:
        return SecretRef(self.api_key_env) if self.api_key_env else None


@dataclass(frozen=True, slots=True)
class AIModel:
    """Model metadata; capabilities may narrow, never widen, the provider."""

    provider_id: str
    model_id: str
    display_name: str = ""
    capabilities: AICapabilities = field(default_factory=AICapabilities)
    context_limit: int = 0
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    enabled: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if not _PROVIDER_ID_PATTERN.match(self.provider_id):
            raise ValueError("provider_id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
        if not _MODEL_ID_PATTERN.match(self.model_id):
            raise ValueError("model_id contains unsupported characters")
        if not self.display_name:
            object.__setattr__(self, "display_name", self.model_id)
        if self.context_limit < 0:
            raise ValueError("context_limit must be non-negative")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")


@dataclass(frozen=True, slots=True)
class AIResponse:
    """Normalized, advisory-only response. Frozen: AI cannot mutate state."""

    request_id: str
    task: AITask
    status: AIStatus = AIStatus.OK
    provider_id: str | None = None
    model_id: str | None = None
    route_id: str | None = None
    content: Mapping[str, Any] | None = None
    text: str | None = None
    usage: AIUsage = field(default_factory=AIUsage)
    latency_ms: int = 0
    attempts: int = 1
    error: AIErrorInfo | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    advisory_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "task", AITask(self.task))
        object.__setattr__(self, "status", AIStatus(self.status))
        if not self.advisory_only:
            raise ValueError("AI responses are advisory only and cannot be authoritative")
        if self.latency_ms < 0 or self.attempts < 1:
            raise ValueError("latency_ms must be non-negative and attempts must be >= 1")
        if self.status is AIStatus.OK:
            if self.error is not None:
                raise ValueError("ok AI response must not carry an error record")
            if self.text is None and self.content is None:
                raise ValueError("ok AI response must carry text or structured content")
        elif self.error is None:
            raise ValueError("failed AI response must carry an AIErrorInfo")
        if self.content is not None:
            object.__setattr__(self, "content", MappingProxyType(dict(self.content)))

    @property
    def ok(self) -> bool:
        return self.status is AIStatus.OK


@runtime_checkable
class AIProviderPort(Protocol):
    """Adapter boundary: one adapter, one dialect, no credentials or endpoints."""

    provider_id: str

    def complete(self, request: AIRequest, *, model: AIModel, timeout_s: float) -> AIResponse:
        """Perform exactly one bounded call; raise :class:`AIError` on failure."""
        ...


@dataclass(frozen=True, slots=True)
class AIResourceLimits:
    """Bounded-workload configuration (concurrency is fixed at exactly one)."""

    max_request_chars: int = DEFAULT_MAX_REQUEST_CHARS
    max_response_chars: int = DEFAULT_MAX_RESPONSE_CHARS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_concurrent_requests: int = MAX_CONCURRENT_AI_REQUESTS
    max_retries: int = DEFAULT_MAX_RETRIES
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_queue_depth: int = 1

    def __post_init__(self) -> None:
        if self.max_concurrent_requests != MAX_CONCURRENT_AI_REQUESTS:
            raise ValueError("max_concurrent_requests must be exactly 1 (single-process design)")
        for name, value in (
            ("max_request_chars", self.max_request_chars),
            ("max_response_chars", self.max_response_chars),
            ("max_output_tokens", self.max_output_tokens),
            ("max_queue_depth", self.max_queue_depth),
        ):
            if not 1 <= value <= 1_000_000:
                raise ValueError(f"{name} must be in [1, 1_000_000]")
        if not 0 <= self.max_retries <= MAX_RETRY_CEILING:
            raise ValueError(f"max_retries must be in [0, {MAX_RETRY_CEILING}]")
        if not 0 < self.timeout_s <= 600:
            raise ValueError("timeout_s must be in (0, 600] seconds")
        if self.max_queue_depth > MAX_QUEUE_DEPTH:
            raise ValueError(f"max_queue_depth must not exceed {MAX_QUEUE_DEPTH}")


DEFAULT_LIMITS = AIResourceLimits()


def _freeze_str_mapping(value: Mapping[str, str], name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    if name == "evidence" and len(value) > MAX_EVIDENCE_KEYS:
        raise ValueError(f"evidence must not exceed {MAX_EVIDENCE_KEYS} keys")
    frozen: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"{name} keys and values must be strings")
        if name == "evidence" and len(item) > MAX_EVIDENCE_VALUE_CHARS:
            raise ValueError(
                f"evidence values must not exceed {MAX_EVIDENCE_VALUE_CHARS} characters"
            )
        frozen[key] = item
    return MappingProxyType(frozen)


def _validate_base_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url must be an absolute http(s) URL")
    if "@" in parsed.netloc:
        raise ValueError("base_url must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query strings or fragments")
