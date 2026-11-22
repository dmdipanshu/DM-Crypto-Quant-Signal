"""Policy tests: fixed veto order, thresholds, edge, AI, determinism (offline)."""

from __future__ import annotations

import pytest

from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline
from crypto_engine.signals.policy import PolicyInputs, apply_gate_veto, decide
from crypto_engine.signals.schema import AiContextSummary, CostEstimate, SignalLevels
from tests.fixtures.signal_fixtures import StubBinaryModel, make_feature_frame


def _base_inputs(**over) -> PolicyInputs:
    frame, _ = make_feature_frame(n=40, seed=0)
    row = {c: float(frame.iloc[0][c]) for c in ["a", "b"]}
    model = StubBinaryModel(feature_names=["a", "b"], score=0.99, label=1)
    model.fit(frame, __import__("numpy").array([1] * 40))
    params: dict = {
        "feature_row": row,
        "feature_names": ["a", "b"],
        "model": model,
        "model_id": "stub-1",
        "model_version": "0.1.0",
        "model_artifact_hash": "h" * 16,
        "calibrator": None,
        "calibrator_meta": {},
        "calibration_required": False,
        "t_long": 0.6,
        "t_short": 0.4,
        "require_edge": False,
        "expected_edge_bp": None,
        "cost": None,
        "generated_at": "2024-01-10T00:00:00+00:00",
        "valid_until": "2024-01-10T01:00:00+00:00",
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "timeframe": "1h",
        "horizon_bars": 24,
    }
    params.update(over)
    return PolicyInputs(**params)


def test_approved_long_stub_high_score() -> None:
    """Raw 0.99 >= t_long with edge disabled approves LONG."""
    rec, trace = decide(_base_inputs())
    assert rec.direction == "LONG"
    assert rec.decision.status == "approved"
    assert rec.decision.reason_codes == ["OK"]
    assert rec.prediction.calibration_status == "uncalibrated"
    assert rec.prediction.calibrated_probability is None
    assert len(rec.signal_id) == 16
    assert trace[0]["gate"] == "policy:timestamps"


def test_approved_short_stub_low_score() -> None:
    """Raw 0.05 <= t_short approves SHORT."""
    frame, _ = make_feature_frame(n=40, seed=0)
    row = {c: float(frame.iloc[0][c]) for c in ["a", "b"]}
    import numpy as np

    model = StubBinaryModel(feature_names=["a", "b"], score=0.05, label=-1)
    model.fit(frame, np.array([-1] * 40))
    rec, _ = decide(_base_inputs(feature_row=row, model=model))
    assert rec.direction == "SHORT"
    assert rec.decision.status == "approved"


def test_neutral_in_band() -> None:
    """Score inside (t_short, t_long) abstains as NEUTRAL/THRESHOLD_NOT_MET."""
    frame, _ = make_feature_frame(n=40, seed=0)
    row = {c: float(frame.iloc[0][c]) for c in ["a", "b"]}
    import numpy as np

    model = StubBinaryModel(feature_names=["a", "b"], score=0.5, label=0, classes=[-1, 0, 1])
    model.fit(frame, np.array([0] * 40))
    rec, _ = decide(_base_inputs(feature_row=row, model=model))
    assert rec.direction == "NEUTRAL"
    assert rec.decision.status == "abstained"
    assert "THRESHOLD_NOT_MET" in rec.decision.reason_codes


def test_unfitted_model_veto() -> None:
    """Unfitted model -> NO_TRADE(MODEL_UNFITTED)."""
    model = StubBinaryModel(feature_names=["a", "b"], score=0.9, label=1)
    rec, _ = decide(_base_inputs(model=model))
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["MODEL_UNFITTED"]


def test_schema_mismatch_veto() -> None:
    """Wrong feature schema -> NO_TRADE(SCHEMA_MISMATCH)."""
    rec, _ = decide(_base_inputs(feature_names=["a", "zzz"]))
    assert rec.direction == "NO_TRADE"
    assert "SCHEMA_MISMATCH" in rec.decision.reason_codes


def test_nonfinite_feature_veto() -> None:
    """NaN feature -> NO_TRADE(NON_FINITE)."""
    rec, _ = decide(_base_inputs(feature_row={"a": float("nan"), "b": 0.0}))
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["NON_FINITE"]


def test_uncalibrated_required_veto() -> None:
    """No calibrator + required -> NO_TRADE(UNCALIBRATED)."""
    rec, _ = decide(_base_inputs(calibration_required=True))
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["UNCALIBRATED"]


def test_rule_based_no_probability() -> None:
    """TrendBaseline records RULE_BASED_NO_PROBABILITY, never calibrated p."""
    import numpy as np
    import pandas as pd

    frame = pd.DataFrame(
        {"trend_ratio_20_50": [0.05], "b": [0.1]},
    )
    model = TrendBaseline(feature_names=["trend_ratio_20_50", "b"])
    model.fit(frame, np.array([1]))
    rec, _ = decide(
        _base_inputs(
            feature_row={"trend_ratio_20_50": 0.05, "b": 0.1},
            feature_names=["trend_ratio_20_50", "b"],
            model=model,
        )
    )
    assert rec.direction == "LONG"
    assert "RULE_BASED_NO_PROBABILITY" in rec.decision.reason_codes
    assert rec.prediction.calibrated_probability is None


def test_rule_based_required_veto() -> None:
    """Rule-based + calibration_required -> NO_TRADE."""
    import numpy as np
    import pandas as pd

    frame = pd.DataFrame({"trend_ratio_20_50": [0.05], "b": [0.1]})
    model = TrendBaseline(feature_names=["trend_ratio_20_50", "b"])
    model.fit(frame, np.array([1]))
    rec, _ = decide(
        _base_inputs(
            feature_row={"trend_ratio_20_50": 0.05, "b": 0.1},
            feature_names=["trend_ratio_20_50", "b"],
            model=model,
            calibration_required=True,
        )
    )
    assert rec.direction == "NO_TRADE"
    assert "UNCALIBRATED" in rec.decision.reason_codes


def test_majority_baseline_neutral_or_long() -> None:
    """MajorityBaseline path is rule-based and finite."""
    import numpy as np
    import pandas as pd

    frame = pd.DataFrame({"a": [0.5, -0.2, 0.3], "b": [0.1, 0.2, 0.3]})
    model = MajorityBaseline(feature_names=["a", "b"])
    model.fit(frame, np.array([1, 1, -1]))
    rec, _ = decide(_base_inputs(feature_row={"a": 0.5, "b": 0.1}, model=model))
    assert rec.prediction.calibrated_probability is None
    assert "RULE_BASED_NO_PROBABILITY" in rec.decision.reason_codes


def test_calibrated_long_beyond_threshold(signal_calibrated_pair) -> None:
    """Calibrated p beyond thresholds approves with status calibrated."""
    import numpy as np

    base, cal, meta, row = signal_calibrated_pair
    raw = float(base.predict_score(np.array([[row["a"], row["b"]]]))[0])
    p = float(cal.predict_proba(np.array([raw]))[0])
    want = "LONG" if p >= 0.6 else ("SHORT" if p <= 0.4 else "NEUTRAL")
    rec, _ = decide(
        _base_inputs(
            feature_row=row,
            model=base,
            calibrator=cal,
            calibrator_meta=meta,
            calibration_required=True,
        )
    )
    assert rec.direction == want
    assert rec.prediction.calibration_status == "calibrated"
    assert rec.prediction.calibrated_probability == pytest.approx(p)


def test_calibration_mismatch_veto() -> None:
    """Wrong calibrator meta -> mismatch path (veto when required)."""
    import numpy as np

    frame, _ = make_feature_frame(n=40, seed=0)
    row = {c: float(frame.iloc[0][c]) for c in ["a", "b"]}
    model = StubBinaryModel(feature_names=["a", "b"], score=0.99, label=1)
    model.fit(frame, np.array([1] * 40))

    class _FakeCal:
        is_fitted = True

        def to_dict(self) -> dict:
            return {
                "model_family": "other",
                "model_version": "x",
                "feature_schema_hash": "y",
                "positive_class": 1,
            }

        def predict_proba(self, s):  # pragma: no cover
            return np.asarray(s)

    rec, _ = decide(
        _base_inputs(
            feature_row=row,
            model=model,
            calibrator=_FakeCal(),
            calibrator_meta={"model_family": "stub", "version": "v", "feature_hash": "h"},
            calibration_required=True,
        )
    )
    assert rec.direction == "NO_TRADE"
    assert "CALIBRATION_MISMATCH" in rec.decision.reason_codes


def test_disabled_direction_veto() -> None:
    """LONG disabled -> NO_TRADE(DIRECTION_DISABLED)."""
    rec, _ = decide(_base_inputs(enabled_directions={"SHORT", "NEUTRAL", "NO_TRADE"}))
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["DIRECTION_DISABLED"]


def test_short_needs_minus_one_class() -> None:
    """Binary model without -1 in label set cannot go SHORT."""
    frame, _ = make_feature_frame(n=40, seed=0)
    row = {c: float(frame.iloc[0][c]) for c in ["a", "b"]}
    import numpy as np

    model = StubBinaryModel(feature_names=["a", "b"], score=0.05, label=0, classes=[0, 1])
    model.fit(frame, np.array([1] * 40))
    rec, _ = decide(_base_inputs(feature_row=row, model=model))
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["DIRECTION_DISABLED"]


def test_edge_below_cost_veto() -> None:
    """Edge below cost+margin -> NO_TRADE(INSUFFICIENT_EDGE)."""
    cost = CostEstimate(fee_bp=10.0, slippage_bp=5.0, source="configured")
    rec, _ = decide(
        _base_inputs(require_edge=True, cost=cost, expected_edge_bp=10.0),
    )
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["INSUFFICIENT_EDGE"]


def test_edge_none_required_veto() -> None:
    """Missing edge with require_edge -> NO_TRADE(EDGE_UNAVAILABLE)."""
    cost = CostEstimate(fee_bp=10.0, source="configured")
    rec, _ = decide(_base_inputs(require_edge=True, cost=cost, expected_edge_bp=None))
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["EDGE_UNAVAILABLE"]


def test_missing_cost_veto() -> None:
    """Missing cost with require_edge -> NO_TRADE(COST_INPUTS_MISSING)."""
    rec, _ = decide(_base_inputs(require_edge=True, cost=None, expected_edge_bp=50.0))
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["COST_INPUTS_MISSING"]


def test_edge_passes_approves() -> None:
    """Edge above cost+margin approves LONG."""
    cost = CostEstimate(fee_bp=10.0, slippage_bp=5.0, source="configured")
    rec, _ = decide(
        _base_inputs(require_edge=True, cost=cost, expected_edge_bp=20.0),
    )
    assert rec.direction == "LONG"
    assert rec.decision.status == "approved"


def test_ai_invalid_not_required_proceeds() -> None:
    """Invalid AI + not required proceeds (note recorded)."""
    ai = AiContextSummary(status="invalid", provider_id="p", model_id="m", note="bad payload")
    rec, _ = decide(_base_inputs(ai=ai, ai_required=False))
    assert rec.direction == "LONG"
    assert rec.ai_context.status == "invalid"


def test_ai_required_unavailable_veto() -> None:
    """AI required + unavailable -> NO_TRADE(AI_REQUIRED_UNAVAILABLE)."""
    ai = AiContextSummary(status="unavailable")
    rec, _ = decide(_base_inputs(ai=ai, ai_required=True))
    assert rec.direction == "NO_TRADE"
    assert rec.decision.reason_codes == ["AI_REQUIRED_UNAVAILABLE"]


def test_determinism_two_calls_identical() -> None:
    """Two identical calls give identical records incl. signal_id."""
    rec1, _ = decide(_base_inputs())
    rec2, _ = decide(_base_inputs())
    assert rec1.signal_id == rec2.signal_id
    assert rec1.model_dump(mode="json") == rec2.model_dump(mode="json")


def test_multiclass_argmax_path() -> None:
    """Multiclass stub without calibrator maps argmax label to direction."""

    class _Multi(StubBinaryModel):
        family: str = "multi"

        def __init__(self) -> None:
            super().__init__(feature_names=["a", "b"], score=0.7, label=-1)
            self._classes = [-1, 0, 1]

    import numpy as np

    frame, _ = make_feature_frame(n=40, seed=0)
    row = {c: float(frame.iloc[0][c]) for c in ["a", "b"]}
    model = _Multi()
    model.fit(frame, np.array([1] * 40))
    rec, _ = decide(_base_inputs(feature_row=row, model=model))
    assert rec.direction == "SHORT"
    assert rec.prediction.calibrated_probability is None


def test_levels_validated_and_bad_rejected() -> None:
    """Valid levels attach; violating levels veto."""
    lv = SignalLevels(direction="LONG", entry_ref=100.0, stop_loss=90.0, take_profit=120.0)
    rec, _ = decide(_base_inputs(levels=lv))
    assert rec.entry_ref == 100.0 and rec.stop_loss == 90.0

    class _Bad:
        entry_ref = 100.0
        stop_loss = 110.0
        take_profit = 120.0
        risk_reward = None

    rec, _ = decide(_base_inputs(levels=_Bad()))
    assert rec.direction == "NO_TRADE"


def test_apply_gate_veto_helper() -> None:
    """Track B helper flips approved records to rejected with RISK_GATE_*."""
    rec, _ = decide(_base_inputs())
    assert rec.decision.status == "approved"
    vetoed = apply_gate_veto(rec, "risk:max_drawdown", "RISK_GATE_DRAWDOWN")
    assert vetoed.decision.status == "rejected"
    assert vetoed.decision.gate == "risk:max_drawdown"
    assert "RISK_GATE_DRAWDOWN" in vetoed.decision.reason_codes
    assert "OK" in vetoed.decision.reason_codes


def test_apply_gate_veto_preserves_first_gate_when_already_rejected() -> None:
    """When policy vetoes first (UNCALIBRATED at policy:calibration) and a
    downstream risk gate vetoes (RISK_GATE_EXPOSURE), the decision gate must
    preserve the first gate (policy:calibration), not overwrite with
    risk:exposure. Both reason codes are retained.
    """
    # Policy abstains with UNCALIBRATED (calibration_required=True, no calibrator)
    rec, _ = decide(
        _base_inputs(
            calibrator=None,
            calibration_required=True,
        )
    )
    assert rec.decision.status == "abstained"
    assert rec.decision.gate == "policy:calibration"
    assert rec.decision.reason_codes == ["UNCALIBRATED"]

    # Downstream risk gate vetoes with RISK_GATE_EXPOSURE (status → "rejected")
    vetoed = apply_gate_veto(rec, "risk:exposure", "RISK_GATE_EXPOSURE")
    assert vetoed.decision.status == "rejected"
    # Gate preserved: first rejection was policy:calibration, not risk:exposure
    assert vetoed.decision.gate == "policy:calibration"
    # Both reason codes retained
    assert vetoed.decision.reason_codes == ["UNCALIBRATED", "RISK_GATE_EXPOSURE"]
