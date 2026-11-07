"""Phase 9 regression tests: label semantics, direction mapping, metric math.

Pins the verified conclusions:
- side="both" labels use long geometry: +1 = upper barrier first
  (long-profitable), -1 = lower barrier first (long stopped),
  0 = timeout/ambiguous (no directional edge within the horizon).
- Policy maps predicted classes -1/0/+1 to SHORT/NEUTRAL/LONG (NEUTRAL is
  abstained, never a trade).
- classification_report_small matches hand-counted confusion math.
- Default production settings veto everything (UNCALIBRATED first), which is
  why no trading backtest is possible without bypassing safety gates.

Offline, deterministic, small fixtures.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_engine.calibration.reliability import classification_report_small
from crypto_engine.labels.triple_barrier import (
    TripleBarrierConfig,
    label_triple_barrier,
    valid_mask,
)
from crypto_engine.signals.policy import PolicyInputs, decide
from tests.fixtures.signal_fixtures import StubBinaryModel, make_feature_frame


def _cfg(**kw) -> TripleBarrierConfig:
    base: dict = {"pt_mult": 2.0, "sl_mult": 1.0, "max_horizon_bars": 5, "vol_window": 5}
    base.update(kw)
    return TripleBarrierConfig(**base)


def _osc_base(n: int) -> tuple[np.ndarray, pd.DatetimeIndex]:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return 100.0 + 0.3 * np.sin(np.arange(n, dtype=float)), idx


def _frame_from_close(close: np.ndarray, idx: pd.DatetimeIndex) -> pd.DataFrame:
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(len(close), 50.0),
        },
        index=idx,
    )


def _drift_frame(n: int = 30, drift: float = 0.0) -> pd.DataFrame:
    """Gentle oscillation plus a linear drift (finite vol, deterministic)."""
    base, idx = _osc_base(n)
    return _frame_from_close(base + drift * np.arange(n, dtype=float), idx)


def _jump_up_frame() -> pd.DataFrame:
    base, idx = _osc_base(20)
    close = base.copy()
    close[10:] = 120.0  # +20% step: upper barrier touched first
    return _frame_from_close(close, idx)


def _jump_down_frame() -> pd.DataFrame:
    base, idx = _osc_base(20)
    close = base.copy()
    close[10:] = 80.0  # -20% step: lower barrier touched first
    return _frame_from_close(close, idx)


def _flat_after_vol_frame() -> pd.DataFrame:
    """Oscillation (bars 0-9) then flat: entries in the flat run time out."""
    base, idx = _osc_base(30)
    close = base.copy()
    close[10:] = 100.0
    return _frame_from_close(close, idx)


def test_both_side_plus_one_means_upper_barrier_first() -> None:
    out = label_triple_barrier(_jump_up_frame(), _cfg())
    assert valid_mask(out).iloc[9]
    row = out.iloc[9]
    assert row["label"] == 1 and row["barrier_hit"] == "pt"
    # Outcome observable strictly after the entry bar.
    assert row["observable_at"] == out.index[10] > out.index[9]


def test_both_side_minus_one_means_lower_barrier_first() -> None:
    out = label_triple_barrier(_jump_down_frame(), _cfg())
    assert valid_mask(out).iloc[9]
    row = out.iloc[9]
    assert row["label"] == -1 and row["barrier_hit"] == "sl"
    assert row["observable_at"] == out.index[10] > out.index[9]


def test_timeout_means_no_barrier_touched() -> None:
    out = label_triple_barrier(_flat_after_vol_frame(), _cfg())
    timeouts = out[out["barrier_hit"] == "timeout"]
    assert len(timeouts) > 0
    assert (timeouts["label"] == 0).all()
    assert (timeouts["tte_bars"] == 5).all()
    assert (valid_mask(out) & (out["barrier_hit"] == "timeout")).any()


def test_policy_maps_classes_to_directions() -> None:
    """Predicted -1/0/+1 must map to SHORT/NEUTRAL/LONG (NEUTRAL abstains)."""

    class _Multi(StubBinaryModel):
        family = "multi"

        def __init__(self, label: int) -> None:
            super().__init__(feature_names=["a", "b"], score=0.7, label=label)
            self._classes = [-1, 0, 1]

    import numpy as np

    frame, _ = make_feature_frame(n=40, seed=0)
    row = {c: float(frame.iloc[0][c]) for c in ["a", "b"]}

    def _inputs(label: int) -> PolicyInputs:
        model = _Multi(label)
        model.fit(frame, np.array([label] * 40))
        return PolicyInputs(
            feature_row=row,
            feature_names=["a", "b"],
            model=model,
            model_id="m",
            model_version="v1",
            model_artifact_hash="h" * 16,
            calibration_required=False,
            require_edge=False,
            generated_at="2024-01-10T00:00:00+00:00",
            valid_until="2024-01-10T01:00:00+00:00",
            symbol="BTC/USDT",
            exchange="binance",
            timeframe="1h",
        )

    rec, _ = decide(_inputs(1))
    assert rec.direction == "LONG" and rec.decision.status == "approved"
    rec, _ = decide(_inputs(0))
    assert rec.direction == "NEUTRAL" and rec.decision.status == "abstained"
    assert rec.entry_ref is None and rec.stop_loss is None and rec.take_profit is None
    rec, _ = decide(_inputs(-1))
    assert rec.direction == "SHORT" and rec.decision.status == "approved"


def test_report_math_matches_hand_count() -> None:
    """classification_report_small must equal hand-computed confusion math."""
    y_true = np.array([-1, -1, -1, 0, 0, 1, 1, 1, 1])
    y_pred = np.array([-1, -1, 1, 0, 1, 1, -1, 1, 0])
    rep = classification_report_small(y_true, y_pred)
    assert rep["accuracy"] == pytest.approx(5 / 9)
    # Hand count: cm rows=true [-1,0,1], cols=pred [-1,0,1]:
    # -1: pred -1 x2, +1 x1 ; 0: pred 0 x1, +1 x1 ; +1: pred -1 x1, 0 x1, +1 x2.
    assert rep["confusion_matrix"] == [[2, 0, 1], [0, 1, 1], [1, 1, 2]]
    p_minus1 = rep["per_class"]["-1"] if "-1" in rep["per_class"] else rep["per_class"][-1]
    assert p_minus1["precision"] == pytest.approx(2 / 3)
    assert p_minus1["recall"] == pytest.approx(2 / 3)


def test_default_production_settings_veto_trading_signal() -> None:
    """Default config (calibration+edge required, no calibrator/edge) vetoes.

    This is the fail-closed behavior that blocks any trading backtest
    without bypassing safety gates (which is forbidden).
    """
    from crypto_engine.config.settings import load_settings

    settings = load_settings()
    assert settings.signal.calibration_required is True
    assert settings.signal.require_edge is True
    frame, _ = make_feature_frame(n=40, seed=0)
    row = {c: float(frame.iloc[0][c]) for c in ["a", "b"]}
    import numpy as np

    model = StubBinaryModel(feature_names=["a", "b"], score=0.99, label=1)
    model.fit(frame, np.array([1] * 40))
    rec, _ = decide(
        PolicyInputs(
            feature_row=row,
            feature_names=["a", "b"],
            model=model,
            model_id="m",
            model_version="v1",
            model_artifact_hash="h" * 16,
            calibration_required=True,
            require_edge=True,
            generated_at="2024-01-10T00:00:00+00:00",
            valid_until="2024-01-10T01:00:00+00:00",
            symbol="BTC/USDT",
            exchange="binance",
            timeframe="1h",
        )
    )
    assert rec.direction == "NO_TRADE"
    assert "UNCALIBRATED" in rec.decision.reason_codes
