"""Unit tests for Phase 8 chronological splitting (offline, deterministic)."""

from __future__ import annotations

import pandas as pd
import pytest

from crypto_engine.training.splits import chronological_split


def _times(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")


def test_split_basic_proportions() -> None:
    split = chronological_split(_times(100), None, train_frac=0.6, valid_frac=0.2, embargo_bars=0)
    assert split.train_idx == list(range(60))
    assert split.valid_idx == list(range(60, 80))
    assert split.holdout_idx == list(range(80, 100))
    assert split.purged_rows == 0 and split.embargoed_rows == 0


def test_purge_drops_future_observable_rows() -> None:
    times = _times(100)
    # First 10 rows only observable after the cutoff -> purged from training.
    obs = pd.Series([times[70] if i < 10 else times[i] for i in range(100)])
    split = chronological_split(times, obs, train_frac=0.6, valid_frac=0.2, embargo_bars=0)
    assert split.train_idx == list(range(10, 60))
    assert split.purged_rows == 10
    # Every kept training row is observable at or before the cutoff.
    cutoff = times[60]
    for i in split.train_idx:
        assert obs[i] <= cutoff


def test_embargo_drops_tail_before_cutoff() -> None:
    obs = pd.Series(_times(100))
    split = chronological_split(_times(100), obs, train_frac=0.6, valid_frac=0.2, embargo_bars=5)
    assert split.train_idx == list(range(55))
    assert split.embargoed_rows == 5


def test_insufficient_data_names_shortfall() -> None:
    with pytest.raises(ValueError, match="training rows"):
        chronological_split(_times(12), pd.Series(_times(12)), train_frac=0.6, valid_frac=0.2)
    with pytest.raises(ValueError, match="holdout rows"):
        chronological_split(_times(40), pd.Series(_times(40)), train_frac=0.8, valid_frac=0.15)


def test_bad_fractions_rejected() -> None:
    with pytest.raises(ValueError, match="train_frac"):
        chronological_split(_times(50), None, train_frac=1.5, valid_frac=0.2)
    with pytest.raises(ValueError, match=r"train_frac \+ valid_frac"):
        chronological_split(_times(50), None, train_frac=0.6, valid_frac=0.5)
    with pytest.raises(ValueError, match="empty"):
        chronological_split(_times(0), None)


def test_deterministic_repeated_splits_identical() -> None:
    times, obs = _times(90), pd.Series(_times(90))
    first = chronological_split(times, obs, embargo_bars=3)
    second = chronological_split(times, obs, embargo_bars=3)
    assert first == second
