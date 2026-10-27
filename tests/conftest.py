"""Shared fixtures for offline tests (no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from crypto_engine.data.types import Candle
from crypto_engine.data.validation import normalize_candles


def sample_rows(
    start_ms: int = 1_700_000_000_000, n: int = 10, step_ms: int = 3_600_000
) -> list[list[Any]]:
    """Build ``n`` deterministic 1h ccxt-style rows for BTC/USDT."""
    rows: list[list[Any]] = []
    price = 40_000.0
    for i in range(n):
        ms = start_ms + i * step_ms
        o = price + i * 10.0
        h = o + 50.0
        low = o - 40.0
        c = o + 5.0
        v = 10.0 + i
        rows.append([ms, o, h, low, c, v])
    return rows


@pytest.fixture
def raw_rows() -> list[list[Any]]:
    """Ten valid 1h candles."""
    return sample_rows()


@pytest.fixture
def sample_candles(raw_rows: list[list[Any]]) -> list[Candle]:
    """Validated sample candles."""
    return normalize_candles(raw_rows)


@pytest.fixture
def tmp_storage(tmp_path: Path) -> Path:
    """Isolated storage root."""
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    return storage


@pytest.fixture
def repo_root() -> Path:
    """Discover the repo root (dir containing pyproject.toml)."""
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise AssertionError("repo root not found")


@pytest.fixture
def default_config_path(repo_root: Path) -> Path:
    """Path to config/default.toml."""
    return repo_root / "config" / "default.toml"


def _dt(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# --- Track A additions (append-only): shared OHLCV frame fixtures ---------
# NOTE: appended 2026-10-27 by Track A; existing fixtures above untouched.


@pytest.fixture
def ohlcv_frame_120():
    """Deterministic 120-row UTC OHLCV frame (Track A shared fixture)."""
    from tests.fixtures.features_fixture import make_ohlcv_frame

    return make_ohlcv_frame(n=120, seed=42)


@pytest.fixture
def ohlcv_frame_300():
    """Deterministic 300-row UTC OHLCV frame (max Track A fixture size)."""
    from tests.fixtures.features_fixture import make_ohlcv_frame

    return make_ohlcv_frame(n=300, seed=42)


# --- Track B additions (append-only): supervised small-frame fixtures ----
# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


@pytest.fixture
def supervised_frame_200():
    """Deterministic 200-row (features, labels) pair for Track B model tests."""
    import numpy as np

    from crypto_engine.features.engineering import compute_features
    from tests.fixtures.features_fixture import make_ohlcv_frame

    df = make_ohlcv_frame(n=200, seed=7)
    feats = compute_features(df).dropna()
    cols = ["ret_1", "trend_ratio_20_50", "rsi_14", "realized_vol_20"]
    X = feats[cols]
    y = np.where(X["trend_ratio_20_50"].to_numpy() > 0, 1, -1).astype(np.int64)
    return X, y


@pytest.fixture
def registry_meta():
    """Minimal provenance meta for Track B registry tests."""
    return {
        "dataset_snapshot_id": "fixture-snap",
        "dataset_checksum": "fixture1234",
        "label_config": {"pt_mult": 2.0, "sl_mult": 1.0},
        "label_hash": "fixturelbl",
        "train_start": "2024-01-01T00:00:00+00:00",
        "train_end": "2024-01-05T00:00:00+00:00",
    }
