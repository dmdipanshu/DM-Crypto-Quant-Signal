"""Clock port (DR-2).

Protocol for wall-clock time. Injected so tests can use a fixed clock;
production uses the system clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Protocol for UTC time."""

    def now_utc(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


__all__ = ["Clock"]
