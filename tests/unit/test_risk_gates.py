"""Unit tests for deterministic risk gates (Phase 5 Track B, offline)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from crypto_engine.config.settings import RiskConfig
from crypto_engine.risk.gates import GateContext, run_gates
from crypto_engine.signals.schema import SignalRecord

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _base_record(**over) -> SignalRecord:
    params: dict = {
        "signal_id": "a" * 16,
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "timeframe": "1h",
        "generated_at": NOW - timedelta(minutes=10),
        "valid_until": NOW + timedelta(minutes=50),
        "direction": "LONG",
        "prediction_horizon_bars": 24,
        "model_id": "trend-test",
        "model_version": "0.1.0",
        "model_artifact_hash": "abc123",
        "dataset_snapshot_id": "snap",
        "feature_snapshot_hash": "f" * 64,
        "prediction": {
            "raw_score": 0.9,
            "calibration_status": "uncalibrated",
            "positive_class": None,
            "model_family": "trend",
            "threshold_version": "v1",
        },
        "market_regime": "",
        "expected_edge_bp": 20.0,
        "cost_estimate": {
            "fee_bp": 10.0,
            "spread_bp": 0.0,
            "slippage_bp": 5.0,
            "funding_bp": 0.0,
            "source": "configured",
            "expected_edge_bp": None,
            "edge_margin_bp": 0.0,
        },
        "entry_ref": 100.0,
        "stop_loss": 99.0,
        "take_profit": 102.0,
        "risk_reward": 2.0,
        "data_quality": "validated",
        "ai_context": {"status": "disabled"},
        "decision": {"status": "approved", "reason_codes": ["OK"], "gate": "policy"},
        "software_version": "0.1.0-test",
    }
    params.update(over)
    return SignalRecord.model_validate(params)


def _ctx(portfolio, **over) -> GateContext:
    params: dict = {"now_utc": NOW, "portfolio": portfolio}
    params.update(over)
    return GateContext(**params)


def _codes(record: SignalRecord) -> list[str]:
    return list(record.decision.reason_codes)


# --- A. data gates ---------------------------------------------------------


def test_freshness_boundary_passes_at_limit(portfolio_valid) -> None:
    rec = _base_record(generated_at=NOW - timedelta(minutes=60))
    out, _ = run_gates(rec, _ctx(portfolio_valid))
    assert out.decision.status == "approved"


def test_freshness_fails_one_second_over(portfolio_valid) -> None:
    rec = _base_record(generated_at=NOW - timedelta(minutes=60, seconds=1))
    out, trace = run_gates(rec, _ctx(portfolio_valid))
    assert out.decision.status == "rejected"
    assert "RISK_GATE_FRESHNESS" in _codes(out)
    assert trace[0]["gate"] == "risk:freshness"


def test_feature_staleness_trips(portfolio_valid) -> None:
    ctx = _ctx(portfolio_valid, feature_timestamp=NOW - timedelta(minutes=91))
    out, _ = run_gates(_base_record(), ctx)
    assert out.decision.status == "rejected"
    assert "RISK_GATE_FRESHNESS" in _codes(out)


def test_completeness_rejects_non_validated(portfolio_valid) -> None:
    for quality, detail in (
        ("stale", "STALE_DATA"),
        ("incomplete", "INCOMPLETE_DATA"),
        ("invalid", "INVALID"),
    ):
        out, trace = run_gates(_base_record(data_quality=quality), _ctx(portfolio_valid))
        assert out.decision.status == "rejected"
        assert "RISK_GATE_COMPLETENESS" in _codes(out)
        assert any(detail in t["outcome"] for t in trace)


def test_consistency_passes_when_valid(portfolio_valid) -> None:
    out, _ = run_gates(_base_record(), _ctx(portfolio_valid))
    assert out.decision.status == "approved"


# --- B. model gates --------------------------------------------------------


def test_artifact_missing_and_corrupt(portfolio_valid) -> None:
    out, _ = run_gates(_base_record(model_artifact_hash=""), _ctx(portfolio_valid))
    assert out.decision.status == "rejected"
    assert "RISK_GATE_ARTIFACT" in _codes(out)
    out2, trace2 = run_gates(_base_record(), _ctx(portfolio_valid), artifact_hash="different-hash")
    assert out2.decision.status == "rejected"
    assert "RISK_GATE_ARTIFACT" in _codes(out2)
    assert any("ARTIFACT_CORRUPT" in t["outcome"] for t in trace2)


def test_artifact_match_passes(portfolio_valid) -> None:
    out, _ = run_gates(_base_record(), _ctx(portfolio_valid), artifact_hash="abc123")
    assert out.decision.status == "approved"


def test_schema_mismatch_trips(portfolio_valid) -> None:
    out, _ = run_gates(
        _base_record(),
        _ctx(portfolio_valid, feature_names=["a", "b"]),
        feature_schema=["a", "c"],
    )
    assert out.decision.status == "rejected"
    assert "RISK_GATE_SCHEMA" in _codes(out)


def test_schema_match_passes(portfolio_valid) -> None:
    out, _ = run_gates(
        _base_record(),
        _ctx(portfolio_valid, feature_names=["a", "b"]),
        feature_schema=["a", "b"],
    )
    assert out.decision.status == "approved"


def test_output_gates(portfolio_valid) -> None:
    rec_none = _base_record(
        prediction={
            "raw_score": None,
            "calibration_status": "unavailable",
            "positive_class": None,
            "model_family": "trend",
            "threshold_version": "v1",
        }
    )
    out, trace = run_gates(rec_none, _ctx(portfolio_valid))
    assert "RISK_GATE_OUTPUT" in _codes(out)
    assert any("INVALID_OUTPUT" in t["outcome"] for t in trace)
    # Non-finite / out-of-range scores cannot exist in a valid record: the
    # schema rejects them at construction (gate checks are defense-in-depth).
    for bad in (float("inf"), float("nan"), 1.5, -0.1):
        with pytest.raises(ValueError):
            _base_record(
                prediction={
                    "raw_score": bad,
                    "calibration_status": "unavailable",
                    "positive_class": None,
                    "model_family": "trend",
                    "threshold_version": "v1",
                }
            )


def test_calibration_gates(portfolio_valid) -> None:
    def _pred(**kw):
        base = {
            "raw_score": 0.9,
            "calibration_status": "mismatch",
            "positive_class": None,
            "model_family": "trend",
            "threshold_version": "v1",
        }
        base.update(kw)
        return base

    out, _ = run_gates(_base_record(prediction=_pred()), _ctx(portfolio_valid))
    assert "RISK_GATE_CALIBRATION" in _codes(out)
    out2, _ = run_gates(
        _base_record(prediction=_pred(calibration_status="calibrated", positive_class=1)),
        _ctx(portfolio_valid, label_set=[-1, 1], threshold_version="v1"),
    )
    assert out2.decision.status == "approved"
    out3, _ = run_gates(
        _base_record(prediction=_pred(calibration_status="calibrated", positive_class=7)),
        _ctx(portfolio_valid, label_set=[-1, 1], threshold_version="v1"),
    )
    assert "RISK_GATE_CALIBRATION" in _codes(out3)
    out4, _ = run_gates(
        _base_record(prediction=_pred(calibration_status="calibrated", positive_class=1)),
        _ctx(portfolio_valid, label_set=[-1, 1], threshold_version="v2"),
    )
    assert "RISK_GATE_CALIBRATION" in _codes(out4)


# --- C. decision gates -----------------------------------------------------


def test_directions_disabled_trips(portfolio_valid) -> None:
    short = _base_record(
        direction="SHORT", entry_ref=100.0, stop_loss=101.0, take_profit=98.0, risk_reward=2.0
    )
    out, _ = run_gates(short, _ctx(portfolio_valid, enabled_directions={"LONG"}))
    assert out.decision.status == "rejected"
    assert "RISK_GATE_DIRECTIONS" in _codes(out)


def test_disagreement_abstains_neutral(portfolio_valid) -> None:
    out, _trace = run_gates(_base_record(), _ctx(portfolio_valid, model_disagreement=True))
    assert out.direction == "NEUTRAL"
    assert out.decision.status == "abstained"
    assert "MODEL_DISAGREEMENT" in _codes(out)
    assert out.entry_ref is None and out.stop_loss is None and out.take_profit is None


def test_disagreement_ignores_flat(portfolio_valid) -> None:
    rec = _base_record(
        direction="NO_TRADE",
        entry_ref=None,
        stop_loss=None,
        take_profit=None,
        risk_reward=None,
        expected_edge_bp=None,
        decision={"status": "abstained", "reason_codes": ["THRESHOLD_NOT_MET"], "gate": "policy"},
    )
    out, _ = run_gates(rec, _ctx(portfolio_valid, model_disagreement=True))
    assert out.decision.status == "abstained"
    assert "MODEL_DISAGREEMENT" not in _codes(out)


def test_edge_gates(portfolio_valid) -> None:
    out, _ = run_gates(_base_record(expected_edge_bp=None), _ctx(portfolio_valid))
    assert "RISK_GATE_EDGE" in _codes(out)
    out2, _ = run_gates(_base_record(expected_edge_bp=5.0), _ctx(portfolio_valid))
    assert "RISK_GATE_EDGE" in _codes(out2)
    out3, _ = run_gates(_base_record(expected_edge_bp=15.0), _ctx(portfolio_valid))
    assert out3.decision.status == "approved"


def test_age_expired_trips(portfolio_valid) -> None:
    out, _ = run_gates(_base_record(valid_until=NOW - timedelta(seconds=1)), _ctx(portfolio_valid))
    assert "RISK_GATE_AGE" in _codes(out)
    rec = _base_record(valid_until=NOW)
    ctx = _ctx(portfolio_valid)
    out2, _ = run_gates(rec, ctx)
    assert out2.decision.status == "approved"


def test_regime_blocked_trips(portfolio_valid) -> None:
    out, _ = run_gates(
        _base_record(market_regime="chop"),
        _ctx(portfolio_valid, blocked_regimes={"chop"}),
    )
    assert "RISK_GATE_REGIME" in _codes(out)


def test_ai_veto_and_advisory(portfolio_valid) -> None:
    out, _ = run_gates(_base_record(), _ctx(portfolio_valid, ai_required=True))
    assert "RISK_GATE_AI" in _codes(out)
    rec_ok = _base_record(ai_context={"status": "ok", "note": ""})
    out2, _ = run_gates(rec_ok, _ctx(portfolio_valid, ai_required=True))
    assert out2.decision.status == "approved"
    assert out2.direction == "LONG"
    rec_invalid = _base_record(ai_context={"status": "invalid", "note": ""})
    out3, _ = run_gates(rec_invalid, _ctx(portfolio_valid, ai_required=False))
    assert out3.decision.status == "approved"
    assert out3.direction == "LONG"


# --- D. exposure gates -----------------------------------------------------


def test_exposure_unknown_fail_closed(portfolio_valid) -> None:
    ctx = _ctx(None)
    assert ctx.effective_fail_closed is True
    out, trace = run_gates(_base_record(), ctx)
    assert out.decision.status == "rejected"
    assert "RISK_GATE_EXPOSURE" in _codes(out)
    assert any("EXPOSURE_UNKNOWN" in t["outcome"] for t in trace)


def test_exposure_unknown_fail_open_passes() -> None:
    ctx = GateContext(now_utc=NOW, portfolio=None, fail_closed=False)
    out, trace = run_gates(_base_record(), ctx)
    assert out.decision.status == "approved"
    assert any("EXPOSURE_UNKNOWN" in t["outcome"] for t in trace)


def test_risk_disabled_skips_exposure() -> None:
    risk = RiskConfig(enabled=False)
    out, _ = run_gates(_base_record(), GateContext(now_utc=NOW, risk=risk, portfolio=None))
    assert out.decision.status == "approved"


def test_trade_risk_boundary(portfolio_valid) -> None:
    out, _ = run_gates(_base_record(), _ctx(portfolio_valid, proposed_risk_pct=1.0))
    assert out.decision.status == "approved"
    out2, _ = run_gates(_base_record(), _ctx(portfolio_valid, proposed_risk_pct=1.5))
    assert "RISK_GATE_TRADE_RISK" in _codes(out2)


def test_exposure_limit(portfolio_valid) -> None:
    bad = dict(portfolio_valid)
    bad["symbol_exposure_pct"] = 25.0
    out, _ = run_gates(_base_record(), _ctx(bad, proposed_risk_pct=0.5))
    assert "RISK_GATE_EXPOSURE" in _codes(out)


def test_drawdown_boundary(portfolio_valid) -> None:
    at_limit = dict(portfolio_valid)
    at_limit["day_start_equity"] = 100_000.0
    at_limit["current_equity"] = 95_000.0
    out, _ = run_gates(_base_record(), _ctx(at_limit))
    assert out.decision.status == "approved"
    over = dict(portfolio_valid)
    over["current_equity"] = 90_000.0
    out2, _ = run_gates(_base_record(), _ctx(over))
    assert "RISK_GATE_DRAWDOWN" in _codes(out2)


def test_strategy_and_circuit(portfolio_valid) -> None:
    off = dict(portfolio_valid)
    off["strategy_enabled"] = False
    out, _ = run_gates(_base_record(), _ctx(off))
    assert "RISK_GATE_STRATEGY" in _codes(out)
    halted = dict(portfolio_valid)
    halted["circuit_open"] = True
    out2, _ = run_gates(_base_record(), _ctx(halted))
    assert "RISK_GATE_CIRCUIT" in _codes(out2)


def test_portfolio_validation_rejects_invented() -> None:
    with pytest.raises(ValueError):
        GateContext(now_utc=NOW, portfolio={"exposure_pct": 1.0})
    with pytest.raises(ValueError):
        GateContext(now_utc=NOW.replace(tzinfo=None), portfolio=None)


# --- order / integrity ------------------------------------------------------


def test_gate_order_fixed_first_failure_wins(portfolio_valid) -> None:
    rec = _base_record(
        generated_at=NOW - timedelta(hours=5),
        model_artifact_hash="",
        valid_until=NOW - timedelta(hours=1),
        data_quality="validated",
    )
    out, trace = run_gates(rec, _ctx(None), artifact_hash="zzz")
    assert "RISK_GATE_FRESHNESS" in _codes(out)
    assert trace[0]["gate"] == "risk:freshness"


def test_no_secrets_in_trace(portfolio_valid) -> None:
    _, trace = run_gates(_base_record(), _ctx(portfolio_valid), artifact_hash="abc123")
    blob = json.dumps(trace).lower()
    assert "api_key" not in blob and "secret" not in blob and "bearer" not in blob


def test_passing_never_upgrades_abstained(portfolio_valid) -> None:
    rec = _base_record(
        direction="NO_TRADE",
        entry_ref=None,
        stop_loss=None,
        take_profit=None,
        risk_reward=None,
        expected_edge_bp=None,
        decision={"status": "abstained", "reason_codes": ["THRESHOLD_NOT_MET"], "gate": "policy"},
    )
    out, _ = run_gates(rec, _ctx(portfolio_valid))
    assert out.decision.status == "abstained"
