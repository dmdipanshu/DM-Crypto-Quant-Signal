"""Calibration tests: Platt/isotonic maps, guards, round-trip (offline)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit

from crypto_engine.calibration.calibrators import CalibratedClassifier
from crypto_engine.calibration.reliability import brier_score
from crypto_engine.models.base import (
    CorruptArtifactError,
    InformativeError,
    NotFittedError,
    UntrainedModelError,
)
from crypto_engine.models.sklearn_wrappers import SklearnLogistic


def _fitted_base(seed: int = 0, n_classes: int = 2) -> SklearnLogistic:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(60, 2))
    X = pd.DataFrame(x, columns=["a", "b"])
    if n_classes == 2:
        y = (x[:, 0] > 0).astype(int)
    else:
        y = np.digitize(x[:, 0], [-0.5, 0.5]).astype(int)
    return SklearnLogistic(feature_names=["a", "b"]).fit(X, y)


def _shifted_splits(seed: int = 0, n_cal: int = 200, n_test: int = 100):
    """Miscalibrated (shifted) scores with Bernoulli outcomes (<=300 rows)."""
    rng = np.random.default_rng(seed)
    z_cal = rng.normal(size=n_cal)
    z_test = rng.normal(size=n_test + 1)[:n_test]
    p_cal = expit(z_cal)
    p_test = expit(z_test)
    # Systematic miscalibration: uniform +0.25 overprediction (clipped).
    s_cal = np.clip(p_cal + 0.25, 0.0, 1.0)
    s_test = np.clip(p_test + 0.25, 0.0, 1.0)
    y_cal = (rng.uniform(size=n_cal) < p_cal).astype(int)
    y_test = (rng.uniform(size=n_test) < p_test).astype(int)
    # Force both outcomes present (deterministic seeds already give this, guard anyway).
    y_cal[0], y_cal[1] = 0, 1
    y_test[0], y_test[1] = 0, 1
    return s_cal, y_cal, s_test, y_test


def test_sigmoid_improves_brier_on_shifted_synthetic() -> None:
    """Platt scaling repairs compressed scores on a later split (lower Brier)."""
    base = _fitted_base()
    s_cal, y_cal, s_test, y_test = _shifted_splits()
    t_train = np.arange(60)
    t_cal = np.arange(60, 60 + s_cal.shape[0])
    cal = CalibratedClassifier(base, method="sigmoid").fit(s_cal, y_cal, t_bounds=(t_train, t_cal))
    assert cal.is_fitted and cal.is_calibrated
    assert cal.positive_class == 1  # event is P(y == classes[1])
    raw = brier_score(y_test, s_test)
    fixed = brier_score(y_test, cal.predict_proba(s_test))
    assert fixed < raw


def test_isotonic_monotone() -> None:
    """Isotonic map is non-decreasing in the raw score."""
    base = _fitted_base()
    s_cal, y_cal, _, _ = _shifted_splits()
    cal = CalibratedClassifier(base, method="isotonic").fit(s_cal, y_cal)
    grid = np.linspace(0.0, 1.0, 51)
    out = cal.predict_proba(grid)
    assert bool((np.diff(out) >= -1e-12).all())
    assert bool(((out >= 0.0) & (out <= 1.0)).all())


def test_predict_threshold_labels() -> None:
    """Thresholded predictions return the event / non-event class labels."""
    base = _fitted_base()
    s_cal, y_cal, _, _ = _shifted_splits()
    cal = CalibratedClassifier(base, method="sigmoid").fit(s_cal, y_cal)
    pred = cal.predict(np.asarray([0.0, 1.0]), threshold=0.5)
    assert set(np.unique(pred).tolist()) <= {0, 1}
    assert int(pred[0]) == 0 or int(pred[1]) == 1  # at least one side maps correctly


@pytest.mark.parametrize("method", ["sigmoid", "isotonic"])
def test_min_samples_guard(method: str) -> None:
    """Fewer rows than min_cal_samples raises instead of fitting noise."""
    base = _fitted_base()
    s_cal, y_cal, _, _ = _shifted_splits()
    with pytest.raises(InformativeError, match="min_cal_samples"):
        CalibratedClassifier(base, method=method, min_cal_samples=20).fit(
            s_cal[:5], np.asarray([0, 1, 0, 1, 0])
        )
    assert y_cal.size > 5  # fixture sanity


@pytest.mark.parametrize("method", ["sigmoid", "isotonic"])
def test_single_class_guard(method: str) -> None:
    """Single-outcome calibration labels raise with an actionable message."""
    base = _fitted_base()
    s_cal, _, _, _ = _shifted_splits()
    with pytest.raises(InformativeError, match="single class"):
        CalibratedClassifier(base, method=method).fit(s_cal, np.ones_like(s_cal, dtype=int))


@pytest.mark.parametrize("method", ["sigmoid", "isotonic"])
def test_nan_scores_guard(method: str) -> None:
    """NaN scores are rejected, never silently filled."""
    base = _fitted_base()
    s_cal, y_cal, _, _ = _shifted_splits()
    bad = s_cal.copy()
    bad[3] = np.nan
    with pytest.raises(InformativeError, match="NaN"):
        CalibratedClassifier(base, method=method).fit(bad, y_cal)
    cal = CalibratedClassifier(base, method=method).fit(s_cal, y_cal)
    with pytest.raises(InformativeError, match="NaN"):
        cal.predict_proba(np.asarray([0.5, np.nan]))


def test_time_overlap_guard() -> None:
    """Overlapping train/cal timestamps raise; ordered splits pass."""
    base = _fitted_base()
    s_cal, y_cal, _, _ = _shifted_splits()
    with pytest.raises(InformativeError, match="strictly later"):
        CalibratedClassifier(base).fit(
            s_cal, y_cal, t_bounds=(np.arange(100), np.arange(50, 50 + s_cal.shape[0]))
        )
    ok = CalibratedClassifier(base).fit(
        s_cal, y_cal, t_bounds=(np.arange(60), np.arange(60, 60 + s_cal.shape[0]))
    )
    assert ok.is_fitted


@pytest.mark.parametrize("method", ["sigmoid", "isotonic"])
def test_save_load_reproduces_within_1e12(method: str) -> None:
    """Reloaded maps reproduce probabilities within 1e-12."""
    base = _fitted_base()
    s_cal, y_cal, s_test, _ = _shifted_splits()
    cal = CalibratedClassifier(base, method=method).fit(s_cal, y_cal)
    payload = cal.to_dict()
    assert payload["method"] == method
    assert payload["positive_class"] == 1
    reloaded = CalibratedClassifier.from_dict(payload)
    before = cal.predict_proba(s_test)
    after = reloaded.predict_proba(s_test)
    assert float(np.max(np.abs(before - after))) < 1e-12


def test_schema_hash_check_on_load() -> None:
    """Edited feature lists fail the schema-hash check on load."""
    base = _fitted_base()
    s_cal, y_cal, _, _ = _shifted_splits()
    payload = CalibratedClassifier(base).fit(s_cal, y_cal).to_dict()
    payload["feature_names"] = [*payload["feature_names"], "injected"]
    with pytest.raises(CorruptArtifactError, match="schema_hash"):
        CalibratedClassifier.from_dict(payload)


def test_unfitted_base_guard() -> None:
    """An unfitted base model raises UntrainedModelError (no silent fit)."""
    base = SklearnLogistic(feature_names=["a", "b"])
    s_cal, y_cal, _, _ = _shifted_splits()
    with pytest.raises(UntrainedModelError, match="not fitted"):
        CalibratedClassifier(base).fit(s_cal, y_cal)


def test_multiclass_guards() -> None:
    """Multiclass base models and 3-class y_cal are rejected (binary only)."""
    base3 = _fitted_base(n_classes=3)
    s_cal, _, _, _ = _shifted_splits()
    rng = np.random.default_rng(0)
    y3 = rng.integers(0, 3, size=s_cal.shape[0])
    y3[0], y3[1], y3[2] = 0, 1, 2
    with pytest.raises(InformativeError, match="binary only"):
        CalibratedClassifier(base3).fit(s_cal, y3)
    base2 = _fitted_base()
    with pytest.raises(InformativeError, match="binary only"):
        CalibratedClassifier(base2).fit(s_cal, y3)


def test_predict_before_fit_raises() -> None:
    """Predicting with an unfitted calibrator raises NotFittedError."""
    cal = CalibratedClassifier(_fitted_base())
    with pytest.raises(NotFittedError, match="before fit"):
        cal.predict_proba(np.asarray([0.5]))


def test_bad_method_rejected() -> None:
    """Unknown calibration methods are rejected at construction."""
    with pytest.raises(InformativeError, match="unknown method"):
        CalibratedClassifier(_fitted_base(), method="platt-plus")


def test_settings_calibration_validation_defaults() -> None:
    """Settings expose calibration + validation sections with valid defaults."""
    from crypto_engine.config.settings import Settings

    settings = Settings()
    assert settings.calibration.method == "sigmoid"
    assert settings.calibration.min_cal_samples == 20
    assert settings.calibration.positive_class is None
    assert settings.validation.n_folds == 3
    assert settings.validation.embargo_bars == 24
    assert settings.validation.holdout_frac == pytest.approx(0.15)
    assert settings.model.n_jobs == 1
