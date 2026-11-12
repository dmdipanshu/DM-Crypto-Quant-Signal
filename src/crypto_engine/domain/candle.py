"""Candle — a single OHLCV candlestick with timezone-aware UTC timestamp.

Pure stdlib: no third-party imports. Represents one closed bar of market data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal


@dataclass(frozen=True)
class Candle:
    """One closed candlestick.

    All prices are Decimal to avoid floating-point drift. Timestamps are
    timezone-aware UTC. ``open_time`` identifies the candle; ``close_time``
    is ``open_time + timeframe duration``.
    """

    exchange: str
    symbol: str
    timeframe: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        if self.open_time.tzinfo is None:
            raise ValueError("open_time must be timezone-aware UTC")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be >= max(open, close, low)")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be <= min(open, close, high)")
        if self.high < self.low:
            raise ValueError("high must be >= low")
        if self.open <= 0 or self.high <= 0 or self.low <= 0 or self.close <= 0:
            raise ValueError("all prices must be finite and > 0")
        if self.volume < 0:
            raise ValueError("volume must be >= 0")

    @property
    def close_time(self) -> datetime:
        """Close time of the candle (open_time + timeframe duration)."""
        from .timeframe import Timeframe

        tf = Timeframe.parse(self.timeframe)
        return self.open_time + timedelta(minutes=tf.to_minutes())

    @property
    def identity(self) -> tuple[str, str, str, datetime]:
        """Candle identity tuple: (exchange, symbol, timeframe, open_time_utc)."""
        return (self.exchange, self.symbol, self.timeframe, self.open_time)


__all__ = ["Candle"]
