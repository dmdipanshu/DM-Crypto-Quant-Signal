"""OpenAI-compatible provider adapter (Stage 2; infrastructure only).

One adapter, one dialect. The gateway never imports this module: the
composition root injects the adapter through :class:`AIProviderPort`.

Transport is stdlib ``urllib`` by default and fully injectable for offline
tests. No provider SDK, no streaming, no tools, no background threads, no
retries inside the adapter (the gateway owns bounded retries).

Egress is bounded in two places: the request host must match the provider's
``egress_allowlist`` (or the configured ``base_url`` host when the allowlist is
empty), and the stdlib transport refuses every HTTP redirect so a credential
can never be forwarded to a host that was not configured.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from crypto_engine.ai.contracts import (
    AIModel,
    AIProviderSpec,
    AIRequest,
    AIResponse,
    AIUsage,
)
from crypto_engine.ai.errors import AIError, AIStatus

#: Hard cap on a provider response body read (4 GB laptop: bounded memory).
MAX_RESPONSE_BYTES = 200_000

#: Constant, application-owned instruction; never built from untrusted text.
SYSTEM_INSTRUCTION = (
    "You are an advisory explanation assistant for a quantitative research "
    "system. Explain only the supplied structured evidence. Do not invent "
    "metrics, do not recommend trades, and never claim execution authority."
)

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect; ``urllib`` then raises ``HTTPError`` for 3xx."""

    def redirect_request(  # type: ignore[no-untyped-def,override]
        self, req, fp, code, msg, headers, newurl
    ):
        return None


@dataclass(frozen=True)
class TransportResponse:
    """Minimal fakeable HTTP response."""

    status_code: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


TransportCallable = Callable[[str, Mapping[str, str], bytes, float], TransportResponse]


def urllib_transport(
    url: str, headers: Mapping[str, str], body: bytes, timeout_s: float
) -> TransportResponse:
    """Single bounded HTTP POST via stdlib; maps transport faults to AIError."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout_s) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            return TransportResponse(
                status_code=int(response.status),
                body=payload,
                headers=dict(response.headers),
            )
    except urllib.error.HTTPError as exc:
        return TransportResponse(
            status_code=int(exc.code),
            body=exc.read(MAX_RESPONSE_BYTES + 1),
            headers=dict(exc.headers or {}),
        )
    except TimeoutError as exc:
        raise AIError(AIStatus.TIMEOUT, "provider request timed out") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise AIError(AIStatus.TIMEOUT, "provider request timed out") from exc
        raise AIError(AIStatus.PROVIDER_UNAVAILABLE, "provider is unreachable") from exc
    except OSError as exc:
        raise AIError(AIStatus.PROVIDER_UNAVAILABLE, "provider transport failed") from exc


class OpenAICompatibleProvider:
    """Chat-completions adapter for OpenAI-compatible endpoints."""

    def __init__(
        self,
        spec: AIProviderSpec,
        *,
        transport: TransportCallable | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if spec.base_url is None:
            raise ValueError(
                f"provider '{spec.provider_id}' requires base_url for an OpenAI-compatible endpoint"
            )
        self.provider_id = spec.provider_id
        self._spec = spec
        self._transport = transport or urllib_transport
        self._environ = environ

    @property
    def spec(self) -> AIProviderSpec:
        return self._spec

    def endpoint(self) -> str:
        """Validated chat-completions URL (TLS required unless loopback)."""
        base = str(self._spec.base_url).rstrip("/")
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme == "http" and (parsed.hostname or "") not in _LOCAL_HOSTS:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"provider '{self._spec.provider_id}' must use https for remote endpoints",
                provider_id=self._spec.provider_id,
            )
        host = (parsed.hostname or "").lower()
        if host not in self._allowed_hosts():
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"provider '{self._spec.provider_id}' host '{host}' is not in its egress allowlist",
                provider_id=self._spec.provider_id,
            )
        return f"{base}/chat/completions"

    def _allowed_hosts(self) -> frozenset[str]:
        """Configured egress hosts; an empty allowlist means the base URL host only."""
        if self._spec.egress_allowlist:
            return frozenset(host.lower() for host in self._spec.egress_allowlist)
        host = (urllib.parse.urlsplit(str(self._spec.base_url)).hostname or "").lower()
        return frozenset({host}) if host else frozenset()

    def complete(self, request: AIRequest, *, model: AIModel, timeout_s: float) -> AIResponse:
        if model.provider_id != self.provider_id:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                "adapter received a model owned by a different provider",
                provider_id=self.provider_id,
                model_id=model.model_id,
            )
        api_key = self._api_key()
        body = self._serialize(request, model)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "SignalForge/0.1",
        }
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"
        response = self._call_transport(self.endpoint(), headers, body, timeout_s, model)
        return self._parse(response, request, model)

    def _call_transport(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_s: float,
        model: AIModel,
    ) -> TransportResponse:
        try:
            return self._transport(url, headers, body, timeout_s)
        except AIError:
            raise
        except TimeoutError as exc:
            raise AIError(
                AIStatus.TIMEOUT,
                "provider request timed out",
                provider_id=self.provider_id,
                model_id=model.model_id,
            ) from exc
        except OSError as exc:
            raise AIError(
                AIStatus.PROVIDER_UNAVAILABLE,
                "provider transport failed",
                provider_id=self.provider_id,
                model_id=model.model_id,
            ) from exc

    def _api_key(self) -> str | None:
        mechanism = self._spec.auth_mechanism
        if mechanism == "none":
            host = (urllib.parse.urlsplit(str(self._spec.base_url)).hostname or "").lower()
            if host not in _LOCAL_HOSTS:
                raise AIError(
                    AIStatus.INVALID_CONFIGURATION,
                    f"provider '{self.provider_id}' may use auth_mechanism='none' only "
                    "for loopback endpoints",
                    provider_id=self.provider_id,
                )
            if self._spec.secret_ref is not None:
                raise AIError(
                    AIStatus.INVALID_CONFIGURATION,
                    f"provider '{self.provider_id}' sets api_key_env with "
                    "auth_mechanism='none'; remove one of them",
                    provider_id=self.provider_id,
                )
            return None
        if mechanism != "bearer":
            raise AIError(
                AIStatus.UNSUPPORTED_CAPABILITY,
                f"provider '{self.provider_id}' auth_mechanism '{mechanism}' is not "
                "implemented; use 'bearer' or loopback 'none'",
                provider_id=self.provider_id,
            )
        reference = self._spec.secret_ref
        if reference is None:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"provider '{self.provider_id}' requires an api_key_env reference",
                provider_id=self.provider_id,
            )
        value = reference.resolve(self._environ)
        if value is None:
            raise AIError(
                AIStatus.AUTHENTICATION_ERROR,
                f"credential environment variable '{reference.env_var}' is not set",
                provider_id=self.provider_id,
            )
        return value

    def _serialize(self, request: AIRequest, model: AIModel) -> bytes:
        evidence = json.dumps(dict(request.evidence), sort_keys=True, ensure_ascii=True)
        max_tokens = request.max_output_tokens or model.max_output_tokens
        payload: dict[str, Any] = {
            "model": model.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": f"{request.task_text}\n\nEvidence:\n{evidence}"},
            ],
            "max_tokens": int(max_tokens),
            "temperature": 0,
            "stream": False,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")

    def _parse(self, response: TransportResponse, request: AIRequest, model: AIModel) -> AIResponse:
        status = response.status_code
        if status in {401, 403}:
            raise AIError(
                AIStatus.AUTHENTICATION_ERROR,
                f"provider rejected the credential (HTTP {status})",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        if status == 429:
            raise AIError(
                AIStatus.RATE_LIMITED,
                "provider rate limit reached",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        if status == 404:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                "provider endpoint or model was not found",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        if status == 408:
            raise AIError(
                AIStatus.TIMEOUT,
                "provider reported a request timeout",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        if status >= 500:
            raise AIError(
                AIStatus.PROVIDER_UNAVAILABLE,
                f"provider is unavailable (HTTP {status})",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        if 300 <= status < 400:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"provider attempted a redirect (HTTP {status}); redirects are "
                "disabled so credentials never leave the configured host",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        if status >= 400:
            raise AIError(
                AIStatus.INVALID_REQUEST,
                f"provider rejected the request (HTTP {status})",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "provider response exceeded the bounded read limit",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "provider response was not valid JSON",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            ) from exc
        if not isinstance(payload, dict):
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "provider response must be a JSON object",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "provider response has no choices",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "provider response has no advisory text",
                provider_id=self.provider_id,
                model_id=model.model_id,
                provider_status=status,
            )
        return AIResponse(
            request_id=request.request_id,
            task=request.task,
            provider_id=self.provider_id,
            model_id=model.model_id,
            text=text,
            usage=_usage(payload.get("usage")),
            latency_ms=0,
        )


def _usage(raw: object) -> AIUsage:
    if not isinstance(raw, dict):
        return AIUsage()
    prompt = raw.get("prompt_tokens", 0)
    completion = raw.get("completion_tokens", 0)
    try:
        return AIUsage(
            input_tokens=max(0, int(prompt)),
            output_tokens=max(0, int(completion)),
        )
    except (TypeError, ValueError):
        return AIUsage()
