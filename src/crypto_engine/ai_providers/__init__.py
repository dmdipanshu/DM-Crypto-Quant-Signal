"""Phase 4 Track A: provider-independent AI interface + optional adapters."""

from __future__ import annotations

from crypto_engine.ai_providers.base import AiProvider, TransportCallable, TransportResponse
from crypto_engine.ai_providers.budget import BudgetTracker, run_with_fallback
from crypto_engine.ai_providers.factory import SUPPORTED_IDS, create_provider, enabled_providers
from crypto_engine.ai_providers.types import AiRequest, AiResponse

__all__ = [
    "SUPPORTED_IDS",
    "AiProvider",
    "AiRequest",
    "AiResponse",
    "BudgetTracker",
    "TransportCallable",
    "TransportResponse",
    "create_provider",
    "enabled_providers",
    "run_with_fallback",
]
