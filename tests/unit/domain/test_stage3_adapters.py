"""Stage 3 adapter tests: MarketDataSource implementations.

Offline tests using fakes; no network access, no ccxt required.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from crypto_engine.data.market_data_source import CcxtMarketDataSource
from crypto_engine.domain.candle import Candle as DomainCandle

# ---------------------------------------------------------------------------
# CcxtMarketDataSource tests (with fake client)
# ---------------------------------------------------------------------------


class FakeExchangeClient:
    """Fake ccxt-compatible client for offline testing."""

    def __init__(self, candles: list[list]) -> None:
        self._candles = candles
        self.fetch_calls: list[tuple] = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.fetch_calls.append((symbol, timeframe, since, limit))
        return list(self._candles)

    @property
    def has_fetch_ohlcv(self):
        return True


def test_market_data_source_fetch_ohlcv() -> None:
    rows = [
        [1700000000000, 60000.0, 61000.0, 59000.0, 60500.0, 1.5],
        [1700003600000, 60500.0, 61500.0, 60000.0, 61000.0, 2.0],
    ]
    fake_client = FakeExchangeClient(rows)
    source = CcxtMarketDataSource(exchange_name="binance", client=fake_client)
    # Type: the constructor param is `client` but the property lazily builds.
    # Override for test.
    source._adapter._client = fake_client

    start_ms = 1700000000000
    end_ms = 1700003600000
    candles = source.fetch_ohlcv("binance", "BTC/USDT", "1h", start_ms, end_ms)

    assert len(candles) == 2
    assert isinstance(candles[0], DomainCandle)
    assert candles[0].symbol == "BTC/USDT"
    assert candles[0].timeframe == "1h"
    assert candles[0].open == Decimal("60000.0")
    assert candles[0].close == Decimal("60500.0")
    assert candles[0].volume == Decimal("1.5")
    assert candles[0].exchange == "binance"


def test_market_data_source_fetch_ohlcv_empty() -> None:
    fake_client = FakeExchangeClient([])
    source = CcxtMarketDataSource(exchange_name="binance", client=fake_client)
    source._adapter._client = fake_client

    candles = source.fetch_ohlcv("binance", "BTC/USDT", "1h", 0, 1000)
    assert candles == []


def test_market_data_source_fetch_funding_not_implemented() -> None:
    source = CcxtMarketDataSource(exchange_name="binance")
    with pytest.raises(NotImplementedError):
        source.fetch_funding("binance", "BTC/USDT", 0, 1000)


__all__ = [
    "FakeExchangeClient",
    "test_market_data_source_fetch_funding_not_implemented",
    "test_market_data_source_fetch_ohlcv",
    "test_market_data_source_fetch_ohlcv_empty",
]
