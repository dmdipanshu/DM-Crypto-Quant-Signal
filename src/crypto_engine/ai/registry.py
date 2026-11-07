"""Read-only AI provider/model registry and task routing (Stage 0 / A0).

The registry is metadata. It stores ``api_key_env`` - the *name* of an
environment variable - and never a credential value, token or authenticated
URL. There is no network access, no provider SDK and no model selection by AI:
routing is validated configuration (``task -> provider -> model``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from crypto_engine.ai.contracts import (
    STRUCTURED_OUTPUT_LEVELS,
    AIModel,
    AIProviderSpec,
    AITask,
)
from crypto_engine.ai.errors import AIError, AIStatus

_STRUCTURED_OUTPUT_RANK = {level: rank for rank, level in enumerate(STRUCTURED_OUTPUT_LEVELS)}


@dataclass(frozen=True, slots=True)
class AIModelRef:
    """Configuration reference to one provider/model pair."""

    provider_id: str
    model_id: str

    def __post_init__(self) -> None:
        if not self.provider_id or not self.model_id:
            raise ValueError("AIModelRef requires non-empty provider_id and model_id")


@dataclass(frozen=True, slots=True)
class ResolvedRoute:
    """The single enabled provider/model chosen for one task."""

    provider: AIProviderSpec
    model: AIModel
    route_id: str


class AIRegistry:
    """In-memory metadata registry. Read-only at request time."""

    def __init__(self, *, enabled: bool = False) -> None:
        self._enabled = bool(enabled)
        self._providers: dict[str, AIProviderSpec] = {}
        self._models: dict[tuple[str, str], AIModel] = {}
        self._routes: dict[AITask, tuple[AIModelRef, ...]] = {}

    @property
    def enabled(self) -> bool:
        """Global AI switch; ``False`` means the deterministic core runs alone."""
        return self._enabled

    def add_provider(self, spec: AIProviderSpec) -> None:
        if not isinstance(spec, AIProviderSpec):
            raise AIError(
                AIStatus.INVALID_CONFIGURATION, "provider entry must be an AIProviderSpec"
            )
        if spec.provider_id in self._providers:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"provider '{spec.provider_id}' is already registered",
            )
        self._providers[spec.provider_id] = spec

    def add_model(self, model: AIModel) -> None:
        if not isinstance(model, AIModel):
            raise AIError(AIStatus.INVALID_CONFIGURATION, "model entry must be an AIModel")
        key = (model.provider_id, model.model_id)
        if key in self._models:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"model '{model.model_id}' is already registered for '{model.provider_id}'",
            )
        self._models[key] = model

    def set_route(self, task: AITask | str, refs: Sequence[AIModelRef]) -> None:
        try:
            resolved_task = AITask(task)
        except ValueError as exc:
            raise AIError(AIStatus.INVALID_CONFIGURATION, f"unknown AI task: {task!r}") from exc
        entries = tuple(refs)
        if not entries:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"route for task '{resolved_task.value}' must list at least one provider:model",
            )
        for ref in entries:
            if not isinstance(ref, AIModelRef):
                raise AIError(
                    AIStatus.INVALID_CONFIGURATION,
                    "route entries must be AIModelRef values",
                )
        self._routes[resolved_task] = entries

    def provider(self, provider_id: str) -> AIProviderSpec | None:
        return self._providers.get(provider_id)

    def model(self, provider_id: str, model_id: str) -> AIModel | None:
        return self._models.get((provider_id, model_id))

    def validate(self) -> None:
        """Reject structurally invalid configuration before any call is made."""
        for spec in self._providers.values():
            if spec.capabilities.local:
                raise AIError(
                    AIStatus.INVALID_CONFIGURATION,
                    f"local inference provider '{spec.provider_id}' is not part of Stage 0",
                )
            if spec.enabled and spec.auth_mechanism != "none" and spec.api_key_env is None:
                raise AIError(
                    AIStatus.INVALID_CONFIGURATION,
                    f"enabled provider '{spec.provider_id}' must reference an api_key_env",
                )
        for model in self._models.values():
            spec = self._providers.get(model.provider_id)
            if spec is None:
                raise AIError(
                    AIStatus.INVALID_CONFIGURATION,
                    f"model '{model.model_id}' references unknown provider '{model.provider_id}'",
                )
            _validate_narrowing(spec, model)
        for task, refs in self._routes.items():
            for ref in refs:
                if self.provider(ref.provider_id) is None:
                    raise AIError(
                        AIStatus.INVALID_CONFIGURATION,
                        f"route '{task.value}' references unknown provider '{ref.provider_id}'",
                    )
                if self.model(ref.provider_id, ref.model_id) is None:
                    raise AIError(
                        AIStatus.INVALID_CONFIGURATION,
                        f"route '{task.value}' references unknown model '{ref.model_id}'",
                    )

    def resolve(self, task: AITask | str) -> ResolvedRoute:
        """Resolve a task to the first enabled provider/model in its route."""
        try:
            resolved_task = AITask(task)
        except ValueError as exc:
            raise AIError(AIStatus.NOT_CONFIGURED, f"unknown AI task '{task}'") from exc
        refs = self._routes.get(resolved_task)
        if not refs:
            raise AIError(
                AIStatus.NOT_CONFIGURED,
                f"no AI route configured for task '{resolved_task.value}'",
            )
        for ref in refs:
            spec = self._providers.get(ref.provider_id)
            model = self._models.get((ref.provider_id, ref.model_id))
            if spec is None or model is None:
                continue
            if not spec.enabled or not model.enabled:
                continue
            return ResolvedRoute(
                provider=spec,
                model=model,
                route_id=f"{resolved_task.value}:{ref.provider_id}:{ref.model_id}",
            )
        raise AIError(
            AIStatus.NOT_CONFIGURED,
            f"no enabled provider/model for task '{resolved_task.value}'",
        )

    def snapshot(self) -> dict[str, Any]:
        """Deterministic metadata view; contains secret references, not values."""
        return {
            "enabled": self._enabled,
            "providers": [
                {
                    "provider_id": spec.provider_id,
                    "display_name": spec.display_name,
                    "dialect": spec.dialect,
                    "base_url": spec.base_url,
                    "api_key_env": spec.api_key_env,
                    "auth_mechanism": spec.auth_mechanism,
                    "enabled": spec.enabled,
                }
                for spec in sorted(self._providers.values(), key=lambda item: item.provider_id)
            ],
            "models": [
                {
                    "provider_id": model.provider_id,
                    "model_id": model.model_id,
                    "display_name": model.display_name,
                    "enabled": model.enabled,
                }
                for model in sorted(
                    self._models.values(), key=lambda item: (item.provider_id, item.model_id)
                )
            ],
            "routes": {
                task.value: [
                    {"provider_id": ref.provider_id, "model_id": ref.model_id} for ref in refs
                ]
                for task, refs in sorted(self._routes.items(), key=lambda item: item[0].value)
            },
        }


def _validate_narrowing(spec: AIProviderSpec, model: AIModel) -> None:
    """A model may narrow provider capabilities, never widen them."""
    provider_rank = _STRUCTURED_OUTPUT_RANK[spec.capabilities.structured_output]
    model_rank = _STRUCTURED_OUTPUT_RANK[model.capabilities.structured_output]
    if model_rank > provider_rank:
        raise AIError(
            AIStatus.INVALID_CONFIGURATION,
            f"model '{model.model_id}' widens structured_output beyond provider "
            f"'{spec.provider_id}'",
        )
    if model.capabilities.text_generation and not spec.capabilities.text_generation:
        raise AIError(
            AIStatus.INVALID_CONFIGURATION,
            f"model '{model.model_id}' widens text_generation beyond provider '{spec.provider_id}'",
        )
