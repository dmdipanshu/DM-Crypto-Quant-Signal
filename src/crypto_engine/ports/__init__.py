"""Ports layer — Protocol interface definitions only (DR-2).

This package contains only ``typing.Protocol`` definitions, ``dataclass`` DTOs
and type aliases. No logic, no I/O, no adapter. No third-party imports beyond
typing and the standard library.
"""

from __future__ import annotations

from .clock import Clock
from .ids import IdGenerator
from .market_data import MarketDataSource
from .signals import SignalStore
from .snapshots import SnapshotStore

__all__ = [
    "Clock",
    "IdGenerator",
    "MarketDataSource",
    "SignalStore",
    "SnapshotStore",
]
