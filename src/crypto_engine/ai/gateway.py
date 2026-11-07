"""Generic AI gateway skeleton (Stage 0 / A0).

Single application-level entry point for AI requests. It validates a
normalized request, resolves ``task -> provider -> model`` through the
registry, checks enablement/capabilities/credentials, enforces bounded
concurrency, calls exactly one adapter, and normalizes responses and failures
into the closed status vocabulary.

It deliberately does **not** calculate signals, adjust or veto risk, approve or
size trades, submit orders, mutate strategy scores, circuit breakers or
ledgers. AI failure never becomes a hidden trading decision: every failure is
returned as a non-ok :class:`AIResponse` and the deterministic core continues.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace

from crypto_engine.ai.contracts import (
    DEFAULT_LIMITS,
    TEXT_SCHEMA_IDS,
    AIErrorInfo,
    AIProviderPort,
    AIProviderSpec,
    AIRequest,
    AIResourceLimits,
    AIResponse,
)
from crypto_engine.ai.errors import AIError, AIStatus, classify_exception
from crypto_engine.ai.registry import AIRegistry, ResolvedRoute


class AIGateway:
    """Provider-neutral gateway; adapters are injected, never imported here."""

    def __init__(
        self,
        registry: AIRegistry,
        ports: Mapping[str, AIProviderPort] | None = None,
        limits: AIResourceLimits | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._registry = registry
        self._ports: dict[str, AIProviderPort] = dict(ports or {})
        self._limits = limits or DEFAULT_LIMITS
        self._environ = environ
        self._clock = clock or time.monotonic
        self._sleep = sleeper or time.sleep
        self._inflight = threading.Lock()

    @property
    def limits(self) -> AIResourceLimits:
        return self._limits

    def register_port(self, provider_id: str, port: AIProviderPort) -> None:
        """Composition-root hook; one adapter per provider id."""
        self._ports[provider_id] = port

    def complete(self, request: AIRequest) -> AIResponse:
        """Run one bounded AI request and always return a normalized response."""
        if not isinstance(request, AIRequest):
            raise TypeError("AIGateway.complete() requires an AIRequest")
        route_id: str | None = None
        try:
            self._validate_request(request)
            if not self._registry.enabled:
                raise AIError(
                    AIStatus.DISABLED,
                    "AI is disabled; the deterministic core continues without AI context",
                )
            route = self._registry.resolve(request.task)
            route_id = route.route_id
            self._check_capabilities(request, route)
            port = self._ports.get(route.provider.provider_id)
            if port is None:
                raise AIError(
                    AIStatus.NOT_CONFIGURED,
                    f"no adapter registered for provider '{route.provider.provider_id}'",
                    provider_id=route.provider.provider_id,
                    model_id=route.model.model_id,
                )
            self._check_auth(route.provider)
            timeout_s = self._effective_timeout(request, route.provider)
            with self._single_flight():
                return self._invoke(request, route, port, timeout_s)
        except AIError as error:
            return self._failure(error, request=request, route_id=route_id)

    def _validate_request(self, request: AIRequest) -> None:
        if request.char_size > self._limits.max_request_chars:
            raise AIError(
                AIStatus.INVALID_REQUEST,
                f"request exceeds max_request_chars={self._limits.max_request_chars}",
            )
        if (
            request.max_output_tokens is not None
            and request.max_output_tokens > self._limits.max_output_tokens
        ):
            raise AIError(
                AIStatus.INVALID_REQUEST,
                f"requested max_output_tokens exceeds limit {self._limits.max_output_tokens}",
            )

    def _check_capabilities(self, request: AIRequest, route: ResolvedRoute) -> None:
        capabilities = route.model.capabilities
        provider_id, model_id = route.provider.provider_id, route.model.model_id
        if route.provider.capabilities.local:
            raise AIError(
                AIStatus.UNSUPPORTED_CAPABILITY,
                f"provider '{provider_id}' declares local inference, which is not part of Stage 0",
                provider_id=provider_id,
                model_id=model_id,
            )
        if not capabilities.text_generation:
            raise AIError(
                AIStatus.UNSUPPORTED_CAPABILITY,
                f"model '{model_id}' does not declare text_generation",
                provider_id=provider_id,
                model_id=model_id,
            )
        if (
            request.output_schema_id not in TEXT_SCHEMA_IDS
            and capabilities.structured_output == "none"
        ):
            raise AIError(
                AIStatus.UNSUPPORTED_CAPABILITY,
                f"model '{model_id}' does not declare structured output for schema "
                f"'{request.output_schema_id}'",
                provider_id=provider_id,
                model_id=model_id,
            )
        if (
            request.max_output_tokens is not None
            and capabilities.max_output_tokens
            and request.max_output_tokens > capabilities.max_output_tokens
        ):
            raise AIError(
                AIStatus.INVALID_REQUEST,
                f"requested max_output_tokens exceeds model ceiling "
                f"{capabilities.max_output_tokens}",
                provider_id=provider_id,
                model_id=model_id,
            )

    def _check_auth(self, spec: AIProviderSpec) -> None:
        if spec.auth_mechanism == "none":
            return
        reference = spec.secret_ref
        if reference is None:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"provider '{spec.provider_id}' requires an api_key_env reference",
                provider_id=spec.provider_id,
            )
        if not reference.is_set(self._environ):
            raise AIError(
                AIStatus.AUTHENTICATION_ERROR,
                f"credential environment variable '{reference.env_var}' is not set for "
                f"provider '{spec.provider_id}'",
                provider_id=spec.provider_id,
            )

    def _effective_timeout(self, request: AIRequest, spec: AIProviderSpec) -> float:
        """Per-request timeout capped by both the global and provider ceilings."""
        ceiling = min(self._limits.timeout_s, spec.timeout_s)
        if request.timeout_s is None:
            return ceiling
        return min(request.timeout_s, ceiling)

    @contextmanager
    def _single_flight(self) -> Iterator[None]:
        if not self._inflight.acquire(blocking=False):
            raise AIError(
                AIStatus.RATE_LIMITED,
                "local AI concurrency limit reached (max_concurrent_requests=1)",
            )
        try:
            yield
        finally:
            self._inflight.release()

    def _invoke(
        self,
        request: AIRequest,
        route: ResolvedRoute,
        port: AIProviderPort,
        timeout_s: float,
    ) -> AIResponse:
        attempts = 0
        max_retries = min(self._limits.max_retries, route.provider.max_retries)
        while True:
            attempts += 1
            started = self._clock()
            try:
                raw = port.complete(request, model=route.model, timeout_s=timeout_s)
            except AIError as error:
                if error.retryable and attempts <= max_retries:
                    self._backoff(attempts)
                    continue
                return self._failure(
                    error, request=request, route_id=route.route_id, attempts=attempts
                )
            except TimeoutError:
                timeout_error = AIError(
                    AIStatus.TIMEOUT, f"adapter exceeded timeout of {timeout_s:.3f}s"
                )
                if attempts <= max_retries:
                    self._backoff(attempts)
                    continue
                return self._failure(
                    timeout_error,
                    request=request,
                    route_id=route.route_id,
                    attempts=attempts,
                )
            except Exception as exc:  # adapter boundary: never crash the core
                return self._failure(
                    classify_exception(exc),
                    request=request,
                    route_id=route.route_id,
                    attempts=attempts,
                )
            latency_ms = max(0, round((self._clock() - started) * 1000))
            try:
                normalized = self._normalize(raw, request, route, attempts, latency_ms)
            except AIError as error:
                return self._failure(
                    error, request=request, route_id=route.route_id, attempts=attempts
                )
            if normalized.status.retryable and attempts <= self._limits.max_retries:
                self._backoff(attempts)
                continue
            return normalized

    def _normalize(
        self,
        response: object,
        request: AIRequest,
        route: ResolvedRoute,
        attempts: int,
        latency_ms: int,
    ) -> AIResponse:
        provider_id, model_id = route.provider.provider_id, route.model.model_id
        if not isinstance(response, AIResponse):
            raise AIError(
                AIStatus.INTERNAL_ERROR,
                f"adapter returned {type(response).__name__}, expected AIResponse",
                provider_id=provider_id,
                model_id=model_id,
            )
        if response.request_id != request.request_id or response.task != request.task:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "adapter response does not match the request id/task",
                provider_id=provider_id,
                model_id=model_id,
            )
        if response.provider_id != provider_id or response.model_id != model_id:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "adapter may not substitute a different provider or model",
                provider_id=provider_id,
                model_id=model_id,
            )
        if not response.advisory_only:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "adapter response must remain advisory-only",
                provider_id=provider_id,
                model_id=model_id,
            )
        if response.text is not None and len(response.text) > self._limits.max_response_chars:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                f"adapter response exceeds max_response_chars={self._limits.max_response_chars}",
                provider_id=provider_id,
                model_id=model_id,
            )
        return replace(
            response,
            route_id=route.route_id,
            latency_ms=latency_ms,
            attempts=attempts,
        )

    def _failure(
        self,
        error: AIError,
        *,
        request: AIRequest,
        route_id: str | None,
        attempts: int = 1,
    ) -> AIResponse:
        return AIResponse(
            request_id=request.request_id,
            task=request.task,
            status=error.status,
            provider_id=error.provider_id,
            model_id=error.model_id,
            route_id=route_id,
            error=AIErrorInfo(
                code=error.status,
                message=error.message,
                retryable=error.retryable,
                provider_status=error.provider_status,
            ),
            attempts=max(1, attempts),
        )

    def _backoff(self, attempts: int) -> None:
        """Bounded exponential backoff, injected for deterministic tests."""
        self._sleep(min(2.0, 0.5 * (2 ** (attempts - 1))))
