"""Phase 6 Track B: divergence-compare tests (offline, deterministic)."""

from __future__ import annotations

import pytest

from crypto_engine.backtesting.engine import run_backtest
from crypto_engine.backtesting.types import Trade
from crypto_engine.config.settings import BacktestConfig
from crypto_engine.paper_trading.compare import compare_account_to_backtest, compare_to_backtest
from tests.fixtures.backtest_fixtures import (
    monotone_frame,
    paper_signal,
    small_cost,
    zero_cost,
)


def _engine_result(cost=None):
    frame = monotone_frame(n=12)
    cost_est = cost or zero_cost()
    sig = paper_signal("LONG", frame.index[0], seed=1, entry=None, stop=None, take=None)
    return run_backtest(frame, [sig], BacktestConfig(), cost_est), frame


def test_identical_sequences_match() -> None:
    result, _ = _engine_result()
    report = compare_to_backtest([t.model_dump() for t in result.trades], result)
    assert report.verdict == "match"
    assert report.reasons == []
    assert report.n_paper == report.n_backtest == 1


def test_empty_vs_trades_diverged() -> None:
    result, _ = _engine_result()
    report = compare_to_backtest([], result)
    assert report.verdict == "diverged"
    assert report.missing_in_paper == [result.trades[0].trade_id]
    assert any("trade_count" in r for r in report.reasons)


def test_perturbed_fill_diverged_with_named_trade() -> None:
    result, _ = _engine_result()
    tampered = result.trades[0].model_copy(deep=True)
    tampered.entry = tampered.entry.model_copy(update={"qty": tampered.entry.qty * 1.5})
    tampered.net_pnl = tampered.net_pnl + 10.0
    report = compare_to_backtest([tampered], result)
    assert report.verdict == "diverged"
    fields = {d.field for d in report.trade_divergences}
    assert {"qty", "net_pnl"} <= fields
    assert all(d.trade_id == result.trades[0].trade_id for d in report.trade_divergences)


def test_tolerance_boundaries() -> None:
    result, _ = _engine_result()
    base = result.trades[0].model_copy(deep=True)
    # Within tolerance -> match.
    tiny = base.model_copy(deep=True)
    tiny.entry = tiny.entry.model_copy(update={"qty": base.entry.qty * (1 + 1e-12)})
    tiny.net_pnl = base.net_pnl + 1e-9
    assert compare_to_backtest([tiny], result).verdict == "match"
    # Beyond tolerance -> diverged.
    big = base.model_copy(deep=True)
    big.entry = big.entry.model_copy(update={"qty": base.entry.qty * 1.01})
    assert compare_to_backtest([big], result).verdict == "diverged"


def test_side_and_entry_ts_mismatch_flagged() -> None:
    result, _ = _engine_result()
    other = result.trades[0].model_copy(deep=True)
    other.side = "SHORT"  # type: ignore[arg-type]
    report = compare_to_backtest([other], result)
    assert report.verdict == "diverged"
    assert any(d.field == "side" for d in report.trade_divergences)


def test_extra_trade_listed() -> None:
    result, _ = _engine_result()
    dupe = result.trades[0].model_copy(deep=True)
    dupe.trade_id = "extra|id"
    report = compare_to_backtest([result.trades[0], dupe], result)
    assert report.verdict == "diverged"
    assert report.extra_in_paper == ["extra|id"]


def test_equity_end_diff_within_and_beyond_tol() -> None:
    from crypto_engine.paper_trading.state import PaperAccount

    result, _ = _engine_result()
    final = float(result.summary["final_equity"])
    acct = PaperAccount(
        symbol="BTC/USDT",
        timeframe="1h",
        cash=final,
        starting_equity=10000.0,
        position_pct=10.0,
        max_holding_bars=24,
        cost={"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0},
        trades_closed=[t.model_dump(mode="json") for t in result.trades],
        equity_curve=[["2024-01-01T00:00:00+00:00", final]],
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
    )
    ok_report = compare_account_to_backtest(acct, result)
    assert ok_report.verdict == "match"
    assert ok_report.equity_end_diff == pytest.approx(0.0)
    acct.equity_curve = [["2024-01-01T00:00:00+00:00", final + 5.0]]
    bad_report = compare_account_to_backtest(acct, result)
    assert bad_report.verdict == "diverged"
    assert any("equity_end" in r for r in bad_report.reasons)


def test_paper_reason_key_stripped() -> None:
    result, _ = _engine_result()
    with_reason = result.trades[0].model_dump(mode="json")
    with_reason["paper_reason"] = "MANUAL_FLATTEN"
    assert compare_to_backtest([with_reason], result).verdict == "match"


def test_type_errors() -> None:
    result, _ = _engine_result()
    with pytest.raises(TypeError, match="BacktestResult"):
        compare_to_backtest([], object())
    with pytest.raises(TypeError, match="Trade or dict"):
        compare_to_backtest([42], result)
    with pytest.raises(ValueError, match="finite >= 0"):
        compare_to_backtest([], result, qty_rel_tol=-1.0)


def test_trade_model_import_used() -> None:
    assert Trade is not None


def test_small_cost_result_compares() -> None:
    result, _ = _engine_result(cost=small_cost())
    report = compare_to_backtest([t.model_dump() for t in result.trades], result)
    assert report.verdict == "match"
