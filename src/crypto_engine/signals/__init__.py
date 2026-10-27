"""Canonical signal package (Phase 5 Track A)."""

from crypto_engine.signals.costs import CostModel, edge_clears_cost
from crypto_engine.signals.levels import suggest_levels, validate_levels
from crypto_engine.signals.policy import PolicyInputs, apply_gate_veto, decide
from crypto_engine.signals.schema import (
    AiContextSummary,
    CostEstimate,
    DecisionInfo,
    PredictionInfo,
    SignalLevels,
    SignalRecord,
)

__all__ = [
    "AiContextSummary",
    "CostEstimate",
    "CostModel",
    "DecisionInfo",
    "PolicyInputs",
    "PredictionInfo",
    "SignalLevels",
    "SignalRecord",
    "apply_gate_veto",
    "decide",
    "edge_clears_cost",
    "suggest_levels",
    "validate_levels",
]
