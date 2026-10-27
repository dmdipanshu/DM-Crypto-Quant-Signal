"""Metric + robustness tests on tiny deterministic fixtures (Track B).

Covers exact cost-proxy math (hand-computed 5-trade case), stress
monotonicity, bootstrap CI containment + seed reproducibility, and
per-fold aggregation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_engine.models.base import InformativeError
from crypto_engine.validation.folds import make_folds
from crypto_engine.validation.metrics import (
    aggregate_folds,
    baseline_comparison,
    classification_metrics,
    proxy_summary,
    score_metrics,
    summarize_folds,
    trade_proxy_returns,
)
from crypto_engine.validation.robustness import (
    block_bootstrap_ci,
    fee_slippage_grid,
    leave_one_fold_out,
    permutation_mc,
)


def test_classification_spot_values() -> None:
    """Hand-checked accuracy / per-class / nonzero read on a tiny fixture."""
    y_true = np.asarray([1, 1, -1, 0, 0, -1])
    y_pred = np.asarray([1, -1, -1, 0, 1, -1])
    report = classification_metrics(y_true, y_pred)
    assert report["accuracy"] == pytest.approx(4 / 6)
    assert report["n"] == 6
    assert report["per_class"]["1"]["precision"] == pytest.approx(1 / 2)
    assert report["per_class"]["1"]["recall"] == pytest.approx(1 / 2)
    # Nonzero rows: [1,1,-1,-1] vs [1,-1,-1,-1] -> 3/4.
    assert report["accuracy_nonzero"] == pytest.approx(3 / 4)
    assert report["n_nonzero"] == 4
    assert sum(report["class_counts"].values()) == 6


def test_score_metrics_kind_tag_and_values() -> None:
    """Brier spot value; kind tag recorded; bad kind rejected."""
    y = np.asarray([1, 0, 1, 0])
    p = np.asarray([0.9, 0.1, 0.8, 0.4])
    out = score_metrics(y, p, score_kind="calibrated", n_bins=2)
    assert out["score_kind"] == "calibrated"
    assert out["brier"] == pytest.approx(float(np.mean((p - y) ** 2)))
    assert 0.0 <= out["ece"] <= 1.0
    assert len(out["reliability_table"]) == 2
    assert len(out["coverage"]) == 5
    with pytest.raises(InformativeError):
        score_metrics(y, p, score_kind="mystery")


def test_cost_proxy_math_exact(proxy_labels_5) -> None:
    """5-trade hand-computed case matches to the cent of a basis point."""
    fix = proxy_labels_5
    returns = trade_proxy_returns(
        fix["labels"],
        pt_ret=fix["pt_ret"],
        sl_ret=fix["sl_ret"],
        fee_bp=fix["fee_bp"],
        slippage_bp=fix["slippage_bp"],
    )
    np.testing.assert_allclose(returns, fix["expected"], rtol=0, atol=1e-12)
    assert float(returns.mean()) == pytest.approx(fix["expected_mean"])
    summary = proxy_summary(returns)
    assert summary["expectancy"] == pytest.approx(0.0025)
    assert summary["win_rate_nonzero"] == pytest.approx(0.4)  # 2 wins of 5 nonzero
    assert summary["n_nonzero"] == 5  # timeouts still pay costs -> nonzero


def test_vol_scaled_proxy_mode() -> None:
    """Vol-scaled outcomes use per-row pt/sl multiples."""
    labels = np.asarray([1, -1, 0])
    vol = np.asarray([0.01, 0.02, 0.03])
    returns = trade_proxy_returns(
        labels, vol_at_entry=vol, pt_mult=2.0, sl_mult=1.0, fee_bp=0.0, slippage_bp=0.0
    )
    np.testing.assert_allclose(returns, [0.02, -0.02, 0.0], atol=1e-12)
    with pytest.raises(InformativeError):
        trade_proxy_returns(labels, pt_ret=0.01, vol_at_entry=vol, pt_mult=1.0, sl_mult=1.0)
    with pytest.raises(InformativeError):
        trade_proxy_returns(np.asarray([1, 2, 0]), pt_ret=0.01, sl_ret=0.01)


def test_stress_monotonic(proxy_labels_5) -> None:
    """Higher costs cannot improve expectancy (2x variants <= base)."""
    fix = proxy_labels_5
    grid = fee_slippage_grid(
        fix["labels"],
        pt_ret=fix["pt_ret"],
        sl_ret=fix["sl_ret"],
        fee_bp=fix["fee_bp"],
        slippage_bp=fix["slippage_bp"],
    )
    assert len(grid) == 4  # 2 fee mults x 2 slip mults
    base = grid[(grid["fee_mult"] == 1.0) & (grid["slip_mult"] == 1.0)]["expectancy"].iloc[0]
    assert base == pytest.approx(fix["expected_mean"])
    assert bool((grid["expectancy"] <= base + 1e-12).all())
    worst = grid[(grid["fee_mult"] == 2.0) & (grid["slip_mult"] == 2.0)]["expectancy"].iloc[0]
    assert worst < base


def test_block_bootstrap_contains_mean_and_reproducible() -> None:
    """Percentile CI covers the sample mean; identical seeds agree exactly."""
    rng = np.random.default_rng(3)
    values = rng.normal(0.002, 0.01, size=120)
    first = block_bootstrap_ci(values, n_boot=50, block_len=20, seed=7)
    second = block_bootstrap_ci(values, n_boot=50, block_len=20, seed=7)
    assert first["lo"] == second["lo"] and first["hi"] == second["hi"]
    assert first["lo"] <= first["observed"] <= first["hi"]
    assert first["warning"] == ""  # n=120 clears the small-sample bar
    small = block_bootstrap_ci(values[:40], n_boot=50, block_len=10, seed=7)
    assert "indicative only" in small["warning"]
    with pytest.raises(InformativeError):
        block_bootstrap_ci(values, n_boot=500, max_bootstrap=200)


def test_permutation_mc_shape() -> None:
    """MC reshuffle returns a calibrated null summary, not a profit claim."""
    rng = np.random.default_rng(11)
    values = rng.normal(0.001, 0.005, size=100)
    out = permutation_mc(values, n_perm=50, seed=5)
    assert 0.0 <= out["p_two_sided"] <= 1.0
    assert out["observed"] == pytest.approx(float(values.mean()))
    assert "order" in out["note"]
    assert permutation_mc(values, n_perm=50, seed=5)["p_two_sided"] == out["p_two_sided"]


def test_proxy_summary_edge_cases() -> None:
    """Zero dispersion -> sharpe None; no nonzero -> win rate None (both warned)."""
    flat = proxy_summary(np.zeros(10))
    assert flat["sharpe_proxy"] is None
    assert flat["win_rate_nonzero"] is None
    assert len(flat["warnings"]) >= 3  # proxy warning + two undefined warnings
    assert any("Proxy-level" in w for w in flat["warnings"])
    with pytest.raises(InformativeError):
        proxy_summary(np.asarray([1.0, np.nan]))


def test_aggregate_and_lofo() -> None:
    """Mean±std aggregation across folds; leave-one-fold-out sensitivity."""
    per_fold = [
        {"accuracy": 0.6, "expectancy": 0.01},
        {"accuracy": 0.5, "expectancy": -0.005},
        {"accuracy": 0.7, "expectancy": 0.02},
    ]
    agg = aggregate_folds(per_fold)
    assert agg["accuracy"]["mean"] == pytest.approx(0.6)
    assert agg["accuracy"]["n"] == 3.0
    assert agg["expectancy"]["min"] == pytest.approx(-0.005)
    bundled = summarize_folds(per_fold)
    assert bundled["n_folds"] == 3
    assert bundled["aggregate"]["accuracy"]["mean"] == pytest.approx(0.6)
    lofo = leave_one_fold_out(per_fold, "expectancy")
    assert len(lofo) == 3
    # Excluding fold 1 (the negative one) raises the mean above the full mean.
    full_mean = float(np.mean([0.01, -0.005, 0.02]))
    assert lofo.loc[1, "mean_excl"] > full_mean


def test_baseline_comparison_tabulates(supervised_frame_200) -> None:
    """Trend/majority/logistic run on identical folds; failures tabulated."""
    X, y = supervised_frame_200
    n = len(X)
    folds = make_folds(n, 80, 20, 2, min_samples=10)
    table = baseline_comparison(X, y, folds, seed=42)
    assert set(table["model"]) == {"trend", "majority", "logistic"}
    assert len(table) == 2 * 3
    assert bool(((table["accuracy"] >= 0) | table["accuracy"].isna()).all())
    assert set(table["status"].str.startswith("ok") | table["status"].str.startswith("failed")) == {
        True
    }
    assert isinstance(table, pd.DataFrame)
