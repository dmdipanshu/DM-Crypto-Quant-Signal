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
