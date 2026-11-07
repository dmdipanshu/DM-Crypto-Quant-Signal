"""Chronological train/validation/holdout splitting with purge and embargo.

The training split must never contain a label whose outcome becomes
observable after the training cutoff. Purging is exact here (not heuristic):
a training row is kept only if its ``observable_at`` is at or before the
cutoff timestamp. An additional embargo drops the last ``embargo_bars`` rows
before the cutoff to guard against boundary effects.

Split order is always chronological: train (oldest) -> validation ->
holdout (newest). No shuffling, no stratification, no peeking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ChronoSplit:
    """Positional index arrays plus audit metadata for one chronological split."""

    train_idx: list[int]
    valid_idx: list[int]
    holdout_idx: list[int]
    train_end_ts: Any = None
    valid_end_ts: Any = None
    purged_rows: int = 0
    embargoed_rows: int = 0
    notes: tuple[str, ...] = ()


def chronological_split(
    times: pd.DatetimeIndex,
    observable_at: pd.Series | None,
    train_frac: float = 0.6,
    valid_frac: float = 0.2,
    embargo_bars: int = 0,
    min_train_rows: int = 10,
    min_eval_rows: int = 5,
) -> ChronoSplit:
    """Split ``range(len(times))`` chronologically with purge + embargo.

    Args:
        times: Monotonic UTC timestamps, one per row (already aligned).
        observable_at: Per-row label-observability timestamps (same order);
            ``None``/``NaT`` entries mean "never observable" (incomplete rows
            must already be excluded by the caller via ``valid_mask``).
        train_frac: Share of rows for training (0 < f < 1).
        valid_frac: Share of rows for validation (0 <= f < 1); the remainder
            is the final holdout.
        embargo_bars: Rows immediately before the train cutoff to drop.
        min_train_rows: Minimum post-purge/embargo training rows.
        min_eval_rows: Minimum rows for each of validation and holdout.

    Raises:
        ValueError: On empty input, bad fractions, non-monotonic times, or
            any split below its row floor (message names the shortfall).
    """
    n = len(times)
    if n == 0:
        raise ValueError("model-train: cannot split an empty frame.")
    if not (0.0 < train_frac < 1.0):
        raise ValueError(f"model-train: train_frac must be in (0, 1), got {train_frac!r}.")
    if not (0.0 <= valid_frac < 1.0) or train_frac + valid_frac >= 1.0:
        raise ValueError(
            f"model-train: need train_frac + valid_frac < 1, got {train_frac!r} + {valid_frac!r}."
        )
    if int(embargo_bars) < 0:
        raise ValueError(f"model-train: embargo_bars must be >= 0, got {embargo_bars!r}.")
    times = pd.DatetimeIndex(times)
    if times.tz is None:
        raise ValueError("model-train: timestamps must be timezone-aware (UTC).")
    if not times.is_monotonic_increasing:
        raise ValueError("model-train: timestamps must be monotonically increasing.")

    cut1 = int(n * train_frac)
    cut2 = int(n * (train_frac + valid_frac))
    if cut1 < 1 or cut2 <= cut1 or n - cut2 < 1:
        raise ValueError(
            f"model-train: fractions leave an empty split for n={n} "
            f"(train_frac={train_frac}, valid_frac={valid_frac})."
        )
    cutoff_ts = times[cut1]

    purged = 0
    train_idx: list[int] = []
    if observable_at is not None:
        obs = list(observable_at)
        if len(obs) != n:
            raise ValueError(f"model-train: observable_at length {len(obs)} != row count {n}.")
        for i in range(cut1):
            ts = obs[i]
            if ts is None or (isinstance(ts, float) and ts != ts):
                purged += 1
                continue
            try:
                obs_ts = pd.Timestamp(ts)
                if obs_ts.tz is None:
                    obs_ts = obs_ts.tz_localize("UTC")
            except (TypeError, ValueError):
                purged += 1
                continue
            if obs_ts <= cutoff_ts:
                train_idx.append(i)
            else:
                purged += 1
    else:
        train_idx = list(range(cut1))

    embargoed = 0
    if embargo_bars > 0 and train_idx:
        drop = min(int(embargo_bars), len(train_idx))
        embargoed = drop
        train_idx = train_idx[: len(train_idx) - drop] if drop else train_idx

    valid_idx = list(range(cut1, cut2))
    holdout_idx = list(range(cut2, n))

    problems: list[str] = []
    if len(train_idx) < min_train_rows:
        problems.append(
            f"training rows {len(train_idx)} < minimum {min_train_rows} "
            f"(purged={purged}, embargoed={embargoed})"
        )
    if len(valid_idx) < min_eval_rows:
        problems.append(f"validation rows {len(valid_idx)} < minimum {min_eval_rows}")
    if len(holdout_idx) < min_eval_rows:
        problems.append(f"holdout rows {len(holdout_idx)} < minimum {min_eval_rows}")
    if problems:
        raise ValueError(
            "model-train: insufficient data for chronological splits: " + "; ".join(problems)
        )

    notes: list[str] = []
    if purged:
        notes.append(f"purged {purged} training rows with observable_at after cutoff")
    if embargoed:
        notes.append(f"embargo dropped {embargoed} rows before cutoff")
    return ChronoSplit(
        train_idx=train_idx,
        valid_idx=valid_idx,
        holdout_idx=holdout_idx,
        train_end_ts=cutoff_ts,
        valid_end_ts=times[cut2],
        purged_rows=purged,
        embargoed_rows=embargoed,
        notes=tuple(notes),
    )


__all__ = ["ChronoSplit", "chronological_split"]
