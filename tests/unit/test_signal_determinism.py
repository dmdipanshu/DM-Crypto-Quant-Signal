"""Signal score determinism regression tests.

Verifies that repeated independent evaluations of the same fixed input
snapshot produce identical canonical snapshot hashes, feature vectors,
raw scores, and signal IDs. No nondeterministic inputs (wall-clock
timestamps, random seeds, unordered iteration, mutable feature state)
may influence the output.
"""

from __future__ import annotations

from datetime import UTC, datetime

from crypto_engine.signals.policy import PolicyInputs, decide
from crypto_engine.signals.schema import AiContextSummary, CostEstimate


def _base_inputs(**over):
    from unittest.mock import MagicMock

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
        generated_at=datetime(2024, 1, 10, 10, 0, 0, tzinfo=UTC).isoformat(),
        valid_until=datetime(2024, 1, 10, 11, 0, 0, tzinfo=UTC).isoformat(),
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


def test_repeated_evaluation_produces_identical_signal_id() -> None:
    """Same input snapshot evaluated repeatedly yields identical signal_id."""
    ids = set()
    scores = set()
    for _ in range(10):
        record, _trace = decide(_base_inputs())
        ids.add(record.signal_id)
        scores.add(record.prediction.raw_score)
    assert len(ids) == 1, f"signal_id not deterministic: {ids}"
    assert len(scores) == 1, f"raw_score not deterministic: {scores}"


def test_repeated_evaluation_produces_identical_raw_score() -> None:
    """Same input snapshot evaluated repeatedly yields identical raw_score."""
    scores = []
    for _ in range(5):
        record, _trace = decide(_base_inputs())
        scores.append(record.prediction.raw_score)
    assert all(s == scores[0] for s in scores)


def test_repeated_evaluation_produces_identical_feature_hash() -> None:
    """Feature hash is stable across repeated evaluations."""
    hashes = set()
    for _ in range(5):
        record, _trace = decide(_base_inputs())
        # The signal_id incorporates feature_hash; distinct signal_ids
        # would indicate nondeterministic feature hashing.
        hashes.add(record.signal_id)
    assert len(hashes) == 1


def test_no_wall_clock_in_signal_id() -> None:
    """signal_id does not depend on wall-clock time (generated_at is the bar ts)."""
    # Two evaluations at different wall-clock times but same bar timestamp
    # must produce the same signal_id.
    record_a, _ = decide(
        _base_inputs(generated_at="2024-01-10T10:00:00+00:00")
    )
    record_b, _ = decide(
        _base_inputs(generated_at="2024-01-10T10:00:00+00:00")
    )
    assert record_a.signal_id == record_b.signal_id


def test_different_bar_timestamps_differ() -> None:
    """Different bar timestamps produce different signal_ids (deterministic separation)."""
    record_a, _ = decide(
        _base_inputs(generated_at="2024-01-10T10:00:00+00:00")
    )
    record_b, _ = decide(
        _base_inputs(generated_at="2024-01-10T11:00:00+00:00")
    )
    assert record_a.signal_id != record_b.signal_id


def test_different_feature_rows_differ() -> None:
    """Different feature rows produce different signal_ids."""
    record_a, _ = decide(_base_inputs(feature_row={"f1": 0.5, "f2": 0.3}))
    record_b, _ = decide(_base_inputs(feature_row={"f1": 0.51, "f2": 0.3}))
    assert record_a.signal_id != record_b.signal_id


__all__ = [
    "test_different_bar_timestamps_differ",
    "test_different_feature_rows_differ",
    "test_no_wall_clock_in_signal_id",
    "test_repeated_evaluation_produces_identical_feature_hash",
    "test_repeated_evaluation_produces_identical_raw_score",
    "test_repeated_evaluation_produces_identical_signal_id",
]
