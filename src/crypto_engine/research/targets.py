"""Q2.2 — explicit, execution-aligned target and horizon definitions.

The Q2.1 audit found a methodology defect: the training target (triple
barrier with ``side="both"``, entry at the decision-bar *close*) and the
simulated strategy (fixed 24-bar next-open-to-open exit) described two
different economic outcomes, so a model was never validated against the
thing it was trained to predict.

This module defines one target that the Q3 executor reproduces exactly:

* **Decision bar ``t``** — features are known at the close of bar ``t``.
* **Entry** — filled at the *open* of bar ``t+1`` (no look-ahead).
* **Barriers** — computed from the fill price and the causal realized
  volatility observed at bar ``t``:
  ``upper = entry * (1 + pt_mult * v)``, ``lower = entry * (1 - sl_mult * v)``.
* **Holding window** — bars ``t+1 .. t + max_horizon_bars`` are inspected
  with their observed ``high``/``low``.
* **Label** — the direction implied by the *first* barrier reached:
  ``+1`` upper first, ``-1`` lower first, ``0`` timeout or ambiguous.

Class semantics (explicit):

======  ==========================================================
``+1``  Upward barrier reached first. A LONG position profits.
``-1``  Downward barrier reached first. A SHORT position profits.
``0``   Neutral: no barrier within the horizon, or both barriers
        touched in the same candle (direction genuinely unknown).
======  ==========================================================

The executor in :mod:`crypto_engine.research.execution` reuses the *same*
``upper``/``lower`` levels, so a ``+1`` prediction takes profit exactly
when the target said the upper barrier came first, and a ``-1``
prediction takes profit exactly when the lower barrier came first.

The historical production label generator
(:func:`crypto_engine.labels.triple_barrier.label_triple_barrier`) is
left untouched so previously produced research artifacts stay
reproducible.
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

#: Canonical label classes for the directional barrier target.
TARGET_CLASSES: tuple[int, ...] = (-1, 0, 1)

#: Outcome vocabulary emitted in the ``barrier_hit`` column.
BARRIER_HITS: tuple[str, ...] = ("upper", "lower", "timeout", "ambiguous", "incomplete")

#: Columns produced by :func:`label_execution_aligned`.
TARGET_COLUMNS: tuple[str, ...] = (
    "label",
    "barrier_hit",
    "tte_bars",
    "observable_at",
    "vol_at_entry",
    "entry_price",
    "upper_barrier",
    "lower_barrier",
)


class TargetDefinition(BaseModel):
    """Validated, reproducible description of the learning target.

    Every field changes the meaning of the labels, so the definition is
    hashed into every artifact to keep old and new results separable.
    """

    name: str = "directional_barrier_next_open_v1"
    entry_convention: Literal["next_bar_open"] = "next_bar_open"
    pt_mult: float = Field(default=2.0, gt=0)
    sl_mult: float = Field(default=1.0, gt=0)
    max_horizon_bars: int = Field(default=24, ge=1)
    vol_window: int = Field(default=20, ge=2)
    min_vol: float = Field(default=1e-8, ge=0)
    ambiguous_policy: Literal["conservative"] = "conservative"

    def definition_hash(self) -> str:
        """Deterministic sha256 over the validated definition."""
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()

    def describe(self) -> dict[str, Any]:
        """JSON-serialisable description including the definition hash."""
        return {
            **self.model_dump(),
            "definition_hash": self.definition_hash(),
            "class_semantics": {
                "1": "upper barrier reached first; LONG profits",
                "0": "neutral (timeout or ambiguous); no directional edge",
                "-1": "lower barrier reached first; SHORT profits",
            },
            "entry_convention_detail": (
                "decision at close of bar t; fill at open of bar t+1; "
                "barriers inspected on bars t+1..t+max_horizon_bars"
            ),
        }


def _require_ohlc(df: pd.DataFrame) -> None:
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Index must be a pandas DatetimeIndex")
    if df.index.tz is None:
        raise ValueError("Index must be timezone-aware (UTC)")


def causal_volatility(close: pd.Series, vol_window: int) -> np.ndarray:
    """Rolling std of close-to-close log returns, shifted so bar ``t`` uses
    only returns observed at or before ``t``."""
    values = close.to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        logret = np.log(values / np.roll(values, 1))
    logret[0] = np.nan
    return (
        pd.Series(logret, index=close.index)
        .rolling(int(vol_window), min_periods=int(vol_window))
        .std()
        .to_numpy(dtype=float)
    )


def label_execution_aligned(
    df: pd.DataFrame, definition: TargetDefinition | None = None
) -> pd.DataFrame:
    """Label each decision bar with the directional barrier outcome.

    The returned frame is indexed exactly like ``df`` and carries
    :data:`TARGET_COLUMNS`. Rows without enough forward bars, or without a
    usable causal volatility estimate, are marked
    ``barrier_hit == "incomplete"`` and must be excluded with
    :func:`crypto_engine.labels.triple_barrier.valid_mask`.

    The same-bar collision of both barriers resolves *conservatively* to
    ``label = 0`` (``barrier_hit == "ambiguous"``): the direction is
    genuinely unknown, so the target makes no directional claim.
    """
    _require_ohlc(df)
    cfg = definition or TargetDefinition()
    n = len(df)
    horizon = int(cfg.max_horizon_bars)

    labels = np.zeros(n, dtype=np.int64)
    hits: list[str] = ["incomplete"] * n
    tte: list[int | None] = [None] * n
    vol_at = np.full(n, np.nan)
    entry_price = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    if n:
        opens = df["open"].to_numpy(dtype=float)
        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)
        vol = causal_volatility(df["close"], cfg.vol_window)

        for t in range(n):
            v = vol[t]
            if not np.isfinite(v) or v < cfg.min_vol:
                continue  # warm-up / degenerate volatility
            entry_idx = t + 1
            last_idx = t + horizon
            if entry_idx > n - 1 or last_idx > n - 1:
                continue  # not enough forward bars
            entry = opens[entry_idx]
            if not np.isfinite(entry) or entry <= 0:
                continue
            vol_at[t] = v
            entry_price[t] = entry
            up_level = entry * (1.0 + cfg.pt_mult * v)
            dn_level = entry * (1.0 - cfg.sl_mult * v)
            if dn_level <= 0:
                continue
            upper[t] = up_level
            lower[t] = dn_level

            for j in range(entry_idx, last_idx + 1):
                hit_upper = highs[j] >= up_level
                hit_lower = lows[j] <= dn_level
                if hit_upper and hit_lower:
                    labels[t] = 0
                    hits[t] = "ambiguous"
                    tte[t] = j - t
                    break
                if hit_upper:
                    labels[t] = 1
                    hits[t] = "upper"
                    tte[t] = j - t
                    break
                if hit_lower:
                    labels[t] = -1
                    hits[t] = "lower"
                    tte[t] = j - t
                    break
            else:
                labels[t] = 0
                hits[t] = "timeout"
                tte[t] = horizon

    idx = df.index
    tte_series = pd.Series(tte, index=idx, dtype="Int64")
    observable = pd.Series(pd.NaT, index=idx, dtype="datetime64[ns, UTC]")
    mask = tte_series.notna()
    if mask.any():
        positions = np.where(mask.to_numpy())[0]
        offsets = tte_series[mask].to_numpy(dtype=int)
        observable.iloc[positions] = idx[positions + offsets]

    return pd.DataFrame(
        {
            "label": pd.Series(labels, index=idx, dtype="int64"),
            "barrier_hit": pd.Series(hits, index=idx, dtype="string"),
            "tte_bars": tte_series,
            "observable_at": observable,
            "vol_at_entry": pd.Series(vol_at, index=idx, dtype="float64"),
            "entry_price": pd.Series(entry_price, index=idx, dtype="float64"),
            "upper_barrier": pd.Series(upper, index=idx, dtype="float64"),
            "lower_barrier": pd.Series(lower, index=idx, dtype="float64"),
        },
        index=idx,
    )


def label_diagnostics(labels_df: pd.DataFrame) -> dict[str, Any]:
    """Class distribution and label-quality diagnostics for a target frame."""
    if "barrier_hit" not in labels_df.columns:
        raise ValueError("labels frame needs a 'barrier_hit' column")
    complete = labels_df[labels_df["barrier_hit"] != "incomplete"]
    total = len(complete)
    counts = complete["label"].value_counts().reindex(list(TARGET_CLASSES), fill_value=0)
    hits = complete["barrier_hit"].value_counts()
    tte = complete["tte_bars"].dropna().astype(float)
    return {
        "total_rows": len(labels_df),
        "complete_rows": total,
        "incomplete_rows": int(len(labels_df) - total),
        "class_counts": {str(int(k)): int(v) for k, v in counts.items()},
        "class_fractions": {
            str(int(k)): (float(v / total) if total else None) for k, v in counts.items()
        },
        "majority_class": str(int(counts.idxmax())) if total and counts.max() else None,
        "majority_fraction": (float(counts.max() / total) if total else None),
        "neutral_fraction": (float(counts.get(0, 0) / total) if total else None),
        "class_count": int((counts > 0).sum()),
        "barrier_hit_counts": {str(k): int(v) for k, v in hits.items()},
        "tte_bars": {
            "mean": (float(tte.mean()) if len(tte) else None),
            "max": (int(tte.max()) if len(tte) else None),
            "p50": (float(tte.median()) if len(tte) else None),
            "p95": (float(tte.quantile(0.95)) if len(tte) else None),
        },
    }


def target_metadata(
    definition: TargetDefinition, labels_df: pd.DataFrame, dataset_sha256: str
) -> dict[str, Any]:
    """Reproducible metadata block for a target definition and its labels."""
    return {
        "definition": definition.describe(),
        "dataset_sha256": dataset_sha256,
        "diagnostics": label_diagnostics(labels_df),
        "columns": list(TARGET_COLUMNS),
    }


__all__ = [
    "BARRIER_HITS",
    "TARGET_CLASSES",
    "TARGET_COLUMNS",
    "TargetDefinition",
    "causal_volatility",
    "label_diagnostics",
    "label_execution_aligned",
    "target_metadata",
]
