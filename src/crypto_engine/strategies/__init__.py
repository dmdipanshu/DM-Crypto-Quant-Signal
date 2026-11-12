"""Strategies and signal composition (Stage 6).

Strategy metadata, versioning, parameter definitions, and deterministic signal
composition. Signal composition never invokes risk gates — risk is applied
later, independently, by the risk layer.
"""

from __future__ import annotations

from .composition import compose_signal
from .metadata import StrategyMetadata, StrategyParameter, StrategyVersion

__all__ = ["StrategyMetadata", "StrategyParameter", "StrategyVersion", "compose_signal"]
