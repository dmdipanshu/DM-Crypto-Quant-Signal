"""Phase Q30: paper trading computed-fields and persistence tests (offline, deterministic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crypto_engine.paper_trading.state import (
    CorruptStateError,
    equity,
    flatten,
    init_account,
    load_account,
    record_rejection,
    save_account,
)
from tests.fixtures.backtest_fixtures import zero_cost


def _make(tmp_path: Path, **kw):
    kw.setdefault("symbol", "BTC/USDT")
    kw.setdefault("timeframe", "1h")
    kw.setdefault("starting_equity", 10000.0)
    kw.setdefault("cost", zero_cost())
    return init_account(tmp_path / "account.json", **kw)


def _open_position(acct, side: str = "LONG", entry_price: float = 100.0, qty: float = 1.0) -> None:
    acct.position = {
        "side": side,
        "entry_ts": "2024-01-01T00:00:00+00:00",
        "entry_price": entry_price,
        "qty": qty,
        "notional": entry_price * qty,
        "entry_fee": 0.0,
        "funding_accrued": 0.0,
        "entry_fill": {
            "ts": "2024-01-01T00:00:00+00:00",
            "side": "BUY" if side == "LONG" else "SELL",
            "price": entry_price,
            "qty": qty,
            "fee_bp": 0.0,
            "spread_bp": 0.0,
            "slippage_bp": 0.0,
            "notional": entry_price * qty,
            "cost_total": 0.0,
        },
        "stop_loss": None,
        "take_profit": None,
        "bars_held": 2,
    }


def test_computed_fields_serialized(tmp_path: Path) -> None:
    """Computed fields appear in model_dump(mode="json") and are derived."""
    target = tmp_path / "account.json"
    acct = init_account(
        target, symbol="BTC/USDT", timeframe="1h", starting_equity=10000.0, cost=zero_cost()
    )
    dump = acct.model_dump(mode="json")
    # Fresh account: all computed fields are zero
    assert dump["total_equity"] == 10000.0  # == cash (no position)
    assert dump["realized_pnl"] == 0.0
    assert dump["unrealized_pnl"] == 0.0
    assert dump["total_pnl"] == 0.0
    assert dump["max_drawdown_pct"] == 0.0
    assert dump["win_rate"] == 0.0


def test_computed_fields_with_position(tmp_path: Path) -> None:
    """Computed fields reflect an open position at mark price."""
    target = tmp_path / "account.json"
    acct = init_account(
        target, symbol="BTC/USDT", timeframe="1h", starting_equity=10000.0, cost=zero_cost()
    )
    acct.cash = 9000.0
    acct.equity_mark_ts = "2024-01-02T00:00:00+00:00"
    _open_position(acct, side="LONG", entry_price=100.0, qty=10.0)
    # equity_mark = total equity = cash + position value at mark price (110)
    acct.equity_mark = 9000.0 + 10.0 * 110.0  # = 10100.0
    dump = acct.model_dump(mode="json")
    # total_equity = equity_mark = 10100
    assert dump["total_equity"] == pytest.approx(10100.0)
    # unrealized_pnl = total_equity - starting_equity - realized = 10100 - 10000 - 0 = 100
    assert dump["unrealized_pnl"] == pytest.approx(100.0)
    # realized_pnl = 0 (no closed trades)
    assert dump["realized_pnl"] == 0.0
    # total_pnl = total_equity - starting_equity = 100
    assert dump["total_pnl"] == pytest.approx(100.0)


def test_computed_fields_with_closed_trades(tmp_path: Path) -> None:
    """Computed fields reflect closed trades (realized P&L)."""
    target = tmp_path / "account.json"
    acct = init_account(
        target, symbol="BTC/USDT", timeframe="1h", starting_equity=10000.0, cost=zero_cost()
    )
    acct.trades_closed = [
        {"net_pnl": 150.0, "gross_pnl": 160.0, "costs_total": 10.0},
        {"net_pnl": -50.0, "gross_pnl": -40.0, "costs_total": 10.0},
        {"net_pnl": 200.0, "gross_pnl": 210.0, "costs_total": 10.0},
    ]
    # When flat (no position), equity_mark = cash = starting_equity + realized_pnl
    acct.equity_mark = 10000.0 + 300.0  # = 10300.0
    acct.equity_mark_ts = "2024-01-02T00:00:00+00:00"
    dump = acct.model_dump(mode="json")
    # realized_pnl = 150 - 50 + 200 = 300
    assert dump["realized_pnl"] == pytest.approx(300.0)
    # total_pnl = total_equity - starting_equity = 10300 - 10000 = 300
    assert dump["total_pnl"] == pytest.approx(300.0)
    # unrealized_pnl = 0 (no open position)
    assert dump["unrealized_pnl"] == 0.0
    # win_rate = 2 wins / 3 trades = 0.667
    assert dump["win_rate"] == pytest.approx(2 / 3, rel=1e-6)


def test_computed_fields_max_drawdown(tmp_path: Path) -> None:
    """max_drawdown_pct computed from equity curve."""
    target = tmp_path / "account.json"
    acct = init_account(
        target, symbol="BTC/USDT", timeframe="1h", starting_equity=10000.0, cost=zero_cost()
    )
    acct.equity_curve = [
        ["2024-01-01T00:00:00+00:00", 10000.0],
        ["2024-01-01T01:00:00+00:00", 11000.0],
        ["2024-01-01T02:00:00+00:00", 9000.0],
        ["2024-01-01T03:00:00+00:00", 10500.0],
    ]
    dump = acct.model_dump(mode="json")
    # Peak = 11000, trough = 9000, drawdown = (11000-9000)/11000 = 18.18%
    assert dump["max_drawdown_pct"] == pytest.approx((11000.0 - 9000.0) / 11000.0 * 100.0, rel=1e-6)


def test_computed_fields_excluded_from_checksum(tmp_path: Path) -> None:
    """Computed fields do not affect the checksum payload."""
    target = tmp_path / "account.json"
    acct = init_account(
        target, symbol="BTC/USDT", timeframe="1h", starting_equity=10000.0, cost=zero_cost()
    )
    from crypto_engine.paper_trading.state import account_hash

    h1 = account_hash(acct)
    # Modify a computed field — checksum should NOT change
    acct.total_equity = 999999.0
    acct.realized_pnl = 999999.0
    h2 = account_hash(acct)
    assert h1 == h2


def test_persistence_roundtrip_with_computed_fields(tmp_path: Path) -> None:
    """Save/load preserves all computed fields correctly."""
    target = tmp_path / "account.json"
    acct = init_account(
        target, symbol="BTC/USDT", timeframe="1h", starting_equity=10000.0, cost=zero_cost()
    )
    acct.cash = 9500.0
    acct.equity_mark_ts = "2024-01-02T00:00:00+00:00"
    _open_position(acct, side="LONG", entry_price=100.0, qty=5.0)
    acct.trades_closed = [{"net_pnl": 100.0, "gross_pnl": 110.0, "costs_total": 10.0}]
    acct.equity_curve = [
        ["2024-01-01T00:00:00+00:00", 10000.0],
        ["2024-01-01T01:00:00+00:00", 10100.0],
    ]
    # equity_mark = total equity = cash + position value at mark price (105)
    acct.equity_mark = 9500.0 + 5.0 * 105.0  # = 10025.0
    save_account(acct, target)
    loaded = load_account(target)
    dump = loaded.model_dump(mode="json")
    assert dump["total_equity"] == pytest.approx(acct.model_dump(mode="json")["total_equity"])
    assert dump["realized_pnl"] == pytest.approx(100.0)
    # unrealized_pnl = total_pnl - realized = 25 - 100 = -75
    assert dump["unrealized_pnl"] == pytest.approx(-75.0)
    assert dump["total_pnl"] == pytest.approx(25.0)
    assert dump["win_rate"] == pytest.approx(1.0)


def test_live_order_isolation_no_exchange_calls(tmp_path: Path) -> None:
    """Paper trading state module never imports or calls exchange APIs."""
    import inspect

    import crypto_engine.paper_trading.state as state_mod

    src = inspect.getsource(state_mod)
    # Must not reference any live exchange / order execution
    forbidden = ["ccxt", "binance", "create_order", "fetch_ticker", "fetch_balance"]
    for token in forbidden:
        assert token not in src, f"state.py must not reference {token}"


def test_corrupt_state_never_silently_replaced(tmp_path: Path) -> None:
    """Corrupted account raises CorruptStateError — never silently recovered."""
    target = tmp_path / "account.json"
    _make(tmp_path)
    # Corrupt the cash value without updating checksum
    raw = target.read_text(encoding="utf-8").replace('"cash": 10000.0', '"cash": 99999.0')
    target.write_text(raw, encoding="utf-8")
    with pytest.raises(CorruptStateError, match="checksum"):
        load_account(target)
    # File still exists but is unreadable — no silent replacement
    assert target.is_file()


def test_equity_function_with_position(tmp_path: Path) -> None:
    """Standalone equity() function computes correctly with positions."""
    acct = _make(tmp_path)
    # Flat: equity == cash == starting_equity
    assert equity(acct, 50000.0) == pytest.approx(10000.0)
    # LONG position: equity = cash + qty * mark (cash NOT reduced on open)
    _open_position(acct, side="LONG", entry_price=100.0, qty=10.0)
    assert equity(acct, 101.0) == pytest.approx(10000.0 + 10 * 101.0)
    # SHORT position: equity = cash - qty * mark (per equity() function)
    acct.position = {"side": "SHORT", "qty": 10.0, "entry_price": 100.0, "notional": 1000.0}
    acct.cash = 10000.0
    assert equity(acct, 99.0) == pytest.approx(10000.0 - 10 * 99.0)
    # Invalid mark price rejected
    with pytest.raises(ValueError, match="mark_price"):
        equity(acct, -5.0)


def test_flatten_preserves_persistence(tmp_path: Path) -> None:
    """Flatten then save/load roundtrips correctly."""
    target = tmp_path / "account.json"
    acct = init_account(
        target, symbol="BTC/USDT", timeframe="1h", starting_equity=10000.0, cost=zero_cost()
    )
    acct.cash = 9000.0
    _open_position(acct, side="LONG", entry_price=100.0, qty=10.0)
    fill = flatten(acct, 110.0, "2024-01-02T00:00:00+00:00")
    assert fill is not None
    save_account(acct, target)
    loaded = load_account(target)
    assert loaded.position is None
    assert len(loaded.trades_closed) == 1
    assert loaded.trades_closed[0]["paper_reason"] == "MANUAL_FLATTEN"


def test_rejection_prevents_reevaluation(tmp_path: Path) -> None:
    """Rejected signal id is consumed and never re-evaluated."""
    acct = _make(tmp_path)
    record_rejection(acct, "sig-1", "RISK_GATE_FRESHNESS", "2024-01-01T00:00:00+00:00")
    from crypto_engine.paper_trading.state import is_processed

    assert is_processed(acct, "sig-1")
    assert len(acct.rejections) == 1
    assert acct.rejections[0]["reason"] == "RISK_GATE_FRESHNESS"
