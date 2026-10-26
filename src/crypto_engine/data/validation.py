"""Pure offline validation helpers for OHLCV data (no network)."""

from __future__ import annotations

import itertools
import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from crypto_engine.data.errors import InvalidCandleError
from crypto_engine.data.types import Candle

_EPS_REL = 1e-8

_TIMEFRAME_UNITS: dict[str, int] = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def timeframe_to_timedelta(timeframe: str) -> timedelta:
    """Parse ccxt-style timeframes (e.g. ``15m``, ``1h``, ``4h``, ``1d``, ``1w``)."""
    tf = timeframe.strip().lower()
    if len(tf) < 2:
        raise ValueError(f"Invalid timeframe: {timeframe!r}")
    unit = tf[-1]
    if unit not in _TIMEFRAME_UNITS:
        raise ValueError(f"Unsupported timeframe unit in {timeframe!r} (use m/h/d/w)")
    try:
        amount = int(tf[:-1])
    except ValueError as exc:
        raise ValueError(f"Invalid timeframe amount in {timeframe!r}") from exc
    if amount <= 0:
        raise ValueError(f"Invalid timeframe amount in {timeframe!r}")
    return timedelta(milliseconds=_TIMEFRAME_UNITS[unit] * amount)


def timeframe_to_ms(timeframe: str) -> int:
    """Return the interval length in milliseconds."""
    return int(timeframe_to_timedelta(timeframe).total_seconds() * 1000)


def _coerce_timestamp(value: Any, index: int) -> datetime:
    """Coerce ms-epoch / datetime / ISO string to an aware UTC datetime."""
    if isinstance(value, datetime):
        ts = value
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)
    if isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidCandleError(f"row {index}: invalid timestamp {value!r}") from exc
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)
    try:
        ms = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidCandleError(f"row {index}: invalid timestamp {value!r}") from exc
    if not math.isfinite(ms):
        raise InvalidCandleError(f"row {index}: non-finite timestamp {value!r}")
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def _get_field(row: dict[str, Any], index: int, *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    raise InvalidCandleError(f"row {index}: missing field (expected one of {names})")


def normalize_candles(raw: list[list[Any] | dict[str, Any]]) -> list[Candle]:
    """Normalize ccxt-style rows (``[ms, o, h, l, c, v]``) or dicts to sorted candles.

    Raises:
        InvalidCandleError: Listing the row index and reason for the first bad row.
    """
    candles: list[Candle] = []
    for i, row in enumerate(raw):
        if isinstance(row, dict):
            ts_raw = row.get("timestamp", row.get("ms", row.get("time", row.get("t"))))
            o = _get_field(row, i, "open", "o")
            h = _get_field(row, i, "high", "h")
            low = _get_field(row, i, "low", "l")
            c = _get_field(row, i, "close", "c")
            v = _get_field(row, i, "volume", "v", "vol", "quote_volume", "base_volume")
            # If dict used alternate base/quote volume, still numeric; handled below.
            ts = _coerce_timestamp(ts_raw, i)
        elif isinstance(row, (list, tuple)):
            if len(row) < 6:
                raise InvalidCandleError(
                    f"row {i}: expected 6 fields [ms,o,h,l,c,v], got {len(row)}"
                )
            ts = _coerce_timestamp(row[0], i)
            o, h, low, c, v = row[1], row[2], row[3], row[4], row[5]
        else:
            raise InvalidCandleError(f"row {i}: unsupported row type {type(row).__name__}")

        try:
            of, hf, lf, cf, vf = float(o), float(h), float(low), float(c), float(v)
        except (TypeError, ValueError) as exc:
            raise InvalidCandleError(f"row {i}: non-numeric OHLCV") from exc

        for label, val in (("open", of), ("high", hf), ("low", lf), ("close", cf)):
            if not math.isfinite(val):
                raise InvalidCandleError(f"row {i}: non-finite {label}={val!r}")
            if val <= 0:
                raise InvalidCandleError(f"row {i}: non-positive {label}={val!r}")
        if not math.isfinite(vf):
            raise InvalidCandleError(f"row {i}: non-finite volume={vf!r}")
        if vf < 0:
            raise InvalidCandleError(f"row {i}: negative volume={vf!r}")
        if hf < lf:
            raise InvalidCandleError(f"row {i}: high ({hf}) < low ({lf})")
        eps = _EPS_REL * max(1.0, abs(hf), abs(lf))
        if of < lf - eps or of > hf + eps:
            raise InvalidCandleError(f"row {i}: open ({of}) outside [low, high] ({lf}, {hf})")
        if cf < lf - eps or cf > hf + eps:
            raise InvalidCandleError(f"row {i}: close ({cf}) outside [low, high] ({lf}, {hf})")

        candles.append(Candle(timestamp=ts, open=of, high=hf, low=lf, close=cf, volume=vf))

    candles.sort(key=lambda c: c.timestamp)
    return candles


def detect_duplicates(candles: list[Candle]) -> list[datetime]:
    """Return sorted duplicate timestamps (each repeated timestamp once)."""
    seen: set[datetime] = set()
    dupes: set[datetime] = set()
    for candle in candles:
        if candle.timestamp in seen:
            dupes.add(candle.timestamp)
        seen.add(candle.timestamp)
    return sorted(dupes)


def find_missing_intervals(
    candles: list[Candle], timeframe: str
) -> list[tuple[datetime, datetime]]:
    """Find gaps larger than one interval; each gap is ``(expected, actual_next)``.

    The missing window starts at ``prev + interval`` and ends at the next
    observed timestamp. An empty or single-candle input yields no gaps.
    """
    interval = timeframe_to_timedelta(timeframe)
    unique = sorted({c.timestamp for c in candles})
    gaps: list[tuple[datetime, datetime]] = []
    for prev, nxt in itertools.pairwise(unique):
        if nxt - prev > interval:
            gaps.append((prev + interval, nxt))
    return gaps


def to_frame(candles: list[Candle]) -> pd.DataFrame:
    """Convert candles to a pandas DataFrame indexed by UTC timestamp."""
    if not candles:
        frame = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        frame.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
        return frame
    data = {
        "timestamp": [c.timestamp for c in candles],
        "open": [c.open for c in candles],
        "high": [c.high for c in candles],
        "low": [c.low for c in candles],
        "close": [c.close for c in candles],
        "volume": [c.volume for c in candles],
    }
    frame = pd.DataFrame(data).set_index("timestamp")
    frame.index = pd.DatetimeIndex(frame.index, tz="UTC")
    frame = frame.sort_index()
    return frame[["open", "high", "low", "close", "volume"]]


__all__ = [
    "detect_duplicates",
    "find_missing_intervals",
    "normalize_candles",
    "timeframe_to_ms",
    "timeframe_to_timedelta",
    "to_frame",
]
