"""Unit tests for triple-barrier labels (offline, deterministic)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from crypto_engine.features.engineering import compute_features
from crypto_engine.labels.triple_barrier import (
    TripleBarrierConfig,
    assert_no_leakage,
    label_config_hash,
    label_triple_barrier,
    load_labels,
    save_labels,
    to_label_frame,
    valid_mask,
)
from tests.fixtures.features_fixture import make_ohlcv_frame


def _cfg(**kw) -> TripleBarrierConfig:
    base = {"pt_mult": 2.0, "sl_mult": 1.0, "max_horizon_bars": 5, "vol_window": 5}
    base.update(kw)
    return TripleBarrierConfig(**base)


def _jump_frame(n: int = 30, jump: float = 0.20, at: int = 10) -> pd.DataFrame:
    """Oscillating base (~0.5% vol) with a one-bar step at ``at``.

    Constant-drift frames have zero realized vol (degenerate barriers), so
    boundary tests use oscillation for a well-defined vol plus an explicit
    jump for deterministic barrier touches.
    """
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    base = 100.0 + 0.3 * np.sin(np.arange(n, dtype=float))
    step = np.where(np.arange(n) >= at, jump * 100.0, 0.0)
    close = base + step
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": np.full(n, 50.0)},
        index=idx,
    )


def test_config_validation() -> None:
    with pytest.raises(ValidationError):
        TripleBarrierConfig(pt_mult=0.0)
    with pytest.raises(ValidationError):
        TripleBarrierConfig(max_horizon_bars=0)
    with pytest.raises(ValidationError):
        TripleBarrierConfig(side="diagonal")


def test_pt_hit_first_bar() -> None:
    df = _jump_frame(jump=0.20)
    out = label_triple_barrier(df, _cfg())
    row = out.iloc[9]  # entry just before the +20% step at bar 10
    assert valid_mask(out).iloc[9]
    assert row["label"] == 1 and row["barrier_hit"] == "pt"
    assert row["tte_bars"] == 1
    assert row["observable_at"] == out.index[10]


def test_sl_hit_and_timeout() -> None:
    df = _jump_frame(jump=-0.20)
    out = label_triple_barrier(df, _cfg())
    row = out.iloc[9]
    assert row["label"] == -1 and row["barrier_hit"] == "sl"
    assert row["tte_bars"] == 1
    calm = _jump_frame(jump=0.20)
    out_calm = label_triple_barrier(calm, _cfg())
    # Entries just after the step see jump-inflated vol (wide barriers)
    # while prices crawl: nothing touches within the horizon.
    # (Only entries whose vol window still covers the step qualify.)
    post = out_calm.iloc[11:15]
    assert valid_mask(post).all()
    assert (post["barrier_hit"] == "timeout").all()
    assert (post["label"] == 0).all()
    assert (post["tte_bars"] == 5).all()


def test_incomplete_horizon_and_warmup() -> None:
    df = _jump_frame(n=12, jump=0.0)
    out = label_triple_barrier(df, _cfg())
    mask = valid_mask(out)
    # Last max_horizon rows lack a full forward window.
    assert (~mask).iloc[-5:].all()
    # First vol_window rows lack causal vol (logret needs vol_window obs).
    assert (~mask).iloc[:5].all()
    # Interior rows are usable.
    assert mask.iloc[5]
    incomplete = out[~mask]
    assert (incomplete["label"] == 0).all()
    assert incomplete["tte_bars"].isna().all()
    assert incomplete["observable_at"].isna().all()


def test_ambiguous_same_bar_conservative() -> None:
    # Giant bar touches both barriers at once.
    df = _jump_frame()
    df.iloc[10, df.columns.get_loc("high")] = 1_000_000.0
    df.iloc[10, df.columns.get_loc("low")] = 0.01
    out = label_triple_barrier(df, _cfg())
    row = out.iloc[9]
    assert row["barrier_hit"] == "ambiguous"
    assert row["label"] == 0
    assert row["tte_bars"] == 1


def test_ambiguous_policies() -> None:
    df = _jump_frame()
    df.iloc[10, df.columns.get_loc("high")] = 1_000_000.0
    df.iloc[10, df.columns.get_loc("low")] = 0.01
    long_first = label_triple_barrier(df, _cfg(ambiguous_policy="long_first"))
    assert long_first.iloc[9]["label"] == 1
    short_first = label_triple_barrier(df, _cfg(ambiguous_policy="short_first"))
    assert short_first.iloc[9]["label"] == -1


def test_cost_widens_barrier() -> None:
    # +1.5% step clears the free barrier (~+1%) but not the 1%-cost one.
    df = _jump_frame(jump=0.015)
    free = label_triple_barrier(df, _cfg(cost_bp=0.0))
    costly = label_triple_barrier(df, _cfg(cost_bp=100.0))  # 1% hurdle
    assert free.iloc[9]["barrier_hit"] == "pt"
    # Higher cost can only delay (never accelerate) the pt hit.
    assert costly.iloc[9]["tte_bars"] >= free.iloc[9]["tte_bars"]


def test_sides() -> None:
    df = _jump_frame(jump=0.20)
    both = label_triple_barrier(df, _cfg(side="both"))
    long = label_triple_barrier(df, _cfg(side="long"))
    pd.testing.assert_series_equal(both["label"], long["label"])
    pd.testing.assert_series_equal(both["barrier_hit"], long["barrier_hit"])
    short = label_triple_barrier(df, _cfg(side="short"))
    # +20% step against a short entry hits its stop first -> +1.
    assert short.iloc[9]["label"] == 1
    assert short.iloc[9]["barrier_hit"] == "sl"
    assert set(short["label"].unique()) <= {-1, 0, 1}


def test_missing_candles_gap_no_crash() -> None:
    df = make_ohlcv_frame(n=60)
    gapped = df.drop(df.index[20:25])  # 5 missing hourly candles
    out = label_triple_barrier(gapped, _cfg())
    assert len(out) == len(gapped)
    assert set(out["barrier_hit"].unique()) <= {
        "pt",
        "sl",
        "timeout",
        "ambiguous",
        "incomplete",
    }
    usable = out[valid_mask(out)]
    assert (usable["tte_bars"].astype(int) >= 1).all()


def test_observability_and_no_leakage() -> None:
    df = make_ohlcv_frame(n=80)
    feats = compute_features(df, chunk_rows=10**9)
    labels, _ = to_label_frame(df, _cfg())
    assert assert_no_leakage(feats, labels) is True
    usable = labels[valid_mask(labels)]
    assert (usable["observable_at"] > usable.index).all()
    assert np.isfinite(usable["vol_at_entry"].to_numpy(dtype=float)).all()


def test_config_hash_stable() -> None:
    assert label_config_hash(_cfg()) == label_config_hash(_cfg())
    assert label_config_hash(_cfg()) != label_config_hash(_cfg(pt_mult=3.0))


def test_save_load_roundtrip(tmp_path) -> None:
    df = make_ohlcv_frame(n=40)
    labels, cfg = to_label_frame(df, _cfg())
    save_labels(labels, cfg, tmp_path / "labels")
    loaded, loaded_cfg = load_labels(tmp_path / "labels")
    pd.testing.assert_frame_equal(loaded, labels, check_freq=False)
    assert loaded_cfg == cfg
