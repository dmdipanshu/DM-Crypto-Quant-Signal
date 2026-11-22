"""Deterministic simulation engine for Q7 paper trading (Phase 4).

Runs a paper-trading account over historical bars with multiple signal
producers. Every decision, fill, position transition, P&L change, and risk
event is recorded in an immutable audit trail. Same bars + same producers
=> identical results (deterministic).

Advisory-only: no live orders, no network, no randomness.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.config.settings import RiskConfig
from crypto_engine.paper_trading.orders import (
    OrderCancelError,
    OrderManager,
    OrderRejectionError,
    OrderSide,
    OrderType,
)
from crypto_engine.paper_trading.risk_extensions import DailyLossTracker
from crypto_engine.paper_trading.state import PaperAccount, equity, flatten
from crypto_engine.risk.gates import GateContext, run_gates
from crypto_engine.signals.schema import CostEstimate, SignalRecord


class SimulatorConfig(BaseModel):
    """Configuration for a deterministic simulation run."""

    model_config = ConfigDict(extra="forbid")

    starting_equity: float = Field(gt=0)
    position_pct: float = Field(gt=0)
    max_holding_bars: int = Field(ge=0)
    require_levels: bool = False
    fee_bp: float = Field(ge=0)
    spread_bp: float = Field(ge=0)
    slippage_bp: float = Field(ge=0)
    funding_bp: float = Field(ge=0)
    max_daily_loss_pct: float = Field(default=5.0, ge=0)
    max_slippage_bp: float | None = None
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"


class SimulatorEvent(BaseModel):
    """One immutable audit-trail event."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    event_type: str
    order_id: str | None = None
    signal_id: str | None = None
    side: str | None = None
    price: float | None = None
    qty: float | None = None
    notional: float | None = None
    fee: float | None = None
    pnl: float | None = None
    equity: float | None = None
    reason: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class SimulatorResult(BaseModel):
    """Complete deterministic simulation result."""

    model_config = ConfigDict(extra="forbid")

    config_hash: str
    n_bars: int
    n_signals: int
    n_orders: int
    n_fills: int
    n_trades: int
    starting_equity: float
    final_equity: float
    net_pnl: float
    gross_pnl: float
    costs_total: float
    max_drawdown_pct: float
    win_rate: float
    trades: list[dict[str, Any]] = Field(default_factory=list)
    equity_curve: list[list[Any]] = Field(default_factory=list)
    orders: list[dict[str, Any]] = Field(default_factory=list)
    fills: list[dict[str, Any]] = Field(default_factory=list)
    events: list[SimulatorEvent] = Field(default_factory=list)
    rejected_signals: list[dict[str, Any]] = Field(default_factory=list)
    daily_loss_breaches: list[str] = Field(default_factory=list)


class Simulator:
    """Deterministic simulation engine.

    Signal producers are callables with signature:
        producer(bar_idx: int, bar: dict, account: PaperAccount) -> SignalRecord | None

    Each bar is processed in order:
      1. Collect signals from all producers
      2. Submit each signal as an order; validate via risk gates (accept/reject)
      3. Process bar fills (market at open, limit at price touch)
      4. SL/TP monitoring on open position
      5. Funding accrual while position open
      6. Mark-to-close equity point
      7. Record all events
    """

    def __init__(
        self,
        config: SimulatorConfig,
        signal_producers: list[Callable[[int, dict[str, Any], PaperAccount], SignalRecord | None]],
        risk_config: RiskConfig | None = None,
    ) -> None:
        self.config = config
        self.signal_producers = list(signal_producers)
        self.risk_config = risk_config or RiskConfig()

        # Build cost model from config
        cost_est = CostEstimate(
            fee_bp=config.fee_bp,
            spread_bp=config.spread_bp,
            slippage_bp=config.slippage_bp,
            funding_bp=config.funding_bp,
            source="configured",
        )
        self.cost = ExecutionCostModel(cost_est)

        # Initialize account (in-memory, not persisted to disk)
        self.account = PaperAccount(
            account_id=f"sim-{config.symbol}-{config.timeframe}",
            symbol=config.symbol,
            timeframe=config.timeframe,
            cash=config.starting_equity,
            starting_equity=config.starting_equity,
            position=None,
            position_pct=config.position_pct,
            max_holding_bars=config.max_holding_bars,
            require_levels=config.require_levels,
            cost={
                "fee_bp": config.fee_bp,
                "spread_bp": config.spread_bp,
                "slippage_bp": config.slippage_bp,
                "funding_bp": config.funding_bp,
            },
            model_id="simulator",
            config_hash="",
            created_at="",
            updated_at="",
        )
        # Manually set checksum since we're not using save_account
        from crypto_engine.paper_trading.state import _checksum_of
        self.account.checksum = _checksum_of(self.account.to_checksum_payload())

        self.order_manager = OrderManager(
            self.account,
            self.cost,
            max_slippage_bp=config.max_slippage_bp,
        )
        self.daily_loss_tracker = DailyLossTracker()

        self._events: list[SimulatorEvent] = []
        self._bar_counter: int = 0

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------
    def _event(
        self,
        event_type: str,
        *,
        order_id: str | None = None,
        signal_id: str | None = None,
        side: str | None = None,
        price: float | None = None,
        qty: float | None = None,
        notional: float | None = None,
        fee: float | None = None,
        pnl: float | None = None,
        equity: float | None = None,
        reason: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        bar_ts = self._current_bar_ts()
        self._events.append(
            SimulatorEvent(
                ts=bar_ts,
                event_type=event_type,
                order_id=order_id,
                signal_id=signal_id,
                side=side,
                price=price,
                qty=qty,
                notional=notional,
                fee=fee,
                pnl=pnl,
                equity=equity,
                reason=reason,
                detail=detail or {},
            )
        )

    def _current_bar_ts(self) -> str:
        if self._bar_counter == 0:
            return ""
        # Will be set by run() per bar
        return getattr(self, "_current_bar_ts_iso", "")

    # ------------------------------------------------------------------
    # Risk gate context
    # ------------------------------------------------------------------
    def _build_gates_ctx(self, signal: SignalRecord, bar: dict[str, Any]) -> GateContext:
        from datetime import datetime as _dt

        bar_ts = _dt.fromisoformat(str(bar["ts"]).replace("Z", "+00:00"))
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=UTC)

        marked = equity(self.account, float(bar["close"]))
        portfolio = {
            "exposure_pct": self.order_manager.exposure_pct(float(bar["close"])),
            "symbol_exposure_pct": self.order_manager.exposure_pct(float(bar["close"])),
            "day_start_equity": float(self.account.starting_equity),
            "current_equity": marked,
            "strategy_enabled": True,
            "circuit_open": False,
        }

        return GateContext(
            now_utc=bar_ts,
            risk=self.risk_config,
            max_age_min=60.0,
            staleness_limit_min=90.0,
            portfolio=portfolio,
        )

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------
    def run(self, bars: list[dict[str, Any]]) -> SimulatorResult:
        """Run the deterministic simulation over all bars.

        Args:
            bars: List of bar dicts {ts, open, high, low, close}, sorted by ts.

        Returns:
            SimulatorResult with complete audit trail.
        """
        if not bars:
            raise ValueError("Simulator: bars must be non-empty.")

        # Validate bars
        for i, bar in enumerate(bars):
            for key in ("ts", "open", "high", "low", "close"):
                if key not in bar:
                    raise ValueError(f"Simulator: bar {i} missing key {key!r}.")

        self._events = []
        self._bar_counter = 0
        n_signals = 0
        rejected_signals: list[dict[str, Any]] = []
        daily_loss_breaches: list[str] = []

        for bar_idx, bar in enumerate(bars):
            self._bar_counter = bar_idx
            bar_ts = str(bar["ts"])
            self._current_bar_ts_iso = bar_ts
            open_px = float(bar["open"])
            high_px = float(bar["high"])
            low_px = float(bar["low"])
            close_px = float(bar["close"])

            # --- Step 1: Collect signals from all producers ---
            signals: list[SignalRecord] = []
            for producer in self.signal_producers:
                try:
                    sig = producer(bar_idx, bar, self.account)
                except Exception:
                    continue
                if sig is not None:
                    signals.append(sig)
                    n_signals += 1

            # --- Step 2: Submit each signal as an order, validate via risk gates ---
            for sig in signals:
                # Determine order side from signal direction
                direction = str(sig.direction)
                if direction == "LONG":
                    order_side: OrderSide = "BUY"
                elif direction == "SHORT":
                    order_side = "SELL"
                else:
                    # NEUTRAL/NO_TRADE: no order
                    self._event(
                        "signal",
                        signal_id=sig.signal_id,
                        side=direction,
                        reason="NO_ORDER",
                        detail={"direction": direction},
                    )
                    continue

                # Determine order type and price
                if sig.entry_ref is not None:
                    order_type: OrderType = "LIMIT"
                    order_price = float(sig.entry_ref)
                else:
                    order_type = "MARKET"
                    order_price = None

                qty = (float(self.account.cash) * self.config.position_pct / 100.0) / open_px
                if not math.isfinite(qty) or qty <= 0:
                    rejected_signals.append({
                        "signal_id": sig.signal_id,
                        "reason": "INSUFFICIENT_CASH",
                        "bar_ts": bar_ts,
                    })
                    self._event(
                        "signal",
                        signal_id=sig.signal_id,
                        side=order_side,
                        reason="INSUFFICIENT_CASH",
                        detail={"cash": float(self.account.cash)},
                    )
                    continue

                # Submit order
                try:
                    order = self.order_manager.submit_order(
                        client_order_id=sig.signal_id,
                        symbol=self.account.symbol,
                        timeframe=self.account.timeframe,
                        side=order_side,
                        order_type=order_type,
                        qty=qty,
                        price=order_price,
                    )
                except Exception as exc:
                    rejected_signals.append({
                        "signal_id": sig.signal_id,
                        "reason": str(exc),
                        "bar_ts": bar_ts,
                    })
                    self._event(
                        "signal",
                        signal_id=sig.signal_id,
                        side=order_side,
                        reason="SUBMIT_ERROR",
                        detail={"error": str(exc)},
                    )
                    continue

                self._event(
                    "order_submitted",
                    order_id=order.order_id,
                    signal_id=sig.signal_id,
                    side=order_side,
                    price=order_price,
                    qty=qty,
                    reason="SUBMITTED",
                )

                # Validate via risk gates
                ctx = self._build_gates_ctx(sig, bar)
                try:
                    gated, _trace = run_gates(sig, ctx)
                except Exception:
                    with contextlib.suppress(Exception):
                        self.order_manager.reject_order(order.order_id, "RISK_GATE_ERROR")
                    rejected_signals.append({
                        "signal_id": sig.signal_id,
                        "reason": "RISK_GATE_ERROR",
                        "bar_ts": bar_ts,
                    })
                    self._event(
                        "order_rejected",
                        order_id=order.order_id,
                        signal_id=sig.signal_id,
                        reason="RISK_GATE_ERROR",
                    )
                    continue

                if gated.decision.status == "rejected":
                    codes = [c for c in gated.decision.reason_codes if str(c).startswith("RISK_GATE_")]
                    code = codes[0] if codes else "RISK_GATE_UNKNOWN"
                    with contextlib.suppress(OrderRejectionError):
                        self.order_manager.reject_order(order.order_id, code)
                    rejected_signals.append({
                        "signal_id": sig.signal_id,
                        "reason": code,
                        "bar_ts": bar_ts,
                    })
                    self._event(
                        "order_rejected",
                        order_id=order.order_id,
                        signal_id=sig.signal_id,
                        reason=code,
                    )
                    continue

                # Accept order
                with contextlib.suppress(OrderRejectionError):
                    self.order_manager.accept_order(order.order_id)
                self._event(
                    "order_accepted",
                    order_id=order.order_id,
                    signal_id=sig.signal_id,
                    side=order_side,
                    price=order_price,
                    qty=qty,
                )

            # --- Step 3: Daily loss limit check ---
            # Check before processing fills: if daily loss already breached, reject new orders
            current_date = bar_ts[:10]  # YYYY-MM-DD
            if self.daily_loss_tracker.is_daily_loss_breached(
                current_date, float(self.account.starting_equity), self.config.max_daily_loss_pct
            ):
                if current_date not in daily_loss_breaches:
                    daily_loss_breaches.append(current_date)
                self._event(
                    "daily_loss_breach",
                    reason="DAILY_LOSS_LIMIT_EXCEEDED",
                    detail={"date": current_date},
                )
                # Cancel all open orders
                for ord_ in self.order_manager.open_orders():
                    try:
                        self.order_manager.cancel_order(ord_.order_id, "DAILY_LOSS_LIMIT")
                        self._event(
                            "order_cancelled",
                            order_id=ord_.order_id,
                            reason="DAILY_LOSS_LIMIT",
                        )
                    except OrderCancelError:
                        pass

            # --- Step 4: Process bar fills (market at open, limit at price touch) ---
            fills = self.order_manager.process_bar(
                bar_ts,
                open_px,
                high_px,
                low_px,
                close_px,
            )
            for fill in fills:
                self._event(
                    "fill",
                    order_id=fill.order_id,
                    side=fill.side,
                    price=fill.price,
                    qty=fill.qty,
                    notional=fill.notional,
                    fee=fill.cost_total,
                    reason="FILL",
                    detail={"bar_ts": fill.bar_ts, "fill_id": fill.fill_id},
                )

            # --- Step 5: SL/TP monitoring on open position ---
            pos = self.account.position
            if pos is not None:
                pos_side = str(pos["side"])
                sl = pos.get("stop_loss")
                tp = pos.get("take_profit")
                sl_hit = tp_hit = False
                if pos_side == "LONG":
                    sl_hit = sl is not None and low_px <= float(sl)
                    tp_hit = tp is not None and high_px >= float(tp)
                else:
                    sl_hit = sl is not None and high_px >= float(sl)
                    tp_hit = tp is not None and low_px <= float(tp)

                if sl_hit or tp_hit:
                    exit_px = float(sl) if sl_hit else float(tp)
                    reason = "STOP_LOSS" if sl_hit else "TAKE_PROFIT"
                    try:
                        fill = flatten(self.account, exit_px, bar_ts)
                    except ValueError:
                        fill = None
                    if fill is not None:
                        gross = 0.0
                        if pos_side == "LONG":
                            gross = (exit_px - float(pos["entry_price"])) * float(pos["qty"])
                        else:
                            gross = (float(pos["entry_price"]) - exit_px) * float(pos["qty"])
                        costs = float(pos.get("entry_fee", 0.0)) + float(fill.get("cost_total", 0.0))
                        net = gross - costs
                        self.daily_loss_tracker.record_trade(
                            {"net_pnl": net}, bar_ts
                        )
                        self._event(
                            "sl_tp_exit",
                            side=pos_side,
                            price=exit_px,
                            qty=float(pos["qty"]),
                            notional=fill.get("notional", 0.0),
                            fee=fill.get("cost_total", 0.0),
                            pnl=net,
                            equity=equity(self.account, close_px),
                            reason=reason,
                        )

            # --- Step 6: Timeout check ---
            if pos is not None:
                held = int(pos.get("bars_held", 0)) + 1
                if held > int(self.account.max_holding_bars):
                    exit_px = open_px
                    try:
                        fill = flatten(self.account, exit_px, bar_ts)
                    except ValueError:
                        fill = None
                    if fill is not None:
                        gross = 0.0
                        if pos_side == "LONG":
                            gross = (exit_px - float(pos["entry_price"])) * float(pos["qty"])
                        else:
                            gross = (float(pos["entry_price"]) - exit_px) * float(pos["qty"])
                        costs = float(pos.get("entry_fee", 0.0)) + float(fill.get("cost_total", 0.0))
                        net = gross - costs
                        self.daily_loss_tracker.record_trade(
                            {"net_pnl": net}, bar_ts
                        )
                        self._event(
                            "timeout_exit",
                            side=pos_side,
                            price=exit_px,
                            qty=float(pos["qty"]),
                            notional=fill.get("notional", 0.0),
                            fee=fill.get("cost_total", 0.0),
                            pnl=net,
                            equity=equity(self.account, close_px),
                            reason="TIMEOUT",
                        )

            # --- Step 7: Funding accrual while position open ---
            pos = self.account.position
            if pos is not None:
                funding = self.cost.funding_for_bars(float(pos["notional"]), 1)
                self.account.cash -= funding
                pos["funding_accrued"] = float(pos.get("funding_accrued", 0.0)) + funding
                self._event(
                    "funding",
                    notional=float(pos["notional"]),
                    fee=funding,
                    reason="FUNDING_ACCRUAL",
                    detail={"bars": 1},
                )

            # --- Step 8: Mark-to-close equity ---
            marked = equity(self.account, close_px)
            self.account.equity_mark_ts = bar_ts
            self.account.equity_mark = marked
            self.account.equity_curve.append([bar_ts, marked])
            self._event(
                "mark_equity",
                equity=marked,
                reason="MARK_TO_CLOSE",
            )

        # --- Build result ---
        trades_closed = list(self.account.trades_closed)
        equity_curve = list(self.account.equity_curve)

        # Compute summary
        nets = [float(t.get("net_pnl", 0.0)) for t in trades_closed]
        gross = float(sum(t.get("gross_pnl", 0.0) for t in trades_closed))
        costs = float(sum(t.get("costs_total", 0.0) for t in trades_closed))
        net = float(sum(nets))
        n_trades = len(trades_closed)
        wins = sum(1 for v in nets if v > 0)
        win_rate = wins / n_trades if n_trades > 0 else 0.0

        # Max drawdown from equity curve
        start_eq = float(self.account.starting_equity)
        peak = start_eq
        max_dd = 0.0
        for _, eq in equity_curve:
            peak = max(peak, float(eq))
            if peak > 0:
                max_dd = max(max_dd, (peak - float(eq)) / peak * 100.0)

        # Config hash
        cfg_payload = self.config.model_dump(mode="json")
        config_hash = hashlib.sha256(
            json.dumps(cfg_payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]

        final_equity = float(equity_curve[-1][1]) if equity_curve else start_eq

        # Order summaries
        order_summaries = []
        for ord_ in self.order_manager.list_orders():
            order_summaries.append({
                "order_id": ord_.order_id,
                "client_order_id": ord_.client_order_id,
                "side": ord_.side,
                "order_type": ord_.order_type,
                "price": ord_.price,
                "qty": ord_.qty,
                "filled_qty": ord_.filled_qty,
                "status": ord_.status,
                "avg_fill_price": ord_.avg_fill_price,
                "submitted_at": ord_.submitted_at,
                "rejection_reason": ord_.rejection_reason,
                "cancel_reason": ord_.cancel_reason,
            })

        # Fill summaries
        fill_summaries = []
        for ord_ in self.order_manager.list_orders():
            for fill in ord_.fills:
                fill_summaries.append({
                    "fill_id": fill.fill_id,
                    "order_id": fill.order_id,
                    "side": fill.side,
                    "price": fill.price,
                    "qty": fill.qty,
                    "fee_bp": fill.fee_bp,
                    "spread_bp": fill.spread_bp,
                    "slippage_bp": fill.slippage_bp,
                    "notional": fill.notional,
                    "cost_total": fill.cost_total,
                    "ts": fill.ts,
                    "bar_ts": fill.bar_ts,
                })

        return SimulatorResult(
            config_hash=config_hash,
            n_bars=len(bars),
            n_signals=n_signals,
            n_orders=len(self.order_manager.list_orders()),
            n_fills=len(fill_summaries),
            n_trades=n_trades,
            starting_equity=start_eq,
            final_equity=final_equity,
            net_pnl=net,
            gross_pnl=gross,
            costs_total=costs,
            max_drawdown_pct=max_dd,
            win_rate=win_rate,
            trades=[dict(t) for t in trades_closed],
            equity_curve=[[ts, eq] for ts, eq in equity_curve],
            orders=order_summaries,
            fills=fill_summaries,
            events=self._events,
            rejected_signals=rejected_signals,
            daily_loss_breaches=daily_loss_breaches,
        )


__all__ = [
    "Simulator",
    "SimulatorConfig",
    "SimulatorEvent",
    "SimulatorResult",
]
