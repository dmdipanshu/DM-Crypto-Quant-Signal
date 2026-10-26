"""Market-data core types (Phase 1)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Candle(BaseModel):
    """A single validated OHLCV candle with a UTC-aware timestamp."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    model_config = {"frozen": True}


class FetchParams(BaseModel):
    """Parameters describing one bounded OHLCV fetch window."""

    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=1000, gt=0)


class FetchResult(BaseModel):
    """Outcome of a bounded fetch, preserving requested vs actual boundaries."""

    candles: list[Candle] = Field(default_factory=list)
    requested_since: datetime | None = None
    requested_until: datetime | None = None
    actual_since: datetime | None = None
    actual_until: datetime | None = None
    row_count: int = 0
    missing_intervals: list[tuple[datetime, datetime]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source: str = ""


__all__ = ["Candle", "FetchParams", "FetchResult"]
