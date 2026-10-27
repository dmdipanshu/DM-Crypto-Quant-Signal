"""Unit tests for the event-safe backtest engine (Phase 6 Track A).

Offline, hand-computed, deterministic. Core invariant under test:
a signal with generated_at in bar interval [t] fills NO EARLIER than
bar t+1 open (entry = first bar with open time strictly greater than
generated_at).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from crypto_engine.backtesting.engine import InsufficientDataError, run_backtest
from crypto_engine.backtesting.metrics import (
    expectancy_per_trade,
    max_drawdown,
    profit_factor,
    sharpe,
    win_rate,
)
from crypto_engine.config.settings import BacktestConfig
from tests.fixtures import backtest_fixtures as fx


def _cfg(**kw) -> BacktestConfig:
    return BacktestConfig(**kw)


def test_next_bar_timing(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[2], seed=1)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert len(res.trades) == 1
    trade = res.trades[0]
    # Entry at bar 3 open (first open strictly after generated_at), never close[2].
    assert trade.entry.ts == bars.index[3].isoformat()
    assert trade.entry.price == pytest.approx(float(bars["open"].iloc[3]))
    assert trade.entry.side == "BUY"


def test_no_trade_noop(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.no_trade_signal(bars.index[1], seed=2)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert res.trades == []
    assert [(s.signal_id, s.reason) for s in res.skipped_signals] == [(sig.signal_id, "NO_TRADE")]


def test_non_approved_signal_never_trades(backtest_monotone_12, backtest_zero_cost) -> None:
    from crypto_engine.signals.schema import DecisionInfo

    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[1], seed=200)
    vetoed = sig.model_copy(
        update={
            "decision": DecisionInfo(
                status="rejected", reason_codes=["RISK_GATE_EDGE"], gate="risk:edge"
            )
        }
    )
    res = run_backtest(bars, [vetoed], _cfg(), backtest_zero_cost)
    assert res.trades == []
    assert [(s.signal_id, s.reason) for s in res.skipped_signals] == [
        (sig.signal_id, "NOT_APPROVED")
    ]


def test_neutral_closes_at_next_open(backtest_monotone_12, backtest_zero_cost) -> None:
    from crypto_engine.signals.schema import DecisionInfo

    bars = backtest_monotone_12
    long_sig = fx.long_signal(bars.index[0], seed=3, entry=100.0, stop=50.0, take=200.0)
    # Only approved records may mutate engine state; approve the NEUTRAL close.
    neu = fx.neutral_signal(bars.index[3], seed=4).model_copy(
        update={"decision": DecisionInfo(status="approved", reason_codes=["OK"], gate="policy")}
    )
    res = run_backtest(bars, [long_sig, neu], _cfg(), backtest_zero_cost)
    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.exit_reason == "NEUTRAL_CLOSE"
    assert trade.exit is not None
    assert trade.exit.ts == bars.index[4].isoformat()
    assert trade.exit.price == pytest.approx(float(bars["open"].iloc[4]))
    # LONG opened at open[1]=100, closed at open[4]=103, qty=10 -> gross 30.
    assert trade.gross_pnl == pytest.approx(30.0)
    assert trade.net_pnl == pytest.approx(30.0)


def test_flip_close_and_open_same_bar(backtest_monotone_12, backtest_small_cost) -> None:
    bars = backtest_monotone_12
    long_sig = fx.long_signal(bars.index[0], seed=5, entry=100.0, stop=50.0, take=200.0)
    short_sig = fx.short_signal(bars.index[1], seed=6, entry=102.0, stop=200.0, take=50.0)
    res = run_backtest(bars, [long_sig, short_sig], _cfg(), backtest_small_cost)
    assert len(res.trades) == 2
    long_t, short_t = res.trades
    assert long_t.exit_reason == "SIGNAL_FLIP"
    assert short_t.entry.ts == long_t.exit.ts == bars.index[2].isoformat()
    # Both legs costed: entry + exit fees on the closed long.
    assert long_t.costs_total > long_t.entry.cost_total > 0
    assert short_t.entry.cost_total > 0
    assert long_t.trade_id != short_t.trade_id


def test_same_direction_noop(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    s1 = fx.long_signal(bars.index[0], seed=7)
    s2 = fx.long_signal(bars.index[1], seed=8)
    res = run_backtest(bars, [s1, s2], _cfg(), backtest_zero_cost)
    assert len(res.trades) == 1
    reasons = {s.signal_id: s.reason for s in res.skipped_signals}
    assert reasons.get(s2.signal_id) == "ALREADY_POSITIONED"


def test_same_bar_sl_tp_prefers_stop_loss(backtest_zero_cost) -> None:
    bars = fx.make_bars([100.0] * 8, high_pad=12.0, low_pad=12.0)
    sig = fx.long_signal(bars.index[0], seed=9, entry=100.0, stop=95.0, take=105.0)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.exit is not None
    assert trade.exit.price == pytest.approx(95.0)


def test_take_profit_only(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[0], seed=10, entry=100.0, stop=50.0, take=105.0)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    trade = res.trades[0]
    assert trade.exit_reason == "TAKE_PROFIT"
    assert trade.exit is not None
    assert trade.exit.price == pytest.approx(105.0)
    assert trade.gross_pnl == pytest.approx((105.0 - 100.0) * 10.0)


def test_timeout_exit_at_next_open(backtest_zero_cost) -> None:
    bars = fx.make_bars([100.0] * 10)
    sig = fx.long_signal(bars.index[0], seed=11, entry=None)
    res = run_backtest(bars, [sig], _cfg(max_holding_bars=2), backtest_zero_cost)
    trade = res.trades[0]
    assert trade.exit_reason == "TIMEOUT"
    assert trade.exit is not None
    # Entry bar 1; breach when i - 1 > 2 -> exit at bar 4 open.
    assert trade.exit.ts == bars.index[4].isoformat()
    assert trade.exit.price == pytest.approx(float(bars["open"].iloc[4]))
    assert trade.bars_held == 3


def test_end_of_data_exit(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[-2], seed=12, entry=None)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    trade = res.trades[0]
    assert trade.exit_reason == "END_OF_DATA"
    assert trade.exit is not None
    assert trade.exit.ts == bars.index[-1].isoformat()
    assert trade.exit.price == pytest.approx(float(bars["close"].iloc[-1]))


def test_no_future_bar_skip(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[-1], seed=13)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert res.trades == []
    assert res.skipped_signals[0].reason == "NO_FUTURE_BAR"


def test_predates_data_skip(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    early = bars.index[0] - timedelta(hours=1)
    sig = fx.long_signal(early, seed=14)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert res.trades == []
    assert res.skipped_signals[0].reason == "PREDATES_DATA"


def test_symbol_mismatch_skip(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[0], seed=15, symbol="ETH/USDT")
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert res.trades == []
    assert res.skipped_signals[0].reason == "SYMBOL_MISMATCH"


def test_expired_skip(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[0], seed=16, valid_hours=0.25)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert res.trades == []
    assert res.skipped_signals[0].reason == "EXPIRED"


def test_require_levels_skip(backtest_monotone_12, backtest_zero_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[0], seed=17, entry=None)  # no stop/take
    res = run_backtest(bars, [sig], _cfg(require_levels=True), backtest_zero_cost)
    assert res.trades == []
    assert res.skipped_signals[0].reason == "LEVELS_REQUIRED"
    # Same signal trades when the gate is off.
    res2 = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert len(res2.trades) == 1


def test_insufficient_and_empty_data() -> None:
    bars = fx.make_bars([100.0])
    sig = fx.long_signal(bars.index[0], seed=18, entry=None)
    with pytest.raises(InsufficientDataError):
        run_backtest(bars, [sig], _cfg(), fx.zero_cost())
    import pandas as pd

    with pytest.raises(ValueError, match="non-empty"):
        run_backtest(pd.DataFrame(), [sig], _cfg(), fx.zero_cost())


def test_nan_rejection_names_column(backtest_zero_cost) -> None:
    bars = fx.make_bars([100.0, 101.0, 102.0, 103.0])
    bars.iloc[2, bars.columns.get_loc("close")] = float("nan")
    sig = fx.long_signal(bars.index[0], seed=19, entry=None)
    with pytest.raises(ValueError, match="close"):
        run_backtest(bars, [sig], _cfg(), backtest_zero_cost)


def test_determinism(backtest_monotone_12, backtest_small_cost) -> None:
    bars = backtest_monotone_12
    sigs = [
        fx.long_signal(bars.index[0], seed=20),
        fx.short_signal(bars.index[4], seed=21),
    ]
    a = run_backtest(bars, sigs, _cfg(), backtest_small_cost)
    b = run_backtest(bars, sigs, _cfg(), backtest_small_cost)
    assert a.model_dump() == b.model_dump()
    assert [t.trade_id for t in a.trades] == [t.trade_id for t in b.trades]


def test_fee_math_reconciles(backtest_monotone_12, backtest_small_cost) -> None:
    bars = backtest_monotone_12
    sig = fx.long_signal(bars.index[0], seed=22, entry=100.0, stop=50.0, take=1e9)
    res = run_backtest(bars, [sig], _cfg(), backtest_small_cost)
    trade = res.trades[0]
    # open[1] = 100; BUY penalty 8bp -> 100.08; qty = 1000/100.08; fee = 1.0.
    assert trade.entry.price == pytest.approx(100.08)
    assert trade.entry.notional == pytest.approx(1000.0)
    assert trade.entry.cost_total == pytest.approx(1.0)
    assert len(res.equity_curve) == len(bars)
    # No open position at end: final equity == cash == start + sum(nets).
    total_net = sum(t.net_pnl for t in res.trades)
    assert res.summary["final_equity"] == pytest.approx(10000.0 + total_net, abs=1e-6)


def test_short_profits_on_decline(backtest_zero_cost) -> None:
    bars = fx.reversal_frame()
    sig = fx.short_signal(bars.index[6], seed=23, entry=None)
    res = run_backtest(bars, [sig], _cfg(), backtest_zero_cost)
    assert len(res.trades) == 1
    assert res.trades[0].net_pnl > 0


def test_metrics_hand_values() -> None:
    dd, w = max_drawdown([100.0, 110.0, 90.0, 100.0])
    assert w is None
    assert dd == pytest.approx(20.0 / 110.0 * 100.0)
    assert max_drawdown([])[0] is None and max_drawdown([])[1] is not None
    pf, w = profit_factor([10.0, -5.0])
    assert w is None and pf == pytest.approx(2.0)
    assert profit_factor([])[0] is None
    assert profit_factor([5.0])[0] is None  # infinite -> warning, not inf
    wr, _ = win_rate([1.0, -1.0, -1.0])
    assert wr == pytest.approx(1.0 / 3.0)
    assert win_rate([])[0] is None
    ex, _ = expectancy_per_trade([10.0, -4.0])
    assert ex == pytest.approx(3.0)
    assert expectancy_per_trade([])[0] is None
    assert sharpe([0.01], 8760.0)[0] is None
    assert sharpe([0.0, 0.0], 8760.0)[0] is None
    s, w = sharpe([0.01, 0.02, 0.015], 8760.0)
    assert w is None and s is not None and s > 0
