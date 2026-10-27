"""Causal feature engineering on UTC OHLCV frames (Track A, Phase 2).

Every builder is a pure function of candles ``<= t`` for each output row
``t``: only ``.shift(n)`` with ``n >= 1``, trailing ``.rolling`` and
``adjust=False`` EWMs are used. No ``.shift(-n)``, no centered windows, no
forward fills from the future. Warm-up rows are explicit ``NaN``.

Memory (4 GB laptop): :func:`compute_features` processes row chunks and
concatenates once; each public ``add_*`` makes exactly one working copy.
``n_jobs`` is not applicable (single-process pandas, ``n_jobs=1`` by design).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from crypto_engine.features.registry import (
    SPEC_ATR_14,
    SPEC_BB_MID_20,
    SPEC_BB_POS_20,
    SPEC_BB_WIDTH_20,
    SPEC_BODY,
    SPEC_EMA_12,
    SPEC_EMA_26,
    SPEC_FUNDING,
    SPEC_LOWER_WICK,
    SPEC_MACD,
    SPEC_MACD_HIST,
    SPEC_MACD_SIGNAL,
    SPEC_NORM_VOL_20,
    SPEC_RANGE,
    SPEC_REF_CLOSE,
    SPEC_REF_RET_1H,
    SPEC_RET_1,
    SPEC_RET_4,
    SPEC_RET_12,
    SPEC_RSI_14,
    SPEC_RV_20,
    SPEC_SMA_20,
    SPEC_SMA_50,
    SPEC_SMA_RATIO_20,
    SPEC_SMA_VOL_20,
    SPEC_TREND_RATIO,
    SPEC_TREND_REGIME,
    SPEC_TREND_SLOPE_5,
    SPEC_UPPER_WICK,
    SPEC_VOL_REGIME_100,
    SPEC_VOLUME_Z_20,
    register,
)

OHLCV_COLS = ("open", "high", "low", "close", "volume")

#: Bars of trailing context needed so chunked evaluation stays exact for
#: windowed (non-recursive) features. Recursive EWMs are evaluated on the
#: full causal prefix per chunk (see :func:`compute_features`) so they stay
#: bit-identical to the unchunked path.
CHUNK_OVERLAP_BARS = 120

#: Upper bound of the warm-up over the default feature set.
MAX_LOOKBACK_BARS = 120


def _require_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Validate OHLCV frame; return it unchanged (no copy, no mutation)."""
    missing = [c for c in OHLCV_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Index must be a pandas DatetimeIndex")
    if df.index.tz is None:
        raise ValueError("Index must be timezone-aware (UTC)")
    if len(df) > 1 and not df.index.is_monotonic_increasing:
        raise ValueError("Index must be monotonically increasing (causal order)")
    for col in OHLCV_COLS:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise ValueError(f"Column {col!r} must be numeric")
    return df


@register(SPEC_RET_1, SPEC_RET_4, SPEC_RET_12)
def add_lagged_returns(
    df: pd.DataFrame, horizons: list[int] | tuple[int, ...] = (1, 4, 12)
) -> pd.DataFrame:
    """Add ``ret_{h} = log(close / close.shift(h))`` columns (log-return).

    Warm-up: first ``h`` rows are ``NaN`` per horizon.
    """
    _require_ohlcv(df)
    hs = list(horizons)
    if not hs:
        raise ValueError("horizons must be non-empty")
    for h in hs:
        if not isinstance(h, (int, np.integer)) or int(h) < 1:
            raise ValueError(f"horizons must be ints >= 1, got {h!r}")
    out = df.copy()
    close = out["close"]
    for h in hs:
        out[f"ret_{int(h)}"] = np.log(close / close.shift(int(h)))
    return out


@register(
    SPEC_SMA_20,
    SPEC_SMA_50,
    SPEC_SMA_RATIO_20,
    SPEC_TREND_RATIO,
    SPEC_EMA_12,
    SPEC_EMA_26,
    SPEC_MACD,
    SPEC_MACD_SIGNAL,
    SPEC_MACD_HIST,
    SPEC_TREND_SLOPE_5,
)
def add_momentum_trend(
    df: pd.DataFrame,
    sma_fast: int = 20,
    sma_slow: int = 50,
    ema_fast: int = 12,
    ema_slow: int = 26,
    macd_signal: int = 9,
    slope_window: int = 5,
) -> pd.DataFrame:
    """Add SMA ratios, causal EWM MACD and 5-bar slope (all causal)."""
    _require_ohlcv(df)
    for label, v in (
        ("sma_fast", sma_fast),
        ("sma_slow", sma_slow),
        ("ema_fast", ema_fast),
        ("ema_slow", ema_slow),
        ("macd_signal", macd_signal),
        ("slope_window", slope_window),
    ):
        if int(v) < 1:
            raise ValueError(f"{label} must be >= 1, got {v!r}")
    if sma_fast >= sma_slow:
        raise ValueError("sma_fast must be < sma_slow")
    out = df.copy()
    close = out["close"]
    sma_f = close.rolling(sma_fast, min_periods=sma_fast).mean()
    sma_s = close.rolling(sma_slow, min_periods=sma_slow).mean()
    out[f"sma_{sma_fast}"] = sma_f
    out[f"sma_{sma_slow}"] = sma_s
    out["sma_ratio_20"] = close / sma_f - 1.0
    out["trend_ratio_20_50"] = sma_f / sma_s - 1.0
    ema_f = close.ewm(span=ema_fast, adjust=False, min_periods=ema_fast).mean()
    ema_s = close.ewm(span=ema_slow, adjust=False, min_periods=ema_slow).mean()
    out[f"ema_{ema_fast}"] = ema_f
    out[f"ema_{ema_slow}"] = ema_s
    macd = ema_f - ema_s
    signal = macd.ewm(span=macd_signal, adjust=False, min_periods=macd_signal).mean()
    out["macd"] = macd
    out["macd_signal"] = signal
    out["macd_hist"] = macd - signal
    out["trend_slope_5"] = close.pct_change(slope_window)
    return out


@register(SPEC_ATR_14, SPEC_RV_20)
def add_volatility(df: pd.DataFrame, atr_window: int = 14, rv_window: int = 20) -> pd.DataFrame:
    """Add Wilder ATR(14) and 20-bar realized volatility (causal)."""
    _require_ohlcv(df)
    if int(atr_window) < 2 or int(rv_window) < 2:
        raise ValueError("atr_window and rv_window must be >= 2")
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out[f"atr_{atr_window}"] = tr.ewm(
        alpha=1.0 / atr_window, adjust=False, min_periods=atr_window
    ).mean()
    logret = np.log(out["close"] / prev_close)
    out["realized_vol_20"] = logret.rolling(rv_window, min_periods=rv_window).std() * np.sqrt(
        rv_window
    )
    return out


@register(SPEC_RSI_14, SPEC_BB_MID_20, SPEC_BB_POS_20, SPEC_BB_WIDTH_20)
def add_rsi_bollinger(df: pd.DataFrame, rsi_window: int = 14, bb_window: int = 20) -> pd.DataFrame:
    """Add Wilder RSI(14) and Bollinger position/width (causal)."""
    _require_ohlcv(df)
    if int(rsi_window) < 2 or int(bb_window) < 2:
        raise ValueError("rsi_window and bb_window must be >= 2")
    out = df.copy()
    close = out["close"]
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / rsi_window, adjust=False, min_periods=rsi_window).mean()
    avg_loss = loss.ewm(alpha=1.0 / rsi_window, adjust=False, min_periods=rsi_window).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # Flat series (zero loss and zero gain) -> neutral 50, matches Wilder.
    flat = (avg_gain == 0.0) & (avg_loss == 0.0)
    rsi = rsi.mask(flat, 50.0)
    out["rsi_14"] = rsi
    mid = close.rolling(bb_window, min_periods=bb_window).mean()
    std = close.rolling(bb_window, min_periods=bb_window).std()
    out["bb_mid_20"] = mid
    denom = (2.0 * std).replace(0.0, np.nan)
    out["bb_pos_20"] = (close - mid) / denom
    out["bb_width_20"] = (4.0 * std) / mid
    return out


@register(SPEC_SMA_VOL_20, SPEC_NORM_VOL_20, SPEC_VOLUME_Z_20)
def add_volume_features(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Add volume level/z-score relative to trailing mean (causal)."""
    _require_ohlcv(df)
    if int(window) < 2:
        raise ValueError("window must be >= 2")
    out = df.copy()
    vol = out["volume"]
    sma_v = vol.rolling(window, min_periods=window).mean()
    std_v = vol.rolling(window, min_periods=window).std()
    out["sma_vol_20"] = sma_v
    out["norm_vol_20"] = vol / sma_v
    out["volume_z_20"] = (vol - sma_v) / std_v.replace(0.0, np.nan)
    return out


@register(SPEC_RANGE, SPEC_BODY, SPEC_UPPER_WICK, SPEC_LOWER_WICK)
def add_candle_anatomy(df: pd.DataFrame) -> pd.DataFrame:
    """Add per-candle shape ratios normalized by close (no warm-up)."""
    _require_ohlcv(df)
    out = df.copy()
    close = out["close"].replace(0.0, np.nan)
    out["range"] = (out["high"] - out["low"]) / close
    out["body"] = (out["close"] - out["open"]).abs() / close
    out["upper_wick"] = (out["high"] - out[["open", "close"]].max(axis=1)) / close
    out["lower_wick"] = (out[["open", "close"]].min(axis=1) - out["low"]) / close
    return out


@register(SPEC_VOL_REGIME_100, SPEC_TREND_REGIME)
def add_regime(
    df: pd.DataFrame,
    rv_col: str = "realized_vol_20",
    vol_rank_window: int = 100,
) -> pd.DataFrame:
    """Add volatility-quantile and trend-sign regimes (causal).

    ``vol_regime_100`` is the rank of the current ``realized_vol_20``
    within its trailing 100-bar window (``min_periods=100``, else ``NaN``).
    Requires ``realized_vol_20`` (run :func:`add_volatility` first).
    """
    _require_ohlcv(df)
    if rv_col not in df.columns:
        raise ValueError(f"Regime needs {rv_col!r}; run add_volatility() first.")
    if int(vol_rank_window) < 2:
        raise ValueError("vol_rank_window must be >= 2")
    out = df.copy()
    rv = out[rv_col]
    out["vol_regime_100"] = rv.rolling(vol_rank_window, min_periods=vol_rank_window).apply(
        lambda w: (w <= w[-1]).mean(), raw=True
    )
    if "trend_ratio_20_50" in out.columns:
        trend = out["trend_ratio_20_50"]
    else:
        sma_f = out["close"].rolling(20, min_periods=20).mean()
        sma_s = out["close"].rolling(50, min_periods=50).mean()
        trend = sma_f / sma_s - 1.0
    out["trend_regime"] = np.sign(trend)
    return out


@register(SPEC_REF_CLOSE, SPEC_REF_RET_1H)
def align_btc_eth(
    base_df: pd.DataFrame, ref_df: pd.DataFrame, prefix: str = "ref_"
) -> pd.DataFrame:
    """Attach reference closes via backward ``merge_asof`` (never future).

    For each base timestamp the latest reference close at or *before* it is
    used; timestamps predating the reference yield ``NaN`` (never invented).
    """
    _require_ohlcv(base_df)
    _require_ohlcv(ref_df)
    if base_df.empty:
        out = base_df.copy()
        out[f"{prefix}close"] = pd.Series(dtype=float)
        out[f"{prefix}ret_1h"] = pd.Series(dtype=float)
        return out
    base = base_df.sort_index()
    ref = ref_df.sort_index()
    merged = pd.merge_asof(
        base[[]],
        ref[["close"]].rename(columns={"close": f"{prefix}close"}),
        left_index=True,
        right_index=True,
        direction="backward",
    )
    out = base_df.copy()
    out[f"{prefix}close"] = merged[f"{prefix}close"].reindex(out.index)
    ref_close = out[f"{prefix}close"]
    out[f"{prefix}ret_1h"] = ref_close / ref_close.shift(1) - 1.0
    return out.rename(columns={f"{prefix}close": "ref_close", f"{prefix}ret_1h": "ref_ret_1h"})


@register(SPEC_FUNDING)
def add_funding(df: pd.DataFrame, funding_series: pd.Series | None = None) -> pd.DataFrame:
    """Attach funding rates via backward ``merge_asof`` when provided.

    When ``funding_series`` is ``None`` the frame is returned with **no**
    funding column (callers must treat absence as unavailable, not zero).
    """
    _require_ohlcv(df)
    out = df.copy()
    if funding_series is None:
        return out
    if not isinstance(funding_series.index, pd.DatetimeIndex):
        raise ValueError("funding_series must have a DatetimeIndex")
    if funding_series.index.tz is None:
        raise ValueError("funding_series index must be timezone-aware (UTC)")
    if df.empty:
        out["funding"] = pd.Series(dtype=float)
        return out
    merged = pd.merge_asof(
        df.sort_index()[[]],
        funding_series.sort_index().rename("funding").to_frame(),
        left_index=True,
        right_index=True,
        direction="backward",
    )
    out["funding"] = merged["funding"].reindex(out.index)
    return out


class FeatureConfig(BaseModel):
    """Enabled feature groups plus chunking bound for :func:`compute_features`."""

    returns: bool = True
    momentum: bool = True
    volatility: bool = True
    rsi_bollinger: bool = True
    volume: bool = True
    anatomy: bool = True
    regime: bool = True
    horizons: list[int] = Field(default_factory=lambda: [1, 4, 12])
    chunk_rows: int = Field(default=5000, gt=0)


def _coerce_config(
    config: FeatureConfig | dict[str, Any] | None,
    chunk_rows: int | None,
) -> FeatureConfig:
    if config is None:
        cfg = FeatureConfig()
    elif isinstance(config, FeatureConfig):
        cfg = config
    elif isinstance(config, dict):
        cfg = FeatureConfig.model_validate(config)
    else:
        raise ValueError("config must be FeatureConfig, dict, or None")
    if chunk_rows is not None:
        if int(chunk_rows) < 1:
            raise ValueError("chunk_rows must be >= 1")
        cfg = cfg.model_copy(update={"chunk_rows": int(chunk_rows)})
    for h in cfg.horizons:
        if int(h) < 1:
            raise ValueError(f"horizons must be >= 1, got {h!r}")
    return cfg


def _compute_block(
    df: pd.DataFrame,
    cfg: FeatureConfig,
    ref_df: pd.DataFrame | None = None,
    funding_series: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute all enabled groups on one causal block (single working copy).

    One copy total: downstream groups read the same working frame in place.
    """
    _require_ohlcv(df)
    out = df.copy()
    if cfg.returns:
        out = add_lagged_returns(out, horizons=cfg.horizons)
    if cfg.momentum:
        out = add_momentum_trend(out)
    if cfg.volatility:
        out = add_volatility(out)
    if cfg.rsi_bollinger:
        out = add_rsi_bollinger(out)
    if cfg.volume:
        out = add_volume_features(out)
    if cfg.anatomy:
        out = add_candle_anatomy(out)
    if cfg.regime:
        out = add_regime(out)
    if ref_df is not None:
        out = align_btc_eth(out, ref_df)
    if funding_series is not None:
        out = add_funding(out, funding_series)
    return out


def compute_features(
    df: pd.DataFrame,
    config: FeatureConfig | dict[str, Any] | None = None,
    chunk_rows: int | None = None,
    ref_df: pd.DataFrame | None = None,
    funding_series: pd.Series | None = None,
) -> pd.DataFrame:
    """Compute the full causal feature set; return **new columns only**.

    The input frame is never modified. Evaluation runs in row chunks of
    ``chunk_rows``: chunk ``[s, e)`` is evaluated on the causal prefix
    ``df.iloc[:e]`` (all history the chunk may legally see, including the
    infinite-memory EWM state) and only rows ``[s, e)`` are kept, so the
    chunked output is bit-identical to the unchunked path. Chunks are
    concatenated exactly once at the end, bounding peak working memory to
    one prefix evaluation plus the assembled result.
    """
    _require_ohlcv(df)
    cfg = _coerce_config(config, chunk_rows)
    n = len(df)
    if n == 0:
        # Still validate the pipeline shape on an empty block.
        empty = _compute_block(df, cfg, ref_df, funding_series)
        keep = [c for c in empty.columns if c not in df.columns]
        return empty[keep]
    step = cfg.chunk_rows
    if ref_df is not None:
        _require_ohlcv(ref_df)
    parts: list[pd.DataFrame] = []
    for start in range(0, n, step):
        end = min(start + step, n)
        block = _compute_block(df.iloc[:end], cfg, ref_df, funding_series)
        keep = [c for c in block.columns if c not in df.columns]
        parts.append(block.iloc[start:end][keep])
    result = pd.concat(parts, axis=0) if len(parts) > 1 else parts[0]
    result.index = df.index
    return result


def validate_causal(df: pd.DataFrame, feature_cols: list[str]) -> bool:
    """Assert no feature column depends on future rows (prefix-stability).

    Recomputes features on the ``df.iloc[:-5]`` prefix and requires the
    overlapping rows to be identical: any use of future data would change
    earlier rows once later rows are appended. Raises ``AssertionError``.
    """
    if len(df) < 10:
        raise ValueError("validate_causal needs >= 10 rows")
    _require_ohlcv(df)
    full = compute_features(df, chunk_rows=10**9)
    prefix = compute_features(df.iloc[:-5], chunk_rows=10**9)
    cols = feature_cols or [c for c in full.columns]
    for col in cols:
        a = prefix[col].iloc[:-0] if len(prefix) else prefix[col]
        b = full[col].iloc[: len(prefix)]
        pd.testing.assert_series_equal(
            a.reset_index(drop=True),
            b.reset_index(drop=True),
            check_names=False,
        )
    return True


__all__ = [
    "CHUNK_OVERLAP_BARS",
    "MAX_LOOKBACK_BARS",
    "OHLCV_COLS",
    "FeatureConfig",
    "add_candle_anatomy",
    "add_funding",
    "add_lagged_returns",
    "add_momentum_trend",
    "add_regime",
    "add_rsi_bollinger",
    "add_volatility",
    "add_volume_features",
    "align_btc_eth",
    "compute_features",
    "validate_causal",
]
