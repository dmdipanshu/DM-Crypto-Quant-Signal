"""Stage 0 / A0 AI gateway tests: routing, failures, bounds, single flight.

Fake adapters only; no network, no credentials, deterministic clocks/sleeps.
"""

from __future__ import annotations

import json

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
    AIResponse,
    AIStatus,
    AITask,
)


class RecordingPort:
    """Deterministic fake adapter; records calls and replays one outcome."""

    def __init__(self, provider_id: str = "custom", outcome: object = None) -> None:
        self.provider_id = provider_id
        self.outcome = outcome
        self.calls: list[tuple[AIRequest, AIModel, float]] = []

    def complete(self, request: AIRequest, *, model: AIModel, timeout_s: float) -> AIResponse:
        self.calls.append((request, model, timeout_s))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if callable(self.outcome):
            return self.outcome(request, model, timeout_s)
        if self.outcome is not None:
            return self.outcome  # type: ignore[return-value]
        return AIResponse(
            request_id=request.request_id,
            task=request.task,
            provider_id=self.provider_id,
            model_id=model.model_id,
            text="ok",
        )


def _registry(
    *,
    enabled: bool = True,
    provider_enabled: bool = True,
    model_enabled: bool = True,
    capabilities: AICapabilities | None = None,
    with_route: bool = True,
) -> AIRegistry:
    caps = capabilities or AICapabilities()
    registry = AIRegistry(enabled=enabled)
    registry.add_provider(
        AIProviderSpec(
            provider_id="custom",
            display_name="Custom",
            base_url="https://ai.example.test/v1",
            api_key_env="CUSTOM_API_KEY",
            capabilities=caps,
            enabled=provider_enabled,
        )
    )
    registry.add_model(
        AIModel(
            provider_id="custom",
            model_id="small-1",
            capabilities=caps,
            enabled=model_enabled,
        )
    )
    if with_route:
        registry.set_route(AITask.ASSISTANT, [AIModelRef("custom", "small-1")])
        registry.set_route(AITask.SIGNAL_EXPLANATION, [AIModelRef("custom", "small-1")])
    return registry


def _gateway(
    registry: AIRegistry,
    port: object,
    *,
    limits: AIResourceLimits | None = None,
    environ: dict[str, str] | None = None,
    sleeper: object = None,
) -> AIGateway:
    return AIGateway(
        registry,
        {getattr(port, "provider_id", "custom"): port},
        limits or AIResourceLimits(max_retries=0),
        environ={"CUSTOM_API_KEY": "sk-test"} if environ is None else environ,
        sleeper=sleeper or (lambda _seconds: None),
    )


def _request(**overrides: object) -> AIRequest:
    payload: dict[str, object] = {
        "task": AITask.ASSISTANT,
        "task_text": "Summarize the deterministic context.",
    }
    payload.update(overrides)
    return AIRequest(**payload)  # type: ignore[arg-type]


def test_disabled_ai_returns_disabled_without_calling_adapter() -> None:
    port = RecordingPort()
    result = _gateway(_registry(enabled=False), port).complete(_request())
    assert result.status is AIStatus.DISABLED
    assert result.error is not None
    assert result.error.code is AIStatus.DISABLED
    assert port.calls == []


def test_missing_route_is_not_configured() -> None:
    port = RecordingPort()
    result = _gateway(_registry(with_route=False), port).complete(_request())
    assert result.status is AIStatus.NOT_CONFIGURED
    assert port.calls == []


@pytest.mark.parametrize("provider_enabled,model_enabled", [(False, True), (True, False)])
def test_disabled_provider_or_model_is_not_configured(
    provider_enabled: bool, model_enabled: bool
) -> None:
    port = RecordingPort()
    registry = _registry(provider_enabled=provider_enabled, model_enabled=model_enabled)
    result = _gateway(registry, port).complete(_request())
    assert result.status is AIStatus.NOT_CONFIGURED
    assert port.calls == []


def test_route_to_unknown_provider_is_not_configured() -> None:
    registry = AIRegistry(enabled=True)
    registry.set_route(AITask.ASSISTANT, [AIModelRef("ghost", "ghost-1")])
    result = _gateway(registry, RecordingPort()).complete(_request())
    assert result.status is AIStatus.NOT_CONFIGURED


def test_registry_validation_rejects_unknown_model() -> None:
    registry = _registry()
    registry.add_model(AIModel(provider_id="ghost", model_id="ghost-1", enabled=True))
    with pytest.raises(AIError) as info:
        registry.validate()
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_registry_validation_rejects_capability_widening() -> None:
    registry = AIRegistry(enabled=True)
    registry.add_provider(
        AIProviderSpec(provider_id="custom", display_name="Custom", auth_mechanism="none")
    )
    registry.add_model(
        AIModel(
            provider_id="custom",
            model_id="wide-1",
            capabilities=AICapabilities(structured_output="json_schema"),
        )
    )
    with pytest.raises(AIError) as info:
        registry.validate()
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_registry_validation_rejects_local_inference() -> None:
    registry = AIRegistry(enabled=True)
    registry.add_provider(
        AIProviderSpec(
            provider_id="custom",
            display_name="Custom",
            auth_mechanism="none",
            capabilities=AICapabilities(local=True),
        )
    )
    with pytest.raises(AIError) as info:
        registry.validate()
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_missing_adapter_is_not_configured() -> None:
    gateway = AIGateway(
        _registry(),
        {},
        AIResourceLimits(max_retries=0),
        environ={"CUSTOM_API_KEY": "sk-test"},
        sleeper=lambda _seconds: None,
    )
    assert gateway.complete(_request()).status is AIStatus.NOT_CONFIGURED


def test_missing_credential_reference_fails_closed() -> None:
    port = RecordingPort()
    result = _gateway(_registry(), port, environ={}).complete(_request())
    assert result.status is AIStatus.AUTHENTICATION_ERROR
    assert result.error is not None
    assert "CUSTOM_API_KEY" in result.error.message
    assert port.calls == []


def test_success_normalizes_response() -> None:
    port = RecordingPort()
    result = _gateway(_registry(), port).complete(_request())
    assert result.status is AIStatus.OK
    assert result.ok is True
    assert result.provider_id == "custom"
    assert result.model_id == "small-1"
    assert result.route_id == "assistant:custom:small-1"
    assert result.text == "ok"
    assert result.advisory_only is True
    assert result.attempts == 1
    assert len(port.calls) == 1


def test_task_routing_selects_provider_per_task() -> None:
    registry = AIRegistry(enabled=True)
    for provider_id in ("alpha", "beta"):
        registry.add_provider(
            AIProviderSpec(
                provider_id=provider_id,
                display_name=provider_id,
                auth_mechanism="none",
                enabled=True,
            )
        )
        registry.add_model(
            AIModel(provider_id=provider_id, model_id=f"{provider_id}-1", enabled=True)
        )
    registry.set_route(AITask.ASSISTANT, [AIModelRef("alpha", "alpha-1")])
    registry.set_route(AITask.RESEARCH_SUMMARY, [AIModelRef("beta", "beta-1")])
    alpha, beta = RecordingPort("alpha"), RecordingPort("beta")
    gateway = AIGateway(
        registry,
        {"alpha": alpha, "beta": beta},
        AIResourceLimits(max_retries=0),
        sleeper=lambda _seconds: None,
    )
    assert gateway.complete(_request(task=AITask.ASSISTANT)).provider_id == "alpha"
    summary = gateway.complete(_request(task=AITask.RESEARCH_SUMMARY))
    assert summary.provider_id == "beta"
    assert summary.route_id == "research_summary:beta:beta-1"


def test_unsupported_capability_is_refused_before_call() -> None:
    port = RecordingPort()
    caps = AICapabilities(text_generation=False)
    result = _gateway(_registry(capabilities=caps), port).complete(_request())
    assert result.status is AIStatus.UNSUPPORTED_CAPABILITY
    assert port.calls == []


def test_structured_schema_requires_declared_capability() -> None:
    port = RecordingPort()
    request = _request(output_schema_id="signal_explanation.v1")
    assert _gateway(_registry(), port).complete(request).status is (AIStatus.UNSUPPORTED_CAPABILITY)
    caps = AICapabilities(structured_output="json_schema")
    result = _gateway(_registry(capabilities=caps), RecordingPort()).complete(request)
    assert result.status is AIStatus.OK


def test_timeout_is_normalized_and_retried_with_bound() -> None:
    port = RecordingPort(outcome=TimeoutError())
    sleeps: list[float] = []
    gateway = AIGateway(
        _registry(),
        {"custom": port},
        AIResourceLimits(max_retries=2),
        environ={"CUSTOM_API_KEY": "sk-test"},
        sleeper=sleeps.append,
    )
    result = gateway.complete(_request())
    assert result.status is AIStatus.TIMEOUT
    assert result.attempts == 3
    assert len(port.calls) == 3
    assert len(sleeps) == 2
    assert all(0 < delay <= 2.0 for delay in sleeps)


def test_auth_failure_is_not_retried() -> None:
    port = RecordingPort(outcome=AIError(AIStatus.AUTHENTICATION_ERROR, "401 from provider"))
    result = _gateway(_registry(), port).complete(_request())
    assert result.status is AIStatus.AUTHENTICATION_ERROR
    assert result.attempts == 1
    assert len(port.calls) == 1


def test_rate_limit_is_retried_then_reported() -> None:
    port = RecordingPort(outcome=AIError(AIStatus.RATE_LIMITED, "429 from provider"))
    gateway = AIGateway(
        _registry(),
        {"custom": port},
        AIResourceLimits(max_retries=1),
        environ={"CUSTOM_API_KEY": "sk-test"},
        sleeper=lambda _seconds: None,
    )
    result = gateway.complete(_request())
    assert result.status is AIStatus.RATE_LIMITED
    assert result.attempts == 2
    assert len(port.calls) == 2


def test_oversized_response_is_invalid() -> None:
    def oversized(request: AIRequest, model: AIModel, timeout_s: float) -> AIResponse:
        return AIResponse(
            request_id=request.request_id,
            task=request.task,
            provider_id="custom",
            model_id=model.model_id,
            text="x" * 5_000,
        )

    port = RecordingPort(outcome=oversized)
    limits = AIResourceLimits(max_response_chars=4_000, max_retries=0)
    result = _gateway(_registry(), port, limits=limits).complete(_request())
    assert result.status is AIStatus.INVALID_RESPONSE
    assert result.attempts == 1


def test_adapter_cannot_substitute_model() -> None:
    def substitute(request: AIRequest, model: AIModel, timeout_s: float) -> AIResponse:
        return AIResponse(
            request_id=request.request_id,
            task=request.task,
            provider_id="custom",
            model_id="other-model",
            text="ok",
        )

    result = _gateway(_registry(), RecordingPort(outcome=substitute)).complete(_request())
    assert result.status is AIStatus.INVALID_RESPONSE


def test_adapter_returning_wrong_type_is_internal_error() -> None:
    port = RecordingPort(outcome="not a response")
    result = _gateway(_registry(), port).complete(_request())
    assert result.status is AIStatus.INTERNAL_ERROR


def test_error_messages_are_redacted_end_to_end() -> None:
    port = RecordingPort(
        outcome=AIError(
            AIStatus.PROVIDER_UNAVAILABLE,
            "upstream failed api_key=sk-live-supersecret",
        )
    )
    result = _gateway(_registry(), port).complete(_request())
    assert result.status is AIStatus.PROVIDER_UNAVAILABLE
    assert result.error is not None
    assert "sk-live-supersecret" not in result.error.message
    assert "<redacted>" in result.error.message


def test_single_flight_concurrency_is_enforced() -> None:
    holder: dict[str, AIGateway] = {}
    nested: list[AIResponse] = []

    class ReentrantPort:
        provider_id = "custom"

        def complete(self, request: AIRequest, *, model: AIModel, timeout_s: float) -> AIResponse:
            nested.append(holder["gateway"].complete(_request()))
            return AIResponse(
                request_id=request.request_id,
                task=request.task,
                provider_id="custom",
                model_id=model.model_id,
                text="outer",
            )

    gateway = AIGateway(
        _registry(),
        {"custom": ReentrantPort()},
        AIResourceLimits(max_retries=0),
        environ={"CUSTOM_API_KEY": "sk-test"},
        sleeper=lambda _seconds: None,
    )
    holder["gateway"] = gateway
    result = gateway.complete(_request())
    assert result.status is AIStatus.OK
    assert len(nested) == 1
    assert nested[0].status is AIStatus.RATE_LIMITED
    assert nested[0].error is not None
    assert "max_concurrent_requests=1" in nested[0].error.message


def test_local_inference_provider_is_refused_at_request_time() -> None:
    registry = AIRegistry(enabled=True)
    registry.add_provider(
        AIProviderSpec(
            provider_id="custom",
            display_name="Custom",
            auth_mechanism="none",
            capabilities=AICapabilities(local=True),
            enabled=True,
        )
    )
    registry.add_model(AIModel(provider_id="custom", model_id="small-1", enabled=True))
    registry.set_route(AITask.ASSISTANT, [AIModelRef("custom", "small-1")])
    port = RecordingPort()
    result = _gateway(registry, port).complete(_request())
    assert result.status is AIStatus.UNSUPPORTED_CAPABILITY
    assert port.calls == []


def test_oversized_request_is_rejected_before_call() -> None:
    port = RecordingPort()
    limits = AIResourceLimits(max_request_chars=100, max_retries=0)
    result = _gateway(_registry(), port, limits=limits).complete(
        AIRequest(task=AITask.ASSISTANT, task_text="x" * 101)
    )
    assert result.status is AIStatus.INVALID_REQUEST
    assert port.calls == []


def test_request_timeout_cannot_exceed_configured_ceiling() -> None:
    port = RecordingPort()
    gateway = _gateway(_registry(), port, limits=AIResourceLimits(timeout_s=5.0, max_retries=0))
    assert gateway.complete(_request(timeout_s=60.0)).status is AIStatus.OK
    assert port.calls[0][2] == 5.0
    assert gateway.complete(_request(timeout_s=1.0)).status is AIStatus.OK
    assert port.calls[1][2] == 1.0


def test_gateway_response_is_advisory_only() -> None:
    result = _gateway(_registry(), RecordingPort()).complete(_request())
    assert result.advisory_only is True
    assert result.error is None
    for name in (
        "signal_direction",
        "risk_limit",
        "position_size",
        "approval_state",
        "execution_authorized",
        "circuit_breaker",
        "ledger",
    ):
        assert not hasattr(result, name)


def test_registry_snapshot_exposes_references_not_secrets() -> None:
    payload = json.dumps(_registry().snapshot(), sort_keys=True)
    assert "CUSTOM_API_KEY" in payload
    assert "sk-test" not in payload
    assert _registry().snapshot()["providers"][0]["api_key_env"] == "CUSTOM_API_KEY"
