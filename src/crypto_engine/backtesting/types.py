"""Backtest ledger types (Phase 6 Track A).

Event-time only: all timestamps are UTC ISO strings. A ``Fill`` prices one
leg; a ``Trade`` pairs entry/exit fills with a deterministic id
``symbol|timeframe|entry_ts|side|counter``; an open ``Position`` holds SL/TP
levels from the originating ``SignalRecord``.

Bar expectations: ``validate_bars`` requires a DataFrame with a UTC
``DatetimeIndex`` plus finite ``open/high/low/close`` (``volume`` optional),
strictly increasing index, no NaN OHLC. Violations raise with the column
named. Single symbol/timeframe, <= ``max_bars`` rows (4GB cap, enforced in
the engine).
"""

from __future__ import annotations

import math
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

ExitReason = Literal[
    "TAKE_PROFIT", "STOP_LOSS", "TIMEOUT", "SIGNAL_FLIP", "NEUTRAL_CLOSE", "END_OF_DATA"
]

REQUIRED_OHLC = ("open", "high", "low", "close")


def validate_bars(bars: pd.DataFrame, max_bars: int = 5000) -> pd.DataFrame:
    """Validate a bar frame; returns a sorted copy on success.

    Raises:
        ValueError: empty frame, tz-naive/non-monotonic index, missing or
            NaN/non-finite/non-positive OHLC column, or len > ``max_bars``.
    """
    if not isinstance(bars, pd.DataFrame):
        raise TypeError(f"bars must be a DataFrame, got {type(bars).__name__}.")
    if bars.empty:
        raise ValueError("bars must be non-empty.")
    if len(bars) > max_bars:
        raise ValueError(f"bars len {len(bars)} exceeds max_bars={max_bars} (4GB cap).")
    idx = bars.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise ValueError("bars index must be a DatetimeIndex (UTC bar open times).")
    tz = idx.tz
    if tz is None:
        raise ValueError("bars index must be timezone-aware (UTC), got tz-naive.")
    try:
        utc_idx = idx.tz_convert("UTC")
    except Exception as exc:
        raise ValueError(f"bars index must be convertible to UTC: {exc}") from exc
    if not bool(utc_idx.is_monotonic_increasing):
        raise ValueError("bars index must be strictly increasing (monotonic).")
    if bool(utc_idx.duplicated().any()):
        raise ValueError("bars index has duplicate timestamps.")
    missing = [c for c in REQUIRED_OHLC if c not in bars.columns]
    if missing:
        raise ValueError(f"bars missing required columns: {missing}.")
    out = bars.copy()
    out.index = utc_idx
    out = out.sort_index()
    for col in REQUIRED_OHLC:
        series = out[col]
        if bool(series.isna().any()):
            raise ValueError(f"bars column {col!r} contains NaN (no silent fills).")
        vals = series.to_numpy(dtype=float)
        import numpy as _np

        if bool(_np.isnan(vals).any()) or bool(_np.isinf(vals).any()):
            raise ValueError(f"bars column {col!r} must be finite (got NaN/inf).")
        if bool((vals <= 0).any()):
            raise ValueError(f"bars column {col!r} must be > 0 (got non-positive).")
    bad_range = out[out["high"] < out["low"]]
    if not bad_range.empty:
        raise ValueError(f"bars have high < low at {bad_range.index[0].isoformat()}.")
    for col in ("open", "close"):
        bad = out[(out[col] > out["high"]) | (out[col] < out["low"])]
        if not bad.empty:
            raise ValueError(
                f"bars column {col!r} outside [low, high] at {bad.index[0].isoformat()}."
            )
    return out


def _require_positive_finite(value: float, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}.") from exc
    if not math.isfinite(v):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    if v <= 0:
        raise ValueError(f"{name} must be > 0, got {v!r}.")
    return v


class Fill(BaseModel):
    """One executed leg (price already cost-adjusted)."""

    model_config = ConfigDict(extra="forbid")

    ts: str
    side: Literal["BUY", "SELL"]
    price: float = Field(gt=0)
    qty: float = Field(gt=0)
    fee_bp: float = Field(ge=0)
    spread_bp: float = Field(ge=0)
    slippage_bp: float = Field(ge=0)
    notional: float = Field(gt=0)
    cost_total: float = Field(ge=0)

    @field_validator("price", "qty", "notional", mode="before")
    @classmethod
    def _positive_finite(cls, v: object) -> object:
        return _require_positive_finite(float(v), "fill leg")  # type: ignore[arg-type]

    @field_validator("fee_bp", "spread_bp", "slippage_bp", mode="before")
    @classmethod
    def _bp(cls, v: object) -> object:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bp leg must be numeric, got {v!r}.") from exc
        if not math.isfinite(f):
            raise ValueError(f"bp leg must be finite, got {v!r}.")
        if f < 0:
            raise ValueError(f"bp leg must be >= 0, got {v!r}.")
        if f >= 10000.0:
            raise ValueError(f"bp leg {f} >= 10000; likely decimal-mixup (pass bp).")
        return f

    @field_validator("ts")
    @classmethod
    def _ts(cls, v: str) -> str:
        if not isinstance(v, str) or not v:
            raise ValueError("Fill.ts must be a non-empty UTC ISO string.")
        return v


class Position(BaseModel):
    """Open position state (single position at a time)."""

    model_config = ConfigDict(extra="forbid")

    side: Literal["LONG", "SHORT"]
    entry_ts: str
    entry_price: float = Field(gt=0)
    qty: float = Field(gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None
    bars_held: int = Field(default=0, ge=0)

    @field_validator("stop_loss", "take_profit", mode="before")
    @classmethod
    def _opt_level(cls, v: object) -> object:
        if v is None:
            return None
        return _require_positive_finite(float(v), "level")  # type: ignore[arg-type]


class Trade(BaseModel):
    """Closed (or open with exit None) round trip with deterministic id."""

    model_config = ConfigDict(extra="forbid")

    trade_id: str
    symbol: str
    timeframe: str
    side: Literal["LONG", "SHORT"]
    entry: Fill
    exit: Fill | None = None
    exit_reason: ExitReason | None = None
    gross_pnl: float = 0.0
    costs_total: float = Field(default=0.0, ge=0)
    net_pnl: float = 0.0
    bars_held: int = Field(default=0, ge=0)


__all__ = ["ExitReason", "Fill", "Position", "Trade", "validate_bars"]
