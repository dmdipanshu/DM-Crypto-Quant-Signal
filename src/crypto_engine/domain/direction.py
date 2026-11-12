"""Direction — canonical trade direction vocabulary.

Signal direction values. Pure stdlib: no third-party imports.
"""

from __future__ import annotations

from enum import StrEnum


class Direction(StrEnum):
    """Canonical signal direction.

    LONG: buy geometry; SHORT: sell geometry; NEUTRAL: no directional bias;
    NO_TRADE: abstain (first-class valid output).
    """

    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"
    NO_TRADE = "NO_TRADE"


__all__ = ["Direction"]
