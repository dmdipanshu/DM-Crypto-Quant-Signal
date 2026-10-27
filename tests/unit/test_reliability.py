"""Reliability-diagnostic tests (offline, deterministic, small fixtures)."""

from __future__ import annotations

import numpy as np
import pytest

from crypto_engine.calibration.reliability import (
    brier_score,
    classification_report_small,
    coverage_at_thresholds,
    expected_calibration_error,
    log_loss_clipped,
    reliability_table,
)


def test_reliability_table_bins_sum_to_n() -> None:
    """Bin counts sum to n for both strategies (empty bins kept as n=0)."""
    rng = np.random.default_rng(0)
    p = rng.uniform(size=120)
    y = (rng.uniform(size=120) < p).astype(int)
    for strategy in ("uniform", "quantile"):
        table, _ = reliability_table(y, p, n_bins=10, strategy=strategy)
        assert list(table.columns) == ["bin", "n", "mean_pred", "frac_pos", "bin_low", "bin_high"]
        assert int(table["n"].sum()) == 120


def test_uniform_bins_equal_width_quantile_adaptive() -> None:
    """Uniform bins are equal-width; quantile bins adapt to score density."""
    p = np.linspace(0.0, 1.0, 100)
    uni, _ = reliability_table(np.zeros(100, dtype=int), p, n_bins=4, strategy="uniform")
    widths = (uni["bin_high"] - uni["bin_low"]).to_numpy()
    assert bool((np.abs(widths - 0.25) < 1e-12).all())
    skewed = np.concatenate([np.full(80, 0.1), np.full(20, 0.9)])
    y = np.zeros(100, dtype=int)
    qtable, _ = reliability_table(y, skewed, n_bins=4, strategy="quantile")
    assert int(qtable["n"].sum()) == 100
    assert float(qtable["bin_low"].iloc[0]) == 0.0
    assert float(qtable["bin_high"].iloc[-1]) == 1.0


def test_small_bins_warned() -> None:
    """Bins with n < 10 produce warnings (reported, never dropped)."""
    rng = np.random.default_rng(0)
    p = rng.uniform(size=15)
    y = (p > 0.5).astype(int)
    table, warnings = reliability_table(y, p, n_bins=5, strategy="uniform")
    assert int(table["n"].sum()) == 15
    assert warnings, "expected small-bin warnings for n=15 over 5 bins"
    assert any("n=" in w for w in warnings)


def test_brier_spot_value() -> None:
    """Brier matches the hand-computed value on a tiny fixture."""
    y = np.asarray([0, 1])
    p = np.asarray([0.25, 0.75])
    assert brier_score(y, p) == pytest.approx(0.0625)


def test_logloss_spot_value() -> None:
    """Clipped log-loss matches the hand-computed value (no clipping active)."""
    y = np.asarray([0, 1])
    p = np.asarray([0.25, 0.75])
    expected = float(-(np.log(0.75) + np.log(0.75)) / 2.0)
    assert log_loss_clipped(y, p) == pytest.approx(expected)
    # Clipping keeps extreme probabilities finite.
    assert np.isfinite(log_loss_clipped(np.asarray([1]), np.asarray([1.0])))


def test_ece_zero_for_perfect() -> None:
    """A perfectly calibrated table has (near-)zero ECE."""
    y = np.asarray([0, 0, 1, 1])
    p = np.asarray([0.0, 0.0, 1.0, 1.0])
    table, _ = reliability_table(y, p, n_bins=2, strategy="uniform")
    assert expected_calibration_error(table) == pytest.approx(0.0, abs=1e-12)
    assert expected_calibration_error(table, norm="l2") == pytest.approx(0.0, abs=1e-12)


def test_coverage_monotone_decreasing() -> None:
    """Higher thresholds retain weakly fewer predictions."""
    rng = np.random.default_rng(0)
    p = rng.uniform(size=80)
    cov = coverage_at_thresholds(p, thresholds=[0.5, 0.6, 0.7, 0.8, 0.9])
    assert list(cov.columns) == ["threshold", "n_retained", "frac_retained"]
    counts = cov["n_retained"].to_numpy()
    assert bool((np.diff(counts) <= 0).all())
    assert float(cov["frac_retained"].iloc[0]) == pytest.approx(counts[0] / 80)


def test_classification_report_small() -> None:
    """Accuracy, per-class precision/recall, CM, and counts on a tiny fixture."""
    y_true = np.asarray([0, 0, 1, 1])
    y_pred = np.asarray([0, 1, 1, 1])
    rep = classification_report_small(y_true, y_pred)
    assert rep["accuracy"] == pytest.approx(0.75)
    assert rep["confusion_matrix"] == [[1, 1], [0, 2]]
    assert rep["class_counts"] == {"0": 2, "1": 2}
    per = rep["per_class"]
    assert per["1"]["precision"] == pytest.approx(2.0 / 3.0)
    assert per["1"]["recall"] == pytest.approx(1.0)
    assert per["0"]["precision"] == pytest.approx(1.0)
    assert per["0"]["recall"] == pytest.approx(0.5)
    assert "sharpe" not in rep and "profit" not in rep and "drawdown" not in rep
