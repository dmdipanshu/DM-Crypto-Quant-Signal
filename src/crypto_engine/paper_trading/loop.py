"""Single-step paper execution loop (Phase 6 Track B).

Mirrors :func:`crypto_engine.backtesting.engine.run_backtest` semantics on
live-arriving bars. A paper step consumes exactly ONE new bar plus at most
ONE pending approved signal. The next-bar rule is enforced relative to the
paper flow: a paper fill happens at the stepped bar's OPEN, and the stepped
signal must satisfy ``signal.generated_at < bar.ts`` (else ``STALE_FUTURE``)
— a signal is never filled at or before its own bar.

Per-step order (mirrors the engine):
1. Append the bar to the bounded bar window (no future-bar use: the step
   consumes exactly the passed bar; later bars cannot change this step).
2. Halted account -> reject ``HALTED`` (no state change, id not consumed).
3. Already-processed signal id -> reject ``DUPLICATE`` (account UNCHANGED:
   fully idempotent replay, no bar append, no equity point).
4. Signal validation: symbol/timeframe match (``SYMBOL_MISMATCH``), causality
   (``STALE_FUTURE``), expiry (``EXPIRED``), levels when required
   (``LEVELS_REQUIRED``). Rejections consume the id (never re-evaluated).
5. Timeout: ``bars_held + 1 > max_holding_bars`` closes the open position at
   the bar OPEN (``TIMEOUT``) BEFORE the new signal is evaluated.
6. Risk gates via the caller-supplied ``gates_ctx_fn`` (portfolio mapped from
   paper equity — never invented inside the loop). Veto -> rejection
   recorded as the ``RISK_GATE_*`` code; no position change.
7. Direction actions at the bar OPEN: ``NEUTRAL`` closes (``NEUTRAL_CLOSE``)
   or no-ops (``NO_POSITION``); ``NO_TRADE`` no-ops; LONG/SHORT opens when
   flat, flips when opposed (close ``SIGNAL_FLIP`` + open same bar, two
   legs), no-ops when aligned (``ALREADY_POSITIONED``).
8. SL/TP monitored on the step bar high/low (entry bar included; same-bar
   touch of both exits ``STOP_LOSS`` — conservative, engine convention).
9. Funding accrues once per step bar while a position remains open
   (side-agnostic per-bar rate, engine convention; intra-bar round trips pay
   no funding). Equity is marked at the bar close and appended.

``step`` is pure-ish: it deep-copies the account, mutates the copy, and
returns ``(new_account, StepResult)`` — the caller persists via
:func:`save_account`. Advisory-only: no live orders (no order/trade API
imports; ``pandas``/``numpy`` only in tests/CLI layers, not here).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.paper_trading.state import (
    BAR_WINDOW,
    MAX_EQUITY_POINTS,
    PaperAccount,
    account_hash,
    equity,
    mark_processed,
    record_rejection,
)
from crypto_engine.risk.gates import GateContext, run_gates
from crypto_engine.signals.schema import SignalRecord

StepStatus = Literal["accepted", "rejected"]


class StepResult(BaseModel):
    """Outcome of one paper step (machine-readable reason + fills)."""

    model_config = ConfigDict(extra="forbid")

    status: StepStatus = "rejected"
    reason: str = ""
    fills: list[dict[str, Any]] = Field(default_factory=list)
    equity_after: float = 0.0
    account_hash: str = ""
    gate_trace: list[dict[str, str]] = Field(default_factory=list)


def _parse_bar_ts(value: Any) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"paper step: bar ts must be ISO-8601, got {value!r}.") from exc
    else:
        raise ValueError(f"paper step: bar ts must be ISO-8601 str, got {value!r}.")
    if dt.tzinfo is None:
        raise ValueError(f"paper step: bar ts must be timezone-aware (UTC), got {value!r}.")
    return dt.astimezone(_dt.UTC)


def _parse_bar(bar: dict[str, Any]) -> dict[str, Any]:
    """Validate a bar dict {ts, open, high, low, close}; returns normalized copy."""
    if not isinstance(bar, dict):
        raise ValueError(f"paper step: bar must be a dict, got {type(bar).__name__}.")
    for key in ("ts", "open", "high", "low", "close"):
        if key not in bar:
            raise ValueError(f"paper step: bar missing required key {key!r}.")
    ts = _parse_bar_ts(bar["ts"])
    nums: dict[str, float] = {}
    for key in ("open", "high", "low", "close"):
        try:
            v = float(bar[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"paper step: bar[{key!r}] must be numeric, got {bar[key]!r}."
            ) from exc
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"paper step: bar[{key!r}] must be > 0 finite, got {bar[key]!r}.")
        nums[key] = v
    if nums["high"] < nums["low"]:
        raise ValueError("paper step: bar high < low.")
    for key in ("open", "close"):
        if not (nums["low"] <= nums[key] <= nums["high"]):
            raise ValueError(f"paper step: bar {key} outside [low, high].")
    return {
        "ts": ts,
        "open": nums["open"],
        "high": nums["high"],
        "low": nums["low"],
        "close": nums["close"],
    }


def _levels_ok(signal: SignalRecord) -> bool:
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


def paper_portfolio(account: PaperAccount, mark_price: float) -> dict[str, Any]:
    """Map paper equity to a caller-supplied gate portfolio (never invented).

    Exposure is derived from the actual position (0 when flat); day-start
    equity is the account's starting equity.
    """
    marked = equity(account, mark_price)
    notional = 0.0
    pos = account.position
    if pos is not None:
        notional = float(pos["qty"]) * float(pos.get("entry_price", mark_price))
    base = max(marked, 1e-12)
    exposure_pct = min(notional / base * 100.0, 100.0)
    return {
        "exposure_pct": exposure_pct,
        "symbol_exposure_pct": exposure_pct,
        "day_start_equity": float(account.starting_equity),
        "current_equity": marked,
        "strategy_enabled": True,
        "circuit_open": False,
    }


def default_gates_ctx(
    account: PaperAccount,
    signal: SignalRecord,
    bar_ts: _dt.datetime,
    risk: Any,
    *,
    max_age_min: float = 60.0,
    staleness_limit_min: float = 90.0,
    require_edge: bool = True,
    edge_margin_bp: float = 0.0,
) -> GateContext:
    """Default gate context: event-time ``now`` = bar ts, portfolio = paper equity.

    The portfolio mark uses the open position's entry price when open (mark
    choice is immaterial when flat since exposure is 0); callers needing an
    exact bar-open mark should build :func:`paper_portfolio` directly.
    """
    return GateContext(
        now_utc=bar_ts,
        risk=risk,
        max_age_min=float(max_age_min),
        staleness_limit_min=float(staleness_limit_min),
        feature_timestamp=None,
        require_edge=bool(require_edge),
        edge_margin_bp=float(edge_margin_bp),
        portfolio=paper_portfolio(account, _open_mark(account)),
    )


def _open_mark(account: PaperAccount) -> float:
    pos = account.position
    if pos is not None:
        return float(pos.get("entry_price", 1.0))
    return float(account.cash) if float(account.cash) > 0 else 1.0


def make_gates_ctx_fn(
    risk: Any,
    *,
    max_age_min: float = 60.0,
    staleness_limit_min: float = 90.0,
    require_edge: bool = True,
    edge_margin_bp: float = 0.0,
) -> Callable[[PaperAccount, SignalRecord, dict[str, Any]], GateContext]:
    """Build a ``gates_ctx_fn`` closing over risk config for CLI/tests."""

    def _fn(account: PaperAccount, signal: SignalRecord, bar: dict[str, Any]) -> GateContext:
        parsed = _parse_bar(bar)
        return default_gates_ctx(
            account,
            signal,
            parsed["ts"],
            risk,
            max_age_min=max_age_min,
            staleness_limit_min=staleness_limit_min,
            require_edge=require_edge,
            edge_margin_bp=edge_margin_bp,
        )

    return _fn


def _mark_equity_point(account: PaperAccount, ts_iso: str, close_px: float) -> None:
    marked = equity(account, close_px)
    account.equity_mark_ts = ts_iso
    account.equity_mark = marked
    account.equity_curve.append([ts_iso, marked])
    while len(account.equity_curve) > MAX_EQUITY_POINTS:
        account.equity_curve.pop(0)


def step(
    account: PaperAccount,
    signal: SignalRecord,
    bar: dict[str, Any],
    gates_ctx_fn: Callable[[PaperAccount, SignalRecord, dict[str, Any]], GateContext],
    cost: ExecutionCostModel,
) -> tuple[PaperAccount, StepResult]:
    """Consume one bar + one signal; returns ``(new_account, StepResult)``.

    The input account is never mutated (deep-copied). Signal validation
    failures and gate vetoes consume the signal id (recorded rejection, never
    re-evaluated); ``DUPLICATE`` and ``HALTED`` leave the copy identical.
    """
    if not isinstance(signal, SignalRecord):
        raise TypeError(f"paper step: signal must be a SignalRecord, got {type(signal).__name__}.")
    if not isinstance(cost, ExecutionCostModel):
        raise TypeError(
            f"paper step: cost must be an ExecutionCostModel, got {type(cost).__name__}."
        )
    parsed = _parse_bar(bar)
    bar_ts: _dt.datetime = parsed["ts"]
    ts_iso = bar_ts.isoformat()

    next_acct = account.model_copy(deep=True)

    def _done(
        status: StepStatus, reason: str, fills: list[dict[str, Any]], trace: Any = None
    ) -> tuple[PaperAccount, StepResult]:
        marked_close = equity(next_acct, parsed["close"])
        return next_acct, StepResult(
            status=status,
            reason=reason,
            fills=fills,
            equity_after=marked_close,
            account_hash=account_hash(next_acct),
            gate_trace=[dict(t) for t in (trace or [])],
        )

    # 2. Halted: refuse everything, consume nothing.
    if next_acct.status == "halted":
        return _done("rejected", "HALTED", [])

    # 3. Duplicate: fully idempotent (account copy identical, hash-equal).
    if str(signal.signal_id) in set(next_acct.processed_signal_ids):
        return _done("rejected", "DUPLICATE", [])

    # 1/4. Append bar to bounded window (only for non-duplicate, non-halted steps).
    next_acct.bars_seen += 1
    next_acct.recent_bars.append(ts_iso)
    while len(next_acct.recent_bars) > BAR_WINDOW:
        next_acct.recent_bars.pop(0)

    gen = signal.generated_at
    gen_utc = gen.replace(tzinfo=_dt.UTC) if gen.tzinfo is None else gen.astimezone(_dt.UTC)
    valid = signal.valid_until
    valid_utc = valid.replace(tzinfo=_dt.UTC) if valid.tzinfo is None else valid.astimezone(_dt.UTC)

    def _reject(reason: str) -> tuple[PaperAccount, StepResult]:
        record_rejection(next_acct, signal.signal_id, reason, ts_iso)
        return _done("rejected", reason, [])

    if signal.symbol != next_acct.symbol or signal.timeframe != next_acct.timeframe:
        return _reject("SYMBOL_MISMATCH")
    if not gen_utc < bar_ts:
        return _reject("STALE_FUTURE")
    if bar_ts > valid_utc:
        return _reject("EXPIRED")
    if (
        signal.direction in ("LONG", "SHORT")
        and bool(next_acct.require_levels)
        and not _levels_ok(signal)
    ):
        return _reject("LEVELS_REQUIRED")

    fills: list[dict[str, Any]] = []
    reasons: list[str] = []

    def _close_position(px: float, reason: str) -> None:
        from crypto_engine.backtesting.types import Trade

        assert next_acct.position is not None
        pos = next_acct.position
        side = str(pos["side"])
        qty = float(pos["qty"])
        exit_side = "SELL" if side == "LONG" else "BUY"
        fill_px = cost.fill_price(exit_side, px)
        notional = qty * fill_px
        fee = cost.fill_fee(notional)
        if side == "LONG":
            next_acct.cash += notional - fee
            gross = (fill_px - float(pos["entry_price"])) * qty
        else:
            next_acct.cash -= notional + fee
            gross = (float(pos["entry_price"]) - fill_px) * qty
        costs_total = (
            float(pos.get("entry_fee", 0.0)) + fee + float(pos.get("funding_accrued", 0.0))
        )
        exit_fill = {
            "ts": ts_iso,
            "side": exit_side,
            "price": fill_px,
            "qty": qty,
            "fee_bp": cost.fee_bp,
            "spread_bp": cost.spread_bp,
            "slippage_bp": cost.slippage_bp,
            "notional": notional,
            "cost_total": fee,
        }
        trade_id = (
            f"{next_acct.symbol}|{next_acct.timeframe}|{pos['entry_ts']}|{side}|"
            f"{next_acct.trade_counter}"
        )
        next_acct.trade_counter += 1
        trade = Trade(
            trade_id=trade_id,
            symbol=next_acct.symbol,
            timeframe=next_acct.timeframe,
            side=side,  # type: ignore[arg-type]
            entry=pos["entry_fill"],  # type: ignore[arg-type]
            exit=exit_fill,  # type: ignore[arg-type]
            exit_reason=reason,  # type: ignore[arg-type]
            gross_pnl=gross,
            costs_total=costs_total,
            net_pnl=gross - costs_total,
            bars_held=int(pos.get("bars_held", 0)),
        )
        next_acct.trades_closed.append(trade.model_dump(mode="json"))
        from crypto_engine.paper_trading.state import MAX_TRADES_CLOSED

        while len(next_acct.trades_closed) > MAX_TRADES_CLOSED:
            next_acct.trades_closed.pop(0)
        next_acct.position = None
        fills.append(exit_fill)

    def _open_position(sig: SignalRecord) -> None:
        side = str(sig.direction)
        assert side in ("LONG", "SHORT")
        buy_side = "BUY" if side == "LONG" else "SELL"
        fill_px = cost.fill_price(buy_side, parsed["open"])
        equity_now = float(next_acct.cash)
        qty = (equity_now * float(next_acct.position_pct) / 100.0) / fill_px
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError(f"paper step: non-positive qty {qty!r} (cash {next_acct.cash}).")
        notional = qty * fill_px
        fee = cost.fill_fee(notional)
        if side == "LONG":
            next_acct.cash -= notional + fee
        else:
            next_acct.cash += notional - fee
        entry_fill = {
            "ts": ts_iso,
            "side": buy_side,
            "price": fill_px,
            "qty": qty,
            "fee_bp": cost.fee_bp,
            "spread_bp": cost.spread_bp,
            "slippage_bp": cost.slippage_bp,
            "notional": notional,
            "cost_total": fee,
        }
        next_acct.position = {
            "side": side,
            "entry_ts": ts_iso,
            "entry_price": fill_px,
            "qty": qty,
            "notional": notional,
            "entry_fee": fee,
            "funding_accrued": 0.0,
            "entry_fill": entry_fill,
            "stop_loss": sig.stop_loss,
            "take_profit": sig.take_profit,
            "bars_held": 0,
        }
        fills.append(entry_fill)

    # 5. Timeout before evaluating the new signal.
    if next_acct.position is not None:
        held = int(next_acct.position.get("bars_held", 0)) + 1
        if held > int(next_acct.max_holding_bars):
            next_acct.position["bars_held"] = held
            _close_position(parsed["open"], "TIMEOUT")
            reasons.append("TIMEOUT")
        else:
            next_acct.position["bars_held"] = held

    # 6. Risk gates (caller-supplied context; portfolio mapped from paper equity).
    ctx = gates_ctx_fn(next_acct, signal, bar)
    if not isinstance(ctx, GateContext):
        raise TypeError(
            f"paper step: gates_ctx_fn must return GateContext, got {type(ctx).__name__}."
        )
    gated, trace = run_gates(signal, ctx)
    if gated.decision.status == "rejected":
        codes = [c for c in gated.decision.reason_codes if str(c).startswith("RISK_GATE_")]
        code = codes[0] if codes else "RISK_GATE_UNKNOWN"
        record_rejection(next_acct, signal.signal_id, code, ts_iso)
        _mark_equity_point(next_acct, ts_iso, parsed["close"])
        result = StepResult(
            status="rejected",
            reason=code,
            fills=fills,
            equity_after=equity(next_acct, parsed["close"]),
            account_hash=account_hash(next_acct),
            gate_trace=[dict(t) for t in trace],
        )
        return next_acct, result

    effective_direction = str(gated.direction)

    # 7. Direction actions at the bar open.
    if effective_direction == "NO_TRADE":
        reasons.append("NO_TRADE")
    elif effective_direction == "NEUTRAL":
        if next_acct.position is not None:
            _close_position(parsed["open"], "NEUTRAL_CLOSE")
            reasons.append("NEUTRAL_CLOSE")
        else:
            reasons.append("NO_POSITION")
    elif effective_direction in ("LONG", "SHORT"):
        if (
            next_acct.position is not None
            and str(next_acct.position["side"]) == effective_direction
        ):
            reasons.append("ALREADY_POSITIONED")
        else:
            if next_acct.position is not None:
                _close_position(parsed["open"], "SIGNAL_FLIP")
                reasons.append("SIGNAL_FLIP")
            _open_position(gated)
            reasons.append("OPENED")
    else:  # pragma: no cover - schema restricts directions; defense in depth
        record_rejection(next_acct, signal.signal_id, "INVALID_DIRECTION", ts_iso)
        _mark_equity_point(next_acct, ts_iso, parsed["close"])
        return next_acct, StepResult(
            status="rejected",
            reason="INVALID_DIRECTION",
            fills=fills,
            equity_after=equity(next_acct, parsed["close"]),
            account_hash=account_hash(next_acct),
            gate_trace=[dict(t) for t in trace],
        )

    # 8. SL/TP on the step bar (fill bar onward; SL wins ties).
    if next_acct.position is not None:
        pos = next_acct.position
        side = str(pos["side"])
        sl = pos.get("stop_loss")
        tp = pos.get("take_profit")
        sl_hit = tp_hit = False
        if side == "LONG":
            sl_hit = sl is not None and parsed["low"] <= float(sl)
            tp_hit = tp is not None and parsed["high"] >= float(tp)
        else:
            sl_hit = sl is not None and parsed["high"] >= float(sl)
            tp_hit = tp is not None and parsed["low"] <= float(tp)
        if sl_hit or tp_hit:
            if sl_hit:
                _close_position(float(sl), "STOP_LOSS")
                reasons.append("STOP_LOSS")
            else:
                assert tp is not None
                _close_position(float(tp), "TAKE_PROFIT")
                reasons.append("TAKE_PROFIT")

    # 9. Funding + mark-to-close.
    if next_acct.position is not None:
        funding = cost.funding_for_bars(float(next_acct.position["notional"]), 1)
        next_acct.cash -= funding
        next_acct.position["funding_accrued"] = (
            float(next_acct.position.get("funding_accrued", 0.0)) + funding
        )
    mark_processed(next_acct, signal.signal_id)
    _mark_equity_point(next_acct, ts_iso, parsed["close"])
    # Multi-leg steps (flip/timeout+open, open+SLTP) join reasons in order.
    primary = "+".join(reasons) if len(reasons) > 1 else (reasons[0] if reasons else "NO_TRADE")
    return next_acct, StepResult(
        status="accepted",
        reason=primary,
        fills=fills,
        equity_after=equity(next_acct, parsed["close"]),
        account_hash=account_hash(next_acct),
        gate_trace=[dict(t) for t in trace],
    )


def _digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


__all__ = [
    "StepResult",
    "StepStatus",
    "default_gates_ctx",
    "make_gates_ctx_fn",
    "paper_portfolio",
    "step",
]
