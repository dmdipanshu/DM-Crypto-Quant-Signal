"""Phase 6 Track B: paper account state tests (offline, deterministic)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crypto_engine.paper_trading.state import (
    CorruptStateError,
    account_hash,
    equity,
    flatten,
    halt,
    init_account,
    is_processed,
    load_account,
    mark_processed,
    record_rejection,
    save_account,
)
from tests.fixtures.backtest_fixtures import small_cost, zero_cost


def _make(tmp_path: Path, **kw):
    kw.setdefault("symbol", "BTC/USDT")
    kw.setdefault("timeframe", "1h")
    kw.setdefault("starting_equity", 10000.0)
    kw.setdefault("cost", zero_cost())
    return init_account(tmp_path / "account.json", **kw)


def test_init_load_roundtrip(tmp_path: Path) -> None:
    acct = _make(tmp_path)
    loaded = load_account(tmp_path / "account.json")
    assert loaded.account_id == acct.account_id
    assert loaded.cash == pytest.approx(10000.0)
    assert loaded.status == "open"
    assert loaded.checksum and loaded.checksum == acct.checksum


def test_missing_file_actionable(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="sf paper init"):
        load_account(tmp_path / "nope" / "account.json")


def test_checksum_tamper_detected(tmp_path: Path) -> None:
    target = tmp_path / "account.json"
    _make(tmp_path)
    raw = target.read_text(encoding="utf-8").replace('"cash": 10000.0', '"cash": 99999.0')
    target.write_text(raw, encoding="utf-8")
    with pytest.raises(CorruptStateError, match="checksum"):
        load_account(target)


def test_corrupt_json_detected(tmp_path: Path) -> None:
    target = tmp_path / "account.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(CorruptStateError, match="cannot read"):
        load_account(target)


def test_init_refuses_overwrite(tmp_path: Path) -> None:
    _make(tmp_path)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        _make(tmp_path)


def test_duplicate_mark_idempotent(tmp_path: Path) -> None:
    acct = _make(tmp_path)
    assert not is_processed(acct, "abc")
    mark_processed(acct, "abc")
    mark_processed(acct, "abc")
    assert is_processed(acct, "abc")
    assert acct.processed_signal_ids.count("abc") == 1


def test_rejection_recorded_and_consumes_id(tmp_path: Path) -> None:
    acct = _make(tmp_path)
    record_rejection(acct, "sig1", "STALE_FUTURE", "2024-01-01T00:00:00+00:00")
    assert is_processed(acct, "sig1")
    assert acct.rejections[-1]["reason"] == "STALE_FUTURE"


def test_halt_blocks_new_positions_flag(tmp_path: Path) -> None:
    acct = _make(tmp_path)
    halt(acct, "MANUAL")
    assert acct.status == "halted"
    assert acct.halt_reason == "MANUAL"


def test_equity_mark_math(tmp_path: Path) -> None:
    acct = _make(tmp_path)
    assert equity(acct, 50000.0) == pytest.approx(10000.0)
    acct.position = {"side": "LONG", "qty": 1.0}
    assert equity(acct, 101.0) == pytest.approx(10101.0)
    acct.position = {"side": "SHORT", "qty": 1.0}
    assert equity(acct, 99.0) == pytest.approx(10000.0 - 99.0)
    with pytest.raises(ValueError, match="mark_price"):
        equity(acct, -5.0)


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


def test_flatten_long_with_costs(tmp_path: Path) -> None:
    target = tmp_path / "account.json"
    acct = init_account(
        target, symbol="BTC/USDT", timeframe="1h", starting_equity=10000.0, cost=small_cost()
    )
    acct.cash = 9000.0
    _open_position(acct, side="LONG", entry_price=100.0, qty=10.0)
    fill = flatten(acct, 110.0, "2024-01-02T00:00:00+00:00")
    assert fill is not None and fill["side"] == "SELL"
    assert acct.position is None
    assert len(acct.trades_closed) == 1
    trade = acct.trades_closed[0]
    assert trade["paper_reason"] == "MANUAL_FLATTEN"
    assert trade["gross_pnl"] > 0
    # Curve stays consistent: last point is flat cash.
    assert acct.equity_curve[-1][1] == pytest.approx(acct.cash)
    save_account(acct, target)
    assert load_account(target).cash == pytest.approx(acct.cash)


def test_flatten_flat_is_noop(tmp_path: Path) -> None:
    acct = _make(tmp_path)
    assert flatten(acct, 100.0, "2024-01-02T00:00:00+00:00") is None
    assert acct.trades_closed == []


def test_save_load_preserves_hash(tmp_path: Path) -> None:
    target = tmp_path / "account.json"
    acct = _make(tmp_path)
    mark_processed(acct, "x")
    save_account(acct, target)
    assert account_hash(load_account(target)) == account_hash(acct)
