"""Feature registry: canonical specs for every Phase 2 causal feature (Track A)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Timestamp convention shared by all causal features: a feature indexed at
# candle ``t`` uses only candles ``<= t`` (candle ``t`` close known) and is
# available for decisions at the *next* candle open.
CANDLE_CLOSE_NEXT_OPEN = "candle_close, available at next open"


@dataclass(frozen=True)
class FeatureSpec:
    """Static documentation + warm-up contract for one feature column."""

    name: str
    formula: str
    lookback_bars: int
    units: str
    warmup_bars: int
    params: dict[str, Any] = field(default_factory=dict)
    timestamp_convention: str = CANDLE_CLOSE_NEXT_OPEN


FEATURE_REGISTRY: dict[str, FeatureSpec] = {}


def register(*specs: FeatureSpec) -> Callable:
    """Decorator registering the given specs under the decorated function.

    The registry is populated as a side effect of importing
    ``crypto_engine.features.engineering`` (which decorates each builder
    with the specs of the columns it produces). Re-registration of an
    identical spec is idempotent; a conflicting re-registration raises.
    """

    def deco(fn: Callable) -> Callable:
        existing = getattr(fn, "__feature_specs__", ())
        fn.__feature_specs__ = (*existing, *specs)  # type: ignore[attr-defined]
        for spec in specs:
            prev = FEATURE_REGISTRY.get(spec.name)
            if prev is not None and prev != spec:
                raise ValueError(f"Conflicting FeatureSpec for {spec.name!r}")
            FEATURE_REGISTRY[spec.name] = spec
        return fn

    return deco


def _s(
    name: str,
    formula: str,
    lookback_bars: int,
    units: str,
    warmup_bars: int,
    **params: Any,
) -> FeatureSpec:
    return FeatureSpec(
        name=name,
        formula=formula,
        lookback_bars=lookback_bars,
        units=units,
        warmup_bars=warmup_bars,
        params=dict(params),
    )


# Canonical specs (single source of truth; engineering.py re-asserts them
# via @register on each builder function).
SPEC_RET_1 = _s("ret_1", "log(close / close.shift(1))", 1, "log-return", 1, h=1)
SPEC_RET_4 = _s("ret_4", "log(close / close.shift(4))", 4, "log-return", 4, h=4)
SPEC_RET_12 = _s("ret_12", "log(close / close.shift(12))", 12, "log-return", 12, h=12)
SPEC_SMA_20 = _s("sma_20", "mean(close, 20)", 20, "price", 20, window=20)
SPEC_SMA_50 = _s("sma_50", "mean(close, 50)", 50, "price", 50, window=50)
SPEC_SMA_RATIO_20 = _s("sma_ratio_20", "close / sma_20 - 1", 20, "fraction", 20, window=20)
SPEC_TREND_RATIO = _s(
    "trend_ratio_20_50",
    "sma_20 / sma_50 - 1",
    50,
    "fraction",
    50,
    fast=20,
    slow=50,
)
SPEC_EMA_12 = _s("ema_12", "ewm(span=12, adjust=False).mean(close)", 12, "price", 12, span=12)
SPEC_EMA_26 = _s("ema_26", "ewm(span=26, adjust=False).mean(close)", 26, "price", 26, span=26)
SPEC_MACD = _s("macd", "ema_12 - ema_26", 26, "price", 26)
SPEC_MACD_SIGNAL = _s(
    "macd_signal", "ewm(span=9, adjust=False).mean(macd)", 35, "price", 35, span=9
)
SPEC_MACD_HIST = _s("macd_hist", "macd - macd_signal", 35, "price", 35)
SPEC_TREND_SLOPE_5 = _s(
    "trend_slope_5", "close.pct_change(5)", 5, "fraction per 5 bars", 5, window=5
)
SPEC_ATR_14 = _s(
    "atr_14",
    "Wilder ewm(alpha=1/14) mean of TR; TR=max(h-l,|h-c.prev|,|l-c.prev|)",
    14,
    "price",
    14,
    window=14,
)
SPEC_RV_20 = _s(
    "realized_vol_20",
    "std(log(close/close.shift(1)), 20) * sqrt(20)",
    21,
    "log-return per 20-bar window",
    21,
    window=20,
)
SPEC_RSI_14 = _s(
    "rsi_14",
    "100 - 100/(1+RS); RS=Wilder-ewm gain / Wilder-ewm loss",
    14,
    "index 0-100",
    14,
    window=14,
)
SPEC_BB_MID_20 = _s("bb_mid_20", "mean(close, 20)", 20, "price", 20, window=20)
SPEC_BB_POS_20 = _s(
    "bb_pos_20",
    "(close - bb_mid_20) / (2 * std(close, 20))",
    20,
    "band half-widths",
    20,
    window=20,
)
SPEC_BB_WIDTH_20 = _s(
    "bb_width_20",
    "(upper - lower) / bb_mid_20; upper/lower=mid +/- 2*std",
    20,
    "fraction",
    20,
    window=20,
)
SPEC_SMA_VOL_20 = _s("sma_vol_20", "mean(volume, 20)", 20, "base-volume", 20, window=20)
SPEC_NORM_VOL_20 = _s("norm_vol_20", "volume / sma_vol_20", 20, "ratio", 20, window=20)
SPEC_VOLUME_Z_20 = _s(
    "volume_z_20",
    "(volume - sma_vol_20) / std(volume, 20)",
    20,
    "std-deviations",
    20,
    window=20,
)
SPEC_RANGE = _s("range", "(high - low) / close", 0, "fraction", 0)
SPEC_BODY = _s("body", "abs(close - open) / close", 0, "fraction", 0)
SPEC_UPPER_WICK = _s("upper_wick", "(high - max(open, close)) / close", 0, "fraction", 0)
SPEC_LOWER_WICK = _s("lower_wick", "(min(open, close) - low) / close", 0, "fraction", 0)
SPEC_VOL_REGIME_100 = _s(
    "vol_regime_100",
    "causal quantile rank of realized_vol_20 within trailing 100 bars",
    100,
    "quantile 0-1",
    120,
    window=100,
)
SPEC_TREND_REGIME = _s("trend_regime", "sign(sma_20 / sma_50 - 1)", 50, "sign in {-1,0,1}", 50)
SPEC_REF_CLOSE = _s(
    "ref_close",
    "merge_asof(backward) ref close at base timestamp",
    0,
    "price (NaN when unavailable)",
    0,
)
SPEC_REF_RET_1H = _s(
    "ref_ret_1h",
    "ref_close / ref_close.shift(1) - 1",
    1,
    "fraction (NaN when unavailable)",
    1,
)
SPEC_FUNDING = _s(
    "funding",
    "merge_asof(backward) funding rate at candle timestamp",
    0,
    "fraction per interval (absent unless provided)",
    0,
)

ALL_SPECS: tuple[FeatureSpec, ...] = (
    SPEC_RET_1,
    SPEC_RET_4,
    SPEC_RET_12,
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
    SPEC_ATR_14,
    SPEC_RV_20,
    SPEC_RSI_14,
    SPEC_BB_MID_20,
    SPEC_BB_POS_20,
    SPEC_BB_WIDTH_20,
    SPEC_SMA_VOL_20,
    SPEC_NORM_VOL_20,
    SPEC_VOLUME_Z_20,
    SPEC_RANGE,
    SPEC_BODY,
    SPEC_UPPER_WICK,
    SPEC_LOWER_WICK,
    SPEC_VOL_REGIME_100,
    SPEC_TREND_REGIME,
    SPEC_REF_CLOSE,
    SPEC_REF_RET_1H,
    SPEC_FUNDING,
)

# Populate directly so the registry is complete on ``registry`` import alone
# (engineering.py decorators re-assert the identical specs).
for _spec in ALL_SPECS:
    FEATURE_REGISTRY[_spec.name] = _spec


def max_warmup(names: list[str] | None = None) -> int:
    """Return the largest warm-up over the given (or all) features."""
    specs = ALL_SPECS if names is None else [FEATURE_REGISTRY[n] for n in names]
    return max((s.warmup_bars for s in specs), default=0)


__all__ = [
    "ALL_SPECS",
    "CANDLE_CLOSE_NEXT_OPEN",
    "FEATURE_REGISTRY",
    "FeatureSpec",
    "max_warmup",
    "register",
]
