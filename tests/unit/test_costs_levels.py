"""Cost/levels tests: totals, decimal guard, geometry, tick rounding (offline)."""

from __future__ import annotations

import pytest

from crypto_engine.signals.costs import CostModel, edge_clears_cost
from crypto_engine.signals.levels import suggest_levels, validate_levels


def test_cost_total_math() -> None:
    """Additive total across the four legs."""
    model = CostModel(fee_bp=10.0, spread_bp=2.0, slippage_bp=5.0, funding_bp=1.0)
    assert model.total_bp() == 18.0
    via_config = CostModel.from_config(fee_bp=10.0, slippage_bp=5.0)
    assert via_config.total_bp() == 15.0


def test_cost_decimal_mixup_rejected() -> None:
    """Magnitudes >= 10000 bp are rejected as likely decimal-mixup."""
    with pytest.raises(ValueError, match="decimal-mixup"):
        CostModel(fee_bp=10000.0)
    with pytest.raises(ValueError, match="decimal-mixup"):
        CostModel(slippage_bp=20000.0)
    with pytest.raises(ValueError):
        CostModel(fee_bp=-1.0)


def test_edge_clears_cost_gate() -> None:
    """Pure edge gate: edge >= cost + margin."""
    assert edge_clears_cost(15.0, 10.0, 5.0) is True
    assert edge_clears_cost(14.9, 10.0, 5.0) is False
    assert edge_clears_cost(10.0, 10.0, 0.0) is True
    with pytest.raises(ValueError):
        edge_clears_cost(float("nan"), 10.0)


def test_levels_long_short_geometry() -> None:
    """LONG stop<entry<take; SHORT mirrored."""
    lv, err = validate_levels("LONG", 100.0, 90.0, 120.0)
    assert err is None and lv is not None and lv.direction == "LONG"
    lv, err = validate_levels("SHORT", 100.0, 110.0, 80.0)
    assert err is None and lv is not None and lv.direction == "SHORT"
    _, err = validate_levels("LONG", 100.0, 110.0, 120.0)
    assert err is not None
    _, err = validate_levels("SHORT", 100.0, 90.0, 80.0)
    assert err is not None
    _, err = validate_levels("NEUTRAL", None, 90.0, None)
    assert err is not None


def test_levels_tick_rounding_conservative() -> None:
    """Tick rounding: stop tightens (LONG down, SHORT up)."""
    lv, err = validate_levels("LONG", 100.03, 90.07, 120.02, tick_size=0.1)
    assert err is None and lv is not None
    assert lv.stop_loss is not None and lv.stop_loss <= 90.07
    assert lv.stop_loss == pytest.approx(90.0)
    lv, err = validate_levels("SHORT", 100.03, 110.07, 80.02, tick_size=0.1)
    assert err is None and lv is not None
    assert lv.stop_loss is not None and lv.stop_loss >= 110.07
    assert lv.stop_loss == pytest.approx(110.1)


def test_levels_take_optional() -> None:
    """Take-profit is optional (None allowed for runners)."""
    lv, err = validate_levels("LONG", 100.0, 90.0, None)
    assert err is None and lv is not None and lv.take_profit is None


def test_suggest_levels_math() -> None:
    """Suggestion math both sides with risk/reward."""
    got = suggest_levels("LONG", 100.0, 0.01, pt_mult=2.0, sl_mult=1.0)
    assert got["stop_loss"] == pytest.approx(99.0)
    assert got["take_profit"] == pytest.approx(102.0)
    assert got["risk_reward"] == pytest.approx(2.0)
    got = suggest_levels("SHORT", 100.0, 0.01, pt_mult=2.0, sl_mult=1.0)
    assert got["stop_loss"] == pytest.approx(101.0)
    assert got["take_profit"] == pytest.approx(98.0)
    with pytest.raises(ValueError):
        suggest_levels("NEUTRAL", 100.0, 0.01)
    with pytest.raises(ValueError):
        suggest_levels("LONG", -100.0, 0.01)
