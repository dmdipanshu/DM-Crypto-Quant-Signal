"""Advisory AI layers (Stage 3 signal explanation, Stage 4 research summary).

Advisory modules sit *downstream* of deterministic outputs. They consume a
canonical record or a deterministic research report, build a bounded evidence
snapshot, call the provider-neutral gateway, and return frozen advisory data.
They never write signal direction, scores, risk decisions, position sizes,
approvals, execution state, promotion flags or ledgers.

The Stage 0 import-boundary test only inspects top-level ``ai/*.py`` modules;
these subpackages are allowed to import deterministic domain schemas because
the dependency direction is ``deterministic -> advisory`` (never the reverse).
"""

from crypto_engine.ai.advisory.research import (
    ALLOWED_METRICS,
    CALCULATION,
    FACT,
    INTERPRETATION,
    NOT_AVAILABLE,
    UNCERTAINTY,
    ResearchEvidence,
    ResearchItem,
    ResearchSummary,
    attach_research_summary,
    build_research_evidence,
    summarize_research,
)
from crypto_engine.ai.advisory.signal import (
    AIExplanation,
    apply_ai_context,
    build_signal_evidence,
    explain_signal,
)

__all__ = [
    "ALLOWED_METRICS",
    "CALCULATION",
    "FACT",
    "INTERPRETATION",
    "NOT_AVAILABLE",
    "UNCERTAINTY",
    "AIExplanation",
    "ResearchEvidence",
    "ResearchItem",
    "ResearchSummary",
    "apply_ai_context",
    "attach_research_summary",
    "build_research_evidence",
    "build_signal_evidence",
    "explain_signal",
    "summarize_research",
]
