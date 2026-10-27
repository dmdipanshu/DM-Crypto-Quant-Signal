"""Ledger-based backtest metrics (Phase 6 Track A).

Pure functions over a ``BacktestResult`` / equity curve. Every metric
returns ``(value, warning)`` with ``value=None`` plus a warning string
when undefined (zero trades, division by zero) instead of NaN.

- ``max_drawdown``: peak-to-trough % on marked-to-close equity.
- ``sharpe``: mean/std of per-bar simple returns annualised by an
  EXPLICIT ``periods_per_year`` for the bar timeframe (e.g. 1h -> 8760;
  caller passes it — no hidden timeframe map; documented assumption).
  Uses sample std (ddof=1); needs >= 2 returns.
- ``profit_factor``: gross profit / gross loss (losses as positive sum).
- ``win_rate``: fraction of closed trades with net_pnl > 0.
- ``expectancy_per_trade``: mean net P&L per closed trade.

Kept separate from Phase 3 proxy metrics (ex-ante estimates); these
are ex-post ledger facts, net of costs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from crypto_engine.backtesting.engine import BacktestResult


def max_drawdown(equities: Sequence[float]) -> tuple[float | None, str | None]:
    """Peak-to-trough drawdown in percent; None + warning when undefined."""
    vals = [float(v) for v in equities]
    if len(vals) < 1:
        return None, "max_drawdown undefined: empty equity curve."
    if any(not math.isfinite(v) for v in vals):
        return None, "max_drawdown undefined: non-finite equity value."
    peak = vals[0]
    best = 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            best = max(best, (peak - v) / peak * 100.0)
    if peak <= 0:
        return None, "max_drawdown undefined: non-positive equity peak."
    return best, None


def sharpe(
    bar_returns: Sequence[float], periods_per_year: float
) -> tuple[float | None, str | None]:
    """Annualised Sharpe (mean/std * sqrt(periods)); None + warning if undefined.

    Assumption: ``periods_per_year`` is caller-supplied for the bar
    timeframe (e.g. 8760 for 1h bars); risk-free rate is 0.
    """
    try:
        ppy = float(periods_per_year)
    except (TypeError, ValueError):
        return None, "sharpe undefined: periods_per_year not numeric."
    if not math.isfinite(ppy) or ppy <= 0:
        return None, "sharpe undefined: periods_per_year must be > 0."
    rets = [float(r) for r in bar_returns]
    if len(rets) < 2:
        return None, "sharpe undefined: need >= 2 bar returns."
    if any(not math.isfinite(r) for r in rets):
        return None, "sharpe undefined: non-finite return."
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    if var <= 0:
        return None, "sharpe undefined: zero return variance."
    return mean / math.sqrt(var) * math.sqrt(ppy), None


def profit_factor(nets: Sequence[float]) -> tuple[float | None, str | None]:
    """Gross profit / gross loss; None + warning when undefined."""
    vals = [float(v) for v in nets]
    if not vals:
        return None, "profit_factor undefined: no trades."
    if any(not math.isfinite(v) for v in vals):
        return None, "profit_factor undefined: non-finite pnl."
    gp = sum(v for v in vals if v > 0)
    gl = -sum(v for v in vals if v < 0)
    if gl == 0:
        if gp == 0:
            return None, "profit_factor undefined: all nets are zero."
        return None, "profit_factor undefined: no losing trades (infinite)."
    return gp / gl, None


def win_rate(nets: Sequence[float]) -> tuple[float | None, str | None]:
    """Fraction of nets > 0; None + warning when no trades."""
    vals = [float(v) for v in nets]
    if not vals:
        return None, "win_rate undefined: no trades."
    return sum(1 for v in vals if v > 0) / len(vals), None


def expectancy_per_trade(nets: Sequence[float]) -> tuple[float | None, str | None]:
    """Mean net P&L per trade; None + warning when no trades."""
    vals = [float(v) for v in nets]
    if not vals:
        return None, "expectancy undefined: no trades."
    if any(not math.isfinite(v) for v in vals):
        return None, "expectancy undefined: non-finite pnl."
    return sum(vals) / len(vals), None


def result_nets(result: BacktestResult) -> list[float]:
    """Net P&L of closed trades in a result (helper for metric inputs)."""
    return [t.net_pnl for t in result.trades if t.exit is not None]


def result_bar_returns(result: BacktestResult) -> list[float]:
    """Per-bar simple returns from the equity curve (first bar = 0.0)."""
    eq = [p.equity for p in result.equity_curve]
    out: list[float] = []
    for i in range(1, len(eq)):
        prev = eq[i - 1]
        out.append((eq[i] - prev) / prev if prev != 0 else 0.0)
    return out


__all__ = [
    "expectancy_per_trade",
    "max_drawdown",
    "profit_factor",
    "result_bar_returns",
    "result_nets",
    "sharpe",
    "win_rate",
]
