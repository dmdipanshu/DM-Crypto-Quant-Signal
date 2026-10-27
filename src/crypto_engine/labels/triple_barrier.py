"""Triple-barrier labels with causal volatility and cost-aware barriers.

Entry convention: the signal fires at candle ``t`` close; barriers are
evaluated on forward candles ``t+1 .. t+max_horizon`` using their observed
``high``/``low``. The label at ``t`` becomes observable only at
``observable_at[t]`` (the hitting/timeout candle). Rows without enough
history (``vol_window``) or without a full forward horizon are marked
``barrier_hit == "incomplete"`` and must be excluded from training via
:func:`valid_mask`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

BarrierHit = Literal["pt", "sl", "timeout", "ambiguous", "incomplete"]


class TripleBarrierConfig(BaseModel):
    """Validated triple-barrier parameters."""

    pt_mult: float = Field(default=2.0, gt=0)
    sl_mult: float = Field(default=1.0, gt=0)
    max_horizon_bars: int = Field(default=24, ge=1)
    vol_window: int = Field(default=20, ge=2)
    min_vol: float = Field(default=1e-8, ge=0)
    cost_bp: float = Field(default=0.0, ge=0)
    side: Literal["long", "short", "both"] = "both"
    ambiguous_policy: Literal["conservative", "long_first", "short_first"] = "conservative"


LABEL_COLUMNS = ("label", "barrier_hit", "tte_bars", "observable_at", "vol_at_entry")


def _require_ohlc(df: pd.DataFrame) -> None:
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Index must be a pandas DatetimeIndex")
    if df.index.tz is None:
        raise ValueError("Index must be timezone-aware (UTC)")


def label_triple_barrier(
    df: pd.DataFrame, config: TripleBarrierConfig | dict | None = None
) -> pd.DataFrame:
    """Label each candle with causal triple-barrier outcomes.

    Geometry (``v`` = causal realized log-vol at entry, ``c`` = cost
    fraction ``cost_bp / 1e4``):

    * long/both: ``pt = close * (1 + pt_mult * v) * (1 + c)``,
      ``sl = close * (1 - sl_mult * v)``; pt-first ``+1``, sl-first ``-1``.
    * short: ``pt = close * (1 - pt_mult * v) / (1 + c)``,
      ``sl = close * (1 + sl_mult * v)``; pt-first ``-1``, sl-first ``+1``.
    * Same-bar touch of both barriers resolves per ``ambiguous_policy``:
      ``conservative`` -> label ``0`` / ``"ambiguous"``; ``long_first`` ->
      upper-barrier outcome (``+1``); ``short_first`` -> lower outcome
      (``-1``). Timeout -> ``0`` / ``"timeout"`` with
      ``tte_bars == max_horizon_bars``.
    """
    _require_ohlc(df)
    cfg = (
        config
        if isinstance(config, TripleBarrierConfig)
        else (TripleBarrierConfig.model_validate(config or {}))
    )
    n = len(df)
    labels = np.zeros(n, dtype=np.int64)
    hits: list[str] = ["incomplete"] * n
    tte: list[int | None] = [None] * n
    vol_at = np.full(n, np.nan)
    cost = cfg.cost_bp / 1e4
    h = cfg.max_horizon_bars

    if n == 0:
        return _frame(df, labels, hits, tte, vol_at)

    close = df["close"].to_numpy(dtype=float)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        logret = np.log(close / np.roll(close, 1))
    logret[0] = np.nan
    # Causal rolling std (min_periods = vol_window), vectorized.
    vol = (
        pd.Series(logret, index=df.index)
        .rolling(cfg.vol_window, min_periods=cfg.vol_window)
        .std()
        .to_numpy()
    )

    for t in range(n):
        v = vol[t]
        if not np.isfinite(v) or v < cfg.min_vol:
            continue  # warm-up / degenerate vol -> incomplete
        if t + h > n - 1:
            continue  # fewer than max_horizon future bars -> incomplete
        vol_at[t] = v
        entry = close[t]
        if cfg.side == "short":
            pt = entry * (1.0 - cfg.pt_mult * v) / (1.0 + cost)
            sl = entry * (1.0 + cfg.sl_mult * v)
            pt_label, sl_label = -1, 1
        else:  # long / both share symmetric geometry
            pt = entry * (1.0 + cfg.pt_mult * v) * (1.0 + cost)
            sl = entry * (1.0 - cfg.sl_mult * v)
            pt_label, sl_label = 1, -1
        done = False
        for j in range(1, h + 1):
            if cfg.side == "short":
                # Profit is a downward move: pt sits below, sl above.
                hit_pt = low[t + j] <= pt
                hit_sl = high[t + j] >= sl
            else:
                hit_pt = high[t + j] >= pt
                hit_sl = low[t + j] <= sl
            if hit_pt and hit_sl:
                if cfg.ambiguous_policy == "conservative":
                    labels[t] = 0
                    hits[t] = "ambiguous"
                elif cfg.ambiguous_policy == "long_first":
                    labels[t] = 1
                    hits[t] = "pt" if cfg.side != "short" else "sl"
                else:  # short_first -> lower-barrier outcome (-1)
                    labels[t] = -1
                    hits[t] = "sl" if cfg.side != "short" else "pt"
                tte[t] = j
                done = True
                break
            if hit_pt:
                labels[t] = pt_label
                hits[t] = "pt"
                tte[t] = j
                done = True
                break
            if hit_sl:
                labels[t] = sl_label
                hits[t] = "sl"
                tte[t] = j
                done = True
                break
        if not done:
            labels[t] = 0
            hits[t] = "timeout"
            tte[t] = h
    return _frame(df, labels, hits, tte, vol_at)


def _frame(
    df: pd.DataFrame,
    labels: np.ndarray,
    hits: list[str],
    tte: list[int | None],
    vol_at: np.ndarray,
) -> pd.DataFrame:
    idx = df.index
    tte_s = pd.Series(tte, index=idx, dtype="Int64")
    obs = pd.Series(pd.NaT, index=idx, dtype="datetime64[ns, UTC]")
    mask = tte_s.notna()
    if mask.any():
        pos = np.where(mask.to_numpy())[0]
        obs.iloc[pos] = idx[pos + tte_s[mask].to_numpy(dtype=int)]
    return pd.DataFrame(
        {
            "label": pd.Series(labels, index=idx, dtype="int64"),
            "barrier_hit": pd.Series(hits, index=idx, dtype="string"),
            "tte_bars": tte_s,
            "observable_at": obs,
            "vol_at_entry": pd.Series(vol_at, index=idx, dtype="float64"),
        },
        index=idx,
    )


def valid_mask(labels_df: pd.DataFrame) -> pd.Series:
    """Boolean mask of rows usable for training (excludes ``incomplete``)."""
    if "barrier_hit" not in labels_df.columns:
        raise ValueError("labels frame needs a 'barrier_hit' column")
    return labels_df["barrier_hit"] != "incomplete"


def assert_no_leakage(features_df: pd.DataFrame, labels_df: pd.DataFrame) -> bool:
    """Check label observability against feature availability.

    Requires identical indexes; every trainable label must satisfy
    ``entry_time <= observable_at`` (outcome known strictly after entry)
    and carry finite ``vol_at_entry``. Returns ``True``; raises
    ``AssertionError`` otherwise.
    """
    if not features_df.index.equals(labels_df.index):
        raise AssertionError("features and labels indexes differ")
    for col in LABEL_COLUMNS:
        if col not in labels_df.columns:
            raise AssertionError(f"labels missing column {col!r}")
    usable = labels_df[valid_mask(labels_df)]
    entry = usable.index
    obs = pd.DatetimeIndex(usable["observable_at"])
    if obs.isna().any():
        raise AssertionError("trainable label with missing observable_at")
    if (obs < entry).any():
        raise AssertionError("observable_at precedes entry time")
    if not np.isfinite(usable["vol_at_entry"].to_numpy(dtype=float)).all():
        raise AssertionError("trainable label with non-finite vol_at_entry")
    return True


def to_label_frame(
    df: pd.DataFrame, config: TripleBarrierConfig | dict | None = None
) -> tuple[pd.DataFrame, TripleBarrierConfig]:
    """Return ``(labels_df, config)`` for snapshot persistence."""
    cfg = (
        config
        if isinstance(config, TripleBarrierConfig)
        else (TripleBarrierConfig.model_validate(config or {}))
    )
    return label_triple_barrier(df, cfg), cfg


def label_config_hash(config: TripleBarrierConfig) -> str:
    """Deterministic sha256 over the validated config JSON."""
    payload = config.model_dump_json().encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def save_labels(
    labels_df: pd.DataFrame, config: TripleBarrierConfig, directory: Path | str
) -> Path:
    """Persist ``labels.parquet`` + ``config.json`` atomically (tmp+rename)."""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_pq = out_dir / "labels.parquet.tmp"
    tmp_cfg = out_dir / "config.json.tmp"
    labels_df.to_parquet(tmp_pq, index=True)
    tmp_cfg.write_text(
        json.dumps(json.loads(config.model_dump_json()), indent=2),
        encoding="utf-8",
    )
    final_pq = out_dir / "labels.parquet"
    final_cfg = out_dir / "config.json"
    tmp_pq.replace(final_pq)
    tmp_cfg.replace(final_cfg)
    return out_dir


def load_labels(directory: Path | str) -> tuple[pd.DataFrame, TripleBarrierConfig]:
    """Load a frame previously written by :func:`save_labels`."""
    in_dir = Path(directory)
    labels_df = pd.read_parquet(in_dir / "labels.parquet")
    # Re-enforce the canonical schema (parquet may widen string/nullable
    # dtypes on round-trip).
    labels_df = labels_df.astype(
        {"label": "int64", "barrier_hit": "string", "tte_bars": "Int64", "vol_at_entry": "float64"}
    )
    labels_df["observable_at"] = pd.to_datetime(labels_df["observable_at"], utc=True)
    cfg = TripleBarrierConfig.model_validate_json(
        (in_dir / "config.json").read_text(encoding="utf-8")
    )
    return labels_df, cfg


__all__ = [
    "LABEL_COLUMNS",
    "TripleBarrierConfig",
    "assert_no_leakage",
    "label_config_hash",
    "label_triple_barrier",
    "load_labels",
    "save_labels",
    "to_label_frame",
    "valid_mask",
]
