"""Research-only strategy simulation on real OHLC bars.

Two simulators live here:

``evaluate_fixed_horizon_strategy``
    The original Q3 diagnostic: decision at close ``t``, fill at the next
    bar open, exit at the open ``horizon`` bars later. It is retained so
    previously published Q3 artifacts stay reproducible.

``evaluate_triple_barrier_strategy``
    The Q2.2 corrected simulator. It executes the *same* barrier geometry
    that produced the training target, so a model's predictions are
    evaluated against the outcome it was trained to predict.

Both are research-only simulators: they have no exchange, order, paper or
live-execution dependency, and neither can grant trading authority.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd


def _finite_nonnegative(value: float, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result) or result < 0 or result >= 10_000:
        raise ValueError(f"{name} must be finite and in [0, 10000)")
    return result


def _trade_metrics(
    gross_returns_bp: list[float],
    net_returns_bp: list[float],
) -> dict[str, Any]:
    """Compute gross and net trade-level metrics independently.

    Gross results are calculated before transaction costs. Net results
    are calculated after all costs. Profit factor is defined as
    gross_profit / gross_loss when gross_loss > 0; otherwise None
    (zero-loss behavior). Zero-trade folds return None for rate/ratio
    metrics and 0.0 for sums.
    """
    if not gross_returns_bp or not net_returns_bp:
        return {
            "trade_count": 0,
            "win_rate": None,
            "expectancy_bp": None,
            "profit_factor": None,
            "gross_profit_factor": None,
            "net_profit_factor": None,
            "total_gross_return_bp": 0.0,
            "total_net_return_bp": 0.0,
            "gross_profit_bp": 0.0,
            "gross_loss_bp": 0.0,
            "net_profit_bp": 0.0,
            "net_loss_bp": 0.0,
            "total_trading_costs_bp": 0.0,
            "max_drawdown_bp": 0.0,
            "average_trade_return_bp": None,
        }
    gross_arr = np.asarray(gross_returns_bp, dtype=float)
    net_arr = np.asarray(net_returns_bp, dtype=float)
    costs_arr = gross_arr - net_arr

    gross_wins = gross_arr[gross_arr > 0]
    gross_losses = gross_arr[gross_arr < 0]
    net_wins = net_arr[net_arr > 0]
    net_losses = net_arr[net_arr < 0]

    gross_profit = float(gross_wins.sum())
    gross_loss = float(-gross_losses.sum())
    net_profit = float(net_wins.sum())
    net_loss = float(-net_losses.sum())
    total_trading_costs = float(costs_arr.sum())

    gross_profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    net_profit_factor = net_profit / net_loss if net_loss > 0 else None

    equity = 10_000.0
    peak = equity
    max_dd = 0.0
    for value in net_arr:
        equity = max(0.0, equity * (1.0 + float(value) / 10_000.0))
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    return {
        "trade_count": int(gross_arr.size),
        "win_rate": float((net_arr > 0).mean()),
        "expectancy_bp": float(net_arr.mean()),
        "profit_factor": net_profit_factor,
        "gross_profit_factor": float(gross_profit_factor)
        if gross_profit_factor is not None
        else None,
        "net_profit_factor": float(net_profit_factor) if net_profit_factor is not None else None,
        "total_gross_return_bp": float(gross_arr.sum()),
        "total_net_return_bp": float(net_arr.sum()),
        "gross_profit_bp": gross_profit,
        "gross_loss_bp": gross_loss,
        "net_profit_bp": net_profit,
        "net_loss_bp": net_loss,
        "total_trading_costs_bp": total_trading_costs,
        "max_drawdown_bp": float(max_dd),
        "average_trade_return_bp": float(net_arr.mean()),
    }


def _account_metrics(
    trade_returns_bp: list[float],
    *,
    position_pct: float,
    starting_equity: float,
) -> dict[str, Any]:
    """Compounded account-level metrics for a declared sizing convention.

    ``position_pct`` is the fraction of equity committed to a single trade.
    The equity curve compounds ``1 + position_pct * r`` per trade, so the
    drawdown and the account return come from the *same* accounting
    convention as the per-trade returns. The arithmetic sum of per-trade
    returns is reported separately and is explicitly labelled a research
    aggregate, never an account-level return.
    """
    if not trade_returns_bp:
        return {
            "position_pct": float(position_pct),
            "starting_equity": float(starting_equity),
            "final_equity": float(starting_equity),
            "account_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "max_drawdown_bp": 0.0,
            "bars_held_total": 0,
            "exposure_bars": 0,
            "turnover_equity_turns": 0.0,
        }
    arr = np.asarray(trade_returns_bp, dtype=float)
    equity = float(starting_equity)
    peak = equity
    max_dd_pct = 0.0
    max_dd_bp = 0.0
    for value in arr:
        equity *= 1.0 + position_pct * float(value) / 10_000.0
        peak = max(peak, equity)
        drawdown_pct = 0.0 if peak <= 0 else (peak - equity) / peak * 100.0
        max_dd_pct = max(max_dd_pct, drawdown_pct)
    # max_drawdown_bp is the largest single-trade drawdown in bp (not account-level).
    max_dd_bp = float(-arr.min()) if arr.size and arr.min() < 0 else 0.0
    return {
        "position_pct": float(position_pct),
        "starting_equity": float(starting_equity),
        "final_equity": float(equity),
        "account_return_pct": (equity - starting_equity) / starting_equity * 100.0,
        "max_drawdown_pct": float(max_dd_pct),
        "max_drawdown_bp": float(max_dd_bp),
    }


def _round_trip_cost_bp(
    *,
    fee_bp: float,
    spread_bp: float,
    slippage_bp: float,
    funding_bp_per_bar: float,
    bars_held: int,
    multiplier: float = 1.0,
) -> float:
    """Round-trip cost in basis points.

    ``2 * fee`` (entry + exit), one round-trip ``spread``, ``2 * slippage``
    (entry + exit) and ``bars_held * funding``. Costs are applied exactly
    once per round trip.
    """
    return float(
        multiplier
        * (
            2.0 * fee_bp
            + spread_bp
            + 2.0 * slippage_bp
            + max(0, int(bars_held)) * funding_bp_per_bar
        )
    )


def evaluate_fixed_horizon_strategy(
    *,
    opens: Sequence[float] | np.ndarray,
    predictions: Sequence[int] | np.ndarray,
    decision_mask: Sequence[bool] | np.ndarray,
    timestamps: Sequence[Any] | pd.DatetimeIndex,
    horizon_bars: int,
    bar_seconds: int = 3_600,
    fee_bp: float,
    spread_bp: float,
    slippage_bp: float,
    funding_bp_per_bar: float,
    stress_multipliers: Sequence[float] = (1.0, 1.5, 2.0),
) -> dict[str, Any]:
    """Simulate non-overlapping OOS trades with next-open fills and full costs.

    ``predictions`` must contain only out-of-fold values where ``decision_mask``
    is true. Values are ``-1`` (short), ``0`` (abstain), ``1`` (long). Signals
    outside the OOS mask are ignored even if their value is non-zero.
    """
    price = np.asarray(opens, dtype=float).reshape(-1)
    raw_predictions = np.asarray(predictions).reshape(-1)
    try:
        numeric_predictions = raw_predictions.astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError("predictions must contain only -1, 0 or 1") from exc
    if (
        not np.isfinite(numeric_predictions).all()
        or not np.equal(numeric_predictions, np.trunc(numeric_predictions)).all()
    ):
        raise ValueError("predictions must contain only -1, 0 or 1")
    pred = numeric_predictions.astype(int)
    raw_eligible = np.asarray(decision_mask).reshape(-1)
    if raw_eligible.dtype == bool:
        eligible = raw_eligible
    else:
        try:
            numeric_mask = raw_eligible.astype(float)
        except (TypeError, ValueError) as exc:
            raise ValueError("decision_mask must contain only booleans or 0/1 values") from exc
        if not np.isfinite(numeric_mask).all() or not np.isin(numeric_mask, [0.0, 1.0]).all():
            raise ValueError("decision_mask must contain only booleans or 0/1 values")
        eligible = numeric_mask.astype(bool)
    times = pd.DatetimeIndex(timestamps)
    n = price.size
    if n < 3 or pred.size != n or eligible.size != n or len(times) != n:
        raise ValueError("opens, predictions, decision_mask and timestamps must align (n >= 3)")
    if not np.isfinite(price).all() or bool((price <= 0).any()):
        raise ValueError("opens must be finite and > 0")
    if not times.is_monotonic_increasing or times.has_duplicates or times.tz is None:
        raise ValueError("timestamps must be unique, monotonic and timezone-aware")
    if not np.isin(pred, [-1, 0, 1]).all():
        raise ValueError("predictions must contain only -1, 0 or 1")
    try:
        raw_horizon = float(horizon_bars)
        raw_bar_seconds = float(bar_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("horizon_bars and bar_seconds must be positive integers") from exc
    if (
        not math.isfinite(raw_horizon)
        or raw_horizon < 1
        or raw_horizon != int(raw_horizon)
        or not math.isfinite(raw_bar_seconds)
        or raw_bar_seconds < 1
        or raw_bar_seconds != int(raw_bar_seconds)
    ):
        raise ValueError("horizon_bars and bar_seconds must be positive integers")
    horizon = int(raw_horizon)
    bar_seconds = int(raw_bar_seconds)

    costs = {
        name: _finite_nonnegative(value, name)
        for name, value in (
            ("fee_bp", fee_bp),
            ("spread_bp", spread_bp),
            ("slippage_bp", slippage_bp),
            ("funding_bp_per_bar", funding_bp_per_bar),
        )
    }
    try:
        multipliers = [float(m) for m in stress_multipliers]
    except (TypeError, ValueError) as exc:
        raise ValueError("stress_multipliers must contain finite values in [1, 5]") from exc
    if (
        not multipliers
        or any(not math.isfinite(m) or m < 1.0 or m > 5.0 for m in multipliers)
        or len(set(multipliers)) != len(multipliers)
        or not any(math.isclose(m, 1.0, rel_tol=0.0, abs_tol=1e-9) for m in multipliers)
    ):
        raise ValueError("stress_multipliers must be unique finite values in [1, 5] and include 1x")
    multipliers.sort()

    trade_rows: list[dict[str, Any]] = []
    next_available_decision = 0
    skipped_no_exit = 0
    skipped_overlap = 0
    for decision_idx in range(n):
        side = int(pred[decision_idx])
        if not eligible[decision_idx] or side == 0:
            continue
        if decision_idx < next_available_decision:
            skipped_overlap += 1
            continue
        entry_idx = decision_idx + 1
        exit_idx = entry_idx + horizon
        if exit_idx >= n:
            skipped_no_exit += 1
            continue
        gross_bp = float(side * (price[exit_idx] / price[entry_idx] - 1.0) * 10_000.0)
        trade_rows.append(
            {
                "decision_bar_open_at": times[decision_idx].isoformat(),
                "decision_at": (times[decision_idx] + timedelta(seconds=bar_seconds)).isoformat(),
                "entry_at": times[entry_idx].isoformat(),
                "exit_at": times[exit_idx].isoformat(),
                "side": "LONG" if side > 0 else "SHORT",
                "entry_price": float(price[entry_idx]),
                "exit_price": float(price[exit_idx]),
                "gross_return_bp": gross_bp,
                "bars_held": horizon,
            }
        )
        next_available_decision = exit_idx

    stress: list[dict[str, Any]] = []
    for multiplier in multipliers:
        round_trip_cost_bp = multiplier * (
            2.0 * costs["fee_bp"]
            + costs["spread_bp"]
            + 2.0 * costs["slippage_bp"]
            + horizon * costs["funding_bp_per_bar"]
        )
        gross_list = [float(row["gross_return_bp"]) for row in trade_rows]
        net_list = [g - round_trip_cost_bp for g in gross_list]
        row = {
            "cost_multiplier": multiplier,
            "round_trip_cost_bp": round_trip_cost_bp,
            **_trade_metrics(gross_list, net_list),
        }
        stress.append(row)

    base = next(row for row in stress if math.isclose(row["cost_multiplier"], 1.0, abs_tol=1e-9))
    base_rt_cost = float(base["round_trip_cost_bp"])
    for row in trade_rows:
        row["net_return_bp"] = float(row["gross_return_bp"]) - base_rt_cost

    return {
        "simulation": "research_fixed_horizon_next_open_v1",
        "execution_rule": "decision at close t; entry next bar open; exit open after horizon bars",
        "position_overlap": "not allowed; next decision only after previous exit bar",
        "horizon_bars": horizon,
        "bar_seconds": bar_seconds,
        "cost_inputs": costs,
        "cost_stress": stress,
        "trades": trade_rows,
        "skipped": {
            "overlapping_signals": skipped_overlap,
            "insufficient_future_bars": skipped_no_exit,
        },
        "trade_count": int(base["trade_count"]),
        "win_rate": base["win_rate"],
        "expectancy_bp": base["expectancy_bp"],
        "profit_factor": base["profit_factor"],
        "total_net_return_bp": base["total_net_return_bp"],
        "max_drawdown_bp": base["max_drawdown_bp"],
    }


def evaluate_triple_barrier_strategy(
    *,
    opens: Sequence[float] | np.ndarray,
    highs: Sequence[float] | np.ndarray,
    lows: Sequence[float] | np.ndarray,
    closes: Sequence[float] | np.ndarray,
    predictions: Sequence[int] | np.ndarray,
    decision_mask: Sequence[bool] | np.ndarray,
    timestamps: Sequence[Any] | pd.DatetimeIndex,
    vol_at_entry: Sequence[float] | np.ndarray | None = None,
    pt_mult: float,
    sl_mult: float,
    max_horizon_bars: int,
    fee_bp: float,
    spread_bp: float,
    slippage_bp: float,
    funding_bp_per_bar: float,
    stress_multipliers: Sequence[float] = (1.0, 1.5, 2.0),
    position_pct: float = 1.0,
    starting_equity: float = 10_000.0,
) -> dict[str, Any]:
    """Execute the Q2.2 side-aware triple-barrier target.

    Decision at close of bar ``t``; fill at the open of bar ``t+1`` (no
    look-ahead). Barriers ``upper = entry * (1 + pt_mult * v)``,
    ``lower = entry * (1 - sl_mult * v)`` are inspected on bars
    ``t+1 .. t + max_horizon_bars`` using observed ``high``/``low``.

    ``+1`` (LONG): take profit at ``upper``, stop at ``lower``.
    ``-1`` (SHORT): take profit at ``lower``, stop at ``upper``.
    ``0`` (abstain): no trade.

    If both barriers touch in the same candle, the adverse barrier wins
    (stop first). If no barrier is reached within the horizon, the trade
    exits at the close of the final holding bar (max holding-period exit).

    Returns per-trade rows, a compounded equity curve, and cost-stress
    results. All monetary/return metrics share one accounting convention.
    """
    price = np.asarray(opens, dtype=float).reshape(-1)
    high = np.asarray(highs, dtype=float).reshape(-1)
    low = np.asarray(lows, dtype=float).reshape(-1)
    close = np.asarray(closes, dtype=float).reshape(-1)
    pred = np.asarray(predictions).reshape(-1).astype(float)
    if not np.isfinite(pred).all() or not np.equal(pred, np.trunc(pred)).all():
        raise ValueError("predictions must contain only -1, 0 or 1")
    pred = pred.astype(int)
    if not np.isin(pred, [-1, 0, 1]).all():
        raise ValueError("predictions must contain only -1, 0 or 1")
    eligible_raw = np.asarray(decision_mask).reshape(-1)
    if eligible_raw.dtype == bool:
        eligible = eligible_raw
    else:
        try:
            eligible = eligible_raw.astype(float)
        except (TypeError, ValueError) as exc:
            raise ValueError("decision_mask must contain only booleans or 0/1 values") from exc
        if not np.isfinite(eligible).all() or not np.isin(eligible, [0.0, 1.0]).all():
            raise ValueError("decision_mask must contain only booleans or 0/1 values")
        eligible = eligible.astype(bool)
    times = pd.DatetimeIndex(timestamps)
    vol = (
        np.asarray(vol_at_entry, dtype=float).reshape(-1)
        if vol_at_entry is not None
        else np.full(price.size, np.nan)
    )
    n = price.size
    if n < 3 or pred.size != n or eligible.size != n or len(times) != n or vol.size != n:
        raise ValueError(
            "opens, highs, lows, closes, predictions, decision_mask, "
            "timestamps and vol_at_entry must align (n >= 3)"
        )
    if not np.isfinite(price).all() or bool((price <= 0).any()):
        raise ValueError("opens must be finite and > 0")
    if not np.isfinite(high).all() or bool((high <= 0).any()):
        raise ValueError("highs must be finite and > 0")
    if not np.isfinite(low).all() or bool((low <= 0).any()):
        raise ValueError("lows must be finite and > 0")
    if not np.isfinite(close).all() or bool((close <= 0).any()):
        raise ValueError("closes must be finite and > 0")
    if not times.is_monotonic_increasing or times.has_duplicates or times.tz is None:
        raise ValueError("timestamps must be unique, monotonic and timezone-aware")
    try:
        horizon = int(max_horizon_bars)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_horizon_bars must be a positive integer") from exc
    if horizon < 1:
        raise ValueError("max_horizon_bars must be >= 1")
    try:
        raw_position_pct = float(position_pct)
        raw_starting_equity = float(starting_equity)
    except (TypeError, ValueError) as exc:
        raise ValueError("position_pct and starting_equity must be numeric") from exc
    if not math.isfinite(raw_position_pct) or raw_position_pct <= 0 or raw_position_pct > 1:
        raise ValueError("position_pct must be within (0, 1]")
    if not math.isfinite(raw_starting_equity) or raw_starting_equity <= 0:
        raise ValueError("starting_equity must be > 0")
    position_pct = raw_position_pct
    starting_equity = raw_starting_equity

    costs = {
        name: _finite_nonnegative(value, name)
        for name, value in (
            ("fee_bp", fee_bp),
            ("spread_bp", spread_bp),
            ("slippage_bp", slippage_bp),
            ("funding_bp_per_bar", funding_bp_per_bar),
        )
    }
    try:
        multipliers = [float(m) for m in stress_multipliers]
    except (TypeError, ValueError) as exc:
        raise ValueError("stress_multipliers must contain finite values in [1, 5]") from exc
    if (
        not multipliers
        or any(not math.isfinite(m) or m < 1.0 or m > 5.0 for m in multipliers)
        or len(set(multipliers)) != len(multipliers)
        or not any(math.isclose(m, 1.0, rel_tol=0.0, abs_tol=1e-9) for m in multipliers)
    ):
        raise ValueError("stress_multipliers must be unique finite values in [1, 5] and include 1x")
    multipliers.sort()

    trade_rows: list[dict[str, Any]] = []
    next_available_decision = 0
    skipped_no_exit = 0
    skipped_overlap = 0
    skipped_invalid_vol = 0

    for decision_idx in range(n):
        side = int(pred[decision_idx])
        if not eligible[decision_idx] or side == 0:
            continue
        if decision_idx < next_available_decision:
            skipped_overlap += 1
            continue
        entry_idx = decision_idx + 1
        exit_idx = decision_idx + horizon
        if exit_idx >= n:
            skipped_no_exit += 1
            continue
        v = vol[decision_idx]
        if not np.isfinite(v) or v <= 0:
            skipped_invalid_vol += 1
            continue
        entry = float(price[entry_idx])
        if not np.isfinite(entry) or entry <= 0:
            skipped_invalid_vol += 1
            continue
        upper = entry * (1.0 + pt_mult * v)
        lower = entry * (1.0 - sl_mult * v)
        if lower <= 0:
            skipped_invalid_vol += 1
            continue
        exit_price: float | None = None
        exit_idx_used: int | None = None
        barrier = "timeout"
        for j in range(entry_idx, exit_idx + 1):
            hit_upper = float(high[j]) >= upper
            hit_lower = float(low[j]) <= lower
            if hit_upper and hit_lower:
                # Conservative: adverse barrier first (stop first) for both sides.
                barrier = "stop_loss"
                exit_price = lower if side > 0 else upper
                exit_idx_used = j
                break
            if side > 0:
                if hit_upper:
                    barrier = "profit_target"
                    exit_price = upper
                    exit_idx_used = j
                    break
                if hit_lower:
                    barrier = "stop_loss"
                    exit_price = lower
                    exit_idx_used = j
                    break
            else:
                if hit_lower:
                    barrier = "profit_target"
                    exit_price = lower
                    exit_idx_used = j
                    break
                if hit_upper:
                    barrier = "stop_loss"
                    exit_price = upper
                    exit_idx_used = j
                    break
        if exit_price is None:
            # Max holding-period exit at the close of the last holding bar.
            exit_price = float(close[exit_idx])
            exit_idx_used = exit_idx
        bars_held = exit_idx_used - decision_idx
        gross_bp = float(side * (exit_price / entry - 1.0) * 10_000.0)
        trade_rows.append(
            {
                "decision_bar_open_at": times[decision_idx].isoformat(),
                "decision_at": (times[decision_idx] + timedelta(seconds=3600)).isoformat(),
                "entry_at": times[entry_idx].isoformat(),
                "exit_at": times[exit_idx_used].isoformat(),
                "side": "LONG" if side > 0 else "SHORT",
                "entry_price": float(entry),
                "exit_price": float(exit_price),
                "gross_return_bp": gross_bp,
                "bars_held": bars_held,
                "barrier": barrier,
            }
        )
        next_available_decision = exit_idx_used

    stress: list[dict[str, Any]] = []
    for multiplier in multipliers:
        round_trip_cost_bp = _round_trip_cost_bp(
            fee_bp=costs["fee_bp"],
            spread_bp=costs["spread_bp"],
            slippage_bp=costs["slippage_bp"],
            funding_bp_per_bar=costs["funding_bp_per_bar"],
            bars_held=horizon,
            multiplier=multiplier,
        )
        gross_list = [float(row["gross_return_bp"]) for row in trade_rows]
        net_list = [g - round_trip_cost_bp for g in gross_list]
        row = {
            "cost_multiplier": multiplier,
            "round_trip_cost_bp": round_trip_cost_bp,
            **_trade_metrics(gross_list, net_list),
        }
        stress.append(row)

    base = next(row for row in stress if math.isclose(row["cost_multiplier"], 1.0, abs_tol=1e-9))
    base_rt_cost = float(base["round_trip_cost_bp"])
    base_net_returns = [float(row["gross_return_bp"]) - base_rt_cost for row in trade_rows]
    account = _account_metrics(
        base_net_returns, position_pct=position_pct, starting_equity=starting_equity
    )
    for row in trade_rows:
        row["net_return_bp"] = float(row["gross_return_bp"]) - base_rt_cost

    gross_total_bp = sum(row["gross_return_bp"] for row in trade_rows)
    total_net_return_bp = sum(base_net_returns)

    # Per-component trading costs applied exactly once per round trip.
    total_fees_bp = 2.0 * costs["fee_bp"] * len(trade_rows)
    total_spread_bp = costs["spread_bp"] * len(trade_rows)
    total_slippage_bp = 2.0 * costs["slippage_bp"] * len(trade_rows)
    total_funding_bp = sum(
        costs["funding_bp_per_bar"] * int(row["bars_held"]) for row in trade_rows
    )
    total_component_costs_bp = (
        total_fees_bp + total_spread_bp + total_slippage_bp + total_funding_bp
    )

    base_tm = base  # _trade_metrics result for 1x costs
    gross_profit_bp = base_tm["gross_profit_bp"]
    gross_loss_bp = base_tm["gross_loss_bp"]
    net_profit_bp = base_tm["net_profit_bp"]
    net_loss_bp = base_tm["net_loss_bp"]
    gross_profit_factor = base_tm["gross_profit_factor"]
    net_profit_factor = base_tm["net_profit_factor"]
    win_rate = base_tm["win_rate"]
    expectancy_bp = base_tm["expectancy_bp"]
    max_drawdown_bp = base_tm["max_drawdown_bp"]
    average_trade_return_bp = base_tm["average_trade_return_bp"]

    bars_held_total = sum(row["bars_held"] for row in trade_rows)
    total_gross_notional = sum(abs(row["entry_price"]) * position_pct for row in trade_rows)
    turnover_turns = total_gross_notional / starting_equity

    return {
        "simulation": "research_triple_barrier_next_open_v1",
        "execution_rule": (
            "decision at close t; fill at open t+1; barriers evaluated "
            "on bars t+1..t+max_horizon_bars; +1=LONG (TP upper/SL lower), "
            "-1=SHORT (TP lower/SL upper); same-bar touch -> stop first; "
            "max holding-period exit at close"
        ),
        "position_overlap": "not allowed; next decision only after previous exit bar",
        "horizon_bars": horizon,
        "cost_inputs": costs,
        "position_pct": position_pct,
        "starting_equity": starting_equity,
        "cost_stress": stress,
        "trades": trade_rows,
        "skipped": {
            "overlapping_signals": skipped_overlap,
            "insufficient_future_bars": skipped_no_exit,
            "missing_or_invalid_vol": skipped_invalid_vol,
        },
        # --- Gross-vs-net accounting (gross before costs, net after all costs) ---
        "trade_count": int(base_tm["trade_count"]),
        "gross_profit_bp": float(gross_profit_bp),
        "gross_loss_bp": float(gross_loss_bp),
        "gross_return_bp": float(gross_total_bp),
        "net_profit_bp": float(net_profit_bp),
        "net_loss_bp": float(net_loss_bp),
        "total_net_return_bp": float(total_net_return_bp),
        "total_trading_costs_bp": float(base_tm["total_trading_costs_bp"]),
        "fees_bp": float(total_fees_bp),
        "spread_bp": float(total_spread_bp),
        "slippage_bp": float(total_slippage_bp),
        "funding_bp": float(total_funding_bp),
        "costs_total_bp": float(total_component_costs_bp),
        "gross_profit_factor": gross_profit_factor,
        "net_profit_factor": net_profit_factor,
        "profit_factor": net_profit_factor,
        "win_rate": win_rate,
        "expectancy_bp": expectancy_bp,
        "maximum_drawdown": float(max_drawdown_bp),
        "average_trade_return_bp": average_trade_return_bp,
        # Compounded account-level result (single accounting convention).
        "account_return_pct": float(account["account_return_pct"]),
        "max_drawdown_pct": float(account["max_drawdown_pct"]),
        "max_drawdown_bp_account": float(account["max_drawdown_bp"]),
        "final_equity": float(account["final_equity"]),
        "bars_held_total": int(bars_held_total),
        "exposure_fraction": float(bars_held_total) / float(max(1, n - horizon - 1)),
        "turnover_equity_turns": float(turnover_turns),
    }


__all__ = ["evaluate_fixed_horizon_strategy", "evaluate_triple_barrier_strategy"]
