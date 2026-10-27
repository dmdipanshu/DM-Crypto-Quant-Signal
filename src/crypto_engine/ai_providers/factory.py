"""Provider factory: ``provider_id`` -> adapter class (Phase 4 Track A)."""

from __future__ import annotations

from typing import Any

from crypto_engine.ai_providers.anthropic_adapter import AnthropicProvider
from crypto_engine.ai_providers.base import AiProvider, TransportCallable
from crypto_engine.ai_providers.compatible_adapter import CompatibleProvider
from crypto_engine.ai_providers.deepseek_adapter import DeepSeekProvider
from crypto_engine.ai_providers.gemini_adapter import GeminiProvider
from crypto_engine.ai_providers.groq_adapter import GroqProvider
from crypto_engine.ai_providers.ollama_adapter import OllamaProvider
from crypto_engine.ai_providers.openai_adapter import OpenAiProvider
from crypto_engine.ai_providers.openrouter_adapter import OpenRouterProvider

_REGISTRY: dict[str, type[AiProvider]] = {
    "openai": OpenAiProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "deepseek": DeepSeekProvider,
    "openrouter": OpenRouterProvider,
    "groq": GroqProvider,
    "ollama": OllamaProvider,
    "compatible": CompatibleProvider,
}

SUPPORTED_IDS = sorted(_REGISTRY)


def create_provider(
    provider_id: str,
    model_id: str,
    config: Any | None = None,
    transport: TransportCallable | None = None,
    **overrides: Any,
) -> AiProvider:
    """Build a provider adapter.

    Args:
        provider_id: One of :data:`SUPPORTED_IDS`.
        model_id: Provider-side model name.
        config: Optional ``AiProviderConfig`` (or mapping) for base_url,
            api_key_env, max_response_chars, enabled, extra headers.
        transport: Optional injected fake transport (tests) / custom sender.
        overrides: Keyword overrides winning over ``config``.

    Raises:
        ValueError: For unknown ``provider_id`` (lists supported ids).
    """
    cls = _REGISTRY.get(provider_id)
    if cls is None:
        raise ValueError(f"unknown provider_id {provider_id!r}; supported: {SUPPORTED_IDS}.")
    kwargs: dict[str, Any] = {}
    if config is not None:
        if isinstance(config, dict):
            cfg = dict(config)
        else:
            cfg = {
                "base_url": getattr(config, "base_url", None),
                "api_key_env": getattr(config, "api_key_env", None),
                "max_response_chars": getattr(config, "max_response_chars", None),
                "enabled": getattr(config, "enabled", None),
            }
        for key in ("base_url", "api_key_env", "max_response_chars", "enabled"):
            value = cfg.get(key)
            if value is not None:
                kwargs[key] = value
        extra = cfg.get("extra_headers") if isinstance(cfg, dict) else None
        if extra:
            kwargs["extra_headers"] = dict(extra)
    kwargs.update(overrides)
    if transport is not None:
        kwargs["transport"] = transport
    return cls(model_id, **kwargs)


def enabled_providers(settings: Any) -> list[str]:
    """Return enabled provider ids from ``settings.ai`` (default: none)."""
    ai = getattr(settings, "ai", None)
    providers = getattr(ai, "providers", None) if ai is not None else None
    if not providers:
        return []
    return [p.provider_id for p in providers if getattr(p, "enabled", False)]


__all__ = ["SUPPORTED_IDS", "create_provider", "enabled_providers"]
