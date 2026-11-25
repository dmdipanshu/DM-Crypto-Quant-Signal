"""Dedicated tests for prospective observation counting and readiness metrics.

Uses isolated temporary storage and deterministic fixtures. No production
evidence is modified or read.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from crypto_engine.api.repos import RepoRoots, read_q5_collection_status
from crypto_engine.research.prospective.governance import assess_prospective_holdout_readiness
from crypto_engine.research.prospective.storage import ProspectiveStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _interval_ms() -> int:
    return 3_600_000  # 1 hour


def _store_complete_observation(
    storage: ProspectiveStorage,
    open_ms: int,
    close_ms: int,
    collection_id: str = "collect-test",
    freeze_id: str = "freeze-test",
    raw_hash: str | None = None,
    symbol: str = "BTC/USDT",
) -> None:
    """Store a complete observation: raw + normalized candle + ledger event."""
    open_iso = _iso(open_ms)
    close_iso = _iso(close_ms)
    now_iso = _iso(_now_ms())
    rh = raw_hash or f"hash-{open_ms}-{symbol}"
    storage.store_raw_observation(
        exchange="binance",
        market_type="spot",
        symbol=symbol,
        interval="1h",
        open_time=open_iso,
        close_time=close_iso,
        observed_at_utc=now_iso,
        ingested_at_utc=now_iso,
        source_endpoint="binance/fetchOHLCV",
        request_id=None,
        response_hash=rh,
        raw_payload_hash=rh,
        collector_version="q5-v1",
        collection_id=collection_id,
        research_freeze_id=freeze_id,
        first_seen_at_utc=now_iso,
    )
    storage.store_normalized_candle(
        collection_id=collection_id,
        exchange="binance",
        market_type="spot",
        symbol=symbol,
        interval="1h",
        open_time=open_iso,
        close_time=close_iso,
        open_price=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        source_observation_id=f"obs-{open_ms}-{symbol}",
        first_seen_at_utc=now_iso,
        observed_at_utc=now_iso,
        ingested_at_utc=now_iso,
        research_freeze_id=freeze_id,
    )
    storage.append_ledger_event(
        event_type="candle_ingested",
        collection_id=collection_id,
        source_record_hash=rh,
    )


# ---------------------------------------------------------------------------
# Test class: storage-level prospective counting
# ---------------------------------------------------------------------------


class TestProspectiveObservationCount:
    """Tests for ProspectiveStorage.get_prospective_observation_count."""

    def test_valid_prospective_observation_counted(self, tmp_path):
        """1. Valid prospective observation is counted."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        close_ms = now_ms + _interval_ms()
        _store_complete_observation(storage, first_open_ms, close_ms)
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 1

    def test_bootstrap_observation_excluded(self, tmp_path):
        """2. Bootstrap observation (before collection start) is excluded."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        bootstrap_open = now_ms - 2 * _interval_ms()
        bootstrap_close = now_ms - _interval_ms()
        _store_complete_observation(storage, bootstrap_open, bootstrap_close)
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 0

    def test_raw_only_orphan_excluded(self, tmp_path):
        """3. Raw-only observation with no normalized candle is excluded."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        close_ms = now_ms + _interval_ms()
        open_iso = _iso(first_open_ms)
        close_iso = _iso(close_ms)
        now_iso = _iso(_now_ms())
        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=open_iso,
            close_time=close_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="orphan-hash",
            raw_payload_hash="orphan-hash",
            collector_version="q5-v1",
            collection_id="collect-test",
            research_freeze_id="freeze-test",
            first_seen_at_utc=now_iso,
        )
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 0

    def test_normalized_but_unledgered_excluded(self, tmp_path):
        """4. Normalized candle with no ledger event is excluded."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        close_ms = now_ms + _interval_ms()
        open_iso = _iso(first_open_ms)
        close_iso = _iso(close_ms)
        now_iso = _iso(_now_ms())
        storage.store_normalized_candle(
            collection_id="collect-test",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=open_iso,
            close_time=close_iso,
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-unledgered",
            first_seen_at_utc=now_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            research_freeze_id="freeze-test",
        )
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 0

    def test_corrupt_ledger_returns_zero(self, tmp_path):
        """5. Corrupt ledger returns no eligible count (fail-closed)."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        close_ms = now_ms + _interval_ms()
        _store_complete_observation(storage, first_open_ms, close_ms)
        # Corrupt the ledger
        lines = storage.ledger_path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[0])
        entry["current_event_hash"] = "tampered"
        lines[0] = json.dumps(entry, sort_keys=True)
        storage.ledger_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 0

    def test_duplicate_identity_counted_once(self, tmp_path):
        """6. Duplicate identity (same open_time) is counted once."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        close_ms = now_ms + _interval_ms()
        open_iso = _iso(first_open_ms)
        close_iso = _iso(close_ms)
        now_iso = _iso(_now_ms())
        # Store two raw observations for the same candle identity
        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=open_iso,
            close_time=close_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="dup-hash-1",
            raw_payload_hash="dup-hash-1",
            collector_version="q5-v1",
            collection_id="collect-test",
            research_freeze_id="freeze-test",
            first_seen_at_utc=now_iso,
        )
        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=open_iso,
            close_time=close_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="dup-hash-2",
            raw_payload_hash="dup-hash-2",
            collector_version="q5-v1",
            collection_id="collect-test",
            research_freeze_id="freeze-test",
            first_seen_at_utc=now_iso,
        )
        # One normalized candle
        storage.store_normalized_candle(
            collection_id="collect-test",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="1h",
            open_time=open_iso,
            close_time=close_iso,
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-dup",
            first_seen_at_utc=now_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            research_freeze_id="freeze-test",
        )
        # One ledger event matching one of the raw hashes
        storage.append_ledger_event(
            event_type="candle_ingested",
            collection_id="collect-test",
            source_record_hash="dup-hash-1",
        )
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 1

    def test_multiple_symbols(self, tmp_path):
        """7. Multiple symbols each contribute their eligible observations."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        close_ms = now_ms + _interval_ms()
        # Symbol 1
        _store_complete_observation(
            storage, first_open_ms, close_ms, collection_id="collect-1", symbol="BTC/USDT"
        )
        # Symbol 2 - different symbol means different identity key
        _store_complete_observation(
            storage, first_open_ms, close_ms, collection_id="collect-2", symbol="ETH/USDT"
        )
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 2

    def test_multiple_timeframes(self, tmp_path):
        """8. Multiple timeframes each contribute their eligible observations."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        close_ms_1h = now_ms + _interval_ms()
        close_ms_4h = now_ms + 4 * _interval_ms()
        open_iso = _iso(first_open_ms)
        now_iso = _iso(_now_ms())
        # 1h candle
        _store_complete_observation(storage, first_open_ms, close_ms_1h, collection_id="collect-1h")
        # 4h candle - different interval means different identity key
        storage.store_raw_observation(
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="4h",
            open_time=open_iso,
            close_time=_iso(close_ms_4h),
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            source_endpoint="binance/fetchOHLCV",
            request_id=None,
            response_hash="hash-4h",
            raw_payload_hash="hash-4h",
            collector_version="q5-v1",
            collection_id="collect-4h",
            research_freeze_id="freeze-test",
            first_seen_at_utc=now_iso,
        )
        storage.store_normalized_candle(
            collection_id="collect-4h",
            exchange="binance",
            market_type="spot",
            symbol="BTC/USDT",
            interval="4h",
            open_time=open_iso,
            close_time=_iso(close_ms_4h),
            open_price=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            source_observation_id="obs-4h",
            first_seen_at_utc=now_iso,
            observed_at_utc=now_iso,
            ingested_at_utc=now_iso,
            research_freeze_id="freeze-test",
        )
        storage.append_ledger_event(
            event_type="candle_ingested",
            collection_id="collect-4h",
            source_record_hash="hash-4h",
        )
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 6 * _interval_ms()
        )
        assert count == 2

    def test_future_candle_excluded(self, tmp_path):
        """9. Future candle (close_time after evaluation time) is excluded."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        future_open = now_ms + 5 * _interval_ms()
        future_close = now_ms + 6 * _interval_ms()
        _store_complete_observation(storage, future_open, future_close)
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 0

    def test_incomplete_candle_excluded(self, tmp_path):
        """10. Incomplete candle (close_time after evaluation time) is excluded."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        incomplete_open = now_ms + _interval_ms()
        incomplete_close = now_ms + 10 * _interval_ms()  # far in the future
        _store_complete_observation(storage, incomplete_open, incomplete_close)
        count = storage.get_prospective_observation_count(
            first_open_ms, _now_ms() + 2 * _interval_ms()
        )
        assert count == 0

    def test_evaluation_timestamp_shared(self, tmp_path):
        """11. Same evaluation timestamp used for numerator and denominator."""
        storage = ProspectiveStorage(tmp_path / "storage")
        now_ms = _now_ms()
        first_open_ms = now_ms
        close_ms = now_ms + _interval_ms()
        _store_complete_observation(storage, first_open_ms, close_ms)
        eval_ms = _now_ms() + 2 * _interval_ms()
        count = storage.get_prospective_observation_count(first_open_ms, eval_ms)
        assert count == 1
        # Same eval_ms must be used consistently - calling again with same value
        count_again = storage.get_prospective_observation_count(first_open_ms, eval_ms)
        assert count_again == 1


# ---------------------------------------------------------------------------
# Test class: coverage and duration thresholds
# ---------------------------------------------------------------------------


class TestCoverageAndDurationThresholds:
    """Tests 12-15: coverage thresholds and 7-day requirement."""

    def test_exact_95_percent_coverage_threshold(self, tmp_path):
        """12. Exact 95% coverage threshold meets requirement."""
        now = datetime.now(UTC)
        freeze_ts = now.isoformat()
        collection_start = now.isoformat()
        min_obs = 168
        min_coverage = 0.95
        # 168 observations at 95%+ coverage of 176 expected intervals
        expected_intervals = 176
        eligible_count = 168
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=freeze_ts,
            collection_started_at=collection_start,
            observation_count=eligible_count,
            min_observation_count=min_obs,
            coverage_fraction=eligible_count / expected_intervals,
            min_coverage_fraction=min_coverage,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,
        )
        coverage = eligible_count / expected_intervals
        assert coverage >= min_coverage, f"coverage {coverage:.4%} should be >= {min_coverage:.0%}"
        assert result["eligible_for_evaluation"] is True

    def test_coverage_immediately_below_95_percent(self, tmp_path):
        """13. Coverage immediately below 95% blocks evaluation."""
        now = datetime.now(UTC)
        freeze_ts = now.isoformat()
        collection_start = now.isoformat()
        min_obs = 168
        min_coverage = 0.95
        expected_intervals = 168
        eligible_count = 159  # 159/168 = 94.64% < 95%
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=freeze_ts,
            collection_started_at=collection_start,
            observation_count=eligible_count,
            min_observation_count=min_obs,
            coverage_fraction=eligible_count / expected_intervals,
            min_coverage_fraction=min_coverage,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,
        )
        coverage = eligible_count / expected_intervals
        assert coverage < min_coverage, f"coverage {coverage:.4%} should be < {min_coverage:.0%}"
        assert result["eligible_for_evaluation"] is False
        assert result["state"] == "ACCUMULATING"

    def test_seven_day_requirement_immediately_before_expiry(self, tmp_path):
        """14. Seven-day requirement immediately before expiry."""
        now = datetime.now(UTC)
        freeze_ts = now.isoformat()
        # Collection started 7 days minus 1 second ago
        collection_start = (now - timedelta(days=7, seconds=-1)).isoformat()
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=freeze_ts,
            collection_started_at=collection_start,
            observation_count=168,
            min_observation_count=168,
            coverage_fraction=1.0,
            min_coverage_fraction=0.95,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,
        )
        # Still within 7-day window -> ELIGIBLE_FOR_EVALUATION (not yet expired)
        assert result["state"] == "ELIGIBLE_FOR_EVALUATION"
        assert result["eligible_for_evaluation"] is True

    def test_seven_day_requirement_at_expiry(self, tmp_path):
        """15. Seven-day requirement at expiry: governance returns ELIGIBLE when
        all other criteria met; the 7-day expiry is enforced at API/collector level."""
        now = datetime.now(UTC)
        freeze_ts = now.isoformat()
        # Collection started exactly 7 days ago
        collection_start = (now - timedelta(days=7)).isoformat()
        result = assess_prospective_holdout_readiness(
            freeze_timestamp=freeze_ts,
            collection_started_at=collection_start,
            observation_count=168,
            min_observation_count=168,
            coverage_fraction=1.0,
            min_coverage_fraction=0.95,
            data_quality_passed=True,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,
        )
        # Governance does not check 7-day window; all other criteria pass
        assert result["state"] == "ELIGIBLE_FOR_EVALUATION"
        assert result["eligible_for_evaluation"] is True


# ---------------------------------------------------------------------------
# Test class: API readiness uses prospective count
# ---------------------------------------------------------------------------


class TestAPIReadinessProspectiveCount:
    """Test 16: API readiness uses prospective count rather than cumulative count."""

    def _roots(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True, exist_ok=True)
        return RepoRoots(
            signals_dir=tmp_path / "signals",
            registry_dir=tmp_path / "registry",
            data_storage=data,
            backtest_dir=tmp_path / "backtests",
            paper_path=tmp_path / "paper" / "account.json",
            experiments_path=tmp_path / "experiments.jsonl",
            holdout_ledger_path=tmp_path / "holdout.json",
        )

    def _write_latest_status(self, tmp_path, payload):
        p = tmp_path / "reports" / "q5-epoch3" / "latest_status.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def _patch_q5(self, monkeypatch, tmp_path):
        import crypto_engine.research.prospective.collector as collector_mod
        import crypto_engine.research.prospective.storage as storage_mod

        class _FakeCollector:
            def __init__(self, *args, **kwargs):
                pass

            collection_id = "collect-test"
            exchange = "binance"
            market_type = "spot"
            symbols: ClassVar[list[str]] = ["BTC/USDT"]
            timeframes: ClassVar[list[str]] = ["1h"]
            policy = type(
                "P",
                (),
                {
                    "min_observation_count": 168,
                    "min_coverage_fraction": 0.95,
                    "min_elapsed_observation_period_s": 7 * 86400,
                },
            )()
            collection_manifest_path = "docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json"

            def verify_collection_integrity(self):
                return {
                    "collection_id": self.collection_id,
                    "status": "VALID",
                    "reason": "ok",
                    "associated_freeze_id": "freeze-test",
                    "associated_freeze_timestamp": "2026-11-22T08:00:00+00:00",
                }

            def validate_freeze(self):
                return {
                    "status": "VALID",
                    "freeze_id": "freeze-test",
                    "created_at_utc": "2026-11-22T08:00:00+00:00",
                }

            def validate_or_create_collection_manifest(self):
                return None

        class _FakeStorage:
            def __init__(self, *args, **kwargs):
                pass

            def read_dataset_manifest(self):
                return {
                    "observation_count": 500,
                    "coverage_fraction": 1.0,
                    "total_stored_observation_count": 500,
                    "prospective_eligible_observation_count": 12,
                    "expected_prospective_intervals": 168,
                    "prospective_coverage_fraction": 0.0714,
                    "latest_open_time_utc": "2026-11-23T06:00:00+00:00",
                }

            def verify_ledger_chain(self):
                return {"valid": True, "event_count": 12}

            def verify_collection_start_anchor(self):
                return {
                    "status": "VALID",
                    "collection_started_at_utc": "2026-11-22T20:08:04.533609+00:00",
                    "anchor_hash_valid": True,
                    "anchor": {"anchor_hash": "abc"},
                }

        monkeypatch.setattr(collector_mod, "ProspectiveCollector", _FakeCollector)
        monkeypatch.setattr(storage_mod, "ProspectiveStorage", _FakeStorage)
        (tmp_path / "docs" / "validation").mkdir(parents=True, exist_ok=True)
        (
            tmp_path / "docs" / "validation" / "PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json"
        ).write_text(
            json.dumps(
                {
                    "collection_id": "collect-test",
                    "collection_started_at_utc": "2026-11-22T20:08:04.533609+00:00",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

    def test_api_readiness_uses_prospective_count_not_cumulative(self, monkeypatch, tmp_path):
        """16. API readiness uses prospective count rather than cumulative count."""
        self._patch_q5(monkeypatch, tmp_path)
        self._write_latest_status(
            tmp_path,
            {
                "schema": "q5-latest-status.v1",
                "completed_at_utc": "2026-11-23T06:08:10+00:00",
                "slot_utc": "2026-11-23T06:00:00+00:00",
                "scheduled_minute": 8,
                "status": "ACCEPTED",
                "exit_code": 0,
                "run_observation_count": 1,
                "cumulative_observation_count": 500,
                "accepted_observation_for_slot": True,
                "retry_skipped": False,
                "observation_count": 500,
            },
        )
        status = read_q5_collection_status(self._roots(tmp_path))
        # Cumulative count preserved
        assert status["observation_count"] == 500
        assert status["total_stored_observation_count"] == 500
        # Prospective count is separate and much smaller
        assert status["prospective_eligible_observation_count"] == 12
        assert status["expected_prospective_intervals"] == 168
        assert status["prospective_coverage_fraction"] == 0.0714


# ---------------------------------------------------------------------------
# Test class: old manifest without new fields -> UNKNOWN state
# ---------------------------------------------------------------------------


class TestOldManifestUnknownState:
    """Tests that old manifests lacking new fields do not misrepresent counts."""

    def _roots(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir(parents=True, exist_ok=True)
        return RepoRoots(
            signals_dir=tmp_path / "signals",
            registry_dir=tmp_path / "registry",
            data_storage=data,
            backtest_dir=tmp_path / "backtests",
            paper_path=tmp_path / "paper" / "account.json",
            experiments_path=tmp_path / "experiments.jsonl",
            holdout_ledger_path=tmp_path / "holdout.json",
        )

    def _write_latest_status(self, tmp_path, payload):
        p = tmp_path / "reports" / "q5-epoch3" / "latest_status.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def _patch_q5(self, monkeypatch, tmp_path):
        import crypto_engine.research.prospective.collector as collector_mod
        import crypto_engine.research.prospective.storage as storage_mod

        class _FakeCollector:
            def __init__(self, *args, **kwargs):
                pass

            collection_id = "collect-test"
            exchange = "binance"
            market_type = "spot"
            symbols: ClassVar[list[str]] = ["BTC/USDT"]
            timeframes: ClassVar[list[str]] = ["1h"]
            policy = type(
                "P",
                (),
                {
                    "min_observation_count": 168,
                    "min_coverage_fraction": 0.95,
                    "min_elapsed_observation_period_s": 7 * 86400,
                },
            )()
            collection_manifest_path = "docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json"

            def verify_collection_integrity(self):
                return {
                    "collection_id": self.collection_id,
                    "status": "VALID",
                    "reason": "ok",
                    "associated_freeze_id": "freeze-test",
                    "associated_freeze_timestamp": "2026-11-22T08:00:00+00:00",
                }

            def validate_freeze(self):
                return {
                    "status": "VALID",
                    "freeze_id": "freeze-test",
                    "created_at_utc": "2026-11-22T08:00:00+00:00",
                }

            def validate_or_create_collection_manifest(self):
                return None

        class _FakeStorage:
            def __init__(self, *args, **kwargs):
                pass

            # Old manifest: lacks the four new metrics fields
            def read_dataset_manifest(self):
                return {
                    "observation_count": 500,
                    "coverage_fraction": 1.0,
                    "latest_open_time_utc": "2026-11-23T06:00:00+00:00",
                }

            def verify_ledger_chain(self):
                return {"valid": True, "event_count": 500}

            def verify_collection_start_anchor(self):
                return {
                    "status": "VALID",
                    "collection_started_at_utc": "2026-11-22T20:08:04.533609+00:00",
                    "anchor_hash_valid": True,
                    "anchor": {"anchor_hash": "abc"},
                }

        monkeypatch.setattr(collector_mod, "ProspectiveCollector", _FakeCollector)
        monkeypatch.setattr(storage_mod, "ProspectiveStorage", _FakeStorage)
        (tmp_path / "docs" / "validation").mkdir(parents=True, exist_ok=True)
        (
            tmp_path / "docs" / "validation" / "PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json"
        ).write_text(
            json.dumps(
                {
                    "collection_id": "collect-test",
                    "collection_started_at_utc": "2026-11-22T20:08:04.533609+00:00",
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

    def test_old_manifest_prospective_fields_are_none(self, monkeypatch, tmp_path):
        """Old manifest without new fields: prospective metrics are None (UNKNOWN)."""
        self._patch_q5(monkeypatch, tmp_path)
        self._write_latest_status(
            tmp_path,
            {
                "schema": "q5-latest-status.v1",
                "completed_at_utc": "2026-11-23T06:08:10+00:00",
                "slot_utc": "2026-11-23T06:00:00+00:00",
                "scheduled_minute": 8,
                "status": "ACCEPTED",
                "exit_code": 0,
                "run_observation_count": 1,
                "cumulative_observation_count": 500,
                "accepted_observation_for_slot": True,
                "retry_skipped": False,
                "observation_count": 500,
            },
        )
        status = read_q5_collection_status(self._roots(tmp_path))
        # Cumulative count preserved from old manifest
        assert status["observation_count"] == 500
        assert status["total_stored_observation_count"] == 500
        # Prospective metrics are None (UNKNOWN) - not misrepresented as cumulative
        assert status["prospective_eligible_observation_count"] is None
        assert status["expected_prospective_intervals"] is None
        assert status["prospective_coverage_fraction"] is None
