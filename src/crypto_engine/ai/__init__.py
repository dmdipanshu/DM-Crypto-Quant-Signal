"""Provider-neutral AI foundation (Stage 0 / A0).

Boundary: contracts, a gateway skeleton and a metadata registry. No provider
SDK, no HTTP client, no database driver and no trading logic live here;
concrete adapters are infrastructure injected by the composition root.
"""

from crypto_engine.ai.contracts import (
    MAX_CONCURRENT_AI_REQUESTS,
    MAX_EVIDENCE_KEYS,
    MAX_EVIDENCE_VALUE_CHARS,
    MAX_RETRY_CEILING,
    WORKLOAD_BUDGET_BYTES,
    AICapabilities,
    AIErrorInfo,
    AIModel,
    AIProviderPort,
    AIProviderSpec,
    AIRequest,
    AIResourceLimits,
    AIResponse,
    AITask,
    AIUsage,
    SecretRef,
)
from crypto_engine.ai.errors import (
    NON_RETRYABLE_STATUSES,
    RETRYABLE_STATUSES,
    AIError,
    AIStatus,
    classify_exception,
    redact_secrets,
)
from crypto_engine.ai.gateway import AIGateway
from crypto_engine.ai.registry import AIModelRef, AIRegistry, ResolvedRoute

__all__ = [
    "MAX_CONCURRENT_AI_REQUESTS",
    "MAX_EVIDENCE_KEYS",
    "MAX_EVIDENCE_VALUE_CHARS",
    "MAX_RETRY_CEILING",
    "NON_RETRYABLE_STATUSES",
    "RETRYABLE_STATUSES",
    "WORKLOAD_BUDGET_BYTES",
    "AICapabilities",
    "AIError",
    "AIErrorInfo",
    "AIGateway",
    "AIModel",
    "AIModelRef",
    "AIProviderPort",
    "AIProviderSpec",
    "AIRegistry",
    "AIRequest",
    "AIResourceLimits",
    "AIResponse",
    "AIStatus",
    "AITask",
    "AIUsage",
    "ResolvedRoute",
    "SecretRef",
    "classify_exception",
    "redact_secrets",
]
