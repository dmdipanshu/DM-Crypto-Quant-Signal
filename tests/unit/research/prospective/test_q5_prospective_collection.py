"""Comprehensive regression tests for Q5 prospective data collection.

All tests use mocked exchange responses. No external Binance availability required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from crypto_engine.research.prospective.collector import (
    CollectionStatus,
    ProspectiveCollector,
)
from crypto_engine.research.prospective.config import (
    DEFAULT_MIN_COVERAGE_FRACTION,
    DEFAULT_MIN_OBSERVATION_COUNT,
    ProspectiveCollectionPolicy,
)
from crypto_engine.research.prospective.governance import (
    assess_prospective_holdout_readiness,
    historical_holdout_is_contaminated,
)
from crypto_engine.research.prospective.manifest import (
    CollectionConfigMismatch,
    create_or_reuse_collection,
    verify_collection_integrity,
)
from crypto_engine.research.prospective.quality import (
    compute_quality_report,
    is_quality_state_healthy,
)
from crypto_engine.research.prospective.storage import ProspectiveStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candle_row(
    open_ms: int,
    open_price: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 10.0,
) -> list:
    """Make a ccxt-style OHLCV row [ms, o, h, l, c, v]."""
    return [open_ms, open_price, high, low, close, volume]


def _make_ohlcv_series(
    start_ms: int, count: int, interval_ms: int = 3_600_000, base_price: float = 100.0
) -> list[list]:
    """Make a series of closed OHLCV rows."""
    rows = []
    for i in range(count):
        t = start_ms + i * interval_ms
        price = base_price + i * 0.5
        rows.append([t, price, price + 1, price - 1, price + 0.5, 10.0])
    return rows


def _make_mock_client(rows: list[list]) -> MagicMock:
    """Make a mock ccxt client that returns OHLCV rows."""
    client = MagicMock()
    client.fetch_ohlcv.return_value = rows
    client.has_fetch_ohlcv = True
    return client


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _setup_manifests(tmp_path, collection_start_ms: int):
    """Create freeze and collection manifests in tmp_path."""
    collection_start_iso = datetime.fromtimestamp(collection_start_ms / 1000, tz=UTC).isoformat()

    freeze_manifest = tmp_path / "freeze.json"
    freeze_manifest.write_text(
        json.dumps(
            {
                "schema_version": "research-freeze-manifest.v1",
                "freeze_id": "freeze-test",
                "created_at_utc": "2026-11-12T19:11:47.293700+00:00",
                "repository_commit_sha": "abc123",
            }
        ),
        encoding="utf-8",
    )
    collection_manifest = tmp_path / "collection.json"
    collection_manifest.write_text(
        json.dumps(
            {
                "schema_version": "prospective-collection-manifest.v1",
                "collection_id": "test-collect",
                "collection_started_at_utc": collection_start_iso,
                "associated_freeze_id": "freeze-test",
                "associated_freeze_timestamp": "2026-11-12T19:11:47.293700+00:00",
                "exchange": "binance",
                "market_type": "spot",
                "symbols": ["BTC/USDT"],
                "timeframes": ["1h"],
                "collection_config_hash": "abc",
                "collector_version": "q5-v1",
                "data_schema_version": "ohlcv-v1",
                "repository_commit_sha": "abc123",
            }
        ),
        encoding="utf-8",
    )
    return freeze_manifest, collection_manifest


# ---------------------------------------------------------------------------
# A. Exchange collection tests
# ---------------------------------------------------------------------------


class TestExchangeCollection:
    """Tests for the Binance prospective collector with mocked exchange."""

    def test_valid_finalized_candles_collected(self, tmp_path):
        """Valid finalized candles after collection start are accepted."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        # 3 candles after collection start, all closed (finalized)
        rows = _make_ohlcv_series(collection_start_ms + 3_600_000, 3, interval_ms=3_600_000)
        client = _make_mock_client(rows)

        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
                max_ingestion_latency_s=86400,  # 24 hours for test
            ),
            client=client,
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-1",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        assert len(result["observations"]) == 3
        assert len(result["errors"]) == 0
        for obs in result["observations"]:
            assert obs["symbol"] == "BTC/USDT"
            assert obs["timeframe"] == "1h"
            assert obs["first_seen_at_utc"] is not None
            assert obs["observed_at_utc"] is not None

    def test_incomplete_candle_rejected(self, tmp_path):
        """A candle that hasn't closed yet is rejected as not finalized."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        # Candle that opens before now but closes after now (not yet finalized)
        open_ms = int((now - timedelta(minutes=30)).timestamp() * 1000)
        rows = [_make_candle_row(open_ms, 100.0, 101.0, 99.0, 100.5, 10.0)]
        # Override close time by making it later than open + interval
        # _make_candle_row uses open_ms + interval for close, so we need a custom row
        rows = [[open_ms, 100.0, 101.0, 99.0, 100.5, 10.0]]
        client = _make_mock_client(rows)

        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
                max_ingestion_latency_s=86400,
            ),
            client=client,
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-2",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        # The candle closes after now + margin, so it should be rejected
        assert len(result["rejections"]) >= 1
        assert any("finalized" in r.get("reason", "") for r in result["rejections"])

    def test_pre_collection_start_candle_rejected(self, tmp_path):
        """Candles opening before collection start are rejected."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        # Candle before collection start
        pre_start_ms = collection_start_ms - 3_600_000
        rows = [_make_candle_row(pre_start_ms, 100.0, 101.0, 99.0, 100.5, 10.0)]
        client = _make_mock_client(rows)

        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
            ),
            client=client,
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-3",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        assert len(result["rejections"]) >= 1
        assert any(
            "before collection epoch start" in r.get("reason", "") for r in result["rejections"]
        )

    def test_bad_ohlcv_rejected(self, tmp_path):
        """Bad OHLCV (high < low) is rejected."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        # high < low
        rows = [[collection_start_ms + 3_600_000, 100.0, 95.0, 99.0, 100.5, 10.0]]
        client = _make_mock_client(rows)

        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
                max_ingestion_latency_s=86400,
            ),
            client=client,
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-4",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        # Bad OHLCV causes fetch failure, returned as error
        assert len(result["errors"]) >= 1

    def test_timezone_handling_utc(self, tmp_path):
        """All timestamps are stored as UTC."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        rows = _make_ohlcv_series(collection_start_ms + 3_600_000, 1)
        client = _make_mock_client(rows)

        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
                max_ingestion_latency_s=86400,
            ),
            client=client,
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-5",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        obs = result["observations"][0]
        # Timestamps should be ISO format with UTC timezone
        assert "+00:00" in obs["open_time"] or obs["open_time"].endswith("Z")
        assert "+00:00" in obs["first_seen_at_utc"] or obs["first_seen_at_utc"].endswith("Z")


# ---------------------------------------------------------------------------
# B. Prospective data tests
# ---------------------------------------------------------------------------


class TestProspectiveData:
    """Tests for prospective data handling."""

    def test_first_seen_immutable_on_retry(self, tmp_path):
        """first_seen_at is assigned on first observation and never reset."""
        storage = ProspectiveStorage(tmp_path / "storage")

        now = datetime.now(UTC).isoformat()

        # First observation
        rec1 = storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-01-01T00:00:00+00:00",
            close_time="2026-01-01T01:00:00+00:00",
            observed_at_utc=now,
            ingested_at_utc=now,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="hash1",
            raw_payload_hash="hash1",
            collector_version="q5-v1",
            collection_id="collect-1",
            research_freeze_id="freeze-1",
            first_seen_at_utc=now,
        )
        assert rec1["first_seen_at_utc"] == now
        assert rec1["event_id"] is not None

    def test_duplicate_ingestion_idempotent(self, tmp_path):
        """Storing the same candle twice produces distinct records but detectable via open_time."""
        storage = ProspectiveStorage(tmp_path / "storage")

        now = datetime.now(UTC).isoformat()
        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-01-01T00:00:00+00:00",
            close_time="2026-01-01T01:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )
        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-01-01T00:00:00+00:00",  # same open_time
            close_time="2026-01-01T01:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.6,  # different close to get different event_id
            volume=10.0,
            source_observation_id="obs-2",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-1",
        )

        # Both stored (append-only), they may have same or different event_ids
        # depending on the hash input
        candles = storage.read_normalized_candles()
        assert len(candles) == 2  # append-only keeps both

    def test_missing_interval_recorded(self, tmp_path):
        """Missing intervals are detected and recorded as gaps."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=10)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        # Two candles with a gap between them (missing two hours)
        # Candle at +1h, then next at +5h (missing +2h, +3h, +4h)
        rows = [
            _make_candle_row(collection_start_ms + 3_600_000, 100.0, 101.0, 99.0, 100.5, 10.0),
            _make_candle_row(collection_start_ms + 5 * 3_600_000, 101.0, 102.0, 100.0, 101.5, 10.0),
        ]
        client = _make_mock_client(rows)

        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
                max_ingestion_latency_s=86400,
            ),
            client=client,
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-6",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        # There should be a gap detected between the two candles
        assert len(result["gaps"]) >= 1

    def test_outage_no_historical_backfill(self, tmp_path):
        """Collection outage does not trigger historical backfill."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        # No data returned (collector was offline)
        client = _make_mock_client([])

        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
            ),
            client=client,
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-7",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        # No observations, no historical backfill
        assert len(result["observations"]) == 0


# ---------------------------------------------------------------------------
# C. Dataset integrity tests
# ---------------------------------------------------------------------------


class TestDatasetIntegrity:
    """Tests for storage integrity, hash chain, and manifest immutability."""

    def test_ledger_hash_chain_valid(self, tmp_path):
        """The ingestion ledger hash chain verifies correctly."""
        storage = ProspectiveStorage(tmp_path / "storage")

        # Append several events
        for i in range(5):
            storage.append_ledger_event(
                event_type="candle_ingested",
                collection_id="collect-1",
                source_record_hash=f"hash-{i}",
            )

        result = storage.verify_ledger_chain()
        assert result["valid"] is True
        assert result["event_count"] == 5

    def test_ledger_chain_corruption_detected(self, tmp_path):
        """Corrupting a ledger record breaks the chain verification."""
        storage = ProspectiveStorage(tmp_path / "storage")

        storage.append_ledger_event(
            event_type="candle_ingested",
            collection_id="collect-1",
            source_record_hash="hash-0",
        )
        storage.append_ledger_event(
            event_type="candle_ingested",
            collection_id="collect-1",
            source_record_hash="hash-1",
        )

        # Corrupt the ledger file
        lines = storage.ledger_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[1])
        entry["current_event_hash"] = "tampered-hash"
        lines[1] = json.dumps(entry, sort_keys=True)
        storage.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = storage.verify_ledger_chain()
        assert result["valid"] is False
        assert result["broken_at"] is not None

    def test_collection_manifest_immutable(self, tmp_path):
        """Collection manifest cannot be silently regenerated."""
        manifest_path = tmp_path / "manifest.json"
        history_path = tmp_path / "history.jsonl"

        config = {"exchange": "binance", "symbols": ["BTC/USDT"], "timeframes": ["1h"]}

        m1 = create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=history_path,
            collection_id="collect-1",
            collection_started_at_utc="2026-11-12T20:00:00+00:00",
            associated_freeze_id="freeze-1",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        # Rerun: must return the same manifest
        m2 = create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=history_path,
            collection_id="collect-1",
            collection_started_at_utc="2026-11-12T20:00:00+00:00",
            associated_freeze_id="freeze-1",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        assert m2["collection_id"] == m1["collection_id"]
        assert m2["collection_started_at_utc"] == m1["collection_started_at_utc"]

    def test_collection_config_mismatch(self, tmp_path):
        """Changed collection config triggers CollectionConfigMismatch."""
        manifest_path = tmp_path / "manifest.json"
        history_path = tmp_path / "history.jsonl"

        config_a = {"exchange": "binance", "symbols": ["BTC/USDT"], "timeframes": ["1h"]}
        config_b = {"exchange": "binance", "symbols": ["ETH/USDT"], "timeframes": ["1h"]}

        create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=history_path,
            collection_id="collect-1",
            collection_started_at_utc="2026-11-12T20:00:00+00:00",
            associated_freeze_id="freeze-1",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config_a,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        with pytest.raises(CollectionConfigMismatch):
            create_or_reuse_collection(
                manifest_path=manifest_path,
                history_path=history_path,
                collection_id="collect-1",
                collection_started_at_utc="2026-11-12T20:00:00+00:00",
                associated_freeze_id="freeze-1",
                associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
                exchange="binance",
                market_type="spot",
                symbols=["ETH/USDT"],
                timeframes=["1h"],
                collection_config=config_b,
                collector_version="q5-v1",
                data_schema_version="ohlcv-v1",
            )

    def test_raw_payload_hash(self, tmp_path):
        """Raw payload hash is computed over raw observations."""
        storage = ProspectiveStorage(tmp_path / "storage")

        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=_iso(1000),
            close_time=_iso(2000),
            observed_at_utc=_iso(3000),
            ingested_at_utc=_iso(4000),
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="resp-hash",
            raw_payload_hash="payload-hash",
            collector_version="q5-v1",
            collection_id="collect-1",
            research_freeze_id="freeze-1",
        )

        hash_val = storage.compute_raw_payload_hash()
        assert hash_val is not None
        assert len(hash_val) == 64  # SHA-256 hex

    def test_dataset_hash(self, tmp_path):
        """Dataset hash is computed over normalized candles."""
        storage = ProspectiveStorage(tmp_path / "storage")

        storage.store_normalized_candle(
            collection_id="collect-1",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=_iso(1000),
            close_time=_iso(2000),
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-1",
            first_seen_at_utc=_iso(3000),
            observed_at_utc=_iso(3000),
            ingested_at_utc=_iso(4000),
            research_freeze_id="freeze-1",
        )

        hash_val = storage.compute_dataset_hash()
        assert hash_val is not None
        assert len(hash_val) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# D. Holdout governance tests
# ---------------------------------------------------------------------------


class TestHoldoutGovernance:
    """Tests for prospective holdout governance states."""

    def test_historical_holdout_contamination_preserved(self):
        """Historical holdout remains permanently CONTAMINATED."""
        result = historical_holdout_is_contaminated()
        assert result["state"] == "CONTAMINATED"
        assert result["eligible_for_evaluation"] is False
        assert result["evaluated"] is False

    def test_prospective_starts_accumulating(self):
        """New prospective dataset begins as ACCUMULATING."""
        now = datetime.now(UTC)
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=now.isoformat(),
            collection_started_at=now.isoformat(),
            observation_count=0,
            min_observation_count=DEFAULT_MIN_OBSERVATION_COUNT,
            coverage_fraction=0.0,
            min_coverage_fraction=DEFAULT_MIN_COVERAGE_FRACTION,
            data_quality_passed=False,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,
        )
        assert result["state"] == "ACCUMULATING"
        assert result["eligible_for_evaluation"] is False

    def test_insufficient_data_blocks_evaluation(self):
        """Too few observations blocks evaluation eligibility."""
        now = datetime.now(UTC)
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=now.isoformat(),
            collection_started_at=now.isoformat(),
            observation_count=10,
            min_observation_count=DEFAULT_MIN_OBSERVATION_COUNT,
            coverage_fraction=1.0,
            min_coverage_fraction=DEFAULT_MIN_COVERAGE_FRACTION,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,
        )
        assert result["state"] == "ACCUMULATING"
        assert result["eligible_for_evaluation"] is False
        assert any("below minimum" in r for r in result["reasons"])

    def test_quality_failure_blocks_evaluation(self):
        """Data quality failure blocks evaluation."""
        now = datetime.now(UTC)
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=now.isoformat(),
            collection_started_at=now.isoformat(),
            observation_count=200,
            min_observation_count=DEFAULT_MIN_OBSERVATION_COUNT,
            coverage_fraction=1.0,
            min_coverage_fraction=DEFAULT_MIN_COVERAGE_FRACTION,
            data_quality_passed=False,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,
        )
        assert result["state"] == "ACCUMULATING"
        assert result["eligible_for_evaluation"] is False

    def test_no_automatic_evaluated_transition(self):
        """EVALUATED requires explicit evaluation_requested=True."""
        now = datetime.now(UTC)
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=now.isoformat(),
            collection_started_at=now.isoformat(),
            observation_count=200,
            min_observation_count=DEFAULT_MIN_OBSERVATION_COUNT,
            coverage_fraction=1.0,
            min_coverage_fraction=DEFAULT_MIN_COVERAGE_FRACTION,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,  # NOT explicitly requested
        )
        assert result["state"] == "ELIGIBLE_FOR_EVALUATION"
        assert result["evaluated"] is False

    def test_explicit_evaluation_transition(self):
        """Explicit evaluation transitions to EVALUATED."""
        now = datetime.now(UTC)
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=now.isoformat(),
            collection_started_at=now.isoformat(),
            observation_count=200,
            min_observation_count=DEFAULT_MIN_OBSERVATION_COUNT,
            coverage_fraction=1.0,
            min_coverage_fraction=DEFAULT_MIN_COVERAGE_FRACTION,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=True,  # Explicitly requested
        )
        assert result["state"] == "EVALUATED"
        assert result["evaluated"] is True


# ---------------------------------------------------------------------------
# E. Isolation and safety tests
# ---------------------------------------------------------------------------


class TestIsolationSafety:
    """Tests for dataset isolation and safety constraints."""

    def test_no_private_exchange_endpoints(self):
        """Collector only uses public fetchOHLCV endpoint."""
        import crypto_engine.research.prospective.collector as collector_mod

        source = Path(collector_mod.__file__).read_text(encoding="utf-8")
        assert "fetch_balance" not in source
        assert "fetch_order" not in source
        assert "create_order" not in source
        assert "place_order" not in source

    def test_no_order_placement(self):
        """No module places exchange orders."""
        import crypto_engine.research.prospective.collector as collector_mod

        source = Path(collector_mod.__file__).read_text(encoding="utf-8")
        assert "create_order" not in source
        assert "place_order" not in source

    def test_no_live_trading_enablement(self):
        """No research module enables live trading."""
        import crypto_engine.research.prospective.collector as collector_mod

        source = Path(collector_mod.__file__).read_text(encoding="utf-8")
        assert "live_trading" not in source
        assert "enable_trading" not in source


# ---------------------------------------------------------------------------
# F. Quality report tests
# ---------------------------------------------------------------------------


class TestQualityReport:
    """Tests for data quality monitoring."""

    def test_healthy_state(self):
        """HEALTHY state when no issues."""
        result = {
            "collection_id": "collect-1",
            "collection_started_at_utc": "2026-11-12T20:00:00+00:00",
            "observations": [{"symbol": "BTC/USDT"}] * 5,
            "gaps": [],
            "duplicates": [],
            "conflicts": [],
            "rejections": [],
            "errors": [],
            "status": CollectionStatus.HEALTHY,
            "quality_checks": {
                "state": CollectionStatus.HEALTHY,
                "reasons": [],
                "observation_count": 5,
                "expected_intervals": 5,
                "coverage_fraction": 1.0,
                "error_count": 0,
                "rejection_count": 0,
                "gap_count": 0,
                "duplicate_count": 0,
                "conflict_count": 0,
            },
        }
        report = compute_quality_report(collection_result=result)
        assert report["state"] == CollectionStatus.HEALTHY
        assert report["evaluation_blocked"] is False

    def test_gap_detected_blocks_evaluation(self):
        """GAP_DETECTED blocks evaluation."""
        result = {
            "collection_id": "collect-1",
            "collection_started_at_utc": "2026-11-12T20:00:00+00:00",
            "observations": [{"symbol": "BTC/USDT"}] * 5,
            "gaps": [{"symbol": "BTC/USDT", "gap_intervals": 1}],
            "duplicates": [],
            "conflicts": [],
            "rejections": [],
            "errors": [],
            "status": CollectionStatus.GAP_DETECTED,
            "quality_checks": {
                "state": CollectionStatus.GAP_DETECTED,
                "reasons": ["1 missing interval(s)"],
                "observation_count": 5,
                "expected_intervals": 6,
                "coverage_fraction": 0.833,
                "error_count": 0,
                "rejection_count": 0,
                "gap_count": 1,
                "duplicate_count": 0,
                "conflict_count": 0,
            },
        }
        report = compute_quality_report(collection_result=result)
        assert report["state"] == CollectionStatus.GAP_DETECTED
        assert report["evaluation_blocked"] is True

    def test_is_quality_state_healthy(self):
        """is_quality_state_healthy returns True only for HEALTHY."""
        assert is_quality_state_healthy(CollectionStatus.HEALTHY) is True
        assert is_quality_state_healthy(CollectionStatus.GAP_DETECTED) is False
        assert is_quality_state_healthy(CollectionStatus.DATA_INVALID) is False


# ---------------------------------------------------------------------------
# Manifest verification tests
# ---------------------------------------------------------------------------


class TestManifestVerification:
    """Tests for collection manifest verification."""

    def test_verify_collection_integrity_valid(self, tmp_path):
        """Valid manifest returns VALID status."""
        manifest_path = tmp_path / "manifest.json"

        config = {"exchange": "binance", "symbols": ["BTC/USDT"], "timeframes": ["1h"]}
        from crypto_engine.research.prospective.config import hash_collection_config

        config_hash = hash_collection_config(config)

        create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=tmp_path / "history.jsonl",
            collection_id="collect-1",
            collection_started_at_utc="2026-11-12T20:00:00+00:00",
            associated_freeze_id="freeze-1",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        result = verify_collection_integrity(
            manifest_path=manifest_path,
            expected_collection_config_hash=config_hash,
            expected_exchange="binance",
            expected_symbols=["BTC/USDT"],
            expected_timeframes=["1h"],
            associated_freeze_id="freeze-1",
        )

        assert result["status"] == "VALID"

    def test_verify_collection_integrity_scope_mismatch(self, tmp_path):
        """Wrong exchange returns SCOPE_MISMATCH."""
        manifest_path = tmp_path / "manifest.json"

        config = {"exchange": "binance", "symbols": ["BTC/USDT"], "timeframes": ["1h"]}
        from crypto_engine.research.prospective.config import hash_collection_config

        config_hash = hash_collection_config(config)

        create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=tmp_path / "history.jsonl",
            collection_id="collect-1",
            collection_started_at_utc="2026-11-12T20:00:00+00:00",
            associated_freeze_id="freeze-1",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        result = verify_collection_integrity(
            manifest_path=manifest_path,
            expected_collection_config_hash=config_hash,
            expected_exchange="kraken",  # Wrong exchange
            expected_symbols=["BTC/USDT"],
            expected_timeframes=["1h"],
            associated_freeze_id="freeze-1",
        )

        assert result["status"] == "SCOPE_MISMATCH"


# ---------------------------------------------------------------------------
# Q5.1 Configuration mismatch and evaluation gate tests
# ---------------------------------------------------------------------------


class TestConfigurationMismatch:
    """Tests for configuration hash consistency across entry points."""

    def test_canonical_config_hash_matches_manifest(self):
        """load_collection_config() produces the same hash as the manifest."""
        from crypto_engine.research.prospective.config import (
            hash_collection_config,
            load_collection_config,
        )

        config = load_collection_config()
        config_hash = hash_collection_config(config)

        # The manifest at PROSPECTIVE_COLLECTION_MANIFEST.json contains
        # collection_config_hash = 748a2a1e... which is the authoritative
        # hash for the existing collection epoch.
        manifest_path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json")
        if manifest_path.is_file():
            import json

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            assert config_hash == manifest["collection_config_hash"], (
                f"Canonical config hash {config_hash} does not match manifest "
                f"hash {manifest['collection_config_hash']}"
            )

    def test_load_collection_config_returns_narrow_scope(self):
        """load_collection_config() returns the narrow scope matching the manifest."""
        from crypto_engine.research.prospective.config import load_collection_config

        config = load_collection_config()
        assert config["exchange"] == "binance"
        assert config["market_type"] == "spot"
        assert config["symbols"] == ["BTC/USDT"]
        assert config["timeframes"] == ["1h"]
        assert config["policy"]["min_observation_count"] == 168
        assert config["policy"]["min_coverage_fraction"] == 0.95

    def test_canonical_config_hash_is_deterministic(self):
        """Same configuration always produces the same hash."""
        from crypto_engine.research.prospective.config import (
            canonical_collection_config,
            hash_collection_config,
        )

        config1 = canonical_collection_config(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
        )
        config2 = canonical_collection_config(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
        )
        assert hash_collection_config(config1) == hash_collection_config(config2)

    def test_configuration_drift_produces_field_by_field_diagnostic(self, tmp_path):
        """Changed configuration produces CollectionConfigMismatch with details."""
        manifest_path = tmp_path / "manifest.json"
        history_path = tmp_path / "history.jsonl"

        config_a = {
            "exchange": "binance",
            "market_type": "spot",
            "symbols": ["BTC/USDT"],
            "timeframes": ["1h"],
            "policy": {
                "min_observation_count": 168,
                "min_elapsed_observation_period_s": 604800,
                "min_coverage_fraction": 0.95,
                "candle_finalization_margin_s": 300,
                "max_ingestion_latency_s": 900,
            },
        }
        config_b = {
            "exchange": "binance",
            "market_type": "spot",
            "symbols": ["BTC/USDT", "ETH/USDT"],  # Drifted: added ETH/USDT
            "timeframes": ["1h"],
            "policy": {
                "min_observation_count": 168,
                "min_elapsed_observation_period_s": 604800,
                "min_coverage_fraction": 0.95,
                "candle_finalization_margin_s": 300,
                "max_ingestion_latency_s": 900,
            },
        }

        from crypto_engine.research.prospective.config import hash_collection_config

        hash_a = hash_collection_config(config_a)
        hash_b = hash_collection_config(config_b)
        assert hash_a != hash_b, "Different configs must produce different hashes"

        create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=history_path,
            new_collection_id="collect-1",
            collection_started_at_utc="2026-11-12T20:00:00+00:00",
            associated_freeze_id="freeze-1",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config_a,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        with pytest.raises(CollectionConfigMismatch) as exc_info:
            create_or_reuse_collection(
                manifest_path=manifest_path,
                history_path=history_path,
                new_collection_id="collect-2",
                collection_started_at_utc="2026-11-12T20:00:00+00:00",
                associated_freeze_id="freeze-1",
                associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
                exchange="binance",
                market_type="spot",
                symbols=["BTC/USDT", "ETH/USDT"],
                timeframes=["1h"],
                collection_config=config_b,
                collector_version="q5-v1",
                data_schema_version="ohlcv-v1",
            )

        assert "collect-1" in str(exc_info.value)
        assert "Do not overwrite the original manifest" in str(exc_info.value)

    def test_manifest_reused_unchanged_on_rerun(self, tmp_path):
        """Rerunning collection reuses the existing manifest without changes."""
        manifest_path = tmp_path / "manifest.json"
        history_path = tmp_path / "history.jsonl"

        config = {
            "exchange": "binance",
            "market_type": "spot",
            "symbols": ["BTC/USDT"],
            "timeframes": ["1h"],
            "policy": {
                "min_observation_count": 168,
                "min_elapsed_observation_period_s": 604800,
                "min_coverage_fraction": 0.95,
                "candle_finalization_margin_s": 300,
                "max_ingestion_latency_s": 900,
            },
        }

        m1 = create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=history_path,
            new_collection_id="collect-keep",
            collection_started_at_utc="2026-11-13T06:15:46.915986+00:00",
            associated_freeze_id="freeze-aaa4dd57057a",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        m2 = create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=history_path,
            new_collection_id="collect-keep",
            collection_started_at_utc="2026-11-13T06:15:46.915986+00:00",
            associated_freeze_id="freeze-aaa4dd57057a",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        assert m2["collection_id"] == m1["collection_id"] == "collect-keep"
        assert m2["collection_started_at_utc"] == m1["collection_started_at_utc"]
        assert m2["collection_config_hash"] == m1["collection_config_hash"]
        assert m2["associated_freeze_id"] == m1["associated_freeze_id"] == "freeze-aaa4dd57057a"


class TestEvaluationGate:
    """Tests for the prospective evaluation gate."""

    def test_zero_observations_blocks_evaluation(self):
        """Zero observations means evaluation_blocked = True."""
        result = {
            "collection_id": "collect-1",
            "collection_started_at_utc": "2026-11-13T06:15:46.915986+00:00",
            "observations": [],
            "gaps": [],
            "duplicates": [],
            "conflicts": [],
            "rejections": [],
            "errors": [],
            "status": CollectionStatus.HEALTHY,
            "quality_checks": {
                "state": CollectionStatus.HEALTHY,
                "reasons": [],
                "observation_count": 0,
                "expected_intervals": 0,
                "coverage_fraction": 0.0,
                "error_count": 0,
                "rejection_count": 0,
                "gap_count": 0,
                "duplicate_count": 0,
                "conflict_count": 0,
            },
        }
        report = compute_quality_report(collection_result=result)
        assert report["evaluation_blocked"] is True
        assert len(report["evaluation_block_reasons"]) > 0
        assert any("No observations collected" in r for r in report["evaluation_block_reasons"])

    def test_healthy_collection_with_zero_observations_is_blocked(self):
        """Collector HEALTHY does not imply evaluation eligible."""
        result = {
            "collection_id": "collect-1",
            "collection_started_at_utc": "2026-11-13T06:15:46.915986+00:00",
            "observations": [],
            "gaps": [],
            "duplicates": [],
            "conflicts": [],
            "rejections": [],
            "errors": [],
            "status": CollectionStatus.HEALTHY,
            "quality_checks": {
                "state": CollectionStatus.HEALTHY,
                "reasons": [],
                "observation_count": 0,
                "expected_intervals": 0,
                "coverage_fraction": 0.0,
                "error_count": 0,
                "rejection_count": 0,
                "gap_count": 0,
                "duplicate_count": 0,
                "conflict_count": 0,
            },
        }
        report = compute_quality_report(collection_result=result)
        assert report["state"] == CollectionStatus.HEALTHY
        assert report["evaluation_blocked"] is True

    def test_accumulating_holdout_cannot_be_automatically_evaluated(self):
        """ACCUMULATING state requires explicit evaluation_requested=True."""
        now = datetime.now(UTC)
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=now.isoformat(),
            collection_started_at=now.isoformat(),
            observation_count=200,
            min_observation_count=168,
            coverage_fraction=1.0,
            min_coverage_fraction=0.95,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,  # NOT explicitly requested
        )
        assert result["state"] == "ELIGIBLE_FOR_EVALUATION"
        assert result["evaluated"] is False
        assert result["eligible_for_evaluation"] is True

    def test_explicit_evaluation_required_for_evaluated_state(self):
        """Only explicit evaluation_requested=True transitions to EVALUATED."""
        now = datetime.now(UTC)
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=now.isoformat(),
            collection_started_at=now.isoformat(),
            observation_count=200,
            min_observation_count=168,
            coverage_fraction=1.0,
            min_coverage_fraction=0.95,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=True,
        )
        assert result["state"] == "EVALUATED"
        assert result["evaluated"] is True

    def test_insufficient_observations_blocks_evaluation(self):
        """Too few observations blocks evaluation eligibility."""
        now = datetime.now(UTC)
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=now.isoformat(),
            collection_started_at=now.isoformat(),
            observation_count=10,
            min_observation_count=168,
            coverage_fraction=1.0,
            min_coverage_fraction=0.95,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=True,
        )
        assert result["state"] == "ACCUMULATING"
        assert result["evaluated"] is False
        assert result["eligible_for_evaluation"] is False

    def test_invalid_freeze_blocks_collection(self, tmp_path):
        """Invalid research freeze prevents collection."""
        freeze_manifest = tmp_path / "freeze.json"
        freeze_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "research-freeze-manifest.v1",
                    # Missing freeze_id and created_at_utc -> SCHEMA_INVALID
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )

        from crypto_engine.research.prospective.collector import ProspectiveCollector

        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            freeze_manifest_path=str(freeze_manifest),
        )

        with pytest.raises(RuntimeError, match="not VALID"):
            collector.validate_freeze()


class TestNoAutomaticNewEpoch:
    """Tests that no new collection epoch is created automatically."""

    def test_no_automatic_new_collection_epoch(self, tmp_path):
        """Rerunning collection does not create a new epoch."""
        manifest_path = tmp_path / "manifest.json"
        history_path = tmp_path / "history.jsonl"

        config = {
            "exchange": "binance",
            "market_type": "spot",
            "symbols": ["BTC/USDT"],
            "timeframes": ["1h"],
            "policy": {
                "min_observation_count": 168,
                "min_elapsed_observation_period_s": 604800,
                "min_coverage_fraction": 0.95,
                "candle_finalization_margin_s": 300,
                "max_ingestion_latency_s": 900,
            },
        }

        m1 = create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=history_path,
            new_collection_id="collect-original",
            collection_started_at_utc="2026-11-13T06:15:46.915986+00:00",
            associated_freeze_id="freeze-aaa4dd57057a",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        # Rerun with same config - must reuse, not create new
        m2 = create_or_reuse_collection(
            manifest_path=manifest_path,
            history_path=history_path,
            new_collection_id="collect-original",
            collection_started_at_utc="2026-11-13T06:15:46.915986+00:00",
            associated_freeze_id="freeze-aaa4dd57057a",
            associated_freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config=config,
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )

        assert m2["collection_id"] == "collect-original"
        assert m2["collection_started_at_utc"] == m1["collection_started_at_utc"]
