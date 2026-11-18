"""Unit tests for Phase 2 model interfaces and baselines (offline, deterministic)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_engine.features.engineering import compute_features
from crypto_engine.models.base import (
    InformativeError,
    NotFittedError,
    SchemaMismatchError,
    feature_schema_hash,
)
from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline
from crypto_engine.models.sklearn_wrappers import SklearnLogistic
from crypto_engine.registry.store import load_model, save_model
from tests.fixtures.features_fixture import make_ohlcv_frame


def _frame(n: int = 300, seed: int = 7) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    df = make_ohlcv_frame(n=n, seed=seed)
    feats = compute_features(df).dropna()
    cols = ["ret_1", "trend_ratio_20_50", "rsi_14", "realized_vol_20"]
    X = feats[cols].copy()
    y = np.where(X["trend_ratio_20_50"].to_numpy() > 0, 1, -1).astype(np.int64)
    # Add a second class member variety: flip a deterministic slice to 0? keep binary.
    return X, y, cols


def test_trend_predict_shapes_and_sign() -> None:
    X, y, _cols = _frame()
    m = TrendBaseline().fit(X, y)
    pred = m.predict(X)
    assert pred.shape == (len(X),)
    assert set(np.unique(pred)).issubset({-1, 1})
    ratio = X["trend_ratio_20_50"].to_numpy()
    assert (pred[ratio > 0] == 1).all()
    assert (pred[ratio <= 0] == -1).all()
    score = m.predict_score(X)
    assert score.shape == (len(X),)
    assert np.all((score >= 0.0) & (score <= 1.0))
    # No calibration claim: score is a monotone rescale of the ratio.
    assert float(np.corrcoef(score, np.nan_to_num(ratio))[0, 1]) > 0.99


def test_trend_alias_column_accepted() -> None:
    X, y, _ = _frame()
    renamed = X.rename(columns={"trend_ratio_20_50": "trend_sma_ratio_20_50"})
    m = TrendBaseline().fit(renamed, y)
    pred = m.predict(renamed)
    assert pred.shape == (len(renamed),)


def test_majority_fit_predict_prior() -> None:
    X, y, _ = _frame()
    y2 = np.ones_like(y)
    y2[:5] = -1  # deterministic skew: majority +1 with prior (n-5)/n
    m = MajorityBaseline().fit(X, y2)
    assert m.majority_ == 1
    assert m.prior_ == pytest.approx((y2 == 1).mean())
    assert (m.predict(X) == 1).all()
    assert np.all(m.predict_score(X) == pytest.approx(m.prior_))


def test_single_class_handling() -> None:
    X, y, _ = _frame()
    y_one = np.full_like(y, -1)
    maj = MajorityBaseline().fit(X, y_one)
    assert (maj.predict(X) == -1).all()
    assert (maj.predict_score(X) == 1.0).all()
    trend = TrendBaseline().fit(X, y_one)
    assert set(np.unique(trend.predict(X))).issubset({-1, 1})
    with pytest.raises(InformativeError, match="classes"):
        SklearnLogistic().fit(X, y_one)


def test_unfitted_raises() -> None:
    X, _, _ = _frame()
    for m in (TrendBaseline(), MajorityBaseline(), SklearnLogistic()):
        with pytest.raises(NotFittedError):
            m.predict(X)
        with pytest.raises(NotFittedError):
            m.predict_score(X)


def test_schema_mismatch_raises() -> None:
    X, y, cols = _frame()
    m = TrendBaseline().fit(X, y)
    with pytest.raises(SchemaMismatchError):
        m.predict(X[cols[::-1]])  # misordered
    with pytest.raises(SchemaMismatchError):
        m.predict(X[cols[:2]])  # missing columns
    with pytest.raises(SchemaMismatchError):
        m.predict(X.to_numpy()[:, :2])  # wrong width ndarray


def test_logistic_fit_predict_shapes() -> None:
    X, y, _ = _frame()
    m = SklearnLogistic(random_state=42).fit(X, y)
    pred = m.predict(X)
    assert pred.shape == (len(X),)
    assert set(np.unique(pred)).issubset({-1, 1})
    score = m.predict_score(X)
    assert np.all((score >= 0.0) & (score <= 1.0))
    # UNCALIBRATED: scores live in [0,1] but no calibration is claimed.


def test_scaler_fit_only_on_train() -> None:
    X, y, _ = _frame()
    Xtr, ytr = X.iloc[:100], y[:100]
    Xev = X.iloc[100:150] + 5.0  # large eval shift; scaler must ignore it
    m = SklearnLogistic(random_state=42).fit(Xtr, ytr)
    stats = m.scaler_stats()
    train_mean = Xtr.to_numpy(dtype=float).mean(axis=0)
    assert np.allclose(stats["mean"], train_mean)
    # Eval shift must not leak into stored stats, and transform must use
    # train-only stats (manual check against stored mean/scale).
    assert not np.allclose(stats["mean"], Xev.to_numpy(dtype=float).mean(axis=0))
    manual = (Xev.to_numpy(dtype=float) - np.asarray(stats["mean"])) / np.asarray(stats["scale"])
    assert np.allclose(m._scale(Xev), manual)


def test_n_jobs_single_threaded() -> None:
    m = SklearnLogistic()
    assert m.n_jobs == 1
    # n_jobs param deprecated since sklearn 1.8 (removal in 1.10); lbfgs is
    # single-threaded regardless. Enforce 1 while the parameter exists.
    assert m._estimator().get_params().get("n_jobs", 1) == 1


def test_save_load_roundtrip_preserves_predictions(tmp_path) -> None:
    from crypto_engine.models.baselines import MajorityBaseline as _Maj  # noqa: F401

    X, y, _ = _frame()
    for model in (
        TrendBaseline().fit(X, y),
        MajorityBaseline().fit(X, y),
        SklearnLogistic(random_state=42).fit(X, y),
    ):
        meta = {
            "dataset_snapshot_id": "test-snap",
            "dataset_checksum": "abc123",
            "label_config": {"pt_mult": 2.0},
            "label_hash": "lbl",
            "train_start": X.index[0].isoformat(),
            "train_end": X.index[-1].isoformat(),
        }
        d = save_model(model, meta, tmp_path / f"reg-{model.family}")
        loaded, _ = load_model(d)
        assert np.array_equal(loaded.predict(X), model.predict(X))
        assert np.allclose(loaded.predict_score(X), model.predict_score(X), atol=1e-9)
        assert loaded.feature_names_ == model.feature_names_
        assert feature_schema_hash(loaded.feature_names_ or []) == feature_schema_hash(
            model.feature_names_ or []
        )


def test_to_from_dict_roundtrip() -> None:
    X, y, _ = _frame()
    m = SklearnLogistic(random_state=42).fit(X, y)
    m2 = SklearnLogistic.from_dict(m.to_dict())
    assert np.array_equal(m2.predict(X), m.predict(X))
    t = TrendBaseline().fit(X, y)
    assert np.array_equal(TrendBaseline.from_dict(t.to_dict()).predict(X), t.predict(X))
    j = MajorityBaseline().fit(X, y)
    assert np.array_equal(MajorityBaseline.from_dict(j.to_dict()).predict(X), j.predict(X))


def test_lightgbm_supports_negative_and_three_class_labels() -> None:
    pytest.importorskip("lightgbm")
    from crypto_engine.models.sklearn_wrappers import LightGBMModel

    X, y_binary, _ = _frame(n=300, seed=71)
    binary_model = LightGBMModel(random_state=42, n_estimators=10).fit(X, y_binary)
    binary_predictions = binary_model.predict(X)
    assert set(np.unique(binary_predictions)).issubset({-1, 1})

    # A feature-derived third class exercises LightGBM's multiclass path with
    # non-zero-based labels: the estimator sees 0/1/2; callers see -1/0/+1.
    ratio = X["trend_ratio_20_50"].to_numpy()
    three_class = np.where(ratio > 0.002, 1, np.where(ratio < -0.002, -1, 0))
    assert set(np.unique(three_class)) == {-1, 0, 1}
    multiclass_model = LightGBMModel(random_state=42, n_estimators=10).fit(X, three_class)
    multiclass_predictions = multiclass_model.predict(X)
    assert set(np.unique(multiclass_predictions)).issubset({-1, 0, 1})
    restored = LightGBMModel.from_dict(multiclass_model.to_dict())
    assert np.array_equal(restored.predict(X), multiclass_predictions)
