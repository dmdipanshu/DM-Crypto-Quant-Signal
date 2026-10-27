"""Shared provider base: transport injection, secrets hygiene, retry loop.

Secrets policy: API keys are read from ``os.environ`` at call time, sent only
in the request headers/body, and never copied into ``AiResponse``,
exceptions, or logs. There is intentionally no "show last 4" helper —
tests assert the key substring is absent everywhere observable.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from crypto_engine.ai_providers.types import NON_RETRYABLE, AiRequest, AiResponse, AiStatus


@dataclass
class TransportResponse:
    """Minimal fakeable HTTP response (both fake and urllib transports)."""

    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    latency_ms: float = 0.0

    def text(self, max_chars: int = 200_000) -> str:
        """Decode body as UTF-8, bounded for 4 GB safety."""
        raw = self.body[:max_chars]
        return raw.decode("utf-8", errors="replace")

    def retry_after_s(self) -> float | None:
        """Parse ``Retry-After`` (seconds) when present."""
        for key, value in self.headers.items():
            if key.lower() == "retry-after":
                try:
                    return max(0.0, float(str(value).strip()))
                except ValueError:
                    return None
        return None


#: ``(method, url, headers, body, timeout_s) -> TransportResponse``.
TransportCallable = Callable[[str, str, dict[str, str], bytes | None, float], TransportResponse]


def redact_text(text: str, secrets: list[str]) -> str:
    """Remove every non-empty secret substring from ``text``."""
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string with milliseconds."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def truncate_raw(text: str, max_chars: int) -> tuple[str, bool]:
    """Bound a raw payload; returns ``(excerpt, truncated_flag)``."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse ``text`` as a JSON object; raise ``ValueError`` otherwise."""
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected JSON object, got {type(parsed).__name__}")
    return parsed


def http_status_to_ai_status(status_code: int) -> AiStatus:
    """Map HTTP codes to typed AI statuses (401/429/5xx/404)."""
    if status_code == 401 or status_code == 403:
        return "auth_failure"
    if status_code == 429:
        return "rate_limited"
    if status_code == 404:
        return "unsupported"
    if 500 <= status_code <= 599:
        return "provider_outage"
    return "provider_outage"


class AiProvider(ABC):
    """Abstract provider: request mapping, typed failures, bounded retries."""

    provider_id: str = "base"
    capabilities: ClassVar[dict[str, bool]] = {
        "structured_output": False,
        "system_messages": False,
        "token_usage": False,
        "retry_after": False,
    }

    def __init__(
        self,
        model_id: str,
        *,
        api_key_env: str | None = None,
        base_url: str = "",
        max_response_chars: int = 4000,
        enabled: bool = True,
        transport: TransportCallable | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.model_id = model_id
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.max_response_chars = max_response_chars
        self.enabled = enabled
        self._transport = transport
        self._extra_headers = dict(extra_headers or {})

    # -- overridable mapping hooks -------------------------------------
    @abstractmethod
    def build_request(self, request: AiRequest, api_key: str | None) -> tuple[str, dict, dict]:
        """Return ``(url, headers, json_body)`` for ``request``."""

    @abstractmethod
    def parse_success(self, request: AiRequest, body_text: str) -> tuple[dict | None, dict]:
        """Parse a 2xx body; return ``(payload, usage)`` or raise ``ValueError``."""

    # -- shared machinery ----------------------------------------------
    def read_secret(self) -> str | None:
        """Read the API key from the environment at call time (never cached)."""
        import os

        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None

    def _send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> TransportResponse:
        if self._transport is not None:
            return self._transport(method, url, headers, body, timeout)
        started = time.perf_counter()
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                latency = (time.perf_counter() - started) * 1000.0
                return TransportResponse(
                    status_code=int(resp.status),
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    body=raw,
                    latency_ms=latency,
                )
        except urllib.error.HTTPError as exc:
            latency = (time.perf_counter() - started) * 1000.0
            try:
                raw = exc.read()
            except Exception:
                raw = b""
            hdrs = {}
            try:
                hdrs = {k.lower(): v for k, v in (exc.headers or {}).items()}
            except Exception:
                hdrs = {}
            return TransportResponse(
                status_code=int(exc.code), headers=hdrs, body=raw, latency_ms=latency
            )
        except TimeoutError as exc:
            raise TimeoutError(f"{self.provider_id}: request timed out: {exc}") from exc
        except Exception as exc:
            if "timed out" in str(exc).lower() or "timeout" in type(exc).__name__.lower():
                raise TimeoutError(f"{self.provider_id}: request timed out: {exc}") from exc
            raise ConnectionError(f"{self.provider_id}: transport failure: {exc}") from exc

    def _failure(
        self,
        request: AiRequest,
        status: AiStatus,
        detail: str,
        *,
        retries: int,
        latency_ms: float | None,
        secrets: list[str],
        usage_extra: dict[str, Any] | None = None,
    ) -> AiResponse:
        excerpt = redact_text(detail, secrets)[:500]
        usage = dict(usage_extra or {})
        usage.setdefault("truncated", False)
        return AiResponse(
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            status=status,
            payload=None,
            raw_excerpt=excerpt,
            usage=usage,
            latency_ms=latency_ms,
            retries=retries,
            fallback_chain=[],
            validation_errors=[excerpt] if excerpt else [],
            generated_at=utc_now_iso(),
        )

    def generate(self, request: AiRequest) -> AiResponse:
        """Execute ``request`` with bounded retries; never synthesize ``ok``."""
        secrets: list[str] = []
        if not self.enabled:
            return self._failure(
                request,
                "disabled",
                f"{self.provider_id}: provider disabled in config.",
                retries=0,
                latency_ms=None,
                secrets=secrets,
            )
        api_key = self.read_secret()
        if self.api_key_env and not api_key and self.provider_id != "ollama":
            return self._failure(
                request,
                "auth_failure",
                f"{self.provider_id}: missing API key; set env var {self.api_key_env}.",
                retries=0,
                latency_ms=None,
                secrets=secrets,
            )
        if api_key:
            secrets.append(api_key)

        attempts = 0
        last: AiResponse | None = None
        while True:
            url, headers, body_json = self.build_request(request, api_key)
            safe_headers = {k: redact_text(v, secrets) for k, v in headers.items()}
            _ = safe_headers  # headers are sent, never logged
            body_bytes = json.dumps(body_json).encode("utf-8")
            started = time.perf_counter()
            try:
                resp = self._send("POST", url, headers, body_bytes, request.timeout_s)
                latency = resp.latency_ms or (time.perf_counter() - started) * 1000.0
            except TimeoutError:
                latency = (time.perf_counter() - started) * 1000.0
                last = self._failure(
                    request,
                    "timeout",
                    f"{self.provider_id}: request timed out.",
                    retries=attempts,
                    latency_ms=latency,
                    secrets=secrets,
                )
            except Exception as exc:
                latency = (time.perf_counter() - started) * 1000.0
                detail = redact_text(str(exc), secrets)
                last = self._failure(
                    request,
                    "provider_outage",
                    f"{self.provider_id}: connection failed; check base_url/network. "
                    f"({type(exc).__name__}: {detail[:200]})"
                    if detail
                    else f"{self.provider_id}: connection failed; check base_url/network. "
                    f"({type(exc).__name__})",
                    retries=attempts,
                    latency_ms=latency,
                    secrets=secrets,
                )
            else:
                last = self._handle_http(request, resp, secrets, attempts, latency)
            assert last is not None
            if last.status == "ok" or last.status in NON_RETRYABLE:
                return last
            if attempts >= request.max_retries:
                return last
            attempts += 1
            last.retries = attempts

    def _handle_http(
        self,
        request: AiRequest,
        resp: TransportResponse,
        secrets: list[str],
        attempts: int,
        latency_ms: float,
    ) -> AiResponse:
        if 200 <= resp.status_code <= 299:
            raw_text = resp.text(max_chars=self.max_response_chars + 1)
            excerpt_src, truncated = truncate_raw(raw_text, self.max_response_chars)
            try:
                payload, usage = self.parse_success(
                    request, raw_text if not truncated else excerpt_src
                )
            except ValueError as exc:
                return self._failure(
                    request,
                    "invalid_output",
                    f"{self.provider_id}: structured parse failed: {exc}",
                    retries=attempts,
                    latency_ms=latency_ms,
                    secrets=secrets,
                    usage_extra={"truncated": truncated},
                )
            usage = dict(usage)
            usage["truncated"] = truncated
            excerpt = redact_text(excerpt_src, secrets)[:500]
            return AiResponse(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                provider_id=self.provider_id,
                model_id=request.model_id,
                status="ok",
                payload=payload,
                raw_excerpt=excerpt,
                usage=usage,
                latency_ms=latency_ms,
                retries=attempts,
                fallback_chain=[],
                validation_errors=[],
                generated_at=utc_now_iso(),
            )
        status = http_status_to_ai_status(resp.status_code)
        raw = redact_text(resp.text(max_chars=2000), secrets)
        detail = f"{self.provider_id}: HTTP {resp.status_code}."
        if status == "auth_failure":
            detail += " Check API key (never logged)."
        elif status == "rate_limited":
            detail += " Rate limited."
        elif status == "unsupported":
            detail += f" Model or endpoint unsupported: {request.model_id}."
        usage_extra: dict[str, Any] = {"truncated": False, "http_status": resp.status_code}
        retry_after = resp.retry_after_s()
        if retry_after is not None:
            usage_extra["retry_after_s"] = retry_after
        if raw.strip():
            detail += f" Body: {raw[:200]}"
        return self._failure(
            request,
            status,
            detail,
            retries=attempts,
            latency_ms=latency_ms,
            secrets=secrets,
            usage_extra=usage_extra,
        )

    # -- introspection ---------------------------------------------------
    def health_check(self) -> dict[str, Any]:
        """Offline-safe health snapshot (no network unless transport set)."""
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "enabled": self.enabled,
            "base_url": self.base_url,
            "key_configured": self.read_secret() is not None,
            "max_response_chars": self.max_response_chars,
        }

    def provider_info(self) -> dict[str, Any]:
        """Static provider metadata (no secrets)."""
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "capabilities": dict(self.capabilities),
        }

    def close(self) -> None:  # noqa: B027 - intentional no-op; urllib is stateless
        """Release resources (urllib is stateless; SDK adapters override)."""


__all__ = [
    "AiProvider",
    "TransportCallable",
    "TransportResponse",
    "extract_json_object",
    "http_status_to_ai_status",
    "redact_text",
    "truncate_raw",
    "utc_now_iso",
]
