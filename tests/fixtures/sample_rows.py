"""Sample ccxt rows fixture (10 1h BTC/USDT candles)."""

from __future__ import annotations

from typing import Any

from tests.conftest import sample_rows

SAMPLE_ROWS: list[list[Any]] = sample_rows()

__all__ = ["SAMPLE_ROWS"]
