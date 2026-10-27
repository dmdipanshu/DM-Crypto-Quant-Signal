"""Purged, embargoed walk-forward folds over pure integer index math.

Policy
------
* Chronological: fold ``k`` (``0``-based) validates on one contiguous block
  anchored at the series tail; the training block strictly precedes it.
* ``train_window is None`` selects an expanding window (all rows before the
  validation block); otherwise a rolling window of ``train_window`` rows.
* Purge: a training row ``i`` whose label-information interval ``[s_i, e_i]``
  overlaps the validation range ``[val_start, val_end)`` is dropped. Overlap
  is ``s_i < val_end and e_i >= val_start``; because training rows satisfy
  ``i < val_start`` this reduces to ``e_i >= val_start`` (the label outcome
  is observable only at/after the validation block starts).
* Embargo: after purging, training rows with ``idx >= val_start -
  embargo_bars`` are dropped (information-latency buffer). A row dropped by
  both rules is counted as purged (purge applied first; embargo sees the
  remainder), so exclusion counts are disjoint and auditable.
* Rows flagged invalid (``valid_idx``; e.g. triple-barrier ``incomplete``
  labels with no observable outcome) are excluded from both sides.

Determinism: pure index arithmetic, no RNG, no timestamps required. Two
calls with identical arguments return identical folds.

Limits: no shuffling (order is the point); folds are anchored at the tail,
so early history is under-represented when ``train_window`` is small.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from crypto_engine.models.base import InformativeError

#: Upper bound on fold counts (4 GB laptop: bounded sequential work).
MAX_FOLDS = 10


class InsufficientDataError(ValueError):
    """A fold cannot meet ``min_samples`` after purge/embargo exclusion."""


@dataclass(eq=False)
class Fold:
    """One walk-forward split with disjoint exclusion bookkeeping."""

    fold_id: int
    train_idx: np.ndarray = field(repr=False)
    val_idx: np.ndarray = field(repr=False)
    purged_idx: np.ndarray = field(repr=False)
    embargo_idx: np.ndarray = field(repr=False)
    val_start: int = 0
    val_end: int = 0
    n_train_candidate: int = 0
    n_val_candidate: int = 0
    n_invalid_train: int = 0
    n_invalid_val: int = 0

    @property
    def n_train(self) -> int:
        """Usable training rows after all exclusions."""
        return int(self.train_idx.shape[0])

    @property
    def n_val(self) -> int:
        """Usable validation rows after all exclusions."""
        return int(self.val_idx.shape[0])

    def exclusion_record(self) -> dict[str, int]:
        """Disjoint exclusion counts; sums reconcile with candidates.

        ``n_train_candidate == n_train + n_purged + n_embargo +
        n_invalid_train`` and ``n_val_candidate == n_val + n_invalid_val``.
        """
        return {
            "fold_id": int(self.fold_id),
            "val_start": int(self.val_start),
            "val_end": int(self.val_end),
            "n_train_candidate": int(self.n_train_candidate),
            "n_train": self.n_train,
            "n_purged": int(self.purged_idx.shape[0]),
            "n_embargo": int(self.embargo_idx.shape[0]),
            "n_invalid_train": int(self.n_invalid_train),
            "n_val_candidate": int(self.n_val_candidate),
            "n_val": self.n_val,
            "n_invalid_val": int(self.n_invalid_val),
        }


def label_intervals(
    observable_idx: Sequence[int | float | None] | np.ndarray | None,
    horizon: int,
) -> list[tuple[int, int] | None] | None:
    """Build per-row label-information intervals ``[i, observable_i]``.

    Args:
        observable_idx: Length-``n`` observable bar per row (the bar at
            which the label outcome is known). ``None``/``NaN`` means the
            observable bar is unknown.
        horizon: Conservative lookahead when the observable bar is unknown;
            the interval becomes ``[i, i + horizon]`` (assumes the label
            could depend on any bar up to ``horizon`` ahead).

    Returns:
        Length-``n`` list of ``(start, end)`` intervals, or ``None`` when
        ``observable_idx`` itself is ``None`` (no purge information). An
        entry is ``None`` when the observable bar precedes its own entry
        bar (``obs < i``: invalid lookahead) — callers must exclude such
        rows (``make_folds`` counts them as purged).
    """
    if observable_idx is None:
        return None
    horizon = int(horizon)
    if horizon < 0:
        raise InformativeError(f"folds: horizon must be >= 0 (got {horizon!r}).")
    obs = list(observable_idx)
    out: list[tuple[int, int] | None] = []
    for i, raw in enumerate(obs):
        if raw is None or bool(pd.isna(raw)):
            out.append((i, i + horizon))
            continue
        try:
            value = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise InformativeError(f"folds: observable_idx[{i}] is not numeric ({raw!r}).") from exc
        if bool(np.isnan(value)):
            out.append((i, i + horizon))
            continue
        end = int(value)
        if end < i:
            out.append(None)
        else:
            out.append((i, end))
    return out


def _as_valid_mask(valid_idx: np.ndarray | Sequence[int | bool] | None, n: int) -> np.ndarray:
    """Normalize ``valid_idx`` to a length-``n`` boolean mask."""
    if valid_idx is None:
        return np.ones(n, dtype=bool)
    arr = np.asarray(list(valid_idx) if not isinstance(valid_idx, np.ndarray) else valid_idx)
    if arr.dtype == bool and arr.shape == (n,):
        return arr.copy()
    if arr.dtype == bool:
        raise InformativeError(f"folds: boolean valid_idx has length {arr.shape[0]} != n={n}.")
    try:
        positions = arr.astype(int)
    except (TypeError, ValueError) as exc:
        raise InformativeError(
            f"folds: valid_idx must be a boolean mask or integer positions (got {arr!r})."
        ) from exc
    if positions.size and (bool((positions < 0).any()) or bool((positions >= n).any())):
        raise InformativeError(f"folds: valid_idx positions out of range for n={n}.")
    mask = np.zeros(n, dtype=bool)
    mask[positions] = True
    return mask


def make_folds(
    n: int,
    train_window: int | None,
    val_window: int,
    n_folds: int,
    embargo_bars: int = 0,
    purge_intervals: Sequence[tuple[int, int] | None] | None = None,
    min_samples: int = 1,
    valid_idx: np.ndarray | Sequence[int | bool] | None = None,
) -> list[Fold]:
    """Build ``n_folds`` purged/embargoed chronological folds.

    Fold ``k`` validates on ``[n - n_folds * val_window + k * val_window,
    ... + val_window)``; training candidates are the ``train_window`` rows
    before ``val_start`` (or all rows before it when ``train_window`` is
    ``None``).

    Raises:
        InformativeError: Invalid configuration (non-positive windows,
            ``n_folds > 10``, negative embargo, length-mismatched inputs).
        InsufficientDataError: Any fold's validation or post-exclusion
            training block is smaller than ``min_samples``.
    """
    n = int(n)
    if n <= 0:
        raise InformativeError(f"folds: n must be > 0 (got {n!r}).")
    if train_window is not None and int(train_window) < 1:
        raise InformativeError(f"folds: train_window must be >= 1 or None (got {train_window!r}).")
    val_window = int(val_window)
    if val_window < 1:
        raise InformativeError(f"folds: val_window must be >= 1 (got {val_window!r}).")
    n_folds = int(n_folds)
    if n_folds < 1:
        raise InformativeError(f"folds: n_folds must be >= 1 (got {n_folds!r}).")
    if n_folds > MAX_FOLDS:
        raise InformativeError(
            f"folds: n_folds={n_folds} exceeds cap {MAX_FOLDS} (4 GB laptop bound)."
        )
    embargo_bars = int(embargo_bars)
    if embargo_bars < 0:
        raise InformativeError(f"folds: embargo_bars must be >= 0 (got {embargo_bars!r}).")
    min_samples = int(min_samples)
    if min_samples < 1:
        raise InformativeError(f"folds: min_samples must be >= 1 (got {min_samples!r}).")
    if purge_intervals is not None and len(purge_intervals) != n:
        raise InformativeError(f"folds: purge_intervals length {len(purge_intervals)} != n={n}.")
    valid = _as_valid_mask(valid_idx, n)

    total_val = n_folds * val_window
    if total_val > n:
        raise InsufficientDataError(
            f"folds: need n >= n_folds * val_window = {total_val} to place "
            f"{n_folds} folds, got n={n}."
        )

    folds: list[Fold] = []
    for k in range(n_folds):
        val_start = n - total_val + k * val_window
        val_end = val_start + val_window
        train_lo = 0 if train_window is None else max(0, val_start - int(train_window))
        train_candidates = np.arange(train_lo, val_start, dtype=np.int64)
        val_candidates = np.arange(val_start, val_end, dtype=np.int64)

        valid_train = train_candidates[valid[train_candidates]]
        valid_val = val_candidates[valid[val_candidates]]
        n_invalid_train = int(train_candidates.shape[0] - valid_train.shape[0])
        n_invalid_val = int(val_candidates.shape[0] - valid_val.shape[0])

        purged = np.zeros(valid_train.shape[0], dtype=bool)
        if purge_intervals is not None:
            for pos, i in enumerate(valid_train.tolist()):
                interval = purge_intervals[int(i)]
                if interval is None:
                    purged[pos] = True  # unresolvable interval: exclude conservatively
                    continue
                s, e = int(interval[0]), int(interval[1])
                if s < val_end and e >= val_start:
                    purged[pos] = True
        purged_idx = valid_train[purged]
        kept = valid_train[~purged]

        if embargo_bars > 0:
            embargo_cut = val_start - embargo_bars
            embargo_mask = kept >= embargo_cut
        else:
            embargo_mask = np.zeros(kept.shape[0], dtype=bool)
        embargo_idx = kept[embargo_mask]
        train_idx = kept[~embargo_mask]

        if valid_val.shape[0] < min_samples:
            raise InsufficientDataError(
                f"folds: fold {k} has n_val={valid_val.shape[0]} < "
                f"min_samples={min_samples} (val [{val_start}, {val_end}) "
                f"after excluding {n_invalid_val} invalid rows)."
            )
        if train_idx.shape[0] < min_samples:
            raise InsufficientDataError(
                f"folds: fold {k} has n_train={train_idx.shape[0]} < "
                f"min_samples={min_samples} (candidate {valid_train.shape[0]}, "
                f"purged {purged_idx.shape[0]}, embargoed {embargo_idx.shape[0]}, "
                f"invalid {n_invalid_train})."
            )
        folds.append(
            Fold(
                fold_id=k,
                train_idx=train_idx.astype(np.int64),
                val_idx=valid_val.astype(np.int64),
                purged_idx=purged_idx.astype(np.int64),
                embargo_idx=embargo_idx.astype(np.int64),
                val_start=val_start,
                val_end=val_end,
                n_train_candidate=int(train_candidates.shape[0]),
                n_val_candidate=int(val_candidates.shape[0]),
                n_invalid_train=n_invalid_train,
                n_invalid_val=n_invalid_val,
            )
        )
    return folds


def exclusion_table(folds: Sequence[Fold]) -> pd.DataFrame:
    """One row per fold from :meth:`Fold.exclusion_record` (audit table)."""
    rows = [fold.exclusion_record() for fold in folds]
    cols = [
        "fold_id",
        "val_start",
        "val_end",
        "n_train_candidate",
        "n_train",
        "n_purged",
        "n_embargo",
        "n_invalid_train",
        "n_val_candidate",
        "n_val",
        "n_invalid_val",
    ]
    return pd.DataFrame(rows, columns=cols)


__all__ = [
    "Fold",
    "InsufficientDataError",
    "exclusion_table",
    "label_intervals",
    "make_folds",
]
