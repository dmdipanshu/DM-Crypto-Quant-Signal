"""Q7: paper PnL, exposure, drawdown, and risk-limit tests (offline, deterministic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.paper_trading.loop import step
from crypto_engine.paper_trading.orders import OrderManager, OrderSide
from crypto_engine.paper_trading.risk_extensions import (
    AbnormalSlippageCheck,
    DailyLossLimitCheck,
    DailyLossTracker,
    InvalidPriceCheck,
)
from crypto_engine.paper_trading.state import (
    PaperAccount,
    account_hash,
    equity,
    halt,
    init_account,
    is_processed,
)
from crypto_engine.signals.schema import CostEstimate, SignalRecord
from tests.fixtures.backtest_fixtures import paper_bar, paper_gates_ctx, paper_signal


def _make_account(tmp_path, **kw) -> PaperAccount:
    kw.setdefault("symbol", "BTC/USDT")
    kw.setdefault("timeframe", "1h")
    kw.setdefault("starting_equity", 10000.0)
    kw.setdefault("cost", CostEstimate())
    return init_account(tmp_path / "account.json", **kw)


def _make_manager(acct: PaperAccount) -> OrderManager:
    return OrderManager(acct, ExecutionCostModel(CostEstimate()))


def _fill_partially(om: OrderManager, order_id: str, side: str, price: float = 100.0) -> None:
    order = om.get_order(order_id)
    assert order is not None
    order.filled_qty = 1.0
    order.status = "PARTIALLY_FILLED"
    order.avg_fill_price = price


def test_unrealized_pnl_long(tmp_path) -> None:
    acct = _make_account(tmp_path)
    om = _make_manager(acct)
    side: OrderSide = "BUY"
    order = om.submit_order("c-1", "BTC/USDT", "1h", side, "LIMIT", 1.0, price=100.0)
    om.accept_order(order.order_id)
    _fill_partially(om, order.order_id, "BUY", 100.0)
    assert om.unrealized_pnl(110.0) == pytest.approx(10.0)
    assert om.unrealized_pnl(100.0) == pytest.approx(0.0)
    assert om.unrealized_pnl(90.0) == pytest.approx(-10.0)


def test_unrealized_pnl_short(tmp_path) -> None:
    acct = _make_account(tmp_path)
    om = _make_manager(acct)
    side: OrderSide = "SELL"
    order = om.submit_order("c-1", "BTC/USDT", "1h", side, "LIMIT", 1.0, price=100.0)
    om.accept_order(order.order_id)
    _fill_partially(om, order.order_id, "SELL", 100.0)
    assert om.unrealized_pnl(90.0) == pytest.approx(10.0)
    assert om.unrealized_pnl(100.0) == pytest.approx(0.0)
    assert om.unrealized_pnl(110.0) == pytest.approx(-10.0)


def test_realized_pnl_after_close(tmp_path) -> None:
    acct = _make_account(tmp_path)
    om = _make_manager(acct)
    buy = om.submit_order("c-1", "BTC/USDT", "1h", "BUY", "MARKET", 1.0)
    om.accept_order(buy.order_id)
    om.process_bar("2024-01-01T00:00:00+00:00", 100.0, 101.0, 99.0, 100.0)
    assert buy.status == "FILLED"
    sell = om.submit_order("c-2", "BTC/USDT", "1h", "SELL", "MARKET", 1.0)
    om.accept_order(sell.order_id)
    om.process_bar("2024-01-01T01:00:00+00:00", 110.0, 111.0, 109.0, 110.0)
    assert sell.status == "FILLED"
    assert om.realized_pnl() == pytest.approx(100.0 + 110.0)


def test_total_pnl(tmp_path) -> None:
    acct = _make_account(tmp_path)
    om = _make_manager(acct)
    buy = om.submit_order("c-1", "BTC/USDT", "1h", "BUY", "LIMIT", 1.0, price=100.0)
    om.accept_order(buy.order_id)
    _fill_partially(om, buy.order_id, "BUY", 100.0)
    mark = 110.0
    total = om.total_pnl(mark)
    assert total == pytest.approx(om.realized_pnl() + om.unrealized_pnl(mark))
    assert total == pytest.approx(10.0)


def test_exposure_pct(tmp_path) -> None:
    acct = _make_account(tmp_path)
    om = _make_manager(acct)
    order = om.submit_order("c-1", "BTC/USDT", "1h", "BUY", "LIMIT", 1.0, price=100.0)
    om.accept_order(order.order_id)
    order.filled_qty = 1.0
    order.status = "PARTIALLY_FILLED"
    order.avg_fill_price = 100.0
    eq = equity(acct, 100.0)
    assert om.exposure_pct(100.0) == pytest.approx(100.0 / eq * 100.0)


def test_drawdown_pct(tmp_path) -> None:
    acct = _make_account(tmp_path)
    om = _make_manager(acct)
    acct.cash = 9000.0
    acct.equity_curve = [
        ["2024-01-01T00:00:00+00:00", 10000.0],
        ["2024-01-01T01:00:00+00:00", 9500.0],
    ]
    assert om.drawdown_pct(100.0) == pytest.approx(10.0)


def test_daily_loss_limit_breached(tmp_path) -> None:
    acct = _make_account(tmp_path)
    tracker = DailyLossTracker()
    tracker.record_trade({"net_pnl": -600.0}, "2024-01-01T10:00:00+00:00")
    assert tracker.daily_loss_amount("2024-01-01") == pytest.approx(-600.0)
    assert tracker.daily_loss_pct("2024-01-01", 10000.0) == pytest.approx(6.0)
    assert tracker.is_daily_loss_breached("2024-01-01", 10000.0, 5.0) is True
    om = _make_manager(acct)
    om.daily_loss_tracker = tracker
    ok, reason = DailyLossLimitCheck.check_daily_loss(om, "2024-01-01T10:00:00+00:00", 5.0)
    assert ok is False
    assert reason == "DAILY_LOSS_LIMIT_EXCEEDED"


def test_daily_loss_limit_not_breached(tmp_path) -> None:
    acct = _make_account(tmp_path)
    tracker = DailyLossTracker()
    tracker.record_trade({"net_pnl": -400.0}, "2024-01-01T10:00:00+00:00")
    assert tracker.daily_loss_pct("2024-01-01", 10000.0) == pytest.approx(4.0)
    assert tracker.is_daily_loss_breached("2024-01-01", 10000.0, 5.0) is False
    om = _make_manager(acct)
    om.daily_loss_tracker = tracker
    ok, reason = DailyLossLimitCheck.check_daily_loss(om, "2024-01-01T10:00:00+00:00", 5.0)
    assert ok is True
    assert reason is None


def test_abnormal_slippage_rejected() -> None:
    ok, reason = AbnormalSlippageCheck.check_slippage(101.0, 100.0, 50.0)
    assert ok is False
    assert reason == "ABNORMAL_SLIPPAGE"


def test_abnormal_slippage_accepted() -> None:
    ok, reason = AbnormalSlippageCheck.check_slippage(101.0, 100.0, 100.0)
    assert ok is True
    assert reason is None
    ok, reason = AbnormalSlippageCheck.check_slippage(100.5, 100.0, 100.0)
    assert ok is True
    assert reason is None


@pytest.mark.parametrize("price", [0.0, -1.5, float("nan"), float("inf"), None])
def test_invalid_price_rejected(price) -> None:
    ok, reason = InvalidPriceCheck.check_price(price)
    assert ok is False
    assert reason == "INVALID_PRICE"


def test_invalid_price_accepted() -> None:
    for price in [0.0001, 1.0, 100.0, 1e308]:
        ok, reason = InvalidPriceCheck.check_price(price)
        assert ok is True
        assert reason is None


def test_kill_switch_halted_rejects_new_orders(tmp_path) -> None:
    acct = _make_account(tmp_path)
    halt(acct, "MANUAL")
    assert acct.status == "halted"
    cost = ExecutionCostModel(CostEstimate())
    sig = paper_signal("LONG", datetime(2024, 1, 1, tzinfo=UTC), seed=1)
    assert isinstance(sig, SignalRecord)
    bar = paper_bar(datetime(2024, 1, 1, 1, tzinfo=UTC), 100.0, 101.0, 99.0, 100.0)
    acct2, res = step(acct, sig, bar, paper_gates_ctx(), cost)
    assert res.status == "rejected"
    assert res.reason == "HALTED"
    assert not is_processed(acct2, sig.signal_id)
    assert acct2.position is None
    assert acct.position is None


def test_kill_switch_position_still_marked(tmp_path) -> None:
    acct = _make_account(tmp_path)
    cost = ExecutionCostModel(CostEstimate())
    sig = paper_signal("LONG", datetime(2024, 1, 1, tzinfo=UTC), seed=1)
    bar = paper_bar(datetime(2024, 1, 1, 1, tzinfo=UTC), 100.0, 101.0, 99.0, 100.0)
    acct, _ = step(acct, sig, bar, paper_gates_ctx(), cost)
    assert acct.position is not None
    cash = acct.cash
    entry = float(acct.position["entry_price"])
    qty = float(acct.position["qty"])
    halt(acct, "RISK")
    assert acct.status == "halted"
    assert equity(acct, entry * 2.0) == pytest.approx(cash + qty * entry * 2.0)


def test_duplicate_order_rejected(tmp_path) -> None:
    acct = _make_account(tmp_path)
    cost = ExecutionCostModel(CostEstimate())
    gen = datetime(2024, 1, 1, tzinfo=UTC)
    sig = paper_signal("LONG", gen, seed=1)
    bar = paper_bar(gen + timedelta(hours=1), 100.0, 101.0, 99.0, 100.0)
    acct, first = step(acct, sig, bar, paper_gates_ctx(), cost)
    assert first.status == "accepted" and first.reason == "OPENED"
    assert acct.position is not None
    before = account_hash(acct)
    acct2, res = step(acct, sig, bar, paper_gates_ctx(), cost)
    assert res.status == "rejected"
    assert res.reason == "DUPLICATE"
    assert account_hash(acct2) == before
    assert acct2.position is not None


def test_stale_signal_rejected(tmp_path) -> None:
    acct = _make_account(tmp_path)
    cost = ExecutionCostModel(CostEstimate())
    gen = datetime(2024, 1, 1, 1, tzinfo=UTC)
    sig = paper_signal("LONG", gen, seed=2)
    assert isinstance(sig, SignalRecord)
    bar = paper_bar(gen, 100.0, 101.0, 99.0, 100.0)
    acct2, res = step(acct, sig, bar, paper_gates_ctx(), cost)
    assert res.status == "rejected"
    assert res.reason == "STALE_FUTURE"
    assert acct2.position is None
    assert is_processed(acct2, sig.signal_id)
