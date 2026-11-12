"""Quality engine — pure stdlib validation functions (Stage 4).

Provides read-only quality checks over stored state. No I/O side effects,
no network, no third-party imports. These functions are composable building
blocks used by validation, research, and backtest layers.
"""

from __future__ import annotations

from .audit import audit
from .schema import validate_schema
from .staleness import detect_staleness

__all__ = ["audit", "detect_staleness", "validate_schema"]
