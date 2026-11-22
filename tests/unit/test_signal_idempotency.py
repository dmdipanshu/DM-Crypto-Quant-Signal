"""Signal idempotency and determinism regression tests.

Verifies that identical inputs produce identical signal_ids and that
retrying the same evaluation does not create duplicate records.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from crypto_engine.signals.history import SignalHistory
from crypto_engine.signals.policy import PolicyInputs, decide
from crypto_engine.signals.schema import AiContextSummary, CostEstimate


def _base_inputs(**over):
    model = MagicMock()
    model.model_id = "m-1"
    model.threshold_version = "v1"
    model.feature_names = ["f1", "f2"]

    defaults = dict(
        feature_row={"f1": 0.5, "f2": 0.3},
        feature_names=["f1", "f2"],
        model=model,
        model_id="m-1",
        model_version="0.1.0",
        model_artifact_hash="abc123",
        calibrator=None,
        calibration_required=False,
        threshold_version="v1",
        t_long=0.6,
        t_short=0.4,
        require_edge=False,
        expected_edge_bp=None,
        cost=CostEstimate(fee_bp=10.0, slippage_bp=5.0, source="configured"),
        enabled_directions={"LONG", "SHORT", "NEUTRAL", "NO_TRADE"},
        data_quality="validated",
        generated_at=datetime(2024, 1, 10, 0, 0, 0, tzinfo=UTC).isoformat(),
        valid_until=datetime(2024, 1, 10, 1, 0, 0, tzinfo=UTC).isoformat(),
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1h",
        horizon_bars=24,
        regime="",
        blocked_regimes=set(),
        ai=AiContextSummary(status="disabled"),
        ai_required=False,
        dataset_snapshot_id="snap-1",
        levels=None,
    )
    defaults.update(over)
    return PolicyInputs(**defaults)


def test_same_inputs_produce_same_signal_id() -> None:
    """Identical inputs produce identical signal_id (idempotent)."""
    inputs_a = _base_inputs()
    inputs_b = _base_inputs()
    record_a, _trace_a = decide(inputs_a)
    record_b, _trace_b = decide(inputs_b)
    assert record_a.signal_id == record_b.signal_id
    assert record_a.prediction.raw_score == record_b.prediction.raw_score


def test_retry_with_same_inputs_does_not_change_signal_id() -> None:
    """Retrying the same evaluation produces the same signal_id (no duplicates)."""
    inputs = _base_inputs()
    record_first, _trace_first = decide(inputs)
    record_retry, _trace_retry = decide(inputs)
    assert record_first.signal_id == record_retry.signal_id


def test_different_bar_timestamps_produce_different_signal_ids() -> None:
    """Different bar timestamps produce different signal_ids."""
    inputs_10 = _base_inputs(
        generated_at=datetime(2024, 1, 10, 10, 0, 0, tzinfo=UTC).isoformat()
    )
    inputs_12 = _base_inputs(
        generated_at=datetime(2024, 1, 10, 12, 0, 0, tzinfo=UTC).isoformat()
    )
    record_10, _trace_10 = decide(inputs_10)
    record_12, _trace_12 = decide(inputs_12)
    assert record_10.signal_id != record_12.signal_id


def test_different_models_produce_different_signal_ids() -> None:
    """Different model versions produce different signal_ids."""
    inputs_a = _base_inputs(model_id="model-a")
    inputs_b = _base_inputs(model_id="model-b")
    record_a, _trace_a = decide(inputs_a)
    record_b, _trace_b = decide(inputs_b)
    assert record_a.signal_id != record_b.signal_id


# ------------------------------------------------------------------
# Persistence-layer deduplication (SignalHistory.append)
# ------------------------------------------------------------------

def _tmp_history(tmp_path: Path) -> SignalHistory:
    return SignalHistory(tmp_path / "signals.jsonl")


def test_signal_history_append_is_idempotent_on_signal_id(tmp_path: Path) -> None:
    """Appending the same signal_id twice does not create a duplicate record."""
    history = _tmp_history(tmp_path)
    record, _trace = decide(_base_inputs())
    first_id = history.append(record)
    assert first_id == record.signal_id
    second_id = history.append(record)
    assert second_id == record.signal_id
    # Only one entry should exist
    records = history.list()
    assert len(records) == 1
    assert records[0].signal_id == record.signal_id


def test_signal_history_append_different_ids_creates_separate_records(
    tmp_path: Path,
) -> None:
    """Appending records with different signal_ids creates separate records."""
    history = _tmp_history(tmp_path)
    record_a, _trace_a = decide(_base_inputs(generated_at="2024-01-10T10:00:00+00:00"))
    record_b, _trace_b = decide(_base_inputs(generated_at="2024-01-10T12:00:00+00:00"))
    history.append(record_a)
    history.append(record_b)
    records = history.list()
    assert len(records) == 2
    assert {r.signal_id for r in records} == {record_a.signal_id, record_b.signal_id}


def test_signal_history_get_returns_existing_record(tmp_path: Path) -> None:
    """SignalHistory.get returns the exact record previously appended."""
    history = _tmp_history(tmp_path)
    record, _trace = decide(_base_inputs())
    history.append(record)
    fetched = history.get(record.signal_id)
    assert fetched is not None
    assert fetched.signal_id == record.signal_id
    assert fetched.direction == record.direction


def test_same_bar_different_features_produces_different_signal_id() -> None:
    """Same bar, different feature hash -> different signal_id (versioned revision)."""
    record_a, _trace_a = decide(_base_inputs(feature_row={"f1": 0.5, "f2": 0.3}))
    record_b, _trace_b = decide(_base_inputs(feature_row={"f1": 0.51, "f2": 0.3}))
    assert record_a.signal_id != record_b.signal_id


def test_same_bar_same_features_appended_once_only_one_record(
    tmp_path: Path,
) -> None:
    """Same bar + same features appended twice -> exactly one history record."""
    history = _tmp_history(tmp_path)
    record, _trace = decide(_base_inputs())
    history.append(record)
    history.append(record)  # duplicate append
    records = history.list()
    assert len(records) == 1
    assert records[0].signal_id == record.signal_id


# ------------------------------------------------------------------
# Storage-layer deduplication (ProspectiveStorage.store_normalized_candle)
# ------------------------------------------------------------------

from crypto_engine.research.prospective.storage import ProspectiveStorage  # noqa: E402


def test_store_normalized_candle_dedupes_by_identity_key(tmp_path: Path) -> None:
    """Storing the same candle twice returns the existing record without duplication."""
    storage = ProspectiveStorage(tmp_path / "binance" / "spot" / "BTC-USDT" / "1h")
    r1 = storage.store_normalized_candle(
        collection_id="collect-1",
        exchange="binance",
        market_type="spot",
        symbol="BTC/USDT",
        interval="1h",
        open_time="2024-01-10T10:00:00+00:00",
        close_time="2024-01-10T11:00:00+00:00",
        open_price=50000.0,
        high=50100.0,
        low=49900.0,
        close=50050.0,
        volume=10.5,
        source_observation_id="obs-1",
        first_seen_at_utc="2024-01-10T10:00:05+00:00",
        observed_at_utc="2024-01-10T10:00:05+00:00",
        ingested_at_utc="2024-01-10T10:00:05+00:00",
        research_freeze_id="freeze-1",
    )
    # Same candle, same values -> should return existing record unchanged
    r2 = storage.store_normalized_candle(
        collection_id="collect-1",
        exchange="binance",
        market_type="spot",
        symbol="BTC/USDT",
        interval="1h",
        open_time="2024-01-10T10:00:00+00:00",
        close_time="2024-01-10T11:00:00+00:00",
        open_price=50000.0,
        high=50100.0,
        low=49900.0,
        close=50050.0,
        volume=10.5,
        source_observation_id="obs-2",
        first_seen_at_utc="2024-01-10T10:00:06+00:00",
        observed_at_utc="2024-01-10T10:00:06+00:00",
        ingested_at_utc="2024-01-10T10:00:06+00:00",
        research_freeze_id="freeze-1",
    )
    assert r1["event_id"] == r2["event_id"]
    # Only one normalized candle record should exist on disk
    unique = storage.read_unique_normalized_candles()
    assert len(unique) == 1


def test_store_normalized_candle_different_times_creates_separate_records(
    tmp_path: Path,
) -> None:
    """Storing candles with different open_times creates separate records."""
    storage = ProspectiveStorage(tmp_path / "binance" / "spot" / "BTC-USDT" / "1h")
    storage.store_normalized_candle(
        collection_id="collect-1",
        exchange="binance",
        market_type="spot",
        symbol="BTC/USDT",
        interval="1h",
        open_time="2024-01-10T10:00:00+00:00",
        close_time="2024-01-10T11:00:00+00:00",
        open_price=50000.0,
        high=50100.0,
        low=49900.0,
        close=50050.0,
        volume=10.5,
        source_observation_id="obs-1",
        first_seen_at_utc="2024-01-10T10:00:05+00:00",
        observed_at_utc="2024-01-10T10:00:05+00:00",
        ingested_at_utc="2024-01-10T10:00:05+00:00",
        research_freeze_id="freeze-1",
    )
    storage.store_normalized_candle(
        collection_id="collect-1",
        exchange="binance",
        market_type="spot",
        symbol="BTC/USDT",
        interval="1h",
        open_time="2024-01-10T11:00:00+00:00",
        close_time="2024-01-10T12:00:00+00:00",
        open_price=50050.0,
        high=50150.0,
        low=49950.0,
        close=50100.0,
        volume=11.0,
        source_observation_id="obs-2",
        first_seen_at_utc="2024-01-10T11:00:05+00:00",
        observed_at_utc="2024-01-10T11:00:05+00:00",
        ingested_at_utc="2024-01-10T11:00:05+00:00",
        research_freeze_id="freeze-1",
    )
    unique = storage.read_unique_normalized_candles()
    assert len(unique) == 2


__all__ = [
    "test_different_bar_timestamps_produce_different_signal_ids",
    "test_different_models_produce_different_signal_ids",
    "test_retry_with_same_inputs_does_not_change_signal_id",
    "test_same_bar_different_features_produces_different_signal_id",
    "test_same_bar_same_features_appended_once_only_one_record",
    "test_same_inputs_produce_same_signal_id",
    "test_signal_history_append_different_ids_creates_separate_records",
    "test_signal_history_append_is_idempotent_on_signal_id",
    "test_signal_history_get_returns_existing_record",
    "test_store_normalized_candle_dedupes_by_identity_key",
    "test_store_normalized_candle_different_times_creates_separate_records",
]
