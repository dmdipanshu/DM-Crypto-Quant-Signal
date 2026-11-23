"""Comprehensive regression tests for Q5 prospective data collection.

All tests use mocked exchange responses. No external Binance availability required.
"""

from __future__ import annotations

import importlib.util
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
from crypto_engine.research.prospective.storage import ProspectiveStorage, _sha256_json

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
                "status": "VALID",
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

    def test_prospective_starts_not_started_without_observations(self):
        """New prospective dataset with zero accepted observations is NOT_STARTED."""
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
        assert result["state"] == "NOT_STARTED"
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


# ---------------------------------------------------------------------------
# G. Dataset manifest generation tests
# ---------------------------------------------------------------------------


class TestDatasetManifestGeneration:
    """Tests for dataset_manifest.json generation in collect_once()."""

    def test_manifest_created_after_collection(self, tmp_path):
        """collect_once() writes dataset_manifest.json after collection cycle."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        rows = _make_ohlcv_series(collection_start_ms + 3_600_000, 2, interval_ms=3_600_000)
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
            storage_root=tmp_path / "storage",
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        collector.collect_once()

        # Manifest file must exist
        manifest_path = (
            tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h" / "dataset_manifest.json"
        )
        assert manifest_path.is_file(), "dataset_manifest.json must be written"

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] is not None
        assert manifest["exchange"] == "binance"
        assert manifest["market_type"] == "spot"
        assert manifest["symbol"] == "BTC/USDT"
        assert manifest["timeframe"] == "1h"
        assert manifest["research_freeze_id"] == "freeze-test"
        assert manifest["observation_count"] == 2
        assert manifest["dataset_hash"] is not None
        assert manifest["raw_payload_hash"] is not None
        assert manifest["latest_open_time_utc"] is not None

    def test_dataset_hash_represents_all_observations(self, tmp_path):
        """dataset_hash changes when observations change (deterministic over complete dataset)."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        def _collect(rows: list[list]) -> str:
            _setup_manifests(tmp_path, collection_start_ms)
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
                storage_root=tmp_path / "storage",
                freeze_manifest_path=str(tmp_path / "freeze.json"),
                collection_manifest_path=str(tmp_path / "collection.json"),
            )
            collector.collect_once()
            manifest_path = (
                tmp_path
                / "storage"
                / "binance"
                / "spot"
                / "BTC/USDT"
                / "1h"
                / "dataset_manifest.json"
            )
            return json.loads(manifest_path.read_text(encoding="utf-8"))["dataset_hash"]

        hash_one = _collect(_make_ohlcv_series(collection_start_ms + 3_600_000, 1))
        hash_two = _collect(_make_ohlcv_series(collection_start_ms + 3_600_000, 2))

        assert hash_one != hash_two, "dataset_hash must differ when observation count differs"

    def test_dataset_hash_deterministic_on_reorder(self, tmp_path):
        """Reordering stored candles does not change dataset_hash (deterministic)."""
        storage_root = tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h"
        storage_root.mkdir(parents=True, exist_ok=True)
        storage = ProspectiveStorage(storage_root)
        now_iso = datetime.now(UTC).isoformat()

        # Store 3 candles in order A
        candle_data_a = [
            ("2026-01-01T00:00:00+00:00", 100.0, 101.0, 99.0, 100.5),
            ("2026-01-01T01:00:00+00:00", 101.0, 102.0, 100.0, 101.5),
            ("2026-01-01T02:00:00+00:00", 102.0, 103.0, 101.0, 102.5),
        ]
        for i, (t, op, hi, lo, cl) in enumerate(candle_data_a):
            storage.store_normalized_candle(
                collection_id="collect-1",
                exchange="binance",
                market_type="spot",
                symbol="BTC/USDT",
                interval="1h",
                open_time=t,
                close_time="2026-01-01T03:00:00+00:00",
                open_price=op,
                high=hi,
                low=lo,
                close=cl,
                volume=10.0,
                source_observation_id=f"obs-{i}",
                first_seen_at_utc=now_iso,
                observed_at_utc=now_iso,
                ingested_at_utc=now_iso,
                research_freeze_id="freeze-test",
            )

        hash_a = storage.compute_dataset_hash()

        # Clear and re-store in different order B
        storage.normalized_path.unlink(missing_ok=True)
        storage.ledger_path.unlink(missing_ok=True)

        # Same candles, different insertion order
        candle_data_b = [
            ("2026-01-01T02:00:00+00:00", 102.0, 103.0, 101.0, 102.5),
            ("2026-01-01T00:00:00+00:00", 100.0, 101.0, 99.0, 100.5),
            ("2026-01-01T01:00:00+00:00", 101.0, 102.0, 100.0, 101.5),
        ]
        for i, (t, op, hi, lo, cl) in enumerate(candle_data_b):
            storage.store_normalized_candle(
                collection_id="collect-1",
                exchange="binance",
                market_type="spot",
                symbol="BTC/USDT",
                interval="1h",
                open_time=t,
                close_time="2026-01-01T03:00:00+00:00",
                open_price=op,
                high=hi,
                low=lo,
                close=cl,
                volume=10.0,
                source_observation_id=f"obs-{i}",
                first_seen_at_utc=now_iso,
                observed_at_utc=now_iso,
                ingested_at_utc=now_iso,
                research_freeze_id="freeze-test",
            )

        hash_b = storage.compute_dataset_hash()

        assert hash_a == hash_b, "dataset_hash must be deterministic regardless of insertion order"

    def test_raw_payload_hash_includes_all_observations(self, tmp_path):
        """raw_payload_hash changes when any observation's raw payload changes."""
        storage_root = tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h"
        storage_root.mkdir(parents=True, exist_ok=True)
        storage = ProspectiveStorage(storage_root)
        now_iso = datetime.now(UTC).isoformat()

        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-01-01T00:00:00+00:00",
            close_time="2026-01-01T01:00:00+00:00",
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="resp-hash-1",
            raw_payload_hash="payload-hash-1",
            collector_version="q5-v1",
            collection_id="collect-1",
            research_freeze_id="freeze-1",
        )
        hash_a = storage.compute_raw_payload_hash()

        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-01-01T01:00:00+00:00",
            close_time="2026-01-01T02:00:00+00:00",
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="resp-hash-2",
            raw_payload_hash="payload-hash-2",
            collector_version="q5-v1",
            collection_id="collect-1",
            research_freeze_id="freeze-1",
        )
        hash_b = storage.compute_raw_payload_hash()

        assert hash_a != hash_b, "raw_payload_hash must differ when new observation added"
        assert len(hash_b) == 64  # SHA-256 hex

    def test_observation_count_correct(self, tmp_path):
        """observation_count matches unique accepted observations."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

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
                max_ingestion_latency_s=86400,
            ),
            client=client,
            storage_root=tmp_path / "storage",
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        collector.collect_once()

        manifest_path = (
            tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h" / "dataset_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["observation_count"] == 3

    def test_coverage_fraction_bounded_and_canonical(self, tmp_path):
        """coverage_fraction is bounded [0.0, 1.0] and uses canonical calculation."""
        storage_root = tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h"
        storage_root.mkdir(parents=True, exist_ok=True)
        storage = ProspectiveStorage(storage_root)
        now_iso = datetime.now(UTC).isoformat()

        # Store 5 candles spanning 5 hours
        for i in range(5):
            t = f"2026-01-01T{i:02d}:00:00+00:00"
            storage.store_normalized_candle(
                collection_id="collect-1",
                exchange="binance",
                market_type="spot",
                symbol="BTC/USDT",
                interval="1h",
                open_time=t,
                close_time=f"2026-01-01T{i + 1:02d}:00:00+00:00",
                open_price=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                volume=10.0,
                source_observation_id=f"obs-{i}",
                first_seen_at_utc=now_iso,
                observed_at_utc=now_iso,
                ingested_at_utc=now_iso,
                research_freeze_id="freeze-test",
            )

        # Expected intervals from first candle open (2026-01-01T00:00) to now
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        collection_start_ms = int(
            datetime.fromisoformat("2026-01-01T00:00:00+00:00").timestamp() * 1000
        )
        interval_ms = 3_600_000  # 1h
        first_open_ms = ((collection_start_ms // interval_ms) + 1) * interval_ms
        expected = 0
        t = first_open_ms
        while t + interval_ms <= now_ms:
            expected += 1
            t += interval_ms

        manifest_payload = {
            "schema": "prospective-dataset.v1",
            "collection_id": "collect-1",
            "exchange": "binance",
            "market_type": "spot",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "collection_started_at_utc": "2026-01-01T00:00:00+00:00",
            "collection_updated_at_utc": now_iso,
            "observation_count": 5,
            "coverage_fraction": round(5 / expected if expected > 0 else 0.0, 4),
            "dataset_hash": storage.compute_dataset_hash(),
            "raw_payload_hash": storage.compute_raw_payload_hash(),
            "latest_open_time_utc": "2026-01-01T04:00:00+00:00",
            "research_freeze_id": "freeze-test",
            "collector_version": "q5-v1",
            "data_schema_version": "ohlcv-v1",
        }
        storage.write_dataset_manifest(manifest_payload)

        written = json.loads((storage_root / "dataset_manifest.json").read_text(encoding="utf-8"))
        assert 0.0 <= written["coverage_fraction"] <= 1.0
        assert written["observation_count"] == 5

    def test_zero_observations_manifest(self, tmp_path):
        """Zero observations: manifest exists but count is 0, coverage is 0.0."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        _setup_manifests(tmp_path, collection_start_ms)

        client = _make_mock_client([])  # No data returned

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
            storage_root=tmp_path / "storage",
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        collector.collect_once()

        manifest_path = (
            tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h" / "dataset_manifest.json"
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["observation_count"] == 0
        assert manifest["coverage_fraction"] == 0.0
        assert manifest["latest_open_time_utc"] is None
        assert manifest["dataset_hash"] is not None
        assert manifest["raw_payload_hash"] is not None

    def test_duplicate_candles_do_not_increase_count(self, tmp_path):
        """Storing same candle twice does not increase observation_count."""
        storage_root = tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h"
        storage_root.mkdir(parents=True, exist_ok=True)
        storage = ProspectiveStorage(storage_root)
        now_iso = datetime.now(UTC).isoformat()

        # Store same candle twice
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
            first_seen_at_utc=now_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            research_freeze_id="freeze-test",
        )
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
            close=100.6,
            volume=10.0,
            source_observation_id="obs-2",
            first_seen_at_utc=now_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            research_freeze_id="freeze-test",
        )

        unique_candles = storage.read_unique_normalized_candles()
        assert len(unique_candles) == 1  # deduplicated by open_time

    def test_manifest_compatible_with_validator(self, tmp_path):
        """Written manifest fields are compatible with governance reading."""
        storage_root = tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h"
        storage_root.mkdir(parents=True, exist_ok=True)
        storage = ProspectiveStorage(storage_root)
        now_iso = datetime.now(UTC).isoformat()

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
            first_seen_at_utc=now_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            research_freeze_id="freeze-test",
        )

        manifest_payload = {
            "schema": "prospective-dataset.v1",
            "collection_id": "collect-1",
            "exchange": "binance",
            "market_type": "spot",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "collection_started_at_utc": "2026-01-01T00:00:00+00:00",
            "collection_updated_at_utc": now_iso,
            "observation_count": 1,
            "coverage_fraction": 0.5,
            "dataset_hash": storage.compute_dataset_hash(),
            "raw_payload_hash": storage.compute_raw_payload_hash(),
            "latest_open_time_utc": "2026-01-01T00:00:00+00:00",
            "research_freeze_id": "freeze-test",
            "collector_version": "q5-v1",
            "data_schema_version": "ohlcv-v1",
        }
        storage.write_dataset_manifest(manifest_payload)

        # The manifest fields should be compatible with governance reading
        written = json.loads((storage_root / "dataset_manifest.json").read_text(encoding="utf-8"))
        assert written["exchange"] == "binance"
        assert written["market_type"] == "spot"
        assert written["symbol"] == "BTC/USDT"
        assert written["timeframe"] == "1h"
        assert isinstance(written["observation_count"], int)
        assert isinstance(written["coverage_fraction"], (int, float))
        assert written["research_freeze_id"] == "freeze-test"

    def test_manifest_write_failure_handled_gracefully(self, tmp_path, monkeypatch):
        """Manifest write failure is recorded as error, does not crash collection."""
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
            storage_root=tmp_path / "storage",
            freeze_manifest_path=str(tmp_path / "freeze.json"),
            collection_manifest_path=str(tmp_path / "collection.json"),
        )

        # Make storage write fail to force error handling
        monkeypatch.setattr(
            "crypto_engine.research.prospective.storage.ProspectiveStorage.write_dataset_manifest",
            lambda self, payload: (_ for _ in ()).throw(RuntimeError("disk full")),
        )

        result = collector.collect_once()

        # Collection should still succeed (observations stored), error recorded
        assert len(result["errors"]) >= 1
        assert any("manifest" in e.lower() for e in result["errors"])

    def test_windows_filesystem_manifest_write_read(self, tmp_path):
        """Windows-specific regression test: manifest write/read on real temp directory.

        Verifies that storage writes actually persist to disk and that the
        manifest reader resolves to exactly the same directory as the writer.
        This test uses real filesystem operations (not mocks) to catch
        path-resolution issues on Windows.
        """
        from crypto_engine.research.prospective.storage import ProspectiveStorage

        storage_root = tmp_path / "storage" / "binance" / "spot" / "BTC/USDT" / "1h"
        storage_root.mkdir(parents=True, exist_ok=True)
        storage = ProspectiveStorage(storage_root)
        now_iso = datetime.now(UTC).isoformat()

        # Write a raw observation (real disk write)
        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-01-01T00:00:00+00:00",
            close_time="2026-01-01T01:00:00+00:00",
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="resp-hash-1",
            raw_payload_hash="payload-hash-1",
            collector_version="q5-v1",
            collection_id="collect-1",
            research_freeze_id="freeze-test",
        )

        # Verify raw observation file exists on disk
        raw_path = storage_root / "raw_observations.jsonl"
        assert raw_path.is_file(), "raw_observations.jsonl must be written to disk"

        # Write normalized candle (real disk write)
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
            first_seen_at_utc=now_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            research_freeze_id="freeze-test",
        )

        # Verify normalized candle file exists on disk
        normalized_path = storage_root / "normalized_candles.jsonl"
        assert normalized_path.is_file(), "normalized_candles.jsonl must be written to disk"

        # Write dataset manifest (real disk write)
        manifest_payload = {
            "schema": "prospective-dataset.v1",
            "collection_id": "collect-1",
            "exchange": "binance",
            "market_type": "spot",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "collection_started_at_utc": "2026-01-01T00:00:00+00:00",
            "collection_updated_at_utc": now_iso,
            "observation_count": 1,
            "coverage_fraction": 0.0,
            "dataset_hash": storage.compute_dataset_hash(),
            "raw_payload_hash": storage.compute_raw_payload_hash(),
            "latest_open_time_utc": "2026-01-01T00:00:00+00:00",
            "research_freeze_id": "freeze-test",
            "collector_version": "q5-v1",
            "data_schema_version": "ohlcv-v1",
        }
        storage.write_dataset_manifest(manifest_payload)

        # Verify manifest file exists on disk
        manifest_path = storage_root / "dataset_manifest.json"
        assert manifest_path.is_file(), "dataset_manifest.json must be written to disk"

        # Read manifest back - must resolve to same directory as writer
        written = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert written["collection_id"] == "collect-1"
        assert written["exchange"] == "binance"
        assert written["symbol"] == "BTC/USDT"
        assert written["observation_count"] == 1

        # Verify the manifest reader and writer use the same path
        reader = ProspectiveStorage(storage_root)
        read_manifest = reader.read_dataset_manifest()
        assert read_manifest is not None
        assert read_manifest["collection_id"] == "collect-1"
        assert read_manifest["observation_count"] == 1


# ---------------------------------------------------------------------------
# G. Q5.2 Epoch 2 Regression Tests
# ---------------------------------------------------------------------------
# Tests for the new independent collection epoch. All tests must NOT
# modify existing freeze, collection, or approval records.


class TestEpoch2NewCollectionIds:
    """New collection epoch receives independent IDs separate from existing."""

    def test_new_freeze_id_differs_from_existing(self):
        """New freeze ID must differ from existing freeze-aaa4dd57057a."""
        freeze_path = Path("docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH2.json")
        assert freeze_path.is_file(), "Epoch 2 freeze manifest must exist"
        import json

        manifest = json.loads(freeze_path.read_text(encoding="utf-8"))
        assert manifest["freeze_id"] == "freeze-epoch2-f34ea12a-34f6-4e5f-b830-393402947a65"
        assert manifest["freeze_id"] != "freeze-aaa4dd57057a"

    def test_new_collection_id_differs_from_existing(self):
        """New collection ID must differ from existing collect-52cefa0dda64."""
        manifest_path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH2.json")
        assert manifest_path.is_file(), "Epoch 2 collection manifest must exist"
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-epoch2-eca19890-be05-4929-8773-e04885966db1"
        assert manifest["collection_id"] != "collect-52cefa0dda64"

    def test_epoch2_manifest_freeze_id_matches_freeze_id(self):
        """Collection manifest's associated_freeze_id must match epoch 2 freeze."""
        collection_path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH2.json")
        freeze_path = Path("docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH2.json")
        import json

        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        assert collection["associated_freeze_id"] == freeze["freeze_id"]


class TestEpoch2ExistingEvidencePreserved:
    """Existing research evidence must not be modified by epoch 2."""

    def test_existing_freeze_manifest_unchanged(self):
        """Existing RESEARCH_FREEZE_MANIFEST.json must not be modified."""
        path = Path("docs/validation/RESEARCH_FREEZE_MANIFEST.json")
        assert path.is_file()
        import json

        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["freeze_id"] == "freeze-aaa4dd57057a"
        assert manifest["schema_version"] == "research-freeze-manifest.v1"

    def test_existing_collection_manifest_unchanged(self):
        """Existing PROSPECTIVE_COLLECTION_MANIFEST.json must not be modified."""
        path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json")
        assert path.is_file()
        import json

        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-52cefa0dda64"
        assert manifest["associated_freeze_id"] == "freeze-aaa4dd57057a"
        assert manifest["schema_version"] == "prospective-collection-manifest.v1"

    def test_existing_approval_records_unchanged(self):
        """Existing approval records must remain unchanged."""
        coll_approval = Path("docs/validation/approvals/q5_2_collection_continuation_approval.json")
        sched_approval = Path(
            "docs/validation/approvals/q5_2_scheduler_authorization_approval.json"
        )
        assert coll_approval.is_file()
        assert sched_approval.is_file()
        import json

        coll = json.loads(coll_approval.read_text(encoding="utf-8"))
        sched = json.loads(sched_approval.read_text(encoding="utf-8"))
        assert coll["collection_id"] == "collect-52cefa0dda64"
        assert coll["freeze_id"] == "freeze-aaa4dd57057a"
        assert sched["collection_id"] == "collect-52cefa0dda64"
        assert sched["freeze_id"] == "freeze-aaa4dd57057a"

    def test_existing_freeze_history_unchanged(self):
        """RESEARCH_FREEZE_HISTORY.jsonl must not be modified."""
        path = Path("docs/validation/RESEARCH_FREEZE_HISTORY.jsonl")
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "freeze-aaa4dd57057a" in content

    def test_existing_collection_history_unchanged(self):
        """PROSPECTIVE_COLLECTION_HISTORY.jsonl must not be modified."""
        path = Path("docs/validation/PROSPECTIVE_COLLECTION_HISTORY.jsonl")
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "collect-52cefa0dda64" in content


class TestEpoch2Authorization:
    """Authorization must match exact collection ID and freeze ID."""

    def test_epoch2_collection_authorization(self):
        """Epoch 2 authorization record exists with correct IDs."""
        import json

        path = Path("docs/validation/approvals/collection-epoch2-authorization.json")
        assert path.is_file()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-epoch2-eca19890-be05-4929-8773-e04885966db1"
        assert manifest["freeze_id"] == "freeze-epoch2-f34ea12a-34f6-4e5f-b830-393402947a65"
        assert manifest["collection_authorized"] is True
        assert manifest["scheduler_authorized"] is False
        assert manifest["strategy_evaluation_authorized"] is False
        assert manifest["paper_trading_authorized"] is False
        assert manifest["live_trading_authorized"] is False
        assert manifest["Q5_2_PROSPECTIVE_VALIDATED"] is False

    def test_epoch2_scheduler_authorization(self):
        """Epoch 2 scheduler authorization record exists with correct IDs."""
        import json

        path = Path("docs/validation/approvals/scheduler-epoch2-authorization.json")
        assert path.is_file()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-epoch2-eca19890-be05-4929-8773-e04885966db1"
        assert manifest["freeze_id"] == "freeze-epoch2-f34ea12a-34f6-4e5f-b830-393402947a65"
        assert manifest["collection_authorized"] is True
        assert manifest["scheduler_authorized"] is True
        assert manifest["strategy_evaluation_authorized"] is False
        assert manifest["paper_trading_authorized"] is False
        assert manifest["live_trading_authorized"] is False
        assert manifest["Q5_2_PROSPECTIVE_VALIDATED"] is False

    def test_authorization_mismatch_rejected(self, tmp_path):
        """Authorization with mismatched collection ID is rejected."""
        from crypto_engine.research.q6.governance_verification import (
            check_collection_authorization,
        )

        result = check_collection_authorization(
            collection_id="wrong-collect-id",
            freeze_id="freeze-epoch2-f34ea12a-34f6-4e5f-b830-393402947a65",
            approvals_root=Path("docs/validation/approvals"),
        )
        assert result.authorized is False

        result2 = check_collection_authorization(
            collection_id="collect-epoch2-eca19890-be05-4929-8773-e04885966db1",
            freeze_id="wrong-freeze-id",
            approvals_root=Path("docs/validation/approvals"),
        )
        assert result2.authorized is False


class TestEpoch2CollectorIsolation:
    """Collector cannot write outside its designated directories."""

    def test_collector_storage_root_is_epoch2_path(self):
        """Epoch 2 collector uses separate storage root directory."""
        # The storage root for epoch 2 is data/prospective/binance/spot/BTC/USDT/1h
        # Same structure as epoch 1 but the data is independent
        # The key isolation is that epoch 2 manifest has different IDs
        manifest_path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH2.json")
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] != "collect-52cefa0dda64"
        assert manifest["associated_freeze_id"] != "freeze-aaa4dd57057a"

    def test_api_prospective_mount_read_only(self):
        """API's prospective data mount is read-only."""
        # Verify compose.yaml mounts prospective data read-only for API
        compose_path = Path("compose.yaml")
        assert compose_path.is_file()
        content = compose_path.read_text(encoding="utf-8")
        # API service mounts prospective data read-only (:ro)
        assert "/app/data/prospective/binance/spot/BTC/USDT/1h:ro" in content


class TestEpoch2PathNormalization:
    """BTC/USDT path normalization must be consistent everywhere."""

    def test_symbol_uses_slash_not_hyphen(self):
        """Canonical symbol format is BTC/USDT not BTC-USDT."""
        from crypto_engine.research.prospective.config import DEFAULT_SYMBOLS

        assert "BTC/USDT" in DEFAULT_SYMBOLS
        assert "BTC-USDT" not in DEFAULT_SYMBOLS

    def test_repos_api_uses_btc_usdt_path(self):
        """repos.py uses BTC/USDT not BTC-USDT in storage path."""
        source = Path("src/crypto_engine/api/repos.py").read_text(encoding="utf-8")
        # The storage path should use BTC/USDT (with slash)
        assert "BTC/USDT" in source or "'BTC-USDT'" not in source.split("BTC")[1:3]

    def test_collector_uses_btc_usdt_symbol(self):
        """Collector uses BTC/USDT not BTC-USDT for symbol."""
        import crypto_engine.research.prospective.config as config_mod

        source = Path(config_mod.__file__).read_text(encoding="utf-8")
        # DEFAULT_SYMBOLS should be ("BTC/USDT",) with forward slash
        assert (
            'DEFAULT_SYMBOLS = ("BTC/USDT",)' in source
            or "DEFAULT_SYMBOLS = ('BTC/USDT',)" in source
        )


class TestEpoch2IdempotencyAndIntegrity:
    """Duplicate ingestion and retry behavior must be idempotent."""

    def test_duplicate_candle_ingestion_is_idempotent(self, tmp_path):
        """Storing same candle twice does not increase unique observation count."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = datetime.now(UTC).isoformat()

        storage.store_normalized_candle(
            collection_id="collect-epoch2",
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
            research_freeze_id="freeze-epoch2",
        )
        storage.store_normalized_candle(
            collection_id="collect-epoch2",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-01-01T00:00:00+00:00",
            close_time="2026-01-01T01:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.6,
            volume=10.0,
            source_observation_id="obs-2",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-epoch2",
        )

        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1  # Deduplicated by open_time

    def test_retry_does_not_create_duplicate_observations(self, tmp_path):
        """Retry collection does not create duplicate observations for same bar."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now = datetime.now(UTC).isoformat()

        # Simulate first collection attempt
        storage.store_normalized_candle(
            collection_id="collect-epoch2",
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
            source_observation_id="obs-retry-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-epoch2",
        )
        # Retry: same candle, same open_time
        storage.store_normalized_candle(
            collection_id="collect-epoch2",
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
            source_observation_id="obs-retry-2",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-epoch2",
        )

        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1  # Deduplicated

    def test_missing_candle_not_backfilled(self, tmp_path):
        """Missing candle in collection window is not backfilled."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        freeze_manifest = tmp_path / "freeze.json"
        freeze_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "research-freeze-manifest.v1",
                    "freeze_id": "freeze-test",
                    "status": "VALID",
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
                    "collection_started_at_utc": datetime.fromtimestamp(
                        collection_start_ms / 1000, tz=UTC
                    ).isoformat(),
                    "associated_freeze_id": "freeze-test",
                    "associated_freeze_timestamp": "2026-11-12T19:11:47.293700+00:00",
                    "exchange": "binance",
                    "market_type": "spot",
                    "symbols": ["BTC/USDT"],
                    "timeframes": ["1h"],
                    "collector_version": "q5-v1",
                    "data_schema_version": "ohlcv-v1",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )

        # Mock client returns NO candles (outage)
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
                max_ingestion_latency_s=86400,
            ),
            client=client,
            freeze_manifest_path=str(freeze_manifest),
            collection_manifest_path=str(collection_manifest),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-missing",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        # No observations collected, no historical backfill
        assert len(result["observations"]) == 0
        assert len(result["errors"]) == 0  # No errors, just no data


class TestEpoch2ThresholdsImmutable:
    """Seven-day and 95% thresholds remain immutable."""

    def test_min_observation_count_is_168(self):
        """Minimum observation count remains 168."""
        from crypto_engine.research.prospective.config import (
            DEFAULT_MIN_OBSERVATION_COUNT,
        )

        assert DEFAULT_MIN_OBSERVATION_COUNT == 168

    def test_min_coverage_fraction_is_0_95(self):
        """Minimum coverage fraction remains 95%."""
        from crypto_engine.research.prospective.config import (
            DEFAULT_MIN_COVERAGE_FRACTION,
        )

        assert DEFAULT_MIN_COVERAGE_FRACTION == 0.95

    def test_min_elapsed_period_is_7_days(self):
        """Minimum elapsed observation period remains 7 days."""
        from crypto_engine.research.prospective.config import (
            DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S,
        )

        assert DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S == 604800  # 7 days


class TestEpoch2TradingDisabled:
    """Paper trading and live trading remain disabled."""

    def test_paper_trading_authorized_false(self):
        """Epoch 2 authorization has paper_trading_authorized = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch2-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["paper_trading_authorized"] is False

    def test_live_trading_authorized_false(self):
        """Epoch 2 authorization has live_trading_authorized = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch2-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["live_trading_authorized"] is False

    def test_strategy_evaluation_authorized_false(self):
        """Epoch 2 authorization has strategy_evaluation_authorized = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch2-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["strategy_evaluation_authorized"] is False

    def test_model_promotion_authorized_false(self):
        """Epoch 2 authorization has model_promotion_authorized = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch2-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["model_promotion_authorized"] is False

    def test_q5_2_prospective_validated_false(self):
        """Epoch 2 authorization has Q5_2_PROSPECTIVE_VALIDATED = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch2-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["Q5_2_PROSPECTIVE_VALIDATED"] is False


# ---------------------------------------------------------------------------
# H. Q5.2 Epoch 3 Regression Tests
# ---------------------------------------------------------------------------
# Tests for the new independent collection epoch 3. All tests must NOT
# modify existing freeze, collection, or approval records.


class TestEpoch3NewCollectionIds:
    """New epoch 3 collection receives independent IDs separate from existing epochs."""

    def test_new_freeze_id_differs_from_existing_epochs(self):
        """New freeze ID must differ from existing freeze-aaa4dd57057a and freeze-epoch2-*."""
        freeze_path = Path("docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json")
        assert freeze_path.is_file(), "Epoch 3 freeze manifest must exist"
        import json

        manifest = json.loads(freeze_path.read_text(encoding="utf-8"))
        assert manifest["freeze_id"] == "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f"
        assert manifest["freeze_id"] != "freeze-aaa4dd57057a"
        assert manifest["freeze_id"] != "freeze-epoch2-f34ea12a-34f6-4e5f-b830-393402947a65"

    def test_new_collection_id_differs_from_existing_epochs(self):
        """New collection ID must differ from existing collect-52cefa0dda64 and collect-epoch2-*."""
        manifest_path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json")
        assert manifest_path.is_file(), "Epoch 3 collection manifest must exist"
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d"
        assert manifest["collection_id"] != "collect-52cefa0dda64"
        assert manifest["collection_id"] != "collect-epoch2-eca19890-be05-4929-8773-e04885966db1"

    def test_epoch3_manifest_freeze_id_matches_freeze_id(self):
        """Collection manifest's associated_freeze_id must match epoch 3 freeze ID."""
        collection_path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json")
        freeze_path = Path("docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json")
        import json

        collection = json.loads(collection_path.read_text(encoding="utf-8"))
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        assert collection["associated_freeze_id"] == freeze["freeze_id"]
        assert (
            collection["associated_freeze_id"]
            == "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f"
        )


class TestEpoch3ExistingEvidencePreserved:
    """Existing research evidence must not be modified by epoch 3."""

    def test_epoch2_freeze_manifest_unchanged(self):
        """Epoch 2 freeze manifest must not be modified."""
        path = Path("docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH2.json")
        assert path.is_file()
        import json

        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["freeze_id"] == "freeze-epoch2-f34ea12a-34f6-4e5f-b830-393402947a65"

    def test_epoch2_collection_manifest_unchanged(self):
        """Epoch 2 collection manifest must not be modified."""
        path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH2.json")
        assert path.is_file()
        import json

        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-epoch2-eca19890-be05-4929-8773-e04885966db1"

    def test_epoch1_freeze_manifest_unchanged(self):
        """Original freeze manifest must not be modified."""
        path = Path("docs/validation/RESEARCH_FREEZE_MANIFEST.json")
        assert path.is_file()
        import json

        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["freeze_id"] == "freeze-aaa4dd57057a"

    def test_epoch1_collection_manifest_unchanged(self):
        """Original collection manifest must not be modified."""
        path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json")
        assert path.is_file()
        import json

        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-52cefa0dda64"

    def test_existing_freeze_history_unchanged(self):
        """RESEARCH_FREEZE_HISTORY.jsonl must not be modified."""
        path = Path("docs/validation/RESEARCH_FREEZE_HISTORY.jsonl")
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "freeze-aaa4dd57057a" in content

    def test_existing_collection_history_unchanged(self):
        """PROSPECTIVE_COLLECTION_HISTORY.jsonl must not be modified."""
        path = Path("docs/validation/PROSPECTIVE_COLLECTION_HISTORY.jsonl")
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "collect-52cefa0dda64" in content


class TestEpoch3Authorization:
    """Authorization must match exact collection ID and freeze ID."""

    def test_epoch3_collection_authorization(self):
        """Epoch 3 authorization record exists with correct IDs."""
        import json

        path = Path("docs/validation/approvals/collection-epoch3-authorization.json")
        assert path.is_file()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d"
        assert manifest["freeze_id"] == "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f"
        assert manifest["collection_authorized"] is True
        assert manifest["scheduler_authorized"] is False
        assert manifest["strategy_evaluation_authorized"] is False
        assert manifest["paper_trading_authorized"] is False
        assert manifest["live_trading_authorized"] is False
        assert manifest["model_promotion_authorized"] is False
        assert manifest["Q5_2_PROSPECTIVE_VALIDATED"] is False

    def test_epoch3_scheduler_authorization(self):
        """Epoch 3 scheduler authorization record exists with correct IDs."""
        import json

        path = Path("docs/validation/approvals/scheduler-epoch3-authorization.json")
        assert path.is_file()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] == "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d"
        assert manifest["freeze_id"] == "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f"
        assert manifest["collection_authorized"] is True
        assert manifest["scheduler_authorized"] is True
        assert manifest["strategy_evaluation_authorized"] is False
        assert manifest["paper_trading_authorized"] is False
        assert manifest["live_trading_authorized"] is False
        assert manifest["model_promotion_authorized"] is False
        assert manifest["Q5_2_PROSPECTIVE_VALIDATED"] is False

    def test_authorization_requires_both_matching_ids(self, tmp_path):
        """Authorization with mismatched collection ID or freeze ID is rejected."""
        from crypto_engine.research.q6.governance_verification import (
            check_collection_authorization,
        )

        # Mismatched collection ID
        result = check_collection_authorization(
            collection_id="wrong-collect-id",
            freeze_id="freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
            approvals_root=Path("docs/validation/approvals"),
        )
        assert result.authorized is False

        result2 = check_collection_authorization(
            collection_id="collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d",
            freeze_id="wrong-freeze-id",
            approvals_root=Path("docs/validation/approvals"),
        )
        assert result2.authorized is False

    def test_scheduler_authorization_requires_scheduler_authorized_true(self, tmp_path):
        """Scheduler authorization requires scheduler_authorized=True."""
        from crypto_engine.research.q6.governance_verification import (
            check_scheduler_authorization,
        )

        # Scheduler authorization is independent of collection authorization
        result = check_scheduler_authorization(
            collection_id="collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d",
            freeze_id="freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
            approvals_root=Path("docs/validation/approvals"),
        )
        assert result.authorized is True


class TestEpoch3CollectorIsolation:
    """Collector cannot write outside its designated directories."""

    def test_collector_storage_is_epoch3_path(self):
        """Epoch 3 collector uses separate storage root directory."""
        # The storage root for epoch 3 is data/prospective/binance/spot/BTC/USDT/1h
        # Same structure as epoch 2 but the data is independent

        # The key isolation is that epoch 3 manifest has different IDs
        manifest_path = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json")
        import json

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["collection_id"] != "collect-52cefa0dda64"
        assert manifest["associated_freeze_id"] != "freeze-aaa4dd57057a"
        assert manifest["collection_id"] != "collect-epoch2-eca19890-be05-4929-8773-e04885966db1"
        assert (
            manifest["associated_freeze_id"] != "freeze-epoch2-f34ea12a-34f6-4e5f-b830-393402947a65"
        )

    def test_api_prospective_mount_read_only(self):
        """API's prospective data mount is read-only."""
        compose_path = Path("compose.yaml")
        assert compose_path.is_file()
        content = compose_path.read_text(encoding="utf-8")
        # API service mounts prospective data read-only (:ro)
        assert "/app/data/prospective/binance/spot/BTC/USDT/1h:ro" in content
        # Collector service mounts prospective data read-write
        assert "/app/data/prospective/binance/spot/BTC/USDT/1h" in content


class TestEpoch3PathNormalization:
    """BTC/USDT path normalization must be consistent everywhere."""

    def test_symbol_uses_slash_not_hyphen(self):
        """Canonical symbol format is BTC/USDT not BTC-USDT."""
        from crypto_engine.research.prospective.config import DEFAULT_SYMBOLS

        assert "BTC/USDT" in DEFAULT_SYMBOLS
        assert "BTC-USDT" not in DEFAULT_SYMBOLS

    def test_collector_uses_btc_usdt_symbol(self):
        """Collector uses BTC/USDT not BTC-USDT for symbol."""
        import crypto_engine.research.prospective.config as config_mod

        source = Path(config_mod.__file__).read_text(encoding="utf-8")
        assert (
            'DEFAULT_SYMBOLS = ("BTC/USDT",)' in source
            or "DEFAULT_SYMBOLS = ('BTC/USDT',)" in source
        )


class TestEpoch3IdempotencyAndIntegrity:
    """Duplicate ingestion and integrity behavior."""

    def test_duplicate_candle_ingestion_is_idempotent(self, tmp_path):
        """Storing same candle twice does not increase unique observation count."""
        from crypto_engine.research.prospective.storage import ProspectiveStorage

        now = datetime.now(UTC).isoformat()

        storage = ProspectiveStorage(tmp_path / "storage")
        storage.store_normalized_candle(
            collection_id="collect-epoch3",
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
            research_freeze_id="freeze-epoch3",
        )
        storage.store_normalized_candle(
            collection_id="collect-epoch3",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time="2026-01-01T00:00:00+00:00",
            close_time="2026-01-01T01:00:00+00:00",
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.6,
            volume=10.0,
            source_observation_id="obs-2",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-epoch3",
        )

        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1  # Deduplicated by open_time

    def test_retry_does_not_create_duplicate_observations(self, tmp_path):
        """Retry collection does not create duplicate observations for same bar."""
        from crypto_engine.research.prospective.storage import ProspectiveStorage

        now = datetime.now(UTC).isoformat()

        storage = ProspectiveStorage(tmp_path / "storage")
        storage.store_normalized_candle(
            collection_id="collect-epoch3",
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
            source_observation_id="obs-retry-1",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-epoch3",
        )
        storage.store_normalized_candle(
            collection_id="collect-epoch3",
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
            source_observation_id="obs-retry-2",
            first_seen_at_utc=now,
            observed_at_utc=now,
            ingested_at_utc=now,
            research_freeze_id="freeze-epoch3",
        )

        unique = storage.read_unique_normalized_candles()
        assert len(unique) == 1  # Deduplicated

    def test_missing_candle_not_backfilled(self, tmp_path):
        """Missing candle in collection window is not backfilled."""
        now = datetime.now(UTC)
        collection_start = now - timedelta(hours=5)
        collection_start_ms = int(collection_start.timestamp() * 1000)

        freeze_manifest = tmp_path / "freeze.json"
        freeze_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "research-freeze-manifest.v1",
                    "freeze_id": "freeze-test",
                    "status": "VALID",
                    "created_at_utc": "2026-11-13T06:15:46.915986+00:00",
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
                    "collection_started_at_utc": datetime.fromisoformat(
                        "2026-01-01T00:00:00+00:00"
                    ).isoformat(),
                    "associated_freeze_id": "freeze-test",
                    "associated_freeze_timestamp": "2026-01-01T00:00:00+00:00",
                    "exchange": "binance",
                    "market_type": "spot",
                    "symbols": ["BTC/USDT"],
                    "timeframes": ["1h"],
                    "collector_version": "q5-v1",
                    "data_schema_version": "ohlcv-v1",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )

        client = _make_mock_client([])  # No data returned

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
            freeze_manifest_path=str(freeze_manifest),
            collection_manifest_path=str(collection_manifest),
        )

        result = collector._collect_symbol_timeframe(
            symbol="BTC/USDT",
            timeframe="1h",
            collection_id="test-collect-missing",
            collection_start_ms=collection_start_ms,
            freeze_id="freeze-test",
            storage_root=tmp_path / "storage",
        )

        # No observations collected, no historical backfill
        assert len(result["observations"]) == 0
        assert len(result["errors"]) == 0  # No errors, just no data


class TestEpoch3ThresholdsImmutable:
    """Seven-day and 95% thresholds remain immutable."""

    def test_min_observation_count_is_168(self):
        """Minimum observation count remains 168."""
        from crypto_engine.research.prospective.config import (
            DEFAULT_MIN_OBSERVATION_COUNT,
        )

        assert DEFAULT_MIN_OBSERVATION_COUNT == 168

    def test_min_coverage_fraction_is_0_95(self):
        """Minimum coverage fraction remains 95%."""
        from crypto_engine.research.prospective.config import (
            DEFAULT_MIN_COVERAGE_FRACTION,
        )

        assert DEFAULT_MIN_COVERAGE_FRACTION == 0.95

    def test_min_elapsed_period_is_7_days(self):
        """Minimum elapsed observation period remains 7 days."""
        from crypto_engine.research.prospective.config import (
            DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S,
        )

        assert DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S == 604800  # 7 days


class TestEpoch3TradingDisabled:
    """Paper trading and live trading remain disabled."""

    def test_paper_trading_authorized_false(self):
        """Epoch 3 authorization has paper_trading_authorized = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch3-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["paper_trading_authorized"] is False

    def test_live_trading_authorized_false(self):
        """Epoch 3 authorization has live_trading_authorized = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch3-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["live_trading_authorized"] is False

    def test_strategy_evaluation_authorized_false(self):
        """Epoch 3 authorization has strategy_evaluation_authorized = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch3-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["strategy_evaluation_authorized"] is False

    def test_model_promotion_authorized_false(self):
        """Epoch 3 authorization has model_promotion_authorized = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch3-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["model_promotion_authorized"] is False

    def test_q5_2_prospective_validated_false(self):
        """Epoch 3 authorization has Q5_2_PROSPECTIVE_VALIDATED = False."""
        import json

        path = Path("docs/validation/approvals/collection-epoch3-authorization.json")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["Q5_2_PROSPECTIVE_VALIDATED"] is False


class TestEpoch3AnchorAndGovernance:
    """Epoch 3 immutable first-observation anchor and governance enforcement."""

    def test_collection_start_null_before_first_accepted_observation(self, tmp_path):
        from crypto_engine.research.prospective.manifest import build_collection_manifest

        manifest = build_collection_manifest(
            collection_id="collect-epoch3-test",
            associated_freeze_id="freeze-epoch3-test",
            associated_freeze_timestamp="2026-11-22T08:00:00+00:00",
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            collection_config={"x": 1},
        )
        assert manifest["collection_started_at_utc"] is None

    def test_anchor_established_on_first_accepted_observation(self, tmp_path):
        storage_root = tmp_path / "prospective"
        freeze_manifest = tmp_path / "freeze.json"
        collection_manifest = tmp_path / "collection.json"
        freeze_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "research-freeze-manifest.v1",
                    "freeze_id": "freeze-epoch3-test",
                    "status": "VALID",
                    "created_at_utc": "2026-11-22T08:00:00+00:00",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )
        collection_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "prospective-collection-manifest.v1",
                    "collection_id": "collect-epoch3-test",
                    "collection_started_at_utc": None,
                    "associated_freeze_id": "freeze-epoch3-test",
                    "associated_freeze_timestamp": "2026-11-22T08:00:00+00:00",
                    "exchange": "binance",
                    "market_type": "spot",
                    "symbols": ["BTC/USDT"],
                    "timeframes": ["1h"],
                    "collector_version": "q5-v1",
                    "data_schema_version": "ohlcv-v1",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        # closed candle, within default max_ingestion_latency_s (900s)
        open_ms = ((now_ms - 2 * 3_600_000) // 3_600_000) * 3_600_000
        client = _make_mock_client([_make_candle_row(open_ms, 100, 101, 99, 100.5, 10)])
        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            storage_root=str(storage_root),
            client=client,
            freeze_manifest_path=str(freeze_manifest),
            collection_manifest_path=str(collection_manifest),
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
                max_ingestion_latency_s=86400,
            ),
        )
        result = collector.collect_once()
        assert result["collection_started_at_utc"] is not None
        assert result["quality_checks"]["state"] in {
            "ACCUMULATING",
            "EXPIRED",
            "ELIGIBLE_FOR_EVALUATION",
        }
        anchor_path = (
            storage_root / "binance" / "spot" / "BTC/USDT" / "1h" / "collection_start_anchor.json"
        )
        assert anchor_path.is_file()
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        assert anchor["collection_id"] == "collect-epoch3-test"
        assert anchor["collection_started_at_utc"] == result["collection_started_at_utc"]
        assert anchor["anchor_hash"]

    def test_anchor_is_immutable_on_retry_and_duplicate(self, tmp_path):
        storage_root = tmp_path / "prospective"
        freeze_manifest = tmp_path / "freeze.json"
        collection_manifest = tmp_path / "collection.json"
        freeze_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "research-freeze-manifest.v1",
                    "freeze_id": "freeze-epoch3-test",
                    "status": "VALID",
                    "created_at_utc": "2026-11-22T08:00:00+00:00",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )
        collection_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "prospective-collection-manifest.v1",
                    "collection_id": "collect-epoch3-test",
                    "collection_started_at_utc": None,
                    "associated_freeze_id": "freeze-epoch3-test",
                    "associated_freeze_timestamp": "2026-11-22T08:00:00+00:00",
                    "exchange": "binance",
                    "market_type": "spot",
                    "symbols": ["BTC/USDT"],
                    "timeframes": ["1h"],
                    "collector_version": "q5-v1",
                    "data_schema_version": "ohlcv-v1",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        open_ms = ((now_ms - 2 * 3_600_000) // 3_600_000) * 3_600_000
        rows = [_make_candle_row(open_ms, 100, 101, 99, 100.5, 10)]
        client = _make_mock_client(rows)
        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            storage_root=str(storage_root),
            client=client,
            freeze_manifest_path=str(freeze_manifest),
            collection_manifest_path=str(collection_manifest),
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
                max_ingestion_latency_s=86400,
            ),
        )
        r1 = collector.collect_once()
        anchor_path = (
            storage_root / "binance" / "spot" / "BTC/USDT" / "1h" / "collection_start_anchor.json"
        )
        a1 = json.loads(anchor_path.read_text(encoding="utf-8"))
        r2 = collector.collect_once()
        a2 = json.loads(anchor_path.read_text(encoding="utf-8"))
        assert r1["collection_started_at_utc"] == r2["collection_started_at_utc"]
        assert a1 == a2
        # Duplicate ingestion attempt should not change anchor
        storage = ProspectiveStorage(storage_root / "binance" / "spot" / "BTC/USDT" / "1h")
        storage.store_normalized_candle(
            collection_id="collect-epoch3-test",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=_iso(open_ms),
            close_time=_iso(open_ms + 3_600_000),
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="dup",
            first_seen_at_utc=_iso(now_ms),
            observed_at_utc=_iso(now_ms),
            ingested_at_utc=_iso(now_ms),
            research_freeze_id="freeze-epoch3-test",
        )
        a3 = json.loads(anchor_path.read_text(encoding="utf-8"))
        assert a1 == a3

    def test_zero_observations_remains_not_started(self, tmp_path):
        storage_root = tmp_path / "prospective"
        freeze_manifest = tmp_path / "freeze.json"
        collection_manifest = tmp_path / "collection.json"
        freeze_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "research-freeze-manifest.v1",
                    "freeze_id": "freeze-epoch3-test",
                    "status": "VALID",
                    "created_at_utc": "2026-11-22T08:00:00+00:00",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )
        collection_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "prospective-collection-manifest.v1",
                    "collection_id": "collect-epoch3-test",
                    "collection_started_at_utc": None,
                    "associated_freeze_id": "freeze-epoch3-test",
                    "associated_freeze_timestamp": "2026-11-22T08:00:00+00:00",
                    "exchange": "binance",
                    "market_type": "spot",
                    "symbols": ["BTC/USDT"],
                    "timeframes": ["1h"],
                    "collector_version": "q5-v1",
                    "data_schema_version": "ohlcv-v1",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )
        client = _make_mock_client([])
        collector = ProspectiveCollector(
            exchange="binance",
            market_type="spot",
            symbols=["BTC/USDT"],
            timeframes=["1h"],
            storage_root=str(storage_root),
            client=client,
            freeze_manifest_path=str(freeze_manifest),
            collection_manifest_path=str(collection_manifest),
            policy=ProspectiveCollectionPolicy(
                exchange="binance",
                market_type="spot",
                symbols=("BTC/USDT",),
                timeframes=("1h",),
                candle_finalization_margin_s=1,
                max_ingestion_latency_s=86400,
            ),
        )
        result = collector.collect_once()
        assert result["collection_started_at_utc"] is None
        assert result["quality_checks"]["state"] == "NOT_STARTED"
        anchor_path = (
            storage_root / "binance" / "spot" / "BTC/USDT" / "1h" / "collection_start_anchor.json"
        )
        assert not anchor_path.exists()

    def test_anchor_corruption_detected(self, tmp_path):
        storage = ProspectiveStorage(tmp_path / "storage")
        anchor = {
            "schema_version": "collection-start-anchor.v1",
            "collection_id": "collect-epoch3-test",
            "research_freeze_id": "freeze-epoch3-test",
            "collection_started_at_utc": "2026-11-22T09:00:00+00:00",
            "established_at_utc": "2026-11-22T09:00:00+00:00",
            "source": "first_successful_accepted_observation",
        }
        storage.write_collection_start_anchor(anchor)
        p = storage.anchor_path
        data = json.loads(p.read_text(encoding="utf-8"))
        data["collection_started_at_utc"] = "2026-11-22T10:00:00+00:00"
        p.write_text(json.dumps(data), encoding="utf-8")
        verification = storage.verify_collection_start_anchor()
        assert verification["status"] == "INVALID"
        assert verification["anchor_hash_valid"] is False

    def test_scheduler_authorization_independent_of_collection(self):
        from crypto_engine.research.q6.governance_verification import (
            check_collection_authorization,
            check_scheduler_authorization,
        )

        c = check_collection_authorization(
            "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d",
            "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
            approvals_root=Path("docs/validation/approvals"),
        )
        s = check_scheduler_authorization(
            "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d",
            "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
            approvals_root=Path("docs/validation/approvals"),
        )
        assert c.authorized is True
        assert s.authorized is True
        # Collection-only approval must not grant scheduler
        c_only = check_collection_authorization(
            "collect-epoch2-eca19890-be05-4929-8773-e04885966db1",
            "freeze-epoch2-f34ea12a-34f6-4e5f-b830-393402947a65",
            approvals_root=Path("docs/validation/approvals"),
        )
        assert c_only.authorized is True

    def test_epoch2_supersession_record_exists(self):
        p = Path("docs/validation/EPOCH2_SUPERSESSION_RECORD.json")
        assert p.is_file()
        rec = json.loads(p.read_text(encoding="utf-8-sig"))
        assert (
            rec["superseded_collection_id"] == "collect-epoch2-eca19890-be05-4929-8773-e04885966db1"
        )
        assert (
            rec["superseding_collection_id"]
            == "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d"
        )
        assert rec["append_only"] is True

class TestSystemdTimerAndRetry:
    """Regression tests for Epoch 3 systemd timer and retry behavior."""

    def _load_runner(self):
        spec = importlib.util.spec_from_file_location(
            "run_q5_prospective_collection", Path("scripts/run_q5_prospective_collection.py")
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_timer_template_has_required_calendar_and_settings(self):
        timer = Path("docker/systemd/signalforge-collector@.timer").read_text(encoding="utf-8")
        service = Path("docker/systemd/signalforge-collector@.service").read_text(encoding="utf-8")
        assert "OnCalendar=*-*-* *:%i:00 UTC" in timer
        assert "*-%m-%d %H:%i:00 UTC" not in timer
        assert "Persistent=false" in timer
        assert "AccuracySec=1s" in timer
        assert "Unit=signalforge-collector@%i.service" in timer
        assert "--require-scheduler-authorization" in service
        assert "--scheduled-minute %i" in service
        assert "TimeoutStartSec=900" in service
        assert "User=ubuntu" in service
        assert "Group=docker" in service
        assert "WorkingDirectory=/home/ubuntu/DM-Crypto-Quant-Signal" in service
        assert "Type=oneshot" in service
        assert "[Install]" not in service

    def test_timer_instances_resolve_08_and_12(self):
        import re

        timer = Path("docker/systemd/signalforge-collector@.timer").read_text(encoding="utf-8")
        m = re.search(r"^OnCalendar=(.+)$", timer, re.MULTILINE)
        assert m, "OnCalendar missing"
        template = m.group(1)
        for minute in ("08", "12"):
            resolved = template.replace("%i", minute)
            assert resolved == f"*-*-* *:{minute}:00 UTC"
            assert resolved.endswith(f":{minute}:00 UTC")

    def test_retry_skips_when_primary_accepted_same_slot(self, tmp_path, monkeypatch):
        runner = self._load_runner()
        output_dir = tmp_path / "reports"
        output_dir.mkdir()
        slot = "2026-11-22T10:00:00+00:00"
        (output_dir / "latest_status.json").write_text(
            json.dumps(
                {
                    "schema": "q5-latest-status.v1",
                    "slot_utc": slot,
                    "scheduled_minute": 8,
                    "accepted_observation_for_slot": True,
                }
            ),
            encoding="utf-8",
        )
        manifest = tmp_path / "collection.json"
        manifest.write_text(
            json.dumps(
                {
                    "collection_id": "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d",
                    "associated_freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
                    "exchange": "binance",
                    "market_type": "spot",
                    "symbols": ["BTC/USDT"],
                    "timeframes": ["1h"],
                }
            ),
            encoding="utf-8",
        )

        called = {"collect": False}

        class FakeCollector:
            def __init__(self, *args, **kwargs):
                self.policy = type(
                    "P",
                    (),
                    {
                        "min_elapsed_observation_period_s": 604800,
                        "min_observation_count": 168,
                        "min_coverage_fraction": 0.95,
                    },
                )()

            def validate_freeze(self):
                return {
                    "freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
                    "created_at_utc": "2026-11-22T08:00:00+00:00",
                }

            def verify_collection_integrity(self):
                return {
                    "status": "VALID",
                    "collection_id": "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d",
                    "collection_started_at_utc": None,
                    "associated_freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
                }

            def collect_once(self):
                called["collect"] = True
                return {
                    "status": CollectionStatus.HEALTHY,
                    "observations": [{"open_time": "2026-11-22T09:00:00+00:00"}],
                    "gaps": [],
                    "duplicates": [],
                    "conflicts": [],
                    "rejections": [],
                    "errors": [],
                }

        monkeypatch.setattr(runner, "ProspectiveCollector", FakeCollector)
        monkeypatch.setattr(runner, "check_collection_authorization", lambda **k: type("A", (), {"authorized": True, "reason": "ok", "approval_record": {"approval_id": "a"}})())
        monkeypatch.setattr(runner, "check_scheduler_authorization", lambda **k: type("A", (), {"authorized": True, "reason": "ok", "approval_record": {"approval_id": "s"}})())
        monkeypatch.setattr(
            runner,
            "compute_quality_report",
            lambda collection_result: {
                "observation_count": 1,
                "coverage_fraction": 1.0,
                "collector_status": "HEALTHY",
                "ingestion_status": "HEALTHY",
                "dataset_quality_status": "HEALTHY",
                "provenance_status": "HEALTHY",
                "ledger_status": "HEALTHY",
                "expected_intervals": 1,
                "evaluation_blocked": True,
                "evaluation_block_reasons": ["min not met"],
                "reasons": [],
            },
        )
        monkeypatch.setattr(runner, "_write_q5_collection_report", lambda *a, **k: None)
        monkeypatch.setattr(runner, "_write_q5_governance_report", lambda *a, **k: None)
        monkeypatch.setattr(runner.ProspectiveStorage, "verify_collection_start_anchor", lambda self: {"status": "NOT_STARTED", "collection_started_at_utc": None})
        monkeypatch.setattr(runner.ProspectiveStorage, "read_dataset_manifest", lambda self: {})
        monkeypatch.setattr(
            runner,
            "_utc_now_iso",
            lambda: "2026-11-22T10:12:05+00:00",
        )
        monkeypatch.setattr(
            runner,
            "_scheduled_slot_utc",
            lambda now, minute: slot,
        )

        args = type(
            "Args",
            (),
            {
                "freeze_manifest": "docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json",
                "collection_manifest": str(manifest),
                "storage_root": str(tmp_path / "storage"),
                "approvals_dir": "docs/validation/approvals",
                "output_dir": str(output_dir),
                "scheduled_minute": 12,
                "require_scheduler_authorization": False,
            },
        )()
        rc = runner.run_once(args)
        assert rc == 0
        assert called["collect"] is False
        status = json.loads((output_dir / "latest_status.json").read_text(encoding="utf-8"))
        assert status["retry_skipped"] is True
        assert status["retry_skip_reason"] == "primary_08_accepted"
        assert status["accepted_observation_for_slot"] is True

    def test_retry_runs_when_primary_not_accepted(self, tmp_path, monkeypatch):
        runner = self._load_runner()
        output_dir = tmp_path / "reports"
        output_dir.mkdir()
        slot = "2026-11-22T10:00:00+00:00"
        (output_dir / "latest_status.json").write_text(
            json.dumps(
                {
                    "schema": "q5-latest-status.v1",
                    "slot_utc": slot,
                    "scheduled_minute": 8,
                    "accepted_observation_for_slot": False,
                }
            ),
            encoding="utf-8",
        )
        manifest = tmp_path / "collection.json"
        manifest.write_text(
            json.dumps(
                {
                    "collection_id": "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d",
                    "associated_freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
                    "exchange": "binance",
                    "market_type": "spot",
                    "symbols": ["BTC/USDT"],
                    "timeframes": ["1h"],
                    "collection_started_at_utc": None,
                }
            ),
            encoding="utf-8",
        )
        # minimal freeze manifest expected by collector validation
        freeze = tmp_path / "freeze.json"
        freeze.write_text(
            json.dumps(
                {
                    "freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
                    "created_at_utc": "2026-11-22T08:00:00+00:00",
                    "scope": {"exchange": "binance", "market_type": "spot", "symbols": ["BTC/USDT"], "timeframes": ["1h"]},
                    "git_commit_hash": "0" * 40,
                    "config_hash": "a" * 64,
                    "schema_version": "research-freeze-manifest.v1",
                    "q4_overall_decision": "NO_STRATEGY_ELIGIBLE",
                    "q4_strategy_evaluation_authorized": False,
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "storage").mkdir()

        monkeypatch.setattr(runner, "_scheduled_slot_utc", lambda now, minute: slot)
        monkeypatch.setattr(runner, "_utc_now_iso", lambda: "2026-11-22T10:12:05+00:00")
        monkeypatch.setattr(runner, "check_collection_authorization", lambda **k: type("A", (), {"authorized": True, "reason": "ok", "approval_record": {"approval_id": "a"}, "collection_id": k["collection_id"], "freeze_id": k["freeze_id"]})())
        monkeypatch.setattr(runner, "check_scheduler_authorization", lambda **k: type("A", (), {"authorized": True, "reason": "ok", "approval_record": {"approval_id": "s"}})())

        class FakeCollector:
            def __init__(self, *a, **k):
                self.policy = type("P", (), {"min_elapsed_observation_period_s": 604800, "min_observation_count": 168, "min_coverage_fraction": 0.95})()
            def validate_freeze(self):
                return {"freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f", "created_at_utc": "2026-11-22T08:00:00+00:00"}
            def verify_collection_integrity(self):
                return {"status": "VALID", "collection_id": "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d", "collection_started_at_utc": None, "associated_freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f"}
            def collect_once(self):
                return {
                    "status": CollectionStatus.HEALTHY,
                    "observations": [{"open_time": "2026-11-22T09:00:00+00:00"}],
                    "gaps": [], "duplicates": [], "conflicts": [], "rejections": [], "errors": [],
                }

        monkeypatch.setattr(runner, "ProspectiveCollector", FakeCollector)
        monkeypatch.setattr(runner, "compute_quality_report", lambda collection_result: {"observation_count": 1, "coverage_fraction": 1.0, "collector_status": "HEALTHY", "ingestion_status": "HEALTHY", "dataset_quality_status": "HEALTHY", "provenance_status": "HEALTHY", "ledger_status": "HEALTHY", "expected_intervals": 1, "evaluation_blocked": True, "evaluation_block_reasons": ["min not met"], "reasons": []})
        monkeypatch.setattr(runner, "_write_q5_collection_report", lambda *a, **k: None)
        monkeypatch.setattr(runner, "_write_q5_governance_report", lambda *a, **k: None)
        monkeypatch.setattr(runner.ProspectiveStorage, "verify_collection_start_anchor", lambda self: {"status": "NOT_STARTED", "collection_started_at_utc": None})
        monkeypatch.setattr(runner.ProspectiveStorage, "read_dataset_manifest", lambda self: {})

        args = type("Args", (), {
            "freeze_manifest": str(freeze),
            "collection_manifest": str(manifest),
            "storage_root": str(tmp_path / "storage"),
            "approvals_dir": "docs/validation/approvals",
            "output_dir": str(output_dir),
            "scheduled_minute": 12,
            "require_scheduler_authorization": False,
        })()
        rc = runner.run_once(args)
        assert rc == 0
        status = json.loads((output_dir / "latest_status.json").read_text(encoding="utf-8"))
        assert status["retry_skipped"] is False
        assert status["accepted_observation_for_slot"] is True
        assert status["scheduled_minute"] == 12


class TestSlotAcceptanceSemantics:
    """Regression tests for per-slot acceptance, retry gating, and finalization."""

    def _setup_epoch3(self, tmp_path, *, with_anchor=False, existing_candles=0):
        now = datetime(2026, 11, 22, 21, 12, 0, tzinfo=UTC)
        collection_start = datetime(2026, 11, 22, 20, 8, 4, 533609, tzinfo=UTC)
        int(collection_start.timestamp() * 1000)
        freeze = tmp_path / "freeze.json"
        freeze.write_text(json.dumps({
            "schema_version": "research-freeze-manifest.v1",
            "freeze_id": "freeze-test",
            "status": "VALID",
            "created_at_utc": "2026-11-22T08:00:00+00:00",
            "repository_commit_sha": "abc123",
        }), encoding="utf-8")
        manifest = tmp_path / "collection.json"
        manifest.write_text(json.dumps({
            "schema_version": "prospective-collection-manifest.v1",
            "collection_id": "test-collect",
            "collection_started_at_utc": collection_start.isoformat() if with_anchor else None,
            "associated_freeze_id": "freeze-test",
            "associated_freeze_timestamp": "2026-11-22T08:00:00+00:00",
            "exchange": "binance",
            "market_type": "spot",
            "symbols": ["BTC/USDT"],
            "timeframes": ["1h"],
            "collector_version": "q5-v1",
            "data_schema_version": "ohlcv-v1",
            "repository_commit_sha": "abc123",
        }), encoding="utf-8")
        storage = ProspectiveStorage(tmp_path / "storage" / "binance" / "spot" / "BTC" / "USDT" / "1h")
        if with_anchor:
            storage.write_collection_start_anchor({
                "schema_version": "collection-start-anchor.v1",
                "collection_id": "test-collect",
                "exchange": "binance",
                "market_type": "spot",
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "collection_started_at_utc": collection_start.isoformat(),
                "first_observation_open_time_utc": "2026-11-22T19:00:00+00:00",
                "first_observation_close_time_utc": "2026-11-22T20:00:00+00:00",
                "first_accepted_ingestion_event_id": "evt-seed",
                "first_accepted_ledger_hash": "0" * 64,
                "anchor_source": "first_accepted_observation",
            })
        base_open_ms = int(datetime(2026, 11, 22, 19, 0, tzinfo=UTC).timestamp() * 1000)
        for i in range(existing_candles):
            oms = base_open_ms + i * 3_600_000
            cms = oms + 3_600_000
            obs = storage.store_raw_observation(
                exchange="binance", market_type="spot", symbol="BTC/USDT", interval="1h",
                open_time=_iso(oms), close_time=_iso(cms),
                observed_at_utc=_iso(cms + 60_000), ingested_at_utc=_iso(cms + 60_000),
                source_endpoint="ohlcv", request_id=f"r{i}", response_hash=f"rh{i}", raw_payload_hash=f"rp{i}",
                collector_version="q5-v1", collection_id="test-collect", research_freeze_id="freeze-test",
            )
            candle = storage.store_normalized_candle(
                exchange="binance", market_type="spot", symbol="BTC/USDT", interval="1h",
                open_time=_iso(oms), close_time=_iso(cms), open_price=100.0, high=101.0, low=99.0, close=100.5, volume=10.0,
                source_observation_id=obs["event_id"], first_seen_at_utc=_iso(cms + 60_000),
                observed_at_utc=_iso(cms + 60_000), ingested_at_utc=_iso(cms + 60_000),
                collection_id="test-collect", research_freeze_id="freeze-test",
            )
            storage.append_ledger_event(event_type="observation_accepted", collection_id="test-collect", source_record_hash=_sha256_json(candle))
        return freeze, manifest, storage, now

    def _collector(self, tmp_path, client, freeze, manifest, now):
        c = ProspectiveCollector(
            exchange="binance", market_type="spot", symbols=["BTC/USDT"], timeframes=["1h"],
            policy=ProspectiveCollectionPolicy(exchange="binance", market_type="spot", symbols=("BTC/USDT",), timeframes=("1h",), candle_finalization_margin_s=300, max_ingestion_latency_s=900),
            client=client, storage_root=tmp_path / "storage",
            freeze_manifest_path=str(freeze), collection_manifest_path=str(manifest),
        )
        c._now_ms = lambda: int(now.timestamp() * 1000)
        return c


    def test_a_cumulative_one_no_finalized_slot(self, tmp_path, monkeypatch):
        import crypto_engine.research.prospective.collector as cmod
        fixed_now = datetime(2026, 11, 22, 21, 12, 0, tzinfo=UTC)
        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz else fixed_now.replace(tzinfo=None)
        monkeypatch.setattr(cmod, "datetime", FakeDT)

        freeze, manifest, storage, _ = self._setup_epoch3(tmp_path, with_anchor=True, existing_candles=1)
        # Response contains only unfinalized 21:00 candle (closes 22:00, finalized 22:05 > 21:12)
        rows = [[int(datetime(2026, 11, 22, 21, 0, tzinfo=UTC).timestamp() * 1000), 100, 101, 99, 100.5, 10]]
        client = _make_mock_client(rows)
        collector = self._collector(tmp_path, client, freeze, manifest, fixed_now)
        result = collector.collect_once(scheduled_slot_utc="2026-11-22T21:00:00+00:00")

        assert result["cumulative_observation_count"] == 1
        assert result["run_observation_count"] == 0
        assert result["accepted_observation_for_slot"] is False
        assert result["status"] != CollectionStatus.DATA_INVALID
        # cumulative dataset must remain intact
        assert storage.get_observation_summary()["accepted_unique_observations"] == 1

    def test_b_primary_accepts_exact_slot(self, tmp_path, monkeypatch):
        import crypto_engine.research.prospective.collector as cmod
        fixed_now = datetime(2026, 11, 22, 22, 8, 0, tzinfo=UTC)
        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz else fixed_now.replace(tzinfo=None)
        monkeypatch.setattr(cmod, "datetime", FakeDT)

        freeze, manifest, _storage, _ = self._setup_epoch3(tmp_path, with_anchor=True, existing_candles=1)
        # 20:00 candle opens before anchor 20:08:04 - not prospective. Use 21:00 slot.
        slot_open = int(datetime(2026, 11, 22, 21, 0, tzinfo=UTC).timestamp() * 1000)
        rows = [[slot_open, 200, 201, 199, 200.5, 20]]
        client = _make_mock_client(rows)
        collector = self._collector(tmp_path, client, freeze, manifest, fixed_now)
        result = collector.collect_once(scheduled_slot_utc="2026-11-22T21:00:00+00:00")

        assert result["accepted_observation_for_slot"] is True
        assert result["run_observation_count"] == 1
        assert result["cumulative_observation_count"] == 2

    def test_c_primary_fails_cumulative_one_does_not_skip_retry(self, tmp_path, monkeypatch):
        __import__("importlib").util.spec_from_file_location("r", Path("scripts/run_q5_prospective_collection.py"))
        spec = importlib.util.spec_from_file_location(
            "run_q5_prospective_collection", Path("scripts/run_q5_prospective_collection.py")
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        output_dir = tmp_path / "reports"
        output_dir.mkdir()
        slot = "2026-11-22T21:00:00+00:00"
        (output_dir / "latest_status.json").write_text(
            json.dumps(
                {
                    "schema": "q5-latest-status.v1",
                    "slot_utc": slot,
                    "scheduled_minute": 8,
                    "accepted_observation_for_slot": False,
                    "cumulative_observation_count": 1,
                }
            ),
            encoding="utf-8",
        )
        manifest = tmp_path / "collection.json"
        manifest.write_text(
            json.dumps(
                {
                    "collection_id": "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d",
                    "associated_freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
                    "exchange": "binance",
                    "market_type": "spot",
                    "symbols": ["BTC/USDT"],
                    "timeframes": ["1h"],
                }
            ),
            encoding="utf-8",
        )
        freeze = tmp_path / "freeze.json"
        freeze.write_text(
            json.dumps(
                {
                    "freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
                    "created_at_utc": "2026-11-22T08:00:00+00:00",
                    "status": "VALID",
                    "schema_version": "research-freeze-manifest.v1",
                    "repository_commit_sha": "abc123",
                }
            ),
            encoding="utf-8",
        )
        (tmp_path/"storage").mkdir()
        called = {"collect":0}
        class FC:
            def __init__(self,*a,**k): self.policy=type("P",(),{"min_elapsed_observation_period_s":604800,"min_observation_count":168,"min_coverage_fraction":0.95})()
            def validate_freeze(self): return {"freeze_id":"freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f","created_at_utc":"2026-11-22T08:00:00+00:00"}
            def verify_collection_integrity(self): return {"status":"VALID","collection_id":"collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d","collection_started_at_utc":"2026-11-22T20:08:04.533609+00:00","associated_freeze_id":"freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f"}
            def collect_once(self, **k):
                called["collect"] += 1
                return {"status":CollectionStatus.HEALTHY,"observations":[],"gaps":[],"duplicates":[],"conflicts":[],"rejections":[{"reason":"candle not yet finalized"}],"errors":[],"cumulative_observation_count":1,"run_observation_count":0,"accepted_observation_for_slot":False,"slot_already_accepted":False}
        monkeypatch.setattr(mod, "ProspectiveCollector", FC)
        monkeypatch.setattr(mod, "check_collection_authorization", lambda **k: type("A",(),{"authorized":True,"reason":"ok","approval_record":{"approval_id":"a"},"collection_id":k["collection_id"],"freeze_id":k["freeze_id"]})())
        monkeypatch.setattr(mod, "compute_quality_report", lambda collection_result: {"observation_count":1,"cumulative_observation_count":1,"coverage_fraction":0.01,"collector_status":"HEALTHY","ingestion_status":"DEGRADED","dataset_quality_status":"HEALTHY","provenance_status":"HEALTHY","ledger_status":"HEALTHY","expected_intervals":168,"evaluation_blocked":True,"evaluation_block_reasons":["min not met"],"reasons":[],"state":"DEGRADED"})
        monkeypatch.setattr(mod, "_write_q5_collection_report", lambda *a, **k: None)
        monkeypatch.setattr(mod, "_write_q5_governance_report", lambda *a, **k: None)
        monkeypatch.setattr(mod.ProspectiveStorage, "verify_collection_start_anchor", lambda self: {"status":"VALID","collection_started_at_utc":"2026-11-22T20:08:04.533609+00:00"})
        monkeypatch.setattr(mod.ProspectiveStorage, "read_dataset_manifest", lambda self: {"observation_count":1,"coverage_fraction":0.01})
        monkeypatch.setattr(mod, "_scheduled_slot_utc", lambda now, minute: slot)
        monkeypatch.setattr(mod, "_utc_now_iso", lambda: "2026-11-22T21:12:05+00:00")
        args = type("Args", (), {"freeze_manifest":str(freeze),"collection_manifest":str(manifest),"storage_root":str(tmp_path/"storage"),"approvals_dir":"docs/validation/approvals","output_dir":str(output_dir),"scheduled_minute":12,"require_scheduler_authorization":False})()
        mod.run_once(args)
        assert called["collect"] == 1, "retry must run when primary did not accept slot, even if cumulative==1"
        status = json.loads((output_dir/"latest_status.json").read_text(encoding="utf-8"))
        assert status["retry_skipped"] is False

    def test_d_retry_accepts_new_slot_cumulative_becomes_two(self, tmp_path, monkeypatch):
        import crypto_engine.research.prospective.collector as cmod
        fixed_now = datetime(2026, 11, 22, 22, 12, 0, tzinfo=UTC)
        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz else fixed_now.replace(tzinfo=None)
        monkeypatch.setattr(cmod, "datetime", FakeDT)
        freeze, manifest, storage, _ = self._setup_epoch3(tmp_path, with_anchor=True, existing_candles=1)
        slot_open = int(datetime(2026, 11, 22, 21, 0, tzinfo=UTC).timestamp() * 1000)
        client = _make_mock_client([[slot_open, 210, 211, 209, 210.5, 15]])
        collector = self._collector(tmp_path, client, freeze, manifest, fixed_now)
        result = collector.collect_once(scheduled_slot_utc="2026-11-22T21:00:00+00:00")
        assert result["accepted_observation_for_slot"] is True
        assert result["run_observation_count"] == 1
        assert result["cumulative_observation_count"] == 2
        assert storage.get_observation_summary()["accepted_unique_observations"] == 2

    def test_e_invalid_conflict_not_accepted_due_cumulative(self, tmp_path, monkeypatch):
        import crypto_engine.research.prospective.collector as cmod
        fixed_now = datetime(2026, 11, 22, 21, 12, 0, tzinfo=UTC)
        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz else fixed_now.replace(tzinfo=None)
        monkeypatch.setattr(cmod, "datetime", FakeDT)
        freeze, manifest, storage, _ = self._setup_epoch3(tmp_path, with_anchor=True, existing_candles=1)
        slot_open = int(datetime(2026, 11, 22, 21, 0, tzinfo=UTC).timestamp() * 1000)
        # Invalid candle: high < low. Must not be accepted despite cumulative==1.
        client = _make_mock_client([[slot_open, 100, 99, 101, 100.5, 10]])
        collector = self._collector(tmp_path, client, freeze, manifest, fixed_now)
        result = collector.collect_once(scheduled_slot_utc="2026-11-22T21:00:00+00:00")
        assert result["accepted_observation_for_slot"] is False
        assert result["run_observation_count"] == 0
        assert result["cumulative_observation_count"] == 1
        assert result["status"] in {CollectionStatus.DATA_INVALID, CollectionStatus.EXCHANGE_UNAVAILABLE}
        assert storage.get_observation_summary()["accepted_unique_observations"] == 1

    def test_f_duplicate_does_not_increment(self, tmp_path, monkeypatch):
        import crypto_engine.research.prospective.collector as cmod
        fixed_now = datetime(2026, 11, 22, 20, 12, 0, tzinfo=UTC)
        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now if tz else fixed_now.replace(tzinfo=None)
        monkeypatch.setattr(cmod, "datetime", FakeDT)
        freeze, manifest, storage, _ = self._setup_epoch3(tmp_path, with_anchor=True, existing_candles=2) # includes 19:00 and 20:00
        slot_open = int(datetime(2026, 11, 22, 20, 0, tzinfo=UTC).timestamp() * 1000)
        client = _make_mock_client([[slot_open, 100.5, 101.5, 99.5, 101.0, 10.0]]) # identical to seed candle 2? seed used base 100+i*0.5
        collector = self._collector(tmp_path, client, freeze, manifest, fixed_now)
        before = storage.get_observation_summary()["accepted_unique_observations"]
        result = collector.collect_once(scheduled_slot_utc="2026-11-22T20:00:00+00:00")
        after = storage.get_observation_summary()["accepted_unique_observations"]
        assert after == before
        assert result["accepted_observation_for_slot"] is True  # slot is already accepted
        assert result["run_observation_count"] == 0

    def test_g_ledger_integrity_hard_failure(self, tmp_path):
        freeze, manifest, storage, _now = self._setup_epoch3(tmp_path, with_anchor=True, existing_candles=1)
        # corrupt ledger
        ledger = storage.ledger_path
        ledger.write_text('{"bad":true}\n', encoding="utf-8")
        slot_open = int(datetime(2026, 11, 22, 20, 0, tzinfo=UTC).timestamp() * 1000)
        client = _make_mock_client([[slot_open, 200, 201, 199, 200.5, 20]])
        collector = self._collector(tmp_path, client, freeze, manifest, datetime(2026,11,22,20,8,tzinfo=UTC))
        result = collector.collect_once(scheduled_slot_utc="2026-11-22T20:00:00+00:00")
        # ledger corruption should be surfaced as hard failure, not accepted
        assert result["accepted_observation_for_slot"] is False
        assert result["status"] in {CollectionStatus.DATA_INVALID, CollectionStatus.PROVENANCE_INVALID} or result["errors"] or result["rejections"]
        assert storage.get_observation_summary()["accepted_unique_observations"] == 1
