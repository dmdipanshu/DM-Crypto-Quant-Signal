"""Q7 Phase 2: paper order lifecycle tests (offline, deterministic).

Covers submission, acceptance, rejection, cancellation, partial/full fills,
limit/market fill logic, audit trails, order IDs, and remaining-qty/status
properties. Uses OrderManager + PaperAccount inline with ExecutionCostModel.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.paper_trading.orders import OrderManager
from crypto_engine.paper_trading.state import PaperAccount, init_account
from tests.fixtures.backtest_fixtures import paper_bar, small_cost, zero_cost

_ORDER_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _make_manager(tmp_path, starting_equity: float = 10000.0, cost_est=None) -> tuple[OrderManager, PaperAccount]:
    cost_est = cost_est or zero_cost()
    acct = init_account(
        tmp_path / "account.json",
        symbol="BTC/USDT",
        timeframe="1h",
        starting_equity=starting_equity,
        cost=cost_est,
    )
    return OrderManager(acct, ExecutionCostModel(cost_est)), acct


def _bar(ts, open_: float, high: float, low: float, close: float) -> dict:
    return paper_bar(ts, open_, high, low, close)


def _ts() -> str:
    return datetime(2024, 1, 1, tzinfo=UTC).isoformat()


def test_market_order_submission_and_fill(tmp_path) -> None:
    om, _ = _make_manager(tmp_path, cost_est=small_cost())
    bar = _bar(_ts(), 100.0, 102.0, 99.0, 100.0)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
    assert order.order_id and _ORDER_ID_RE.match(order.order_id)
    assert order.status == "SUBMITTED"
    assert order.filled_qty == 0.0
    om.accept_order(order.order_id)
    assert order.status == "ACCEPTED"
    fills = om.process_bar(bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"])
    assert len(fills) == 1
    assert order.status == "FILLED"
    assert order.filled_qty == 1.0
    assert order.fills[0].fee_bp == 10.0


def test_limit_order_buy_fills_when_low_below_limit(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    bar = _bar(_ts(), 101.0, 103.0, 99.0, 100.0)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "LIMIT", 1.0, price=100.0)
    om.accept_order(order.order_id)
    fills = om.process_bar(bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"])
    assert len(fills) == 1
    assert order.status == "FILLED"
    assert order.filled_qty == 1.0
    assert order.fills[0].price == 100.0


def test_limit_order_sell_fills_when_high_above_limit(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    bar = _bar(_ts(), 99.0, 101.0, 98.0, 100.0)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "SELL", "LIMIT", 1.0, price=100.0)
    om.accept_order(order.order_id)
    fills = om.process_bar(bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"])
    assert len(fills) == 1
    assert order.status == "FILLED"
    assert order.filled_qty == 1.0
    assert order.fills[0].price == 100.0


def test_limit_order_not_filled_when_price_not_touched(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    bar = _bar(_ts(), 102.0, 103.0, 101.0, 102.0)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "LIMIT", 1.0, price=100.0)
    om.accept_order(order.order_id)
    fills = om.process_bar(bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"])
    assert len(fills) == 0
    assert order.status == "ACCEPTED"
    assert order.is_open
    assert not order.is_terminal
    assert order.filled_qty == 0.0


def test_order_accept_and_reject(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
    assert order.status == "SUBMITTED"
    om.accept_order(order.order_id)
    assert order.status == "ACCEPTED"
    assert order.accepted_at is not None
    om.reject_order(order.order_id, "RISK_GATE_EXPOSURE")
    assert order.status == "REJECTED"
    assert order.rejection_reason == "RISK_GATE_EXPOSURE"
    assert order.is_terminal
    assert not order.is_open


def test_order_cancel(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "LIMIT", 1.0, price=100.0)
    om.accept_order(order.order_id)
    assert order.status == "ACCEPTED"
    om.cancel_order(order.order_id, "USER_CANCEL")
    assert order.status == "CANCELLED"
    assert order.cancel_reason == "USER_CANCEL"
    assert order.cancelled_at is not None
    assert order.is_terminal
    assert not order.is_open


def test_order_cannot_cancel_filled_order(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    bar = _bar(_ts(), 100.0, 102.0, 99.0, 100.0)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
    om.accept_order(order.order_id)
    om.process_bar(bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"])
    assert order.status == "FILLED"
    assert order.is_terminal
    with pytest.raises(ValueError, match="cannot cancel"):
        om.cancel_order(order.order_id, "LATE_CANCEL")


def test_partial_fill_stays_partially_filled(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "LIMIT", 10.0, price=100.0)
    om.accept_order(order.order_id)
    order.filled_qty = 4.0
    order.status = "PARTIALLY_FILLED"
    assert order.is_open
    assert not order.is_terminal
    assert order.remaining_qty == 6.0
    bar = _bar(_ts(), 102.0, 103.0, 101.0, 102.0)
    fills = om.process_bar(bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"])
    assert len(fills) == 0
    assert order.filled_qty == 4.0
    assert order.status == "PARTIALLY_FILLED"
    assert order.remaining_qty == 6.0


def test_order_rejected_for_insufficient_cash(tmp_path) -> None:
    om, acct = _make_manager(tmp_path, starting_equity=100.0)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "LIMIT", 10.0, price=100.0)
    notional = order.qty * (order.price or 0.0)
    assert notional > acct.cash
    om.reject_order(order.order_id, "INSUFFICIENT_CASH")
    assert order.status == "REJECTED"
    assert order.rejection_reason == "INSUFFICIENT_CASH"
    assert order.is_terminal
    assert not order.is_open


def test_order_id_uniqueness(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    ids: list[str] = []
    for i in range(5):
        order = om.submit_order(f"client-{i}", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
        assert _ORDER_ID_RE.match(order.order_id), order.order_id
        ids.append(order.order_id)
    assert len(set(ids)) == len(ids)


def test_order_audit_trail(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    bar = _bar(_ts(), 100.0, 102.0, 99.0, 100.0)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
    assert len(order.audit_events) == 1
    assert order.audit_events[0]["event"] == "submitted"
    assert "ts" in order.audit_events[0]
    om.accept_order(order.order_id)
    assert any(e["event"] == "accepted" for e in order.audit_events)
    om.process_bar(bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"])
    fill_events = [e for e in order.audit_events if e["event"] == "filled"]
    assert len(fill_events) == 1
    assert "fill_id" in fill_events[0]["detail"]
    for event in order.audit_events:
        assert "ts" in event and "event" in event


def test_order_remaining_qty(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "LIMIT", 10.0, price=100.0)
    assert order.remaining_qty == 10.0
    order.filled_qty = 3.0
    assert order.remaining_qty == 7.0
    order.filled_qty = 10.0
    assert order.remaining_qty == 0.0


def test_order_is_open_and_is_terminal(tmp_path) -> None:
    om, _ = _make_manager(tmp_path)
    order = om.submit_order("client-1", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
    assert order.is_open
    assert not order.is_terminal
    om.accept_order(order.order_id)
    assert order.is_open
    assert not order.is_terminal
    order.status = "PARTIALLY_FILLED"
    assert order.is_open
    assert not order.is_terminal

    order2 = om.submit_order("client-2", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
    om.accept_order(order2.order_id)
    bar = _bar(_ts(), 100.0, 102.0, 99.0, 100.0)
    om.process_bar(bar["ts"], bar["open"], bar["high"], bar["low"], bar["close"])
    assert not order2.is_open
    assert order2.is_terminal

    order3 = om.submit_order("client-3", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
    om.reject_order(order3.order_id, "TEST")
    assert not order3.is_open
    assert order3.is_terminal

    order4 = om.submit_order("client-4", "BTC/USDT", "1h", "BUY", "LIMIT", 1.0, price=100.0)
    om.accept_order(order4.order_id)
    om.cancel_order(order4.order_id, "TEST")
    assert not order4.is_open
    assert order4.is_terminal
