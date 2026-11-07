"""Concrete AI provider adapters (Stage 2; infrastructure layer).

Adapters are injected into :class:`crypto_engine.ai.gateway.AIGateway` through
``AIProviderPort``. Nothing in the provider-neutral core imports this package.
"""

from crypto_engine.ai.providers.openai_compatible import (
    MAX_RESPONSE_BYTES,
    OpenAICompatibleProvider,
    TransportResponse,
    urllib_transport,
)

__all__ = [
    "MAX_RESPONSE_BYTES",
    "OpenAICompatibleProvider",
    "TransportResponse",
    "urllib_transport",
]
