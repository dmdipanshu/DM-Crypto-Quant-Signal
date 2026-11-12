"""Stage 3 adapter tests: SnapshotStore and MarketDataSource implementations.

Offline tests using fakes; no network access, no ccxt required.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from crypto_engine.data.market_data_source import CcxtMarketDataSource
from crypto_engine.domain.candle import Candle as DomainCandle
from crypto_engine.storage.snapshot_store import FileSnapshotStore

# ---------------------------------------------------------------------------
# FileSnapshotStore tests
# ---------------------------------------------------------------------------


def test_snapshot_store_write_read(tmp_path: Path) -> None:
    store = FileSnapshotStore(tmp_path / "snapshots")
    payload = b"parquet-payload-bytes"
    manifest = {"rows": 42, "exchange": "binance", "symbol": "BTC/USDT"}

    sid = store.write("snap-001", payload, manifest)
    assert sid == "snap-001"

    data, man = store.read("snap-001")
    assert data == payload
    assert man["rows"] == 42
    assert man["exchange"] == "binance"
    assert "checksum" in man
    assert man["checksum_algorithm"] == "sha256"


def test_snapshot_store_exists(tmp_path: Path) -> None:
    store = FileSnapshotStore(tmp_path / "snapshots")
    assert not store.exists("snap-001")
    store.write("snap-001", b"data", {"rows": 1})
    assert store.exists("snap-001")


def test_snapshot_store_checksum(tmp_path: Path) -> None:
    store = FileSnapshotStore(tmp_path / "snapshots")
    store.write("snap-001", b"data", {"rows": 1})
    cs = store.checksum("snap-001")
    import hashlib

    assert cs == hashlib.sha256(b"data").hexdigest()


def test_snapshot_store_checksum_verification_on_corruption(tmp_path: Path) -> None:
    store = FileSnapshotStore(tmp_path / "snapshots")
    store.write("snap-001", b"original", {"rows": 1})
    # Corrupt the payload file directly
    payload_path = store.root / "snap-001.parquet"
    payload_path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="checksum mismatch"):
        store.read("snap-001")


def test_snapshot_store_no_overwrite(tmp_path: Path) -> None:
    store = FileSnapshotStore(tmp_path / "snapshots")
    store.write("snap-001", b"first", {"rows": 1})
    with pytest.raises(FileExistsError):
        store.write("snap-001", b"second", {"rows": 2})


def test_snapshot_store_read_missing(tmp_path: Path) -> None:
    store = FileSnapshotStore(tmp_path / "snapshots")
    with pytest.raises(KeyError):
        store.read("nope")


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
    "test_snapshot_store_checksum",
    "test_snapshot_store_checksum_verification_on_corruption",
    "test_snapshot_store_exists",
    "test_snapshot_store_no_overwrite",
    "test_snapshot_store_read_missing",
    "test_snapshot_store_write_read",
]
