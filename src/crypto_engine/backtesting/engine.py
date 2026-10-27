"""Event-safe backtest engine (Phase 6 Track A).

Timing contract (core invariant): a signal with ``generated_at`` in bar
interval [t] (decision on bar t close) may fill NO EARLIER than bar t+1
open. ``entry_bar = first bar with open time strictly greater than
generated_at``. Signals dated before the first bar open are skipped
(``PREDATES_DATA``); signals with no later bar are skipped
(``NO_FUTURE_BAR``); entry opens after ``valid_until`` are skipped
(``EXPIRED``). No wall-clock use — all event time.

Intra-bar policy (documented conventions):
- One position at a time (single symbol/timeframe). Opposite-direction
  signal closes the open position at the entry-bar open (``SIGNAL_FLIP``)
  then opens the new position the same bar (two legs, both costed).
  Same-direction signal while open is a no-op (``ALREADY_POSITIONED``).
- SL/TP from signal levels are monitored from the fill bar onward using
  bar high/low. Same-bar touch of both stop and take exits
  ``STOP_LOSS`` (conservative; matches triple-barrier convention).
  Exiting LONG is a SELL at the touched level minus costs; exiting
  SHORT is a BUY at the level plus costs (via ``ExecutionCostModel``).
- Timeout: ``(bar_idx - entry_idx) > max_holding_bars`` exits at that
  bar's open (``TIMEOUT``), checked before SL/TP on the same bar.
- End of data with an open position exits at the last bar close with
  costs (``END_OF_DATA`` convention — a forced mark, not a real fill).
- Funding accrues once per bar close while a position is open
  (``funding_bp`` per-bar rate, charged regardless of side —
  conservative; intra-bar round trips pay no funding).
- Sizing: ``qty = (equity_at_entry * position_pct / 100) / fill_price``;
  equity is cash plus position market value (long ``+qty*close``,
  short ``-qty*close``). Float arithmetic, no hidden rounding;
  identical inputs give identical ledgers (tested).

Limitations: single position (no pyramiding/partial fills), full-bar
SL/TP touch (no intra-bar ordering beyond the SL-wins-ties rule),
END_OF_DATA is a marking convention, funding side-agnostic.
Only ``decision.status == "approved"`` records may trade; abstained or
rejected signals are skipped (``NOT_APPROVED``) and never open/close.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import math
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.backtesting.types import ExitReason, Fill, Trade, validate_bars
from crypto_engine.signals.schema import SignalRecord

SkipReason = Literal[
    "NO_TRADE",
    "SYMBOL_MISMATCH",
    "PREDATES_DATA",
    "NO_FUTURE_BAR",
    "EXPIRED",
    "LEVELS_REQUIRED",
    "ALREADY_POSITIONED",
    "NO_POSITION",
]


class InsufficientDataError(ValueError):
    """Raised when fewer than 2 bars are supplied (need entry + exit room)."""


class SkippedSignal(BaseModel):
    """A signal that produced no fill, with machine-readable reason."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    reason: str


class EquityPoint(BaseModel):
    """Marked-to-close equity sample (ts = bar open ISO, value at bar close)."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    equity: float


class BacktestResult(BaseModel):
    """Full reproducible backtest ledger."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    trades: list[Trade] = Field(default_factory=list)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    summary: dict[str, float] = Field(default_factory=dict)
    skipped_signals: list[SkippedSignal] = Field(default_factory=list)
    config_hash: str = ""
    bar_start: str = ""
    bar_end: str = ""
    n_bars: int = 0
    warnings: list[str] = Field(default_factory=list)


def _levels_ok(signal: SignalRecord) -> bool:
    """Valid levels for a directional signal: stop+take present, geometry valid."""
    if signal.stop_loss is None or signal.take_profit is None:
        return False
    try:
        from crypto_engine.signals.schema import check_direction_geometry

        check_direction_geometry(
            signal.direction, signal.entry_ref, signal.stop_loss, signal.take_profit
        )
    except ValueError:
        return False
    return True


def _summarize(
    trades: list[Trade],
    equity_curve: list[EquityPoint],
    starting_equity: float,
    exposure_bars: int,
) -> tuple[dict[str, float], list[str]]:
    closed = [t for t in trades if t.exit is not None]
    nets = [t.net_pnl for t in closed]
    gross = float(sum(t.gross_pnl for t in closed))
    costs = float(sum(t.costs_total for t in closed))
    net = float(sum(nets))
    n = len(closed)
    wins = sum(1 for v in nets if v > 0)
    final = equity_curve[-1].equity if equity_curve else starting_equity
    peak = starting_equity
    max_dd = 0.0
    for pt in equity_curve:
        peak = max(peak, pt.equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - pt.equity) / peak * 100.0)
    summary = {
        "n_trades": float(n),
        "wins": float(wins),
        "win_rate": float(wins / n) if n else 0.0,
        "gross_pnl": gross,
        "costs_total": costs,
        "net_pnl": net,
        "max_drawdown_pct": max_dd,
        "return_pct": (final - starting_equity) / starting_equity * 100.0
        if starting_equity > 0
        else 0.0,
        "exposure_bars": float(exposure_bars),
        "final_equity": final,
    }
    warnings: list[str] = []
    if n == 0:
        warnings.append("no closed trades; win_rate/max_drawdown are degenerate (0.0).")
    return summary, warnings


def run_backtest(
    bars: pd.DataFrame,
    signals: list[SignalRecord],
    config: Any,
    cost: Any,
) -> BacktestResult:
    """Run an event-safe backtest over ``bars`` with ``signals``.

    Args:
        bars: OHLC frame with UTC DatetimeIndex of bar opens.
        signals: candidate signals (any order; sorted by generated_at).
        config: ``BacktestConfig`` (symbol/timeframe/starting_equity/
            position_pct/max_holding_bars/require_levels/max_bars).
        cost: ``CostEstimate`` (legs reused by ``ExecutionCostModel``).

    Returns:
        Deterministic ``BacktestResult`` ledger.
    """
    from crypto_engine.signals.schema import CostEstimate as _CE

    if not isinstance(cost, _CE):
        raise TypeError(f"cost must be a CostEstimate, got {type(cost).__name__}.")
    max_bars = int(config.max_bars)
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        raise ValueError("run_backtest: bars must be a non-empty DataFrame.")
    if len(bars) < 2:
        raise InsufficientDataError(
            f"run_backtest: need >= 2 bars (got {len(bars)}); no exit room."
        )
    if len(bars) > max_bars:
        raise ValueError(
            f"run_backtest: bars len {len(bars)} exceeds max_bars={max_bars} (4GB cap)."
        )
    frame = validate_bars(bars, max_bars=max_bars)

    for name in ("starting_equity", "position_pct"):
        v = float(getattr(config, name))
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"run_backtest: {name} must be > 0 finite, got {v!r}.")
    max_holding = int(config.max_holding_bars)
    if max_holding < 0:
        raise ValueError("run_backtest: max_holding_bars must be >= 0.")

    symbol = str(config.symbol)
    timeframe = str(config.timeframe)
    cash = float(config.starting_equity)
    starting = cash
    position_pct = float(config.position_pct)
    require_levels = bool(config.require_levels)

    model = ExecutionCostModel(cost)
    times = list(frame.index)
    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    n = len(frame)

    ordered = sorted(signals, key=lambda s: (s.generated_at, s.signal_id))
    skipped: list[SkippedSignal] = []
    # Precompute entry bars; bucket executable signals per bar.
    from collections import defaultdict

    buckets: dict[int, list[SignalRecord]] = defaultdict(list)
    first_open = times[0]
    for sig in ordered:
        if sig.direction == "NO_TRADE":
            skipped.append(SkippedSignal(signal_id=sig.signal_id, reason="NO_TRADE"))
            continue
        if sig.decision.status != "approved":
            # Only approved records may mutate backtest state; abstained or
            # rejected signals (gates vetoed) must never open/close positions.
            skipped.append(SkippedSignal(signal_id=sig.signal_id, reason="NOT_APPROVED"))
            continue
        if sig.symbol != symbol or sig.timeframe != timeframe:
            skipped.append(SkippedSignal(signal_id=sig.signal_id, reason="SYMBOL_MISMATCH"))
            continue
        gen = sig.generated_at
        gen_ts = gen.replace(tzinfo=_dt.UTC) if gen.tzinfo is None else gen
        if gen_ts < first_open:
            skipped.append(SkippedSignal(signal_id=sig.signal_id, reason="PREDATES_DATA"))
            continue
        entry_idx: int | None = None
        for i, t in enumerate(times):
            if t > gen_ts:
                entry_idx = i
                break
        if entry_idx is None:
            skipped.append(SkippedSignal(signal_id=sig.signal_id, reason="NO_FUTURE_BAR"))
            continue
        valid_until = sig.valid_until
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=_dt.UTC)
        if times[entry_idx] > valid_until:
            skipped.append(SkippedSignal(signal_id=sig.signal_id, reason="EXPIRED"))
            continue
        if require_levels and sig.direction in ("LONG", "SHORT") and not _levels_ok(sig):
            skipped.append(SkippedSignal(signal_id=sig.signal_id, reason="LEVELS_REQUIRED"))
            continue
        buckets[entry_idx].append(sig)

    trades: list[Trade] = []
    equity_curve: list[EquityPoint] = []
    exposure_bars = 0
    open_pos: dict[str, Any] | None = None
    active_trade: Trade | None = None
    funding_accrued = 0.0
    trade_counter = 0

    def _equity(close_px: float) -> float:
        if open_pos is None:
            return cash
        if open_pos["side"] == "LONG":
            return cash + open_pos["qty"] * close_px
        return cash - open_pos["qty"] * close_px

    def _close_position(bar_idx: int, px: float, reason: ExitReason, ts_iso: str) -> None:
        nonlocal cash, open_pos, active_trade, funding_accrued
        assert open_pos is not None and active_trade is not None
        side = open_pos["side"]
        qty = open_pos["qty"]
        notional = qty * px
        fee = model.fill_fee(notional)
        exit_side = "SELL" if side == "LONG" else "BUY"
        if side == "LONG":
            cash += notional - fee
            gross = (px - open_pos["entry_price"]) * qty
        else:
            cash -= notional + fee
            gross = (open_pos["entry_price"] - px) * qty
        costs = open_pos["entry_fee"] + fee + funding_accrued
        active_trade.exit = Fill(
            ts=ts_iso,
            side=exit_side,  # type: ignore[arg-type]
            price=px,
            qty=qty,
            fee_bp=model.fee_bp,
            spread_bp=model.spread_bp,
            slippage_bp=model.slippage_bp,
            notional=notional,
            cost_total=fee,
        )
        active_trade.exit_reason = reason
        active_trade.gross_pnl = gross
        active_trade.costs_total = costs
        active_trade.net_pnl = gross - costs
        active_trade.bars_held = bar_idx - open_pos["entry_idx"]
        open_pos = None
        active_trade = None
        funding_accrued = 0.0

    def _open_position(bar_idx: int, sig: SignalRecord) -> None:
        nonlocal cash, open_pos, active_trade, funding_accrued, trade_counter
        side = sig.direction
        assert side in ("LONG", "SHORT")
        buy_side = "BUY" if side == "LONG" else "SELL"
        raw = opens[bar_idx]
        px = model.fill_price(buy_side, float(raw))
        equity_now = cash
        qty = (equity_now * position_pct / 100.0) / px
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError(f"run_backtest: non-positive qty {qty!r} (equity {cash}).")
        notional = qty * px
        fee = model.fill_fee(notional)
        if side == "LONG":
            cash -= notional + fee
        else:
            cash += notional - fee
        ts_iso = times[bar_idx].isoformat()
        trade_id = f"{symbol}|{timeframe}|{ts_iso}|{side}|{trade_counter}"
        trade_counter += 1
        entry_fill = Fill(
            ts=ts_iso,
            side=buy_side,  # type: ignore[arg-type]
            price=px,
            qty=qty,
            fee_bp=model.fee_bp,
            spread_bp=model.spread_bp,
            slippage_bp=model.slippage_bp,
            notional=notional,
            cost_total=fee,
        )
        trade = Trade(
            trade_id=trade_id,
            symbol=symbol,
            timeframe=timeframe,
            side=side,  # type: ignore[arg-type]
            entry=entry_fill,
            exit=None,
            exit_reason=None,
            gross_pnl=0.0,
            costs_total=fee,
            net_pnl=0.0,
            bars_held=0,
        )
        trades.append(trade)
        active_trade = trade
        open_pos = {
            "side": side,
            "entry_price": px,
            "qty": qty,
            "notional": notional,
            "entry_fee": fee,
            "entry_idx": bar_idx,
            "stop_loss": sig.stop_loss,
            "take_profit": sig.take_profit,
        }
        funding_accrued = 0.0

    for i in range(n):
        ts_iso = times[i].isoformat()
        # 1. Timeout exits at bar open (before new signals/SLTP on this bar).
        if open_pos is not None and (i - open_pos["entry_idx"]) > max_holding:
            px = model.fill_price("SELL" if open_pos["side"] == "LONG" else "BUY", float(opens[i]))
            _close_position(i, px, "TIMEOUT", ts_iso)
        # 2. Signal actions at this bar open.
        for sig in buckets.get(i, []):
            if sig.direction == "NEUTRAL":
                if open_pos is not None:
                    px = model.fill_price(
                        "SELL" if open_pos["side"] == "LONG" else "BUY",
                        float(opens[i]),
                    )
                    _close_position(i, px, "NEUTRAL_CLOSE", ts_iso)
                else:
                    skipped.append(SkippedSignal(signal_id=sig.signal_id, reason="NO_POSITION"))
            elif sig.direction in ("LONG", "SHORT"):
                if open_pos is not None and open_pos["side"] == sig.direction:
                    skipped.append(
                        SkippedSignal(signal_id=sig.signal_id, reason="ALREADY_POSITIONED")
                    )
                else:
                    if open_pos is not None:
                        px = model.fill_price(
                            "SELL" if open_pos["side"] == "LONG" else "BUY",
                            float(opens[i]),
                        )
                        _close_position(i, px, "SIGNAL_FLIP", ts_iso)
                    _open_position(i, sig)
        # 3. SL/TP on this bar (fill bar onward; SL wins ties).
        if open_pos is not None:
            sl = open_pos["stop_loss"]
            tp = open_pos["take_profit"]
            side = open_pos["side"]
            sl_hit = tp_hit = False
            if side == "LONG":
                sl_hit = sl is not None and lows[i] <= float(sl)
                tp_hit = tp is not None and highs[i] >= float(tp)
            else:
                sl_hit = sl is not None and highs[i] >= float(sl)
                tp_hit = tp is not None and lows[i] <= float(tp)
            if sl_hit or tp_hit:
                if sl_hit:
                    level = float(sl)
                    reason: ExitReason = "STOP_LOSS"
                else:
                    level = float(tp)
                    reason = "TAKE_PROFIT"
                px = model.fill_price("SELL" if side == "LONG" else "BUY", level)
                _close_position(i, px, reason, ts_iso)
        # 4. Funding + mark-to-close.
        if open_pos is not None:
            f = model.funding_for_bars(open_pos["notional"], 1)
            cash -= f
            funding_accrued += f
            exposure_bars += 1
        equity_curve.append(EquityPoint(ts=ts_iso, equity=_equity(float(closes[i]))))

    # 5. End of data: forced exit at last close.
    if open_pos is not None:
        px = model.fill_price("SELL" if open_pos["side"] == "LONG" else "BUY", float(closes[n - 1]))
        _close_position(n - 1, px, "END_OF_DATA", times[n - 1].isoformat())
        equity_curve[-1].equity = cash

    summary, warnings = _summarize(trades, equity_curve, starting, exposure_bars)
    import json as _json

    cfg_payload = config.model_dump(mode="json") if hasattr(config, "model_dump") else dict(config)
    digest = hashlib.sha256(
        _json.dumps(cfg_payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        trades=trades,
        equity_curve=equity_curve,
        summary=summary,
        skipped_signals=skipped,
        config_hash=digest,
        bar_start=times[0].isoformat(),
        bar_end=times[-1].isoformat(),
        n_bars=n,
        warnings=warnings,
    )


__all__ = [
    "BacktestResult",
    "EquityPoint",
    "InsufficientDataError",
    "SkipReason",
    "SkippedSignal",
    "run_backtest",
]
