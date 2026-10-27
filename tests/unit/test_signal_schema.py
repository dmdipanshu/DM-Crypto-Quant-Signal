"""Signal schema tests: roundtrip, rejections, NO_TRADE, levels (offline)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crypto_engine.signals.schema import (
    AiContextSummary,
    CostEstimate,
    DecisionInfo,
    PredictionInfo,
    SignalLevels,
    SignalRecord,
    check_direction_geometry,
)


def _record_kwargs(**over: object) -> dict:
    base: dict = {
        "signal_id": "a1b2c3d4e5f60718",
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "timeframe": "1h",
        "generated_at": "2024-01-10T00:00:00+00:00",
        "valid_until": "2024-01-10T01:00:00+00:00",
        "direction": "LONG",
        "prediction_horizon_bars": 24,
        "model_id": "m-1",
        "model_version": "0.1.0",
        "model_artifact_hash": "abc123",
        "dataset_snapshot_id": "snap-1",
        "feature_snapshot_hash": "f" * 64,
        "prediction": {
            "raw_score": 0.8,
            "calibrated_probability": 0.75,
            "calibration_status": "calibrated",
            "positive_class": 1,
            "model_family": "logistic",
            "threshold_version": "v1",
        },
        "market_regime": "trend",
        "expected_edge_bp": 12.0,
        "cost_estimate": {"fee_bp": 10.0, "slippage_bp": 5.0, "source": "configured"},
        "entry_ref": 40000.0,
        "stop_loss": 39500.0,
        "take_profit": 41000.0,
        "risk_reward": 2.0,
        "data_quality": "validated",
        "ai_context": {"status": "disabled"},
        "decision": {"status": "approved", "reason_codes": ["OK"], "gate": "policy"},
        "software_version": "0.1.0",
    }
    base.update(over)
    return base


def test_valid_long_record_roundtrip() -> None:
    """A valid LONG record validates and JSON-round-trips."""
    rec = SignalRecord.model_validate(_record_kwargs())
    assert rec.direction == "LONG"
    assert rec.decision.reason_codes == ["OK"]
    assert rec.cost_estimate is not None and rec.cost_estimate.cost_bp_total == 15.0
    again = SignalRecord.model_validate(rec.model_dump(mode="json"))
    assert again.signal_id == rec.signal_id


def test_no_trade_with_reasons_valid() -> None:
    """NO_TRADE + abstained + reasons is first-class, never an exception."""
    rec = SignalRecord.model_validate(
        _record_kwargs(
            direction="NO_TRADE",
            entry_ref=None,
            stop_loss=None,
            take_profit=None,
            risk_reward=None,
            expected_edge_bp=None,
            decision={"status": "abstained", "reason_codes": ["UNCALIBRATED"], "gate": "policy"},
        )
    )
    assert rec.direction == "NO_TRADE"
    assert rec.decision.status == "abstained"


def test_no_trade_approved_rejected() -> None:
    """NO_TRADE cannot be approved."""
    with pytest.raises(ValidationError):
        SignalRecord.model_validate(
            _record_kwargs(
                direction="NO_TRADE",
                entry_ref=None,
                stop_loss=None,
                take_profit=None,
                risk_reward=None,
                decision={"status": "approved", "reason_codes": ["OK"], "gate": "policy"},
            )
        )


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.1])
def test_prediction_rejects_nonfinite_outofrange(bad: float) -> None:
    """PredictionInfo rejects NaN/Inf/out-of-range scores."""
    with pytest.raises(ValidationError):
        PredictionInfo(raw_score=bad, calibration_status="uncalibrated", model_family="logistic")


def test_calibrated_probability_none_unless_calibrated() -> None:
    """calibrated_probability must be None unless status == calibrated."""
    with pytest.raises(ValidationError):
        PredictionInfo(
            raw_score=0.7,
            calibrated_probability=0.7,
            calibration_status="uncalibrated",
            model_family="logistic",
        )
    ok = PredictionInfo(
        raw_score=0.7,
        calibrated_probability=0.7,
        calibration_status="calibrated",
        positive_class=1,
        model_family="logistic",
    )
    assert ok.calibrated_probability == 0.7


def test_malformed_timestamps_rejected() -> None:
    """Naive or inverted timestamps are rejected."""
    with pytest.raises(ValidationError):
        SignalRecord.model_validate(_record_kwargs(generated_at="2024-01-10 00:00:00"))
    with pytest.raises(ValidationError):
        SignalRecord.model_validate(
            _record_kwargs(
                generated_at="2024-01-10T02:00:00+00:00",
                valid_until="2024-01-10T01:00:00+00:00",
            )
        )


def test_unknown_schema_version_rejected() -> None:
    """Only schema_version '1.0' is accepted."""
    with pytest.raises(ValidationError):
        SignalRecord.model_validate(_record_kwargs(schema_version="2.0"))


def test_extra_fields_rejected() -> None:
    """Closed schema: extra fields raise."""
    with pytest.raises(ValidationError):
        SignalRecord.model_validate(_record_kwargs(some_unknown_field=1))


def test_bad_reason_codes_rejected() -> None:
    """Empty or malformed reason codes are rejected."""
    with pytest.raises(ValidationError):
        DecisionInfo(status="abstained", reason_codes=[], gate="policy")
    with pytest.raises(ValidationError):
        DecisionInfo(status="abstained", reason_codes=["not-valid!"], gate="policy")


def test_cost_total_computed_and_mixup_rejected() -> None:
    """cost_bp_total is computed; decimal-mixup magnitudes rejected."""
    est = CostEstimate(fee_bp=10.0, spread_bp=2.0, slippage_bp=5.0, funding_bp=1.0)
    assert est.cost_bp_total == 18.0
    with pytest.raises(ValidationError):
        CostEstimate(fee_bp=15000.0)


def test_levels_direction_both_sides() -> None:
    """LONG/SHORT geometry validated on both sides."""
    check_direction_geometry("LONG", 100.0, 90.0, 120.0)
    check_direction_geometry("SHORT", 100.0, 110.0, 80.0)
    with pytest.raises(ValueError, match="LONG requires stop_loss < entry_ref"):
        check_direction_geometry("LONG", 100.0, 110.0, 120.0)
    with pytest.raises(ValueError, match="SHORT requires stop_loss > entry_ref"):
        check_direction_geometry("SHORT", 100.0, 90.0, 80.0)
    with pytest.raises(ValueError, match="must have all levels None"):
        check_direction_geometry("NO_TRADE", 100.0, None, None)
    with pytest.raises(ValueError, match="must have all levels None"):
        check_direction_geometry("NEUTRAL", None, 90.0, None)
    lv = SignalLevels(direction="NO_TRADE")
    assert lv.entry_ref is None


def test_ai_note_length_and_secrets_rejected() -> None:
    """AI notes are length-capped and prompt/secret-free."""
    with pytest.raises(ValidationError):
        AiContextSummary(status="ok", note="x" * 501)
    with pytest.raises(ValidationError):
        AiContextSummary(status="ok", note="the prompt was to buy")
