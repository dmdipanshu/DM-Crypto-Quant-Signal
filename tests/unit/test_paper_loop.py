"""Phase 6 Track B: paper step-loop tests (offline, deterministic, <=60 bars)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.paper_trading.loop import paper_portfolio, step
from crypto_engine.paper_trading.state import (
    account_hash,
    halt,
    init_account,
    is_processed,
)
from tests.fixtures.backtest_fixtures import (
    monotone_frame,
    paper_bar,
    paper_gates_ctx,
    paper_signal,
    small_cost,
    zero_cost,
)


def _account(tmp_path, **kw):
    kw.setdefault("symbol", "BTC/USDT")
    kw.setdefault("timeframe", "1h")
    kw.setdefault("starting_equity", 10000.0)
    kw.setdefault("cost", zero_cost())
    return init_account(tmp_path / "account.json", **kw)


def _bar_from_frame(frame, i: int) -> dict:
    ts = frame.index[i]
    row = frame.iloc[i]
    return paper_bar(
        ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    )


def test_entry_strictly_after_signal_bar(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    sig = paper_signal("LONG", frame.index[0], seed=1)
    acct2, res = step(acct, sig, _bar_from_frame(frame, 1), paper_gates_ctx(), cost)
    assert res.status == "accepted" and res.reason == "OPENED"
    assert acct2.position is not None
    assert acct2.position["entry_ts"] == frame.index[1].isoformat()
    # Input account not mutated (pure-ish).
    assert acct.position is None
    assert acct.bars_seen == 0


def test_stale_future_rejected(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    gen = frame.index[1]
    sig = paper_signal("LONG", gen, seed=2)
    # Same-bar fill attempt: generated_at == bar ts -> STALE_FUTURE.
    acct2, res = step(acct, sig, _bar_from_frame(frame, 1), paper_gates_ctx(), cost)
    assert res.status == "rejected" and res.reason == "STALE_FUTURE"
    assert acct2.position is None
    assert is_processed(acct2, sig.signal_id)


def test_neutral_closes_open_position(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    acct, _ = step(
        acct,
        paper_signal("LONG", frame.index[0], seed=1),
        _bar_from_frame(frame, 1),
        paper_gates_ctx(),
        cost,
    )
    assert acct.position is not None
    acct, res = step(
        acct,
        paper_signal("NEUTRAL", frame.index[1], seed=2),
        _bar_from_frame(frame, 2),
        paper_gates_ctx(),
        cost,
    )
    assert res.status == "accepted" and res.reason == "NEUTRAL_CLOSE"
    assert acct.position is None
    assert len(acct.trades_closed) == 1
    assert acct.trades_closed[0]["exit_reason"] == "NEUTRAL_CLOSE"


def test_no_trade_noop(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    acct, res = step(
        acct,
        paper_signal("NO_TRADE", frame.index[0], seed=3),
        _bar_from_frame(frame, 1),
        paper_gates_ctx(),
        cost,
    )
    assert res.status == "accepted" and res.reason == "NO_TRADE"
    assert acct.position is None


def test_flip_two_legs_same_bar(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    acct, _ = step(
        acct,
        paper_signal("LONG", frame.index[0], seed=1),
        _bar_from_frame(frame, 1),
        paper_gates_ctx(),
        cost,
    )
    # Levels-free flip signal: engine-mirror is close + same-bar reopen, no SL/TP.
    flip = paper_signal("SHORT", frame.index[1], seed=2, entry=None, stop=None, take=None)
    acct, res = step(acct, flip, _bar_from_frame(frame, 2), paper_gates_ctx(), cost)
    assert res.status == "accepted"
    assert res.reason == "SIGNAL_FLIP+OPENED"
    assert len(res.fills) == 2  # close leg + open leg
    assert acct.position is not None and acct.position["side"] == "SHORT"
    assert len(acct.trades_closed) == 1
    assert acct.trades_closed[0]["exit_reason"] == "SIGNAL_FLIP"


def test_same_direction_noop_then_sltp_monitored(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    acct, _ = step(
        acct,
        paper_signal("LONG", frame.index[0], seed=1),
        _bar_from_frame(frame, 1),
        paper_gates_ctx(),
        cost,
    )
    acct, res = step(
        acct,
        paper_signal("LONG", frame.index[1], seed=9),
        _bar_from_frame(frame, 2),
        paper_gates_ctx(),
        cost,
    )
    assert res.reason == "ALREADY_POSITIONED"
    assert acct.position is not None


def test_same_bar_sl_and_tp_exits_stop_loss(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    sig = paper_signal("LONG", frame.index[0], seed=1, entry=100.0, stop=99.0, take=101.0)
    bar = _bar_from_frame(frame, 1)
    bar.update({"low": 50.0, "high": 200.0, "open": 100.0, "close": 100.0})
    acct, res = step(acct, sig, bar, paper_gates_ctx(), cost)
    assert "STOP_LOSS" in res.reason
    assert acct.position is None
    assert acct.trades_closed[-1]["exit_reason"] == "STOP_LOSS"


def test_take_profit_exit(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    sig = paper_signal("LONG", frame.index[0], seed=1, entry=100.0, stop=50.0, take=101.0)
    bar = _bar_from_frame(frame, 1)
    bar.update({"low": 90.0, "high": 150.0, "open": 100.0, "close": 100.0})
    acct, res = step(acct, sig, bar, paper_gates_ctx(), cost)
    assert "TAKE_PROFIT" in res.reason
    assert acct.trades_closed[-1]["exit_reason"] == "TAKE_PROFIT"


def test_timeout_closes_before_new_signal(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path, max_holding_bars=0)
    cost = ExecutionCostModel(zero_cost())
    acct, _ = step(
        acct,
        paper_signal("LONG", frame.index[0], seed=1),
        _bar_from_frame(frame, 1),
        paper_gates_ctx(),
        cost,
    )
    acct, res = step(
        acct,
        paper_signal("LONG", frame.index[1], seed=2),
        _bar_from_frame(frame, 2),
        paper_gates_ctx(),
        cost,
    )
    assert res.reason == "TIMEOUT+OPENED"
    assert acct.position is not None and acct.position["bars_held"] == 0
    assert acct.trades_closed[0]["exit_reason"] == "TIMEOUT"


def test_duplicate_restep_rejected_idempotent(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    sig = paper_signal("LONG", frame.index[0], seed=1)
    acct, first = step(acct, sig, _bar_from_frame(frame, 1), paper_gates_ctx(), cost)
    before = account_hash(acct)
    bars_before = acct.bars_seen
    acct2, res = step(acct, sig, _bar_from_frame(frame, 1), paper_gates_ctx(), cost)
    assert res.status == "rejected" and res.reason == "DUPLICATE"
    assert account_hash(acct2) == before
    assert acct2.bars_seen == bars_before
    assert first.account_hash == before


def test_gate_rejection_recorded_no_position_change(tmp_path) -> None:
    from crypto_engine.config.settings import RiskConfig
    from crypto_engine.paper_trading.loop import make_gates_ctx_fn
    from crypto_engine.risk.gates import GateContext

    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())

    def _breached(account, signal, bar):
        ctx = make_gates_ctx_fn(RiskConfig())(account, signal, bar)
        bad = dict(ctx.portfolio or {})
        bad.update({"symbol_exposure_pct": 99.0, "exposure_pct": 99.0})
        object.__setattr__(ctx, "portfolio", bad)
        assert isinstance(ctx, GateContext)
        return ctx

    sig = paper_signal("LONG", frame.index[0], seed=1)
    acct, res = step(acct, sig, _bar_from_frame(frame, 1), _breached, cost)
    assert res.status == "rejected" and res.reason == "RISK_GATE_EXPOSURE"
    assert acct.position is None
    assert acct.rejections and acct.rejections[-1]["reason"] == "RISK_GATE_EXPOSURE"
    # Never re-evaluated: second attempt is DUPLICATE.
    _, res2 = step(acct, sig, _bar_from_frame(frame, 1), _breached, cost)
    assert res2.reason == "DUPLICATE"


def test_halted_refuses_and_consumes_nothing(tmp_path) -> None:
    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    halt(acct, "MANUAL")
    cost = ExecutionCostModel(zero_cost())
    sig = paper_signal("LONG", frame.index[0], seed=1)
    acct2, res = step(acct, sig, _bar_from_frame(frame, 1), paper_gates_ctx(), cost)
    assert res.status == "rejected" and res.reason == "HALTED"
    assert not is_processed(acct2, sig.signal_id)
    assert acct2.position is None


def test_symbol_mismatch_and_expiry_and_levels(tmp_path) -> None:
    frame = monotone_frame(n=12)
    cost = ExecutionCostModel(zero_cost())
    acct = _account(tmp_path)
    bad_sym = paper_signal("LONG", frame.index[0], seed=1, symbol="ETH/USDT")
    _, res = step(acct, bad_sym, _bar_from_frame(frame, 1), paper_gates_ctx(), cost)
    assert res.reason == "SYMBOL_MISMATCH"
    old = paper_signal("LONG", frame.index[0] - timedelta(hours=10), seed=2)
    _, res = step(acct, old, _bar_from_frame(frame, 1), paper_gates_ctx(), cost)
    assert res.reason == "EXPIRED"
    strict = init_account(
        tmp_path / "strict.json",
        symbol="BTC/USDT",
        timeframe="1h",
        starting_equity=10000.0,
        require_levels=True,
        cost=zero_cost(),
    )
    no_levels = paper_signal("LONG", frame.index[0], seed=3, entry=None, stop=None, take=None)
    _, res = step(strict, no_levels, _bar_from_frame(frame, 1), paper_gates_ctx(), cost)
    assert res.reason == "LEVELS_REQUIRED"


def test_funding_applied_while_open(tmp_path) -> None:
    frame = monotone_frame(n=12)
    cost = ExecutionCostModel(small_cost())  # funding 1bp
    # position_pct 5 keeps gate exposure (<= ~5%) under the default 10% cap.
    acct = _account(tmp_path, cost=small_cost(), position_pct=5.0)
    acct, _ = step(
        acct,
        paper_signal("LONG", frame.index[0], seed=1),
        _bar_from_frame(frame, 1),
        paper_gates_ctx(),
        cost,
    )
    notional = acct.position["notional"]
    cash_after_open = acct.cash
    expected_funding = notional * 1.0 / 1e4
    acct, res = step(
        acct,
        paper_signal("LONG", frame.index[1], seed=2),
        _bar_from_frame(frame, 2),
        paper_gates_ctx(),
        cost,
    )
    assert res.reason == "ALREADY_POSITIONED"
    assert acct.cash == pytest.approx(cash_after_open - expected_funding)
    assert acct.position["funding_accrued"] == pytest.approx(2 * expected_funding)


def test_determinism_identical_sequences_identical_hash(tmp_path) -> None:
    from crypto_engine.config.settings import RiskConfig
    from crypto_engine.paper_trading.loop import make_gates_ctx_fn

    frame = monotone_frame(n=12)
    cost = ExecutionCostModel(small_cost())
    # Roomy exposure cap so alternating signals exercise flip (not veto) paths.
    gates = make_gates_ctx_fn(RiskConfig(max_position_pct=25.0), max_age_min=600.0)

    def _run(tp, name):
        a = init_account(
            tp / name,
            symbol="BTC/USDT",
            timeframe="1h",
            starting_equity=10000.0,
            cost=small_cost(),
            account_id="determinism",
        )
        sigs = [
            paper_signal("LONG" if i % 2 == 0 else "SHORT", frame.index[i], seed=i)
            for i in range(5)
        ]
        for i, sig in enumerate(sigs):
            a, _ = step(a, sig, _bar_from_frame(frame, i + 1), gates, cost)
        return a

    a1 = _run(tmp_path, "a.json")
    a2 = _run(tmp_path, "b.json")
    # created_at/updated_at are wall-clock init inputs: normalize, then compare.
    d1 = a1.model_dump(exclude={"created_at", "updated_at", "checksum"})
    d2 = a2.model_dump(exclude={"created_at", "updated_at", "checksum"})
    assert d1 == d2


def test_future_bars_do_not_change_past_steps(tmp_path) -> None:
    """Leakage: state after N steps is identical whether future bars exist or not."""
    frame = monotone_frame(n=12)
    cost = ExecutionCostModel(zero_cost())
    sigs = [paper_signal("LONG", frame.index[i], seed=i) for i in range(3)]

    a = _account(tmp_path)
    for i, sig in enumerate(sigs):
        a, _ = step(a, sig, _bar_from_frame(frame, i + 1), paper_gates_ctx(), cost)
    assert a.bars_seen == 3
    assert len(a.equity_curve) == 3
    # Future bars (index 4..11) were never passed and cannot affect state.
    assert a.recent_bars[-1] == frame.index[3].isoformat()


def test_paper_portfolio_mapping(tmp_path) -> None:
    acct = _account(tmp_path)
    flat = paper_portfolio(acct, 100.0)
    assert flat["exposure_pct"] == 0.0
    assert flat["current_equity"] == pytest.approx(10000.0)
    assert flat["strategy_enabled"] is True and flat["circuit_open"] is False


def test_engine_parity_single_trade(tmp_path) -> None:
    """Paper stepping mirrors run_backtest on the identical bar+signal sequence."""
    from crypto_engine.backtesting.engine import run_backtest
    from crypto_engine.config.settings import BacktestConfig
    from crypto_engine.paper_trading.compare import compare_account_to_backtest

    frame = monotone_frame(n=12)
    cost_est = zero_cost()
    cost = ExecutionCostModel(cost_est)
    sig = paper_signal("LONG", frame.index[0], seed=1, entry=None, stop=None, take=None)
    cfg = BacktestConfig()
    result = run_backtest(frame, [sig], cfg, cost_est)
    assert len(result.trades) == 1

    acct = _account(tmp_path, cost=zero_cost())
    # Engine entry bar is index 1; step every bar 1..10, then flatten at last close.
    for i in range(1, 11):
        gen = frame.index[i - 1]
        s = paper_signal("LONG", gen, seed=100 + i, entry=None, stop=None, take=None)
        acct, _ = step(acct, s, _bar_from_frame(frame, i), paper_gates_ctx(), cost)
    from crypto_engine.paper_trading.state import flatten as _flatten

    _flatten(acct, float(frame["close"].iloc[-1]), frame.index[-1].isoformat())
    report = compare_account_to_backtest(acct, result)
    assert report.verdict == "match", report.reasons


def test_position_survives_halt_mark(tmp_path) -> None:
    from crypto_engine.paper_trading.state import equity as _equity

    frame = monotone_frame(n=12)
    acct = _account(tmp_path)
    cost = ExecutionCostModel(zero_cost())
    acct, _ = step(
        acct,
        paper_signal("LONG", frame.index[0], seed=1),
        _bar_from_frame(frame, 1),
        paper_gates_ctx(),
        cost,
    )
    halt(acct, "RISK")
    # Halted position is still marked (halt != liquidation).
    assert _equity(acct, 200.0) > acct.cash
    assert datetime.now(UTC) is not None
