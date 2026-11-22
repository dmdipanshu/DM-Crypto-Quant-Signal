"""Q7 Phase 3 risk extensions for the paper trading engine.

Deterministic advisory-only risk checks: daily loss tracking and limit
enforcement, abnormal slippage detection, and price validation. Pure
stdlib datetime for date parsing, pydantic for models, no network,
no randomness.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AbnormalSlippageCheck",
    "DailyLossLimitCheck",
    "DailyLossRecord",
    "DailyLossTracker",
    "InvalidPriceCheck",
]


class DailyLossRecord(BaseModel):
    """Cumulative realized P&L for one UTC calendar day."""

    model_config = ConfigDict(extra="forbid")

    date: str = Field(min_length=1)
    net_pnl: float = Field(default=0.0)
    trade_count: int = Field(default=0, ge=0)


def utc_date_from_iso(ts_iso: str) -> str:
    text = str(ts_iso).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).date().isoformat()


def _canonical_date(date_str: str) -> str:
    return date.fromisoformat(str(date_str)).isoformat()


class DailyLossTracker:
    """Tracks realized P&L per UTC calendar day."""

    def __init__(self) -> None:
        self._days: dict[str, DailyLossRecord] = {}

    def record_trade(self, trade_dict: dict[str, Any], ts_iso: str) -> None:
        date_str = utc_date_from_iso(ts_iso)
        pnl = self._net_pnl(trade_dict)
        record = self._days.get(date_str)
        if record is None:
            record = DailyLossRecord(date=date_str)
            self._days[date_str] = record
        record.net_pnl = float(record.net_pnl) + float(pnl)
        record.trade_count = int(record.trade_count) + 1

    @staticmethod
    def _net_pnl(trade_dict: Any) -> float:
        if not isinstance(trade_dict, dict):
            return 0.0
        for key in ("net_pnl", "pnl", "realized_pnl"):
            if key in trade_dict:
                try:
                    return float(trade_dict[key])
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def daily_loss_amount(self, date_str: str) -> float:
        record = self._days.get(_canonical_date(date_str))
        if record is None:
            return 0.0
        return float(record.net_pnl)

    def daily_loss_pct(self, date_str: str, starting_equity: float) -> float:
        amount = self.daily_loss_amount(date_str)
        equity = float(starting_equity)
        if not math.isfinite(equity) or equity <= 0:
            return 0.0
        loss = -amount
        if loss <= 0:
            return 0.0
        return loss / equity * 100.0

    def is_daily_loss_breached(
        self, date_str: str, starting_equity: float, max_daily_loss_pct: float
    ) -> bool:
        pct = self.daily_loss_pct(date_str, starting_equity)
        limit = float(max_daily_loss_pct)
        if not math.isfinite(limit) or limit < 0:
            return False
        return pct > limit


class DailyLossLimitCheck:
    """Fail-closed daily loss limit check for order submission."""

    @staticmethod
    def check_daily_loss(
        order_manager: Any, bar_ts: str, max_daily_loss_pct: float
    ) -> tuple[bool, str | None]:
        tracker = getattr(order_manager, "daily_loss_tracker", None)
        if tracker is None:
            return (True, None)
        date_str = utc_date_from_iso(bar_ts)
        starting_equity = float(getattr(order_manager.account, "starting_equity", 0.0))
        breached = tracker.is_daily_loss_breached(
            date_str, starting_equity, float(max_daily_loss_pct)
        )
        if breached:
            return (False, "DAILY_LOSS_LIMIT_EXCEEDED")
        return (True, None)


class AbnormalSlippageCheck:
    """Checks fill slippage against a threshold expressed in basis points."""

    @staticmethod
    def check_slippage(
        fill_price: float, reference_price: float, max_slippage_bp: float
    ) -> tuple[bool, str | None]:
        ok_ref, _ = InvalidPriceCheck.check_price(reference_price)
        if not ok_ref:
            return (False, "ABNORMAL_SLIPPAGE")
        try:
            fill = float(fill_price)
        except (TypeError, ValueError):
            return (False, "ABNORMAL_SLIPPAGE")
        ref = float(reference_price)
        if not math.isfinite(fill):
            return (False, "ABNORMAL_SLIPPAGE")
        slippage_bp = abs(fill - ref) / ref * 10000.0
        if not math.isfinite(slippage_bp):
            return (False, "ABNORMAL_SLIPPAGE")
        limit = float(max_slippage_bp)
        if not math.isfinite(limit) or limit < 0:
            return (False, "ABNORMAL_SLIPPAGE")
        if slippage_bp > limit:
            return (False, "ABNORMAL_SLIPPAGE")
        return (True, None)


class InvalidPriceCheck:
    """Validates a price is finite and positive."""

    @staticmethod
    def check_price(price: float) -> tuple[bool, str | None]:
        try:
            value = float(price)
        except (TypeError, ValueError):
            return (False, "INVALID_PRICE")
        if not math.isfinite(value) or value <= 0:
            return (False, "INVALID_PRICE")
        return (True, None)
