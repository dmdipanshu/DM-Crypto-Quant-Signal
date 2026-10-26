"""Offline validation tests: normalization, duplicates, gaps, rejection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from crypto_engine.data.errors import InvalidCandleError
from crypto_engine.data.validation import (
    detect_duplicates,
    find_missing_intervals,
    normalize_candles,
    to_frame,
)


def test_utc_normalization(raw_rows: list[list[Any]]) -> None:
    """Millisecond epochs become UTC-aware datetimes, sorted ascending."""
    rows = list(reversed(raw_rows))  # ensure sorting is applied
    candles = normalize_candles(rows)
    assert len(candles) == len(raw_rows)
    for candle in candles:
        assert candle.timestamp.tzinfo is not None
        assert candle.timestamp.utcoffset() is not None
    stamps = [c.timestamp for c in candles]
    assert stamps == sorted(stamps)
    expected = datetime.fromtimestamp(raw_rows[0][0] / 1000.0, tz=UTC)
    assert candles[0].timestamp == expected


def test_dict_rows_accepted() -> None:
    """Dict-style rows are coerced identically to list rows."""
    candles = normalize_candles(
        [
            {
                "timestamp": 1_700_000_000_000,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 3.0,
            }
        ]
    )
    assert len(candles) == 1
    assert candles[0].timestamp.tzinfo is not None


def test_duplicate_detection(sample_candles: list) -> None:
    """Duplicated timestamps are reported."""
    doubled = [*sample_candles, sample_candles[0]]
    dupes = detect_duplicates(doubled)
    assert dupes == [sample_candles[0].timestamp]
    assert detect_duplicates(sample_candles) == []


def test_missing_intervals_1h_gap() -> None:
    """A skipped 1h candle produces exactly one missing window."""
    base = 1_700_000_000_000
    rows = [
        [base, 1.0, 2.0, 0.5, 1.5, 1.0],
        [base + 3_600_000, 1.0, 2.0, 0.5, 1.5, 1.0],
        [base + 3 * 3_600_000, 1.0, 2.0, 0.5, 1.5, 1.0],  # skip one hour
    ]
    candles = normalize_candles(rows)
    gaps = find_missing_intervals(candles, "1h")
    assert len(gaps) == 1
    start, end = gaps[0]
    assert (end - start).total_seconds() == 3600  # one missing hourly candle
    assert end == candles[2].timestamp
    assert start == candles[1].timestamp + timedelta(hours=1)


def test_missing_intervals_none_when_continuous(raw_rows: list[list[Any]]) -> None:
    """Continuous hourly series has no gaps."""
    assert find_missing_intervals(normalize_candles(raw_rows), "1h") == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.__setitem__(2, 0.1),  # high < low
        lambda r: r.__setitem__(1, -5.0),  # negative open
        lambda r: r.__setitem__(5, -1.0),  # negative volume
        lambda r: r.__setitem__(2, float("nan")),  # NaN high
        lambda r: r.__setitem__(1, 999.0),  # open outside [low, high]
    ],
    ids=["high<low", "negative-open", "negative-volume", "nan-high", "open-out-of-range"],
)
def test_invalid_ohlcv_rejected(raw_rows: list[list[Any]], mutate) -> None:  # type: ignore[no-untyped-def]
    """Invalid OHLCV rows raise InvalidCandleError (no silent acceptance)."""
    rows = [list(r) for r in raw_rows]
    mutate(rows[3])
    with pytest.raises(InvalidCandleError):
        normalize_candles(rows)


def test_to_frame_utc_index(sample_candles: list) -> None:
    """to_frame yields a UTC-indexed DataFrame with OHLCV columns."""
    frame = to_frame(sample_candles)
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert str(frame.index.tz) == "UTC"
    assert len(frame) == len(sample_candles)
    assert to_frame([]).empty
