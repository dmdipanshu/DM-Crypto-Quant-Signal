"""Unit tests for causal feature engineering (offline, deterministic)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_engine.features.engineering import (
    FeatureConfig,
    add_candle_anatomy,
    add_funding,
    add_lagged_returns,
    add_momentum_trend,
    add_rsi_bollinger,
    add_volatility,
    align_btc_eth,
    compute_features,
)
from crypto_engine.features.registry import FEATURE_REGISTRY, max_warmup
from tests.fixtures.features_fixture import make_ohlcv_frame, make_trend_frame


@pytest.fixture
def frame() -> pd.DataFrame:
    return make_ohlcv_frame()


def test_registry_covers_all_features() -> None:
    expected = {
        "ret_1",
        "ret_4",
        "ret_12",
        "sma_20",
        "sma_50",
        "sma_ratio_20",
        "trend_ratio_20_50",
        "ema_12",
        "ema_26",
        "macd",
        "macd_signal",
        "macd_hist",
        "trend_slope_5",
        "atr_14",
        "realized_vol_20",
        "rsi_14",
        "bb_mid_20",
        "bb_pos_20",
        "bb_width_20",
        "sma_vol_20",
        "norm_vol_20",
        "volume_z_20",
        "range",
        "body",
        "upper_wick",
        "lower_wick",
        "vol_regime_100",
        "trend_regime",
        "ref_close",
        "ref_ret_1h",
        "funding",
    }
    assert expected <= set(FEATURE_REGISTRY)
    for name in expected:
        spec = FEATURE_REGISTRY[name]
        assert spec.formula and spec.units
        assert spec.lookback_bars >= 0 and spec.warmup_bars >= 0
        assert spec.timestamp_convention.startswith("candle_close")
    assert max_warmup() >= 100


def test_lagged_return_shift_correctness() -> None:
    df = make_trend_frame(n=15, drift=0.0)
    df["close"] = 100.0 + np.arange(15.0)
    df["open"] = df["close"]
    df["high"] = df["close"] + 1.0
    df["low"] = df["close"] - 1.0
    out = add_lagged_returns(df, horizons=[1, 4])
    assert out["ret_1"].iloc[:1].isna().all()
    assert out["ret_4"].iloc[:4].isna().all()
    c = df["close"].to_numpy()
    assert out["ret_1"].iloc[5] == pytest.approx(np.log(c[5] / c[4]))
    assert out["ret_4"].iloc[8] == pytest.approx(np.log(c[8] / c[4]))


def test_invalid_horizons_raise() -> None:
    df = make_ohlcv_frame(n=20)
    with pytest.raises(ValueError):
        add_lagged_returns(df, horizons=[0])
    with pytest.raises(ValueError):
        add_lagged_returns(df, horizons=[-3])
    with pytest.raises(ValueError):
        add_lagged_returns(df, horizons=[])


def test_rsi_bounds_and_atr_positive(frame: pd.DataFrame) -> None:
    out = add_rsi_bollinger(add_volatility(frame))
    rsi = out["rsi_14"].dropna()
    assert ((rsi >= 0.0) & (rsi <= 100.0)).all()
    assert (out["atr_14"].dropna() > 0.0).all()
    assert (out["realized_vol_20"].dropna() >= 0.0).all()


def test_warmup_nans(frame: pd.DataFrame) -> None:
    out = compute_features(frame)
    assert out["ret_12"].iloc[:12].isna().all()
    assert out["sma_50"].iloc[:49].isna().all()
    assert out["realized_vol_20"].iloc[:20].isna().all()
    assert out["vol_regime_100"].iloc[:119].isna().all()
    assert out["trend_regime"].iloc[:49].isna().all()
    # Anatomy needs no warm-up.
    assert out[["range", "body", "upper_wick", "lower_wick"]].notna().all().all()


def test_momentum_spot_check() -> None:
    df = make_trend_frame(n=60, drift=0.0)
    df["close"] = 100.0
    df["open"] = 100.0
    df["high"] = 101.0
    df["low"] = 99.0
    out = add_momentum_trend(df)
    assert out["sma_20"].iloc[19] == pytest.approx(100.0)
    assert out["sma_ratio_20"].iloc[19] == pytest.approx(0.0)
    assert out["trend_slope_5"].iloc[5] == pytest.approx(0.0)


def test_candle_anatomy_spot_check() -> None:
    df = make_ohlcv_frame(n=10)
    out = add_candle_anatomy(df)
    row, src = out.iloc[3], df.iloc[3]
    assert row["range"] == pytest.approx((src.high - src.low) / src.close)
    assert row["body"] == pytest.approx(abs(src.close - src.open) / src.close)
    assert row["upper_wick"] >= 0.0 and row["lower_wick"] >= 0.0


def test_cross_asset_merge_asof_no_future() -> None:
    base = make_ohlcv_frame(n=30, seed=1)
    ref = make_ohlcv_frame(n=30, seed=2)
    # Reference starts 5h late: early base rows must stay NaN (never invented).
    ref_late = ref.iloc[5:]
    out = align_btc_eth(base, ref_late)
    assert out["ref_close"].iloc[:5].isna().all()
    assert out["ref_close"].iloc[5] == pytest.approx(ref_late["close"].iloc[0])
    # Reference value at base t must equal last ref close at-or-before t.
    assert out["ref_ret_1h"].iloc[:6].isna().all()


def test_funding_absent_without_series(frame: pd.DataFrame) -> None:
    out = add_funding(frame, None)
    assert "funding" not in out.columns
    funding = pd.Series(0.0001, index=frame.index, dtype=float)
    out2 = add_funding(frame, funding)
    assert (out2["funding"] == 0.0001).all()


def test_chunk_vs_nonchunk_equality(frame: pd.DataFrame) -> None:
    small = compute_features(frame, chunk_rows=50)
    big = compute_features(frame, chunk_rows=10**9)
    pd.testing.assert_frame_equal(small, big)


def test_input_not_modified(frame: pd.DataFrame) -> None:
    snapshot = frame.copy(deep=True)
    compute_features(frame, chunk_rows=25)
    pd.testing.assert_frame_equal(frame, snapshot)


def test_config_dict_and_object_equivalent(frame: pd.DataFrame) -> None:
    from_dict = compute_features(frame, {"returns": True, "regime": False})
    from_obj = compute_features(frame, FeatureConfig(returns=True, regime=False))
    pd.testing.assert_frame_equal(from_dict, from_obj)
    assert "vol_regime_100" not in from_dict.columns
    assert "ret_1" in from_dict.columns


def test_empty_frame() -> None:
    empty = make_ohlcv_frame(n=10).iloc[0:0]
    out = compute_features(empty)
    assert len(out) == 0
