"""Composition root for the universal AI gateway (Stage 5).

This is the **only** module allowed to import concrete provider adapters. The
provider-neutral core (``contracts``/``registry``/``gateway``) and the advisory
layers stay adapter-free; the CLI and future API/dashboard call this module to
wire configuration + adapters + limits into one gateway.

Offline mode injects a deterministic mock transport and a clearly-labelled
placeholder credential value (never a real secret, never sent anywhere). Live
mode uses the stdlib transport and the real environment.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from crypto_engine.ai.configuration import build_gateway, build_provider_spec
from crypto_engine.ai.contracts import AIProviderPort
from crypto_engine.ai.errors import AIError, AIStatus
from crypto_engine.ai.gateway import AIGateway
from crypto_engine.ai.providers import OpenAICompatibleProvider, TransportResponse
from crypto_engine.ai.providers.openai_compatible import TransportCallable
from crypto_engine.ai.telemetry import TelemetryStore
from crypto_engine.config.settings import AiConfig

SUPPORTED_DIALECTS = frozenset({"openai_compatible"})

#: Placeholder used only for offline mock mode; not a credential.
OFFLINE_MOCK_API_KEY = "offline-mock-placeholder"

MOCK_RESPONSE_TEXT = (
    "Offline mock: the AI gateway is wired correctly and no network call was made. Advisory only."
)


def mock_transport(
    url: str, headers: Mapping[str, str], body: bytes, timeout_s: float
) -> TransportResponse:
    """Deterministic in-memory transport for offline ``sf ai test``."""
    payload = {
        "choices": [{"message": {"content": MOCK_RESPONSE_TEXT}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    }
    return TransportResponse(
        status_code=200,
        body=json.dumps(payload, sort_keys=True).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def build_ports(
    config: AiConfig,
    *,
    environ: Mapping[str, str] | None = None,
    transport: TransportCallable | None = None,
) -> dict[str, AIProviderPort]:
    """Instantiate one adapter per enabled, supported provider."""
    ports: dict[str, AIProviderPort] = {}
    for entry in config.providers:
        if not entry.enabled:
            continue
        if entry.dialect not in SUPPORTED_DIALECTS:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"provider '{entry.provider_id}' uses unsupported dialect "
                f"'{entry.dialect}' (supported: {sorted(SUPPORTED_DIALECTS)})",
            )
        if entry.base_url is None:
            raise AIError(
                AIStatus.INVALID_CONFIGURATION,
                f"enabled provider '{entry.provider_id}' requires base_url",
            )
        spec = build_provider_spec(entry)
        ports[entry.provider_id] = OpenAICompatibleProvider(
            spec, transport=transport, environ=environ
        )
    return ports


def build_configured_gateway(
    config: AiConfig,
    *,
    environ: Mapping[str, str] | None = None,
    transport: TransportCallable | None = None,
    mock: bool = False,
) -> AIGateway:
    """Wire validated configuration into a ready-to-use gateway.

    ``mock=True`` injects the offline transport and a placeholder credential for
    every referenced ``api_key_env`` so routing can be exercised without
    network access or real secrets.
    """
    effective: Mapping[str, str] | None = environ
    effective_transport = transport
    if mock:
        placeholders = {
            entry.api_key_env: OFFLINE_MOCK_API_KEY
            for entry in config.providers
            if entry.api_key_env
        }
        effective = {**placeholders, **dict(environ or {})}
        effective_transport = transport or mock_transport
    ports = build_ports(config, environ=effective, transport=effective_transport)
    # Telemetry store: best-effort append-only JSONL under data/ai_telemetry/.
    try:
        from crypto_engine.config.settings import load_settings

        cfg = load_settings()
        ai_cfg = getattr(cfg, "ai", None)
        telemetry_path = (
            Path(ai_cfg.telemetry_path)
            if ai_cfg is not None and hasattr(ai_cfg, "telemetry_path")
            else Path("data/ai_telemetry/events.jsonl")
        )
    except Exception:
        telemetry_path = Path("data/ai_telemetry/events.jsonl")
    telemetry_store = TelemetryStore(telemetry_path)
    return build_gateway(
        config,
        ports,
        environ=effective,
        telemetry_store=telemetry_store,
    )


__all__ = [
    "MOCK_RESPONSE_TEXT",
    "OFFLINE_MOCK_API_KEY",
    "SUPPORTED_DIALECTS",
    "build_configured_gateway",
    "build_ports",
    "mock_transport",
]
