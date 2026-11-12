"""Domain layer — pure value objects and invariants (stdlib only).

DR-1: This package imports only the Python standard library. No pandas,
numpy, pydantic, ccxt, sqlite3, fastapi, typer or other project module.
"""

from __future__ import annotations

from .candle import Candle
from .direction import Direction
from .reason_codes import ReasonCode
from .timeframe import Timeframe

__all__ = ["Candle", "Direction", "ReasonCode", "Timeframe"]
