"""Market data source adapter implementing ports.MarketDataSource.

Wraps the existing Phase 1 CcxtAdapter to satisfy the ports.MarketDataSource
protocol. Funding-rate fetching is not implemented (raises NotImplementedError);
OHLCV fetch maps directly to CcxtAdapter.fetch_range with bounded pagination
and no silent fills.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from crypto_engine.data.adapter import CcxtAdapter
from crypto_engine.data.types import FetchParams
from crypto_engine.domain.candle import Candle as DomainCandle


class CcxtMarketDataSource:
    """MarketDataSource backed by CcxtAdapter (ccxt public REST, read-only)."""

    def __init__(
        self,
        exchange_name: str = "binance",
        timeout_ms: int = 10000,
        max_retries: int = 3,
        rate_limit: bool = True,
        client: Any = None,
    ) -> None:
        self._adapter = CcxtAdapter(
            exchange_name=exchange_name,
            timeout_ms=timeout_ms,
            max_retries=max_retries,
            rate_limit=rate_limit,
            client=client,
        )

    def fetch_ohlcv(
        self,
        exchange: str,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
    ) -> list[DomainCandle]:
        """Fetch OHLCV candles for a symbol/timeframe range.

        Maps start_ms/end_ms to FetchParams since/until and returns domain
        Candle objects. No silent fills: missing intervals are reported as
        warnings, not invented rows.
        """
        since_dt = datetime.fromtimestamp(start_ms / 1000, tz=UTC) if start_ms is not None else None
        until_dt = datetime.fromtimestamp(end_ms / 1000, tz=UTC) if end_ms is not None else None
        params = FetchParams(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            since=since_dt,
            until=until_dt,
            limit=1000,
        )
        result = self._adapter.fetch_range(params)
        return [
            DomainCandle(
                exchange=result.source,
                symbol=symbol,
                timeframe=timeframe,
                open_time=c.timestamp,
                open=Decimal(str(c.open)),
                high=Decimal(str(c.high)),
                low=Decimal(str(c.low)),
                close=Decimal(str(c.close)),
                volume=Decimal(str(c.volume)),
            )
            for c in result.candles
        ]

    def fetch_funding(
        self,
        exchange: str,
        symbol: str,
        start_ms: int,
        end_ms: int,
    ) -> list[tuple[int, float]]:
        """Funding-rate samples are not supported by this adapter."""
        raise NotImplementedError("fetch_funding is not implemented")


__all__ = ["CcxtMarketDataSource"]
