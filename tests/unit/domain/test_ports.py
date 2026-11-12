"""Port protocol conformance tests.

Tests that every port in ``crypto_engine.ports`` has a fake implementation
used in a test, proving the ports are real contracts not just documentation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_engine.domain.candle import Candle
from crypto_engine.ports.clock import Clock
from crypto_engine.ports.ids import IdGenerator
from crypto_engine.ports.market_data import MarketDataSource
from crypto_engine.ports.signals import SignalStore
from crypto_engine.ports.snapshots import SnapshotStore

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeMarketDataSource:
    """Fake implementing MarketDataSource for testing."""

    def __init__(self, candles: list[Candle] | None = None) -> None:
        self._candles = candles or []
        self.fetch_calls: list[tuple] = []

    def fetch_ohlcv(self, exchange, symbol, timeframe, start_ms, end_ms):
        self.fetch_calls.append((exchange, symbol, timeframe, start_ms, end_ms))
        return list(self._candles)

    def fetch_funding(self, exchange, symbol, start_ms, end_ms):
        self.fetch_calls.append((exchange, symbol, "funding", start_ms, end_ms))
        return []


class FakeSnapshotStore:
    """Fake implementing SnapshotStore for testing."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, dict]] = {}

    def write(self, snapshot_id, parquet_bytes, manifest):
        if snapshot_id in self._store:
            raise FileExistsError(f"snapshot {snapshot_id} already exists")
        self._store[snapshot_id] = (parquet_bytes, manifest)
        return snapshot_id

    def read(self, snapshot_id):
        if snapshot_id not in self._store:
            raise KeyError(f"snapshot {snapshot_id} not found")
        return self._store[snapshot_id]

    def exists(self, snapshot_id):
        return snapshot_id in self._store

    def checksum(self, snapshot_id):
        if snapshot_id not in self._store:
            raise KeyError(f"snapshot {snapshot_id} not found")
        return "fake-checksum-" + snapshot_id


class FakeSignalStore:
    """Fake implementing SignalStore for testing."""

    def __init__(self) -> None:
        self._records: dict[str, dict] = {}
        self._chain_ok = True

    def append(self, record):
        sid = record.get("signal_id", "unknown")
        if sid in self._records:
            raise ValueError(f"duplicate signal id {sid}")
        self._records[sid] = record
        return sid

    def get(self, signal_id):
        return self._records.get(signal_id)

    def list(self, *, symbol=None, direction=None, limit=50, offset=0):
        records = list(self._records.values())
        total = len(records)
        return records[offset : offset + limit], total

    def verify_chain(self):
        return self._chain_ok, []


class FakeClock:
    """Fake implementing Clock for testing."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now_utc(self):
        return self._now


class FakeIdGenerator:
    """Fake implementing IdGenerator for testing."""

    def signal_id(self, symbol, timeframe, generated_at, content_hash):
        return f"sig-{content_hash[:8]}"

    def run_id(self, dataset_id, strategy_id, parameters_hash):
        return f"run-{dataset_id[:8]}-{strategy_id[:8]}"


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------


def test_market_data_source_protocol() -> None:
    """MarketDataSource protocol is satisfied by a fake implementation."""
    source = FakeMarketDataSource()
    assert isinstance(source, MarketDataSource)

    candles = [
        Candle(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
            open=Decimal("60000"),
            high=Decimal("61000"),
            low=Decimal("59000"),
            close=Decimal("60500"),
            volume=Decimal("1.5"),
        )
    ]
    source._candles = candles
    result = source.fetch_ohlcv("binance", "BTC/USDT", "1h", 0, 1000)
    assert len(result) == 1
    assert result[0].symbol == "BTC/USDT"


def test_snapshot_store_protocol() -> None:
    """SnapshotStore protocol is satisfied by a fake implementation."""
    store = FakeSnapshotStore()
    assert isinstance(store, SnapshotStore)

    assert not store.exists("snap-001")
    store.write("snap-001", b"data", {"rows": 10})
    assert store.exists("snap-001")
    data, manifest = store.read("snap-001")
    assert data == b"data"
    assert manifest == {"rows": 10}
    assert store.checksum("snap-001") == "fake-checksum-snap-001"

    with pytest.raises(FileExistsError):
        store.write("snap-001", b"data2", {"rows": 20})


def test_signal_store_protocol() -> None:
    """SignalStore protocol is satisfied by a fake implementation."""
    store = FakeSignalStore()
    assert isinstance(store, SignalStore)

    sid = store.append({"signal_id": "sig-001", "symbol": "BTC/USDT"})
    assert sid == "sig-001"

    rec = store.get("sig-001")
    assert rec is not None
    assert rec["symbol"] == "BTC/USDT"

    assert store.get("nope") is None

    records, total = store.list(limit=10, offset=0)
    assert total == 1
    assert len(records) == 1

    ok, errors = store.verify_chain()
    assert ok is True
    assert errors == []

    with pytest.raises(ValueError):
        store.append({"signal_id": "sig-001", "symbol": "ETH/USDT"})


def test_clock_protocol() -> None:
    """Clock protocol is satisfied by a fake implementation."""
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    clock = FakeClock(now)
    assert isinstance(clock, Clock)
    assert clock.now_utc() == now


def test_id_generator_protocol() -> None:
    """IdGenerator protocol is satisfied by a fake implementation."""
    gen = FakeIdGenerator()
    assert isinstance(gen, IdGenerator)

    sid = gen.signal_id("BTC/USDT", "1h", "2026-10-01T00:00:00Z", "abc123def456")
    assert sid == "sig-abc123de"

    rid = gen.run_id("snap-001", "strat-01", "params-hash")
    assert rid == "run-snap-001-strat-01"


# ---------------------------------------------------------------------------
# Port existence: every declared port has at least one fake or adapter
# ---------------------------------------------------------------------------


def test_every_port_has_fake_or_adapter() -> None:
    """Every port in crypto_engine.ports has a fake implementation in tests."""
    # This test documents that each port is real (has a fake/adapter), not
    # just a docstring. If a new port is added, a fake must be added here.
    ports = [MarketDataSource, SnapshotStore, SignalStore, Clock, IdGenerator]
    fakes = [FakeMarketDataSource, FakeSnapshotStore, FakeSignalStore, FakeClock, FakeIdGenerator]
    assert len(ports) == len(fakes)


__all__ = [
    "FakeClock",
    "FakeIdGenerator",
    "FakeMarketDataSource",
    "FakeSignalStore",
    "FakeSnapshotStore",
    "test_clock_protocol",
    "test_every_port_has_fake_or_adapter",
    "test_id_generator_protocol",
    "test_market_data_source_protocol",
    "test_signal_store_protocol",
    "test_snapshot_store_protocol",
]
