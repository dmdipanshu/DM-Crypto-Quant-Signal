"""SignalForge analyst-agent layer (Phase 4 Track B)."""

from __future__ import annotations

from crypto_engine.agents.analysts import (
    ANALYST_IDS,
    BaseAnalyst,
    BearAnalyst,
    BullAnalyst,
    ExplainerAnalyst,
    MacroAnalyst,
    NewsAnalyst,
    RegimeAnalyst,
    TechnicalAnalyst,
    get_analyst,
)
from crypto_engine.agents.orchestrator import run_analysis
from crypto_engine.agents.schemas import (
    ANALYST_IDS as SCHEMA_ANALYST_IDS,
)
from crypto_engine.agents.schemas import (
    AnalystOutput,
    ContextScore,
    EvidenceItem,
    JudgeOutput,
)

__all__ = [
    "ANALYST_IDS",
    "SCHEMA_ANALYST_IDS",
    "AnalystOutput",
    "BaseAnalyst",
    "BearAnalyst",
    "BullAnalyst",
    "ContextScore",
    "EvidenceItem",
    "ExplainerAnalyst",
    "JudgeOutput",
    "MacroAnalyst",
    "NewsAnalyst",
    "RegimeAnalyst",
    "TechnicalAnalyst",
    "get_analyst",
    "run_analysis",
]
