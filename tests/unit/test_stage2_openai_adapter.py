"""Stage 2 tests: one OpenAI-compatible adapter behind ``AIProviderPort``.

Offline and deterministic: a fake transport replaces the network. No provider
SDK, no real endpoint, no credentials.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from crypto_engine.ai import (
    AICapabilities,
    AIError,
    AIGateway,
    AIModel,
    AIModelRef,
    AIProviderSpec,
    AIRegistry,
    AIRequest,
    AIResourceLimits,
    AIStatus,
    AITask,
)
from crypto_engine.ai.providers import (
    MAX_RESPONSE_BYTES,
    OpenAICompatibleProvider,
    TransportResponse,
)

FORBIDDEN_IMPORTS = ("openai", "anthropic", "google", "httpx", "requests", "aiohttp")


class FakeTransport:
    def __init__(
        self,
        response: TransportResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_s": timeout_s,
            }
        )
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _spec(**overrides: object) -> AIProviderSpec:
    payload: dict[str, object] = {
        "provider_id": "custom",
        "display_name": "Custom",
        "base_url": "https://ai.example.test/v1",
        "api_key_env": "CUSTOM_API_KEY",
        "auth_mechanism": "bearer",
        "capabilities": AICapabilities(structured_output="json_schema"),
        "enabled": True,
    }
    payload.update(overrides)
    return AIProviderSpec(**payload)  # type: ignore[arg-type]


def _model(**overrides: object) -> AIModel:
    payload: dict[str, object] = {
        "provider_id": "custom",
        "model_id": "small-1",
        "capabilities": AICapabilities(structured_output="json_schema"),
        "max_output_tokens": 512,
        "enabled": True,
    }
    payload.update(overrides)
    return AIModel(**payload)  # type: ignore[arg-type]


def _request(**overrides: object) -> AIRequest:
    payload: dict[str, object] = {
        "task": AITask.SIGNAL_EXPLANATION,
        "task_text": "Explain the deterministic signal record.",
        "evidence": {"signal_id": "abc123", "direction": "NO_TRADE"},
        "output_schema_id": "signal_explanation.v1",
    }
    payload.update(overrides)
    return AIRequest(**payload)  # type: ignore[arg-type]


def _json_response(payload: object, status: int = 200) -> TransportResponse:
    return TransportResponse(
        status_code=status,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _adapter(transport: FakeTransport, **spec_overrides: object) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        _spec(**spec_overrides),
        transport=transport,
        environ={"CUSTOM_API_KEY": "sk-test-adapter-secret"},
    )


def test_success_parses_text_and_usage() -> None:
    transport = FakeTransport(
        _json_response(
            {
                "choices": [{"message": {"content": "Deterministic signal is NO_TRADE."}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }
        )
    )
    response = _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert response.status is AIStatus.OK
    assert response.provider_id == "custom"
    assert response.model_id == "small-1"
    assert response.text == "Deterministic signal is NO_TRADE."
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 8
    assert response.usage.total_tokens == 20
    assert response.advisory_only is True


def test_request_serialization_is_bounded_and_secret_free() -> None:
    transport = FakeTransport(_json_response({"choices": [{"message": {"content": "ok"}}]}))
    _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    call = transport.calls[0]
    body = json.loads(call["body"])
    assert call["url"] == "https://ai.example.test/v1/chat/completions"
    assert body["model"] == "small-1"
    assert body["stream"] is False
    assert body["max_tokens"] == 512
    assert "abc123" in body["messages"][1]["content"]
    assert call["headers"]["Authorization"].startswith("Bearer ")
    assert "sk-test-adapter-secret" not in json.dumps(body)
    assert call["timeout_s"] == 5.0


def test_missing_usage_is_zeroed() -> None:
    transport = FakeTransport(_json_response({"choices": [{"message": {"content": "ok"}}]}))
    response = _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert response.usage.total_tokens == 0


@pytest.mark.parametrize("status", [401, 403])
def test_authentication_failure_is_normalized(status: int) -> None:
    transport = FakeTransport(TransportResponse(status_code=status))
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.AUTHENTICATION_ERROR
    assert info.value.provider_status == status
    assert "sk-test-adapter-secret" not in info.value.message


def test_rate_limit_is_retryable() -> None:
    transport = FakeTransport(TransportResponse(status_code=429))
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.RATE_LIMITED
    assert info.value.retryable is True


@pytest.mark.parametrize("status", [500, 503])
def test_provider_outage_is_normalized(status: int) -> None:
    transport = FakeTransport(TransportResponse(status_code=status))
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.PROVIDER_UNAVAILABLE
    assert info.value.retryable is True


def test_unknown_model_is_invalid_configuration() -> None:
    transport = FakeTransport(TransportResponse(status_code=404))
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_bad_request_is_invalid_request() -> None:
    transport = FakeTransport(TransportResponse(status_code=400))
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.INVALID_REQUEST


def test_provider_timeout_status_is_timeout() -> None:
    transport = FakeTransport(TransportResponse(status_code=408))
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.TIMEOUT


def test_malformed_json_is_invalid_response() -> None:
    transport = FakeTransport(TransportResponse(status_code=200, body=b"{not json"))
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.INVALID_RESPONSE


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"tool_calls": []}}]},
    ],
)
def test_missing_advisory_text_is_invalid_response(payload: dict) -> None:
    transport = FakeTransport(_json_response(payload))
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.INVALID_RESPONSE


def test_oversized_response_is_rejected() -> None:
    transport = FakeTransport(
        TransportResponse(status_code=200, body=b"x" * (MAX_RESPONSE_BYTES + 1))
    )
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.INVALID_RESPONSE


def test_transport_timeout_is_normalized() -> None:
    transport = FakeTransport(error=TimeoutError())
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.TIMEOUT


def test_missing_credential_fails_before_transport() -> None:
    transport = FakeTransport(_json_response({"choices": [{"message": {"content": "ok"}}]}))
    adapter = OpenAICompatibleProvider(_spec(), transport=transport, environ={})
    with pytest.raises(AIError) as info:
        adapter.complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.AUTHENTICATION_ERROR
    assert "CUSTOM_API_KEY" in info.value.message
    assert transport.calls == []


def test_remote_plain_http_is_rejected() -> None:
    adapter = _adapter(FakeTransport(_json_response({})), base_url="http://ai.example.test/v1")
    with pytest.raises(AIError) as info:
        adapter.endpoint()
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_loopback_plain_http_is_allowed() -> None:
    adapter = _adapter(FakeTransport(_json_response({})), base_url="http://127.0.0.1:8080/v1")
    assert adapter.endpoint() == "http://127.0.0.1:8080/v1/chat/completions"


def test_egress_allowlist_rejects_an_unlisted_host() -> None:
    adapter = _adapter(
        FakeTransport(_json_response({})),
        egress_allowlist=("other.example.test",),
    )
    with pytest.raises(AIError) as info:
        adapter.endpoint()
    assert info.value.status is AIStatus.INVALID_CONFIGURATION
    assert "egress" in info.value.message
    assert "ai.example.test" in info.value.message


def test_egress_allowlist_accepts_the_configured_host() -> None:
    adapter = _adapter(
        FakeTransport(_json_response({})),
        egress_allowlist=("ai.example.test",),
    )
    assert adapter.endpoint() == "https://ai.example.test/v1/chat/completions"


def test_empty_egress_allowlist_means_base_url_host_only() -> None:
    adapter = _adapter(FakeTransport(_json_response({})))
    assert adapter._allowed_hosts() == frozenset({"ai.example.test"})


def test_redirect_response_is_rejected_not_followed() -> None:
    transport = FakeTransport(
        TransportResponse(
            status_code=307,
            headers={"Location": "https://evil.example.test/v1/chat/completions"},
        )
    )
    with pytest.raises(AIError) as info:
        _adapter(transport).complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION
    assert "redirect" in info.value.message


def test_stdlib_transport_installs_a_no_redirect_handler() -> None:
    from crypto_engine.ai.providers.openai_compatible import _NoRedirectHandler

    handler = _NoRedirectHandler()
    assert (
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example.test") is None
    )


def test_no_auth_loopback_omits_the_authorization_header() -> None:
    transport = FakeTransport(_json_response({"choices": [{"message": {"content": "ok"}}]}))
    adapter = OpenAICompatibleProvider(
        _spec(
            base_url="http://127.0.0.1:11434/v1",
            api_key_env=None,
            auth_mechanism="none",
        ),
        transport=transport,
        environ={},
    )
    response = adapter.complete(_request(), model=_model(), timeout_s=5.0)
    assert response.status is AIStatus.OK
    assert "Authorization" not in transport.calls[0]["headers"]


def test_no_auth_remote_endpoint_is_rejected() -> None:
    adapter = OpenAICompatibleProvider(
        _spec(api_key_env=None, auth_mechanism="none"),
        transport=FakeTransport(_json_response({})),
        environ={},
    )
    with pytest.raises(AIError) as info:
        adapter.complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION
    assert "loopback" in info.value.message


def test_unimplemented_auth_mechanism_fails_closed() -> None:
    adapter = _adapter(FakeTransport(_json_response({})), auth_mechanism="header")
    with pytest.raises(AIError) as info:
        adapter.complete(_request(), model=_model(), timeout_s=5.0)
    assert info.value.status is AIStatus.UNSUPPORTED_CAPABILITY
    assert "not implemented" in info.value.message


def test_model_owned_by_another_provider_is_rejected() -> None:
    with pytest.raises(AIError) as info:
        _adapter(FakeTransport(_json_response({}))).complete(
            _request(), model=_model(provider_id="other"), timeout_s=5.0
        )
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def _gateway(adapter: OpenAICompatibleProvider, *, max_retries: int = 0) -> AIGateway:
    registry = AIRegistry(enabled=True)
    registry.add_provider(_spec())
    registry.add_model(_model())
    registry.set_route(AITask.SIGNAL_EXPLANATION, [AIModelRef("custom", "small-1")])
    return AIGateway(
        registry,
        {"custom": adapter},
        AIResourceLimits(max_retries=max_retries),
        environ={"CUSTOM_API_KEY": "sk-test-adapter-secret"},
        sleeper=lambda _seconds: None,
    )


def test_gateway_accepts_adapter_behind_the_port() -> None:
    transport = FakeTransport(
        _json_response({"choices": [{"message": {"content": "advisory summary"}}]})
    )
    result = _gateway(_adapter(transport)).complete(_request())
    assert result.status is AIStatus.OK
    assert result.route_id == "signal_explanation:custom:small-1"
    assert result.text == "advisory summary"


def test_provider_failure_does_not_raise_through_the_gateway() -> None:
    transport = FakeTransport(TransportResponse(status_code=503))
    result = _gateway(_adapter(transport), max_retries=1).complete(_request())
    assert result.status is AIStatus.PROVIDER_UNAVAILABLE
    assert result.attempts == 2
    assert len(transport.calls) == 2
    assert result.error is not None


def test_adapter_package_has_no_provider_sdk_imports() -> None:
    package = Path(__file__).resolve().parents[2] / "src" / "crypto_engine" / "ai" / "providers"
    files = sorted(package.glob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules.add(node.module)
        for module in modules:
            for forbidden in FORBIDDEN_IMPORTS:
                assert not (module == forbidden or module.startswith(f"{forbidden}.")), (
                    f"{path.name} imports forbidden SDK {module}"
                )
