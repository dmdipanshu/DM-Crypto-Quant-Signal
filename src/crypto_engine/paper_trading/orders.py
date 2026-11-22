"""Advisory-only paper order lifecycle manager (Phase 10).

Tracks submitted LIMIT/MARKET orders through submission, acceptance,
partial/full fills, rejection, and cancellation. Maintains per-order audit
events and PnL / exposure / drawdown bookkeeping. Pure advisory: no live
order/trade API imports; only pydantic, stdlib, and the Phase 5/6 cost and
state modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.paper_trading.state import PaperAccount, equity

OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]
OrderStatus = Literal["SUBMITTED", "ACCEPTED", "REJECTED", "FILLED", "PARTIALLY_FILLED", "CANCELLED"]

class OrderRejectionError(ValueError):
    """Raised when an order submission or acceptance fails validation."""


class OrderCancelError(ValueError):
    """Raised when an order cancellation is invalid."""



_ORDER_ID_PATTERN = r"^[0-9a-f]{16}$"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class FillRecord(BaseModel):
    """A single execution fill for an order."""

    model_config = ConfigDict(extra="forbid")

    fill_id: str
    order_id: str
    side: OrderSide
    price: float = Field(gt=0)
    qty: float = Field(gt=0)
    fee_bp: float = Field(ge=0)
    spread_bp: float = Field(ge=0)
    slippage_bp: float = Field(ge=0)
    notional: float = Field(gt=0)
    cost_total: float = Field(ge=0)
    ts: str
    bar_ts: str


class Order(BaseModel):
    """A single paper order with lifecycle state and fill history."""

    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(pattern=_ORDER_ID_PATTERN)
    client_order_id: str
    symbol: str
    timeframe: str
    side: OrderSide
    order_type: OrderType
    price: float | None = Field(default=None, ge=0)
    qty: float = Field(gt=0)
    filled_qty: float = Field(default=0.0, ge=0)
    status: OrderStatus = "SUBMITTED"
    fills: list[FillRecord] = Field(default_factory=list)
    avg_fill_price: float | None = Field(default=None, ge=0)
    submitted_at: str
    accepted_at: str | None = None
    filled_at: str | None = None
    cancelled_at: str | None = None
    rejection_reason: str | None = None
    cancel_reason: str | None = None
    audit_events: list[dict] = Field(default_factory=list)

    @field_validator("price")
    @classmethod
    def _price_required_for_limit(cls, v: float | None, info) -> float | None:
        order_type = info.data.get("order_type")
        if order_type == "LIMIT" and v is None:
            raise ValueError("Order: price is required for LIMIT orders.")
        if order_type == "MARKET" and v is not None:
            raise ValueError("Order: price must be None for MARKET orders.")
        return v

    @model_validator(mode="after")
    def _filled_qty_not_exceed_qty(self) -> Order:
        if self.filled_qty > self.qty:
            raise ValueError("Order: filled_qty cannot exceed qty.")
        return self

    @property
    def remaining_qty(self) -> float:
        return self.qty - self.filled_qty

    @property
    def is_open(self) -> bool:
        return self.status in ("SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED")

    @property
    def is_terminal(self) -> bool:
        return self.status in ("FILLED", "REJECTED", "CANCELLED")

    def _audit(self, event: str, detail: dict | None = None) -> None:
        entry: dict = {"ts": _utc_now_iso(), "event": event}
        if detail:
            entry["detail"] = detail
        self.audit_events.append(entry)


class OrderManager:
    """Manages a set of paper orders with PnL, exposure, and drawdown tracking."""

    def __init__(
        self,
        account: PaperAccount,
        cost: ExecutionCostModel,
        max_slippage_bp: float | None = None,
    ) -> None:
        self.account = account
        self.cost = cost
        self.max_slippage_bp = max_slippage_bp
        self._orders: dict[str, Order] = {}
        self._counter = 0

        self._audit_events: list[dict] = []
    def _next_order_id(self) -> str:
        self._counter += 1
        return f"{self._counter:016x}"

    def _audit_log_entry(self, order_id: str, event: str, detail: dict | None = None) -> dict:
        entry = {"order_id": order_id, "ts": _utc_now_iso(), "event": event}
        if detail:
            entry["detail"] = detail
        return entry

    def submit_order(
        self,
        client_order_id: str,
        symbol: str,
        timeframe: str,
        side: OrderSide,
        order_type: OrderType,
        qty: float,
        price: float | None = None,
    ) -> Order:
        """Create and register a SUBMITTED order."""
        order_id = self._next_order_id()
        now = _utc_now_iso()
        order = Order(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            timeframe=timeframe,
            side=side,
            order_type=order_type,
            price=price,
            qty=qty,
            filled_qty=0.0,
            status="SUBMITTED",
            submitted_at=now,
        )
        order._audit("submitted", {"order_type": order_type, "qty": qty, "price": price})
        self._orders[order_id] = order
        self._audit_events.append(self._audit_log_entry(order_id, "submitted"))
        return order

    def accept_order(self, order_id: str) -> Order:
        """Transition a SUBMITTED order to ACCEPTED."""
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"OrderManager: unknown order_id {order_id!r}.")
        if order.status != "SUBMITTED":
            raise ValueError(
                f"OrderManager: order {order_id!r} is {order.status}, cannot accept."
            )
        order.status = "ACCEPTED"
        order.accepted_at = _utc_now_iso()
        order._audit("accepted")
        self._audit_events.append(self._audit_log_entry(order_id, "accepted"))
        return order

    def reject_order(self, order_id: str, reason: str) -> Order:
        """Transition a SUBMITTED/ACCEPTED order to REJECTED (terminal)."""
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"OrderManager: unknown order_id {order_id!r}.")
        if order.is_terminal:
            raise ValueError(f"OrderManager: order {order_id!r} already terminal ({order.status}).")
        order.status = "REJECTED"
        order.rejection_reason = reason
        order._audit("rejected", {"reason": reason})
        self._audit_events.append(self._audit_log_entry(order_id, "rejected", {"reason": reason}))
        return order

    def cancel_order(self, order_id: str, reason: str | None = None) -> Order:
        """Transition an open order to CANCELLED (terminal)."""
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"OrderManager: unknown order_id {order_id!r}.")
        if not order.is_open:
            raise ValueError(f"OrderManager: order {order_id!r} is {order.status}, cannot cancel.")
        order.status = "CANCELLED"
        order.cancel_reason = reason
        order.cancelled_at = _utc_now_iso()
        order._audit("cancelled", {"reason": reason})
        self._audit_events.append(
            self._audit_log_entry(order_id, "cancelled", {"reason": reason})
        )
        return order

    def process_bar(
        self, bar_ts: str, open_px: float, high_px: float, low_px: float, close_px: float
    ) -> list[FillRecord]:
        """Attempt to fill open/accepted orders against a bar; returns new fills."""
        new_fills: list[FillRecord] = []
        for order in list(self._orders.values()):
            if not order.is_open:
                continue
            if order.order_type == "MARKET":
                fill_px = self.cost.fill_price(order.side, open_px)
                fill_qty = order.remaining_qty
                if fill_qty <= 0:
                    continue
                self._apply_fill(order, fill_px, fill_qty, bar_ts)
                new_fills.append(order.fills[-1])
                order.status = "FILLED"
                order.filled_at = _utc_now_iso()
            else:  # LIMIT
                fill_px = order.price
                if fill_px is None:
                    continue
                if (order.side == "BUY" and low_px <= fill_px) or (order.side == "SELL" and high_px >= fill_px):
                    fill_qty = order.remaining_qty
                    self._apply_fill(order, fill_px, fill_qty, bar_ts)
                    new_fills.append(order.fills[-1])
                    order.status = "FILLED"
                    order.filled_at = _utc_now_iso()
        return new_fills

    def _apply_fill(self, order: Order, price: float, qty: float, bar_ts: str) -> None:
        notional = qty * price
        fee = self.cost.fill_fee(notional)
        fill_id = f"{order.order_id}-{len(order.fills) + 1:x}"
        fill = FillRecord(
            fill_id=fill_id,
            order_id=order.order_id,
            side=order.side,
            price=price,
            qty=qty,
            fee_bp=self.cost.fee_bp,
            spread_bp=self.cost.spread_bp,
            slippage_bp=self.cost.slippage_bp,
            notional=notional,
            cost_total=fee,
            ts=_utc_now_iso(),
            bar_ts=bar_ts,
        )
        order.filled_qty += qty
        total_notional = sum(f.notional for f in order.fills) + notional

        order.avg_fill_price = total_notional / (order.filled_qty) if order.filled_qty > 0 else None
        order.fills.append(fill)
        order._audit(
            "filled",
            {
                "fill_id": fill_id,
                "price": price,
                "qty": qty,
                "notional": notional,
                "fee": fee,
                "filled_qty": order.filled_qty,
            },
        )
        self._audit_events.append(
            self._audit_log_entry(order.order_id, "filled", {"fill_id": fill_id})
        )

    def unrealized_pnl(self, mark_price: float) -> float:
        """Unrealized PnL on open/partially-filled orders vs mark price."""
        total = 0.0
        for order in self._orders.values():
            if not order.is_open or order.filled_qty <= 0:
                continue
            avg = order.avg_fill_price or 0.0
            if order.side == "BUY":
                total += (mark_price - avg) * order.filled_qty
            else:
                total += (avg - mark_price) * order.filled_qty
        return total

    def realized_pnl(self) -> float:
        """Realized PnL from filled orders (notional delta net of fees)."""
        total = 0.0
        for order in self._orders.values():
            if order.status != "FILLED":
                continue
            for f in order.fills:
                total += f.notional - f.cost_total
        return total

    def total_pnl(self, mark_price: float) -> float:
        """Realized + unrealized PnL."""
        return self.realized_pnl() + self.unrealized_pnl(mark_price)

    def exposure_pct(self, mark_price: float) -> float:
        """Current notional exposure as pct of equity (from state.equity)."""
        eq = equity(self.account, mark_price)
        if eq <= 0:
            return 0.0
        notional = 0.0
        for order in self._orders.values():
            if order.is_open and order.filled_qty > 0:
                avg = order.avg_fill_price or 0.0
                notional += order.filled_qty * avg
        return min(notional / eq * 100.0, 100.0)

    def drawdown_pct(self, mark_price: float) -> float:
        """Max drawdown pct from equity curve high-water mark."""
        curve = self.account.equity_curve
        if not curve:
            return 0.0
        current = equity(self.account, mark_price)
        peak = current
        for _ts, val in curve:
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            if v > peak:
                peak = v
        if peak <= 0:
            return 0.0
        return max((peak - current) / peak * 100.0, 0.0)

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def list_orders(self) -> list[Order]:
        return list(self._orders.values())

    def open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.is_open]

    def audit_log(self) -> list[dict]:
        return list(self._audit_events)


__all__ = [
    "FillRecord",
    "Order",
    "OrderCancelError",
    "OrderManager",
    "OrderRejectionError",
    "OrderSide",
    "OrderStatus",
    "OrderType",
]




