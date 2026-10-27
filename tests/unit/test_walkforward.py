"""Walk-forward fold tests: chronology, purge, embargo, determinism (Track B).

Offline, deterministic, pure index math (no frames needed).
"""

from __future__ import annotations

import numpy as np
import pytest

from crypto_engine.models.base import InformativeError
from crypto_engine.validation.folds import (
    InsufficientDataError,
    exclusion_table,
    label_intervals,
    make_folds,
)

N = 400
TRAIN_WINDOW = 200
VAL_WINDOW = 50
N_FOLDS = 3


def test_fold_chronology_and_tail_anchor() -> None:
    """Val blocks are contiguous tail blocks; train strictly precedes val."""
    folds = make_folds(N, TRAIN_WINDOW, VAL_WINDOW, N_FOLDS)
    assert len(folds) == N_FOLDS
    # Tail-anchored: last val block ends at n.
    assert folds[-1].val_end == N
    for k, fold in enumerate(folds):
        assert fold.val_start == N - N_FOLDS * VAL_WINDOW + k * VAL_WINDOW
        assert fold.val_end == fold.val_start + VAL_WINDOW
        assert fold.val_idx.tolist() == list(range(fold.val_start, fold.val_end))
        # Strict precedence, disjoint, no gaps inside train block.
        assert fold.train_idx.size > 0
        assert int(fold.train_idx.max()) < fold.val_start
        assert len(set(fold.train_idx.tolist()) & set(fold.val_idx.tolist())) == 0
        # Rolling window respected for fold 0 ([50, 250)); later folds capped.
        assert int(fold.train_idx.min()) == max(0, fold.val_start - TRAIN_WINDOW)


def test_expanding_window_when_train_window_none() -> None:
    """train_window=None trains on all rows before the validation block."""
    folds = make_folds(N, None, VAL_WINDOW, N_FOLDS)
    assert folds[0].train_idx.tolist() == list(range(folds[0].val_start))


def test_purge_removes_overlaps() -> None:
    """Train rows whose label interval reaches the val block are purged."""
    folds_plain = make_folds(N, TRAIN_WINDOW, VAL_WINDOW, N_FOLDS)
    fold0 = folds_plain[0]  # val = [250, 300)
    obs = np.arange(N)  # observable at own bar -> no overlap
    intervals = label_intervals(obs, horizon=24)
    assert intervals is not None
    # Force the last 10 train rows to resolve inside the val block.
    intervals = list(intervals)
    for i in range(240, 250):
        intervals[i] = (i, 260)
    folds = make_folds(N, TRAIN_WINDOW, VAL_WINDOW, N_FOLDS, purge_intervals=intervals)
    purged = folds[0].purged_idx.tolist()
    assert list(range(240, 250)) == purged
    assert len(set(folds[0].train_idx.tolist()) & set(range(240, 250))) == 0
    # Untouched folds keep full training sets.
    assert folds[0].n_train == fold0.n_train - 10


def test_purge_boundary_exact() -> None:
    """Interval ending exactly at val_start purges; ending one before does not."""
    val_start = N - VAL_WINDOW  # single fold anchors the final block
    intervals = [(i, i) for i in range(N)]
    intervals[val_start - 1] = (val_start - 1, val_start)  # touches -> purge
    intervals[val_start - 2] = (val_start - 2, val_start - 1)  # clear -> keep
    folds = make_folds(N, TRAIN_WINDOW, VAL_WINDOW, 1, purge_intervals=intervals)
    assert (val_start - 1) in folds[0].purged_idx.tolist()
    assert (val_start - 2) in folds[0].train_idx.tolist()


def test_embargo_boundary_exact() -> None:
    """Embargo drops exactly the ``embargo_bars`` rows before val_start."""
    folds = make_folds(N, TRAIN_WINDOW, VAL_WINDOW, 1, embargo_bars=3)
    fold = folds[0]
    assert fold.val_start == N - VAL_WINDOW
    assert fold.embargo_idx.tolist() == [fold.val_start - 3, fold.val_start - 2, fold.val_start - 1]
    assert int(fold.train_idx.max()) == fold.val_start - 4


def test_deterministic_two_calls_equal() -> None:
    """Identical arguments yield identical folds."""
    kwargs = dict(
        train_window=TRAIN_WINDOW,
        val_window=VAL_WINDOW,
        n_folds=N_FOLDS,
        embargo_bars=5,
        min_samples=10,
    )
    first = make_folds(N, **kwargs)
    second = make_folds(N, **kwargs)
    for a, b in zip(first, second, strict=True):
        assert np.array_equal(a.train_idx, b.train_idx)
        assert np.array_equal(a.val_idx, b.val_idx)
        assert np.array_equal(a.purged_idx, b.purged_idx)
        assert np.array_equal(a.embargo_idx, b.embargo_idx)


def test_incomplete_labels_excluded() -> None:
    """Rows flagged invalid never appear on either side of any fold."""
    valid = np.ones(N, dtype=bool)
    valid[::7] = False  # every 7th row is an "incomplete" label
    folds = make_folds(N, TRAIN_WINDOW, VAL_WINDOW, N_FOLDS, valid_idx=valid)
    bad = set(np.where(~valid)[0].tolist())
    for fold in folds:
        assert not (bad & set(fold.train_idx.tolist()))
        assert not (bad & set(fold.val_idx.tolist()))
        assert fold.n_invalid_train + fold.n_invalid_val > 0


def test_insufficient_val_raises() -> None:
    """Val block below min_samples raises InsufficientDataError."""
    with pytest.raises(InsufficientDataError):
        make_folds(60, 40, 20, 3, min_samples=21)  # val blocks have 20 rows


def test_insufficient_train_after_embargo_raises() -> None:
    """Embargo eating the whole training block raises."""
    with pytest.raises(InsufficientDataError):
        make_folds(120, 10, 20, 1, embargo_bars=10, min_samples=5)


def test_too_many_rows_requested_raises() -> None:
    """n smaller than n_folds * val_window raises before any fold is built."""
    with pytest.raises(InsufficientDataError):
        make_folds(40, 20, 20, 3)


def test_invalid_config_rejected() -> None:
    """Bad windows/counts raise InformativeError, not silent misbehavior."""
    with pytest.raises(InformativeError):
        make_folds(N, TRAIN_WINDOW, 0, N_FOLDS)
    with pytest.raises(InformativeError):
        make_folds(N, TRAIN_WINDOW, VAL_WINDOW, 11)
    with pytest.raises(InformativeError):
        make_folds(N, TRAIN_WINDOW, VAL_WINDOW, N_FOLDS, embargo_bars=-1)
    with pytest.raises(InformativeError):
        make_folds(N, TRAIN_WINDOW, VAL_WINDOW, N_FOLDS, purge_intervals=[(0, 1)])


def test_exclusion_records_reconcile() -> None:
    """Disjoint counts sum back to candidates; table has one row per fold."""
    valid = np.ones(N, dtype=bool)
    valid[::11] = False
    intervals = label_intervals(np.arange(N), horizon=24)
    assert intervals is not None
    intervals = list(intervals)
    for i in range(245, 250):
        intervals[i] = (i, 280)
    folds = make_folds(
        N,
        TRAIN_WINDOW,
        VAL_WINDOW,
        N_FOLDS,
        embargo_bars=2,
        purge_intervals=intervals,
        valid_idx=valid,
    )
    for fold in folds:
        rec = fold.exclusion_record()
        assert rec["n_train_candidate"] == (
            rec["n_train"] + rec["n_purged"] + rec["n_embargo"] + rec["n_invalid_train"]
        )
        assert rec["n_val_candidate"] == rec["n_val"] + rec["n_invalid_val"]
        assert len(set(fold.train_idx.tolist()) & set(fold.purged_idx.tolist())) == 0
        assert len(set(fold.train_idx.tolist()) & set(fold.embargo_idx.tolist())) == 0
    table = exclusion_table(folds)
    assert len(table) == N_FOLDS
    assert table["n_train"].tolist() == [f.n_train for f in folds]


def test_label_intervals_conservative_and_invalid() -> None:
    """Unknown observables use [i, i+horizon]; obs < i yields None."""
    out = label_intervals([5, None, float("nan"), 1], horizon=24)
    assert out == [(0, 5), (1, 25), (2, 26), None]
    assert label_intervals(None, horizon=24) is None
