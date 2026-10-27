"""Deterministic OHLCV fixtures for Track A tests (<= 300 rows, offline)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv_frame(n: int = 120, seed: int = 42) -> pd.DataFrame:
    """Build ``n`` deterministic 1h candles (UTC) with a fixed seed."""
    if n < 1 or n > 300:
        raise ValueError("fixture size must be within 1..300 rows")
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    rets = rng.normal(0.0005, 0.004, size=n)
    close = 40_000.0 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[40_000.0], close[:-1]])
    spread = np.abs(rng.normal(20.0, 8.0, size=n))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = 10.0 + np.abs(rng.normal(5.0, 2.0, size=n))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def make_trend_frame(n: int = 60, drift: float = 0.01) -> pd.DataFrame:
    """Monotonic-drift candles for barrier touch tests (deterministic)."""
    if n < 1 or n > 300:
        raise ValueError("fixture size must be within 1..300 rows")
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = 100.0 * np.exp(drift * np.arange(n))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    volume = np.full(n, 50.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


__all__ = ["make_ohlcv_frame", "make_trend_frame"]
