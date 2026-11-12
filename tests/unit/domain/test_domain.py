"""Domain layer unit tests.

Tests value objects and invariants in ``crypto_engine.domain``. All tests
are offline and use only the standard library.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from crypto_engine.domain.candle import Candle
from crypto_engine.domain.direction import Direction
from crypto_engine.domain.reason_codes import ReasonCode
from crypto_engine.domain.timeframe import Timeframe

# ---------------------------------------------------------------------------
# Timeframe
# ---------------------------------------------------------------------------


def test_timeframe_parse_valid() -> None:
    tf = Timeframe.parse("1h")
    assert tf.value == 1
    assert tf.unit == "h"
    assert str(tf) == "1h"


def test_timeframe_parse_various() -> None:
    assert Timeframe.parse("15m") == Timeframe(15, "m")
    assert Timeframe.parse("4h") == Timeframe(4, "h")
    assert Timeframe.parse("1d") == Timeframe(1, "d")
    assert Timeframe.parse("1w") == Timeframe(1, "w")


def test_timeframe_parse_invalid() -> None:
    for bad in ("1x", "abc", "", "0h", "-1h", "1:30", "hour"):
        with pytest.raises(ValueError):
            Timeframe.parse(bad)


def test_timeframe_value_validation() -> None:
    with pytest.raises(ValueError):
        Timeframe(0, "h")
    with pytest.raises(ValueError):
        Timeframe(-5, "m")


def test_timeframe_unit_validation() -> None:
    with pytest.raises(ValueError):
        Timeframe(1, "x")


def test_timeframe_to_minutes() -> None:
    assert Timeframe(1, "m").to_minutes() == 1
    assert Timeframe(1, "h").to_minutes() == 60
    assert Timeframe(4, "h").to_minutes() == 240
    assert Timeframe(1, "d").to_minutes() == 1440
    assert Timeframe(1, "w").to_minutes() == 10080


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


def test_direction_values() -> None:
    assert Direction.LONG == "LONG"
    assert Direction.SHORT == "SHORT"
    assert Direction.NEUTRAL == "NEUTRAL"
    assert Direction.NO_TRADE == "NO_TRADE"


def test_direction_is_str_enum() -> None:
    assert str(Direction.LONG) == "LONG"
    assert Direction("LONG") is Direction.LONG


# ---------------------------------------------------------------------------
# ReasonCode
# ---------------------------------------------------------------------------


def test_reason_code_closed_vocabulary() -> None:
    codes = ReasonCode.all_codes()
    assert "STALE_DATA" in codes
    assert "UNCALIBRATED" in codes
    assert "INSUFFICIENT_EDGE" in codes
    assert "EXPOSURE_UNKNOWN" in codes
    assert "CIRCUIT_BREAKER" in codes
    # Closed vocabulary: no arbitrary codes
    assert len(codes) == len(set(codes))


def test_reason_code_is_str_enum() -> None:
    assert str(ReasonCode.STALE_DATA) == "STALE_DATA"
    assert ReasonCode("UNCALIBRATED") is ReasonCode.UNCALIBRATED


# ---------------------------------------------------------------------------
# Candle
# ---------------------------------------------------------------------------


def _sample_candle() -> Candle:
    return Candle(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1h",
        open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        open=Decimal("60000.00"),
        high=Decimal("61000.00"),
        low=Decimal("59000.00"),
        close=Decimal("60500.00"),
        volume=Decimal("12.5"),
    )


def test_candle_creation() -> None:
    c = _sample_candle()
    assert c.exchange == "binance"
    assert c.symbol == "BTC/USDT"
    assert c.timeframe == "1h"
    assert c.open == Decimal("60000.00")
    assert c.volume == Decimal("12.5")


def test_candle_identity() -> None:
    c = _sample_candle()
    ident = c.identity
    assert ident == ("binance", "BTC/USDT", "1h", datetime(2026, 10, 1, 0, 0, tzinfo=UTC))


def test_candle_close_time() -> None:
    c = _sample_candle()
    ct = c.close_time
    assert ct == datetime(2026, 10, 1, 1, 0, tzinfo=UTC)


def test_candle_validation_ohlc() -> None:
    # high < open -> invalid
    with pytest.raises(ValueError):
        Candle(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
            open=Decimal("60000"),
            high=Decimal("59000"),
            low=Decimal("58000"),
            close=Decimal("59500"),
            volume=Decimal("1.0"),
        )


def test_candle_validation_zero_price() -> None:
    with pytest.raises(ValueError):
        Candle(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
            open=Decimal("0"),
            high=Decimal("100"),
            low=Decimal("1"),
            close=Decimal("50"),
            volume=Decimal("1.0"),
        )


def test_candle_naive_timestamp_rejected() -> None:
    with pytest.raises(ValueError):
        Candle(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            open_time=datetime(2026, 10, 1, 0, 0),  # naive, no tzinfo
            open=Decimal("60000"),
            high=Decimal("61000"),
            low=Decimal("59000"),
            close=Decimal("60500"),
            volume=Decimal("1.0"),
        )


def test_candle_negative_volume_rejected() -> None:
    with pytest.raises(ValueError):
        Candle(
            exchange="binance",
            symbol="BTC/USDT",
            timeframe="1h",
            open_time=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
            open=Decimal("60000"),
            high=Decimal("61000"),
            low=Decimal("59000"),
            close=Decimal("60500"),
            volume=Decimal("-1.0"),
        )


# ---------------------------------------------------------------------------
# Domain purity: stdlib only
# ---------------------------------------------------------------------------


def test_domain_imports_stdlib_only() -> None:
    """domain/ imports only the Python standard library (DR-1)."""

    domain_dir = Path(__file__).resolve().parents[2] / "src" / "crypto_engine" / "domain"
    forbidden = {
        "pandas",
        "numpy",
        "scipy",
        "pydantic",
        "ccxt",
        "sqlite3",
        "fastapi",
        "typer",
        "rich",
        "tenacity",
        "pyarrow",
        "sklearn",
        "lightgbm",
        "optuna",
    }
    for path in sorted(domain_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    assert top not in forbidden, f"{path.name} imports forbidden {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                assert top not in forbidden, f"{path.name} imports forbidden {node.module}"
