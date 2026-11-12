"""Timeframe value object.

Represents a candlestick timeframe (e.g. "1h", "4h", "1d"). Pure stdlib:
no third-party imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TIMEFRAME_RE = re.compile(r"^(\d+)(m|h|d|w)$")


@dataclass(frozen=True)
class Timeframe:
    """A candlestick timeframe with a numeric value and unit.

    Valid units: m (minute), h (hour), d (day), w (week).
    Examples: Timeframe(1, "h"), Timeframe(4, "h"), Timeframe(1, "d").
    """

    value: int
    unit: str

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError(f"timeframe value must be positive, got {self.value}")
        if self.unit not in ("m", "h", "d", "w"):
            raise ValueError(f"invalid timeframe unit {self.unit!r}; use m/h/d/w")

    @classmethod
    def parse(cls, text: str) -> Timeframe:
        """Parse a timeframe string like '1h', '4h', '1d'."""
        match = _TIMEFRAME_RE.match(text.strip())
        if not match:
            raise ValueError(f"invalid timeframe {text!r}; expected e.g. '1h', '4d'")
        return cls(int(match.group(1)), match.group(2))

    def __str__(self) -> str:
        return f"{self.value}{self.unit}"

    def to_minutes(self) -> int:
        """Convert to minutes (for alignment and grid calculations)."""
        multipliers = {"m": 1, "h": 60, "d": 1440, "w": 10080}
        return self.value * multipliers[self.unit]


__all__ = ["Timeframe"]
