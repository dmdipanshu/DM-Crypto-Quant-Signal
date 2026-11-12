"""Configuration -> AI registry adapter (Stage 1).

This module is the only place that maps validated TOML settings onto the
provider-neutral registry. Contracts, registry and gateway stay independent of
the configuration layer; a future composition root wires them together.

Secrets are never resolved here: ``api_key_env`` is copied as an environment
variable *name* only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from crypto_engine.ai.contracts import (
    AICapabilities,
    AIModel,
    AIProviderPort,
    AIProviderSpec,
    AIResourceLimits,
    AITask,
)
from crypto_engine.ai.errors import AIError, AIStatus
from crypto_engine.ai.gateway import AIGateway
from crypto_engine.ai.registry import AIModelRef, AIRegistry
from crypto_engine.ai.telemetry import TelemetryStore
from crypto_engine.config.settings import (
    AiConfig,
    AiModelConfig,
    AiProviderConfig,
    AiTaskRouteConfig,
    load_settings,
)


def build_provider_spec(entry: AiProviderConfig) -> AIProviderSpec:
    """Map one configured provider onto the registry contract."""
    try:
        return AIProviderSpec(
            provider_id=entry.provider_id,
            display_name=entry.display_name or entry.provider_id,
            dialect=entry.dialect,
            base_url=entry.base_url,
            api_key_env=entry.api_key_env,
            auth_mechanism=entry.auth_mechanism,
            capabilities=AICapabilities(
                structured_output=entry.structured_output,
                text_generation=bool(entry.capabilities.get("text_generation", True)),
                max_context_tokens=entry.max_context_tokens,
                max_output_tokens=entry.max_output_tokens,
                reasoning_metadata=entry.reasoning_metadata,
                usage_reporting=entry.usage_reporting,
                local=entry.local,
            ),
            egress_allowlist=tuple(entry.egress_allowlist),
            enabled=entry.enabled,
            timeout_s=entry.timeout_s,
            max_retries=entry.max_retries,
        )
    except ValueError as exc:
        raise AIError(
            AIStatus.INVALID_CONFIGURATION,
            f"provider '{entry.provider_id}': {exc}",
        ) from exc


def build_model(entry: AiModelConfig) -> AIModel:
    """Map one configured model onto the registry contract."""
    try:
        return AIModel(
            provider_id=entry.provider_id,
            model_id=entry.model_id,
            display_name=entry.display_name,
            capabilities=AICapabilities(
                structured_output=entry.structured_output,
                text_generation=entry.text_generation,
                max_output_tokens=entry.max_output_tokens,
                local=entry.local,
            ),
            context_limit=entry.context_limit,
            max_output_tokens=entry.max_output_tokens,
            enabled=entry.enabled,
        )
    except ValueError as exc:
        raise AIError(
            AIStatus.INVALID_CONFIGURATION,
            f"model '{entry.provider_id}:{entry.model_id}': {exc}",
        ) from exc


def parse_route(task: str, route: AiTaskRouteConfig) -> tuple[AIModelRef, ...]:
    """Parse an ordered ``provider_id:model_id`` fallback list."""
    refs: list[AIModelRef] = []
    seen: set[AIModelRef] = set()
    for raw in route.route:
        provider_id, separator, model_id = str(raw).partition(":")
        if not separator or not provider_id.strip() or not model_id.strip():
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"task '{task}' route entry {raw!r} must be 'provider_id:model_id'",
            )
        ref = AIModelRef(provider_id.strip(), model_id.strip())
        if ref in seen:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"task '{task}' route repeats '{provider_id}:{model_id}'",
            )
        seen.add(ref)
        refs.append(ref)
    if not refs:
        raise AIError(
            AIStatus.INVALID_CONFIGURATION,
            f"task '{task}' route must list at least one 'provider_id:model_id'",
        )
    return tuple(refs)


def build_limits(config: AiConfig) -> AIResourceLimits:
    """Map bounded-workload settings onto validated gateway limits."""
    try:
        return AIResourceLimits(
            max_request_chars=config.max_request_chars,
            max_response_chars=config.max_response_chars,
            max_output_tokens=config.max_output_tokens,
            max_concurrent_requests=config.max_concurrent_requests,
            max_retries=config.max_retries,
            timeout_s=config.timeout_s,
        )
    except ValueError as exc:
        raise AIError(AIStatus.INVALID_CONFIGURATION, f"AI resource limits: {exc}") from exc


def build_registry(config: AiConfig) -> AIRegistry:
    """Build and validate a read-only registry from configuration."""
    registry = AIRegistry(enabled=config.enabled)
    for provider in config.providers:
        registry.add_provider(build_provider_spec(provider))
    for model in config.models:
        registry.add_model(build_model(model))
    for task, route in config.tasks.items():
        registry.set_route(task, parse_route(task, route))
    registry.validate()
    return registry


def build_gateway(
    config: AiConfig,
    ports: Mapping[str, AIProviderPort] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    telemetry_store: TelemetryStore | None = None,
) -> AIGateway:
    """Composition helper: registry + limits + injected adapters."""
    return AIGateway(
        build_registry(config),
        ports,
        build_limits(config),
        environ=environ,
        clock=clock,
        sleeper=sleeper,
        telemetry_store=telemetry_store,
    )


def load_ai_config(toml_path: Path | None = None) -> AiConfig:
    """Load validated settings and return the ``[ai]`` section."""
    return load_settings(toml_path).ai


def task_bounds(config: AiConfig, task: AITask | str) -> tuple[int | None, float | None]:
    """Per-task ``(max_output_tokens, timeout_s)`` bounds from configuration."""
    try:
        resolved = AITask(task)
    except ValueError as exc:
        raise AIError(AIStatus.INVALID_CONFIGURATION, f"unknown AI task '{task}'") from exc
    entry = config.tasks.get(resolved.value)
    if entry is None:
        return None, None
    return entry.max_output_tokens, entry.timeout_s
