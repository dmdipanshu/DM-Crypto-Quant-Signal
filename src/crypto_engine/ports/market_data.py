"""Market data source port (DR-2).

Protocol for fetching public OHLCV market data. Implementations live in the
infrastructure layer (e.g. ``market_data/exchange/ccxt_rest.py``) and are
resolved only at the composition root.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crypto_engine.domain.candle import Candle


@runtime_checkable
class MarketDataSource(Protocol):
    """Protocol for public market data fetching."""

    def fetch_ohlcv(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
    ) -> list[Candle]:
        """Fetch OHLCV candles for a symbol/timeframe range.

        Returns closed candles only. Raises on network or schema failure;
        never returns partial data as complete.
        """
        ...

    def fetch_funding(
        self,
        exchange: str,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[tuple[int, float]]:
        """Fetch funding-rate samples for a symbol range.

        Returns list of ``(timestamp_ms, rate)`` tuples.
        """
        ...


__all__ = ["MarketDataSource"]
