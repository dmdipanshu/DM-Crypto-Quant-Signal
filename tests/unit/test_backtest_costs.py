"""Unit tests for ExecutionCostModel (Phase 6 Track A).

Hand-computed, deterministic, offline. Reference legs (small_cost):
fee 10bp, spread 4bp, slip 6bp, funding 1bp/bar.
Price penalty = spread/2 + slippage = 2 + 6 = 8bp.
"""

from __future__ import annotations

import pytest

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.signals.schema import CostEstimate


def test_fill_price_buy_sell_exact() -> None:
    cost = CostEstimate(fee_bp=10.0, spread_bp=4.0, slippage_bp=6.0, funding_bp=1.0)
    model = ExecutionCostModel(cost)
    assert model.half_spread_plus_slip_bp() == pytest.approx(8.0)
    assert model.fill_price("BUY", 100.0) == pytest.approx(100.0 * 1.0008)
    assert model.fill_price("SELL", 100.0) == pytest.approx(100.0 * 0.9992)
    assert model.fill_price("BUY", 250.0) == pytest.approx(250.0 * 1.0008)
    assert model.fill_price("SELL", 250.0) == pytest.approx(250.0 * 0.9992)


def test_fill_fee_exact() -> None:
    cost = CostEstimate(fee_bp=10.0, spread_bp=4.0, slippage_bp=6.0, funding_bp=1.0)
    model = ExecutionCostModel(cost)
    assert model.fill_fee(1000.0) == pytest.approx(1.0)  # 1000 * 10/1e4
    assert model.fill_fee(25000.0) == pytest.approx(25.0)
    assert model.fill_fee(0.0) == pytest.approx(0.0)


def test_funding_multi_bar_exact() -> None:
    cost = CostEstimate(fee_bp=10.0, spread_bp=4.0, slippage_bp=6.0, funding_bp=1.0)
    model = ExecutionCostModel(cost)
    # 10000 * 1/1e4 * 3 = 3.0
    assert model.funding_for_bars(10000.0, 3) == pytest.approx(3.0)
    assert model.funding_for_bars(10000.0, 0) == pytest.approx(0.0)
    assert model.funding_for_bars(5000.0, 1) == pytest.approx(0.5)


def test_zero_cost_passthrough() -> None:
    model = ExecutionCostModel(CostEstimate())
    assert model.fill_price("BUY", 123.45) == pytest.approx(123.45)
    assert model.fill_price("SELL", 123.45) == pytest.approx(123.45)
    assert model.fill_fee(9999.0) == pytest.approx(0.0)
    assert model.funding_for_bars(9999.0, 10) == pytest.approx(0.0)


def test_decimal_mixup_rejected() -> None:
    with pytest.raises(Exception, match=r"decimal-mixup|greater|10000|>= 10000"):
        CostEstimate(fee_bp=10000.0)
    with pytest.raises(Exception, match=r"decimal-mixup|greater|10000|>= 10000"):
        CostEstimate(slippage_bp=1.5e4)


def test_invalid_inputs_rejected() -> None:
    model = ExecutionCostModel(CostEstimate())
    with pytest.raises(ValueError, match="BUY/SELL"):
        model.fill_price("HOLD", 100.0)
    with pytest.raises(ValueError, match="> 0"):
        model.fill_price("BUY", 0.0)
    with pytest.raises(ValueError, match="> 0"):
        model.fill_price("BUY", -5.0)
    with pytest.raises(ValueError, match=">= 0"):
        model.fill_fee(-1.0)
    with pytest.raises(ValueError, match="non-negative int"):
        model.funding_for_bars(100.0, -1)
