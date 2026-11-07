"""Oscillating-snapshot fixtures for Phase 8 model-training tests (offline).

Sine-wave prices produce mixed triple-barrier outcomes (+1/-1/0) so the
logistic baseline sees >= 2 classes. All deterministic, <= 200 rows.
"""

from __future__ import annotations

import math
from pathlib import Path


def make_oscillating_snapshot_dir(
    storage_root: Path | str,
    n: int = 150,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    seed: int = 0,
    start_ms: int = 1_700_000_000_000,
) -> Path:
    """Persist an oscillating OHLC snapshot; return the snapshot dir."""
    from crypto_engine.data.validation import normalize_candles
    from crypto_engine.datasets.snapshots import save_snapshot

    base = 100.0 + seed * 5.0
    rows: list[list] = []
    for i in range(n):
        wave = 8.0 * math.sin(2.0 * math.pi * i / 24.0) + 0.4 * math.sin(2.0 * math.pi * i / 7.0)
        c = base + wave + i * 0.02
        o = c - 0.3
        rows.append([start_ms + i * 3_600_000, o, c + 0.6, c - 0.6, c, 10.0 + (i % 5)])
    candles = normalize_candles(rows)
    return save_snapshot(
        candles,
        {"requested_start": None, "requested_end": None, "warnings": [], "missing_count": 0},
        Path(storage_root),
        exchange="binance",
        symbol=symbol,
        timeframe=timeframe,
    )


def make_monotone_snapshot_dir(
    storage_root: Path | str,
    n: int = 60,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
) -> Path:
    """Persist an accelerating uptrend snapshot (single-class labels expected).

    Quadratic drift keeps realized volatility positive (finite features)
    while the price never falls back to a stop, so surviving labels are
    overwhelmingly one class.
    """
    from crypto_engine.data.validation import normalize_candles
    from crypto_engine.datasets.snapshots import save_snapshot

    rows: list[list] = []
    for i in range(n):
        o = 100.0 + 0.05 * i * i + i * 0.1
        rows.append(
            [1_700_000_000_000 + i * 3_600_000, o, o + 0.5, o - 0.5, o + 0.2, 10.0 + (i % 5)]
        )
    candles = normalize_candles(rows)
    return save_snapshot(
        candles,
        {"requested_start": None, "requested_end": None, "warnings": [], "missing_count": 0},
        Path(storage_root),
        exchange="binance",
        symbol=symbol,
        timeframe=timeframe,
    )


__all__ = ["make_monotone_snapshot_dir", "make_oscillating_snapshot_dir"]
