"""Backtest timing / leakage tests (Phase 6 Track A).

The backtester must be causal: a signal decided on bar t close can only
use bars <= t, and its fill can occur no earlier than bar t+1 open.
These tests perturb future bars and shift signal timestamps to prove
no lookahead leaks into entry fills.
"""

from __future__ import annotations

from datetime import timedelta

from crypto_engine.backtesting.engine import run_backtest
from crypto_engine.config.settings import BacktestConfig
from tests.fixtures import backtest_fixtures as fx


def _cfg(**kw) -> BacktestConfig:
    return BacktestConfig(**kw)


def test_future_perturbation_does_not_change_entry() -> None:
    bars = fx.monotone_frame(n=12)
    sig = fx.long_signal(bars.index[1], seed=31, entry=None)
    base = run_backtest(bars, [sig], _cfg(), fx.zero_cost())
    # Perturb closes/highs/lows of bars AFTER entry+1 (keep opens fixed).
    mutated = bars.copy()
    for i in range(4, len(mutated)):
        for col in ("open", "high", "low", "close"):
            mutated.iloc[i, mutated.columns.get_loc(col)] *= 1.25
    alt = run_backtest(mutated, [sig], _cfg(), fx.zero_cost())
    assert base.trades[0].entry.price == alt.trades[0].entry.price
    assert base.trades[0].entry.ts == alt.trades[0].entry.ts
    assert base.trades[0].trade_id == alt.trades[0].trade_id


def test_shift_later_moves_entry_later_never_earlier() -> None:
    bars = fx.monotone_frame(n=12)
    early_sig = fx.long_signal(bars.index[1], seed=32, entry=None)
    late_sig = fx.long_signal(bars.index[1] + timedelta(minutes=30), seed=32, entry=None)
    # +30min is still inside bar interval [1] (hourly bars) -> same entry bar.
    same = run_backtest(bars, [late_sig], _cfg(), fx.zero_cost())
    base = run_backtest(bars, [early_sig], _cfg(), fx.zero_cost())
    assert same.trades[0].entry.ts == base.trades[0].entry.ts
    later_sig = fx.long_signal(bars.index[2], seed=32, entry=None)
    later = run_backtest(bars, [later_sig], _cfg(), fx.zero_cost())
    assert later.trades[0].entry.ts > base.trades[0].entry.ts


def test_entry_always_strictly_after_signal_bar() -> None:
    bars = fx.monotone_frame(n=12)
    for t in range(0, len(bars) - 2):
        sig = fx.long_signal(bars.index[t], seed=40 + t, entry=None)
        res = run_backtest(bars, [sig], _cfg(), fx.zero_cost())
        assert len(res.trades) == 1
        assert res.trades[0].entry.ts == bars.index[t + 1].isoformat()
        assert res.trades[0].entry.ts > sig.generated_at.isoformat()


def test_truncated_prefix_gives_same_early_entry() -> None:
    # Signal decided on bar 2 close from a truncated prefix must fill at
    # the same bar-3 open whether the engine later sees 6 or 12 bars
    # (decision uses only bars <= t; no future leak into the entry).
    full = fx.monotone_frame(n=12)
    sig = fx.long_signal(full.index[2], seed=50, entry=None)
    res_full = run_backtest(full, [sig], _cfg(), fx.zero_cost())
    prefix = full.iloc[:6].copy()
    res_prefix = run_backtest(prefix, [sig], _cfg(), fx.zero_cost())
    assert res_full.trades[0].entry.price == res_prefix.trades[0].entry.price
    assert res_full.trades[0].entry.ts == res_prefix.trades[0].entry.ts
