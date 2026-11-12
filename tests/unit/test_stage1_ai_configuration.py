"""Stage 1 tests: configuration-driven provider/model/task routing.

Offline and deterministic: no provider SDK, no network, no credentials.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crypto_engine.ai import AIError, AIRequest, AIResponse, AIStatus, AITask
from crypto_engine.ai.configuration import (
    build_gateway,
    build_limits,
    build_registry,
    load_ai_config,
    parse_route,
    task_bounds,
)
from crypto_engine.config.settings import (
    AiConfig,
    AiModelConfig,
    AiProviderConfig,
    AiTaskRouteConfig,
)


def _provider(**overrides: object) -> AiProviderConfig:
    payload: dict[str, object] = {
        "provider_id": "custom",
        "display_name": "Custom",
        "enabled": True,
        "base_url": "https://ai.example.test/v1",
        "api_key_env": "CUSTOM_API_KEY",
        "structured_output": "json_schema",
    }
    payload.update(overrides)
    return AiProviderConfig(**payload)  # type: ignore[arg-type]


def _model(**overrides: object) -> AiModelConfig:
    payload: dict[str, object] = {
        "provider_id": "custom",
        "model_id": "small-1",
        "enabled": True,
        "structured_output": "json_schema",
    }
    payload.update(overrides)
    return AiModelConfig(**payload)  # type: ignore[arg-type]


def _config(**overrides: object) -> AiConfig:
    payload: dict[str, object] = {
        "enabled": True,
        "providers": [_provider()],
        "models": [_model()],
        "tasks": {"assistant": AiTaskRouteConfig(route=["custom:small-1"])},
    }
    payload.update(overrides)
    return AiConfig(**payload)  # type: ignore[arg-type]


def test_default_configuration_is_disabled_and_bounded() -> None:
    config = AiConfig()
    registry = build_registry(config)
    limits = build_limits(config)
    assert registry.enabled is False
    assert registry.snapshot()["providers"] == []
    assert registry.snapshot()["routes"] == {}
    assert limits.max_concurrent_requests == 1
    assert limits.max_retries == 2
    assert limits.timeout_s == 30.0


def test_valid_configuration_builds_registry_and_route() -> None:
    registry = build_registry(_config())
    resolved = registry.resolve(AITask.ASSISTANT)
    assert resolved.provider.provider_id == "custom"
    assert resolved.model.model_id == "small-1"
    assert resolved.route_id == "assistant:custom:small-1"
    snapshot = registry.snapshot()
    assert snapshot["providers"][0]["api_key_env"] == "CUSTOM_API_KEY"
    assert snapshot["providers"][0]["base_url"] == "https://ai.example.test/v1"
    assert snapshot["routes"]["assistant"] == [{"provider_id": "custom", "model_id": "small-1"}]


def test_registry_rejects_secret_shaped_api_key_env() -> None:
    config = _config(providers=[_provider(api_key_env="sk-live-secret")])
    with pytest.raises(AIError) as info:
        build_registry(config)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION
    assert "sk-live-secret" not in info.value.message


def test_custom_endpoint_is_preserved() -> None:
    registry = build_registry(
        _config(providers=[_provider(base_url="https://localhost.example:8443/v1")])
    )
    assert registry.provider("custom").base_url == "https://localhost.example:8443/v1"


def test_endpoint_with_embedded_credentials_is_rejected() -> None:
    config = _config(providers=[_provider(base_url="https://user:pass@ai.example.test/v1")])
    with pytest.raises(AIError) as info:
        build_registry(config)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


@pytest.mark.parametrize(
    "route",
    [
        ["custom"],  # missing separator
        ["custom:"],  # missing model
        [":small-1"],  # missing provider
        [],  # empty route
    ],
)
def test_invalid_route_syntax_is_rejected(route: list[str]) -> None:
    with pytest.raises(AIError) as info:
        parse_route("assistant", AiTaskRouteConfig(route=route))
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_duplicate_route_entry_is_rejected() -> None:
    with pytest.raises(AIError, match="repeats"):
        parse_route(
            "assistant",
            AiTaskRouteConfig(route=["custom:small-1", "custom:small-1"]),
        )


def test_unknown_task_is_rejected() -> None:
    config = _config(tasks={"not_a_task": AiTaskRouteConfig(route=["custom:small-1"])})
    with pytest.raises(AIError) as info:
        build_registry(config)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_missing_model_is_rejected() -> None:
    config = _config(tasks={"assistant": AiTaskRouteConfig(route=["custom:missing-1"])})
    with pytest.raises(AIError) as info:
        build_registry(config)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_missing_provider_is_rejected() -> None:
    config = _config(models=[], tasks={"assistant": AiTaskRouteConfig(route=["ghost:g1"])})
    with pytest.raises(AIError) as info:
        build_registry(config)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_enabled_provider_requires_a_credential_reference() -> None:
    config = _config(providers=[_provider(api_key_env=None)])
    with pytest.raises(AIError, match="api_key_env"):
        build_registry(config)


def test_disabled_provider_or_model_is_not_configured() -> None:
    provider_disabled = _config(providers=[_provider(enabled=False)])
    with pytest.raises(AIError) as info:
        build_registry(provider_disabled).resolve(AITask.ASSISTANT)
    assert info.value.status is AIStatus.NOT_CONFIGURED

    model_disabled = _config(models=[_model(enabled=False)])
    with pytest.raises(AIError) as info:
        build_registry(model_disabled).resolve(AITask.ASSISTANT)
    assert info.value.status is AIStatus.NOT_CONFIGURED


def test_capability_widening_is_rejected() -> None:
    config = _config(
        providers=[_provider(structured_output="none")],
        models=[_model(structured_output="json_schema")],
    )
    with pytest.raises(AIError) as info:
        build_registry(config)
    assert info.value.status is AIStatus.INVALID_CONFIGURATION


def test_local_inference_provider_is_rejected() -> None:
    config = _config(providers=[_provider(local=True)])
    with pytest.raises(AIError, match="local inference"):
        build_registry(config)


def test_invalid_concurrency_is_rejected_by_configuration_validation() -> None:
    with pytest.raises(ValidationError):
        AiConfig(max_concurrent_requests=2)


def test_task_bounds_are_read_from_configuration() -> None:
    config = _config(
        tasks={
            "assistant": AiTaskRouteConfig(
                route=["custom:small-1"], max_output_tokens=256, timeout_s=15
            )
        }
    )
    assert task_bounds(config, AITask.ASSISTANT) == (256, 15.0)
    assert task_bounds(config, AITask.RESEARCH_SUMMARY) == (None, None)


class _Port:
    provider_id = "custom"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: AIRequest, *, model, timeout_s):
        self.calls += 1
        return AIResponse(
            request_id=request.request_id,
            task=request.task,
            provider_id="custom",
            model_id=model.model_id,
            text="advisory",
        )


def test_gateway_built_from_configuration_routes_and_normalizes() -> None:
    port = _Port()
    gateway = build_gateway(
        _config(),
        {"custom": port},
        environ={"CUSTOM_API_KEY": "sk-test-value"},
        sleeper=lambda _seconds: None,
    )
    result = gateway.complete(AIRequest(task=AITask.ASSISTANT, task_text="Summarize the context."))
    assert result.status is AIStatus.OK
    assert result.route_id == "assistant:custom:small-1"
    assert port.calls == 1


def test_gateway_built_from_disabled_configuration_makes_no_call() -> None:
    port = _Port()
    gateway = build_gateway(_config(enabled=False), {"custom": port})
    result = gateway.complete(AIRequest(task=AITask.ASSISTANT, task_text="Summarize the context."))
    assert result.status is AIStatus.DISABLED
    assert port.calls == 0


def test_repository_default_config_is_enabled_and_parses() -> None:
    config = load_ai_config()
    assert config.enabled is True
    assert config.max_concurrent_requests == 1
    assert len(config.providers) == 1
    provider_ids = {p.provider_id for p in config.providers}
    assert provider_ids == {"meta_muse"}
    meta_muse = next(p for p in config.providers if p.provider_id == "meta_muse")
    assert meta_muse.dialect == "openai_compatible"
    assert meta_muse.base_url == "https://api.meta.ai/v1"
    assert meta_muse.api_key_env == "META_MUSE_API_KEY"
    assert meta_muse.auth_mechanism == "bearer"
    assert meta_muse.enabled is True
    assert len(config.models) == 1
    model_keys = {(m.provider_id, m.model_id) for m in config.models}
    assert model_keys == {("meta_muse", "muse-spark-1.3")}
    assert "signal_explanation" in config.tasks
    assert len(config.tasks["signal_explanation"].route) == 1
    route_keys = {tuple(r.split(":")) for r in config.tasks["signal_explanation"].route}
    assert route_keys == {("meta_muse", "muse-spark-1.3")}
