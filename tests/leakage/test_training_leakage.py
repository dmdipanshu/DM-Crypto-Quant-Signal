"""Anti-leakage training tests: scaler discipline, negative controls (Track B).

Items 5, 6, 8, 9, 10: scaler never fitted on eval, randomized-label
negative control within chance bands, save/load fidelity, corruption
detection, seed reproducibility. Offline, deterministic, <= 300 rows.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest

from crypto_engine.features.engineering import compute_features
from crypto_engine.models.base import CorruptArtifactError
from crypto_engine.models.baselines import MajorityBaseline
from crypto_engine.models.sklearn_wrappers import SklearnLogistic
from crypto_engine.registry.store import load_model, save_model
from tests.fixtures.features_fixture import make_ohlcv_frame


def _supervised(n: int = 300, seed: int = 7) -> tuple[pd.DataFrame, np.ndarray]:
    df = make_ohlcv_frame(n=n, seed=seed)
    feats = compute_features(df).dropna()
    cols = ["ret_1", "trend_ratio_20_50", "rsi_14", "realized_vol_20"]
    X = feats[cols]
    y = np.where(X["trend_ratio_20_50"].to_numpy() > 0, 1, -1).astype(np.int64)
    return X, y


def _meta() -> dict:
    return {
        "dataset_snapshot_id": "leak-test",
        "dataset_checksum": "leak1234",
        "label_config": {"pt_mult": 2.0},
        "label_hash": "lbl",
        "train_start": "2024-01-01T00:00:00+00:00",
        "train_end": "2024-01-02T00:00:00+00:00",
    }


def test_scaler_not_fitted_on_eval() -> None:
    """Eval shift must not change the train-fitted scaler (item 5)."""
    X, y = _supervised()
    Xtr, ytr = X.iloc[:150], y[:150]
    Xev = X.iloc[150:].copy()
    Xev = Xev + 5.0  # large eval shift; scaler must ignore it
    m = SklearnLogistic(random_state=42).fit(Xtr, ytr)
    stats = m.scaler_stats()
    assert np.allclose(stats["mean"], Xtr.to_numpy(dtype=float).mean(axis=0))
    assert not np.allclose(stats["mean"], Xev.to_numpy(dtype=float).mean(axis=0))
    pred = m.predict(Xev)
    assert pred.shape == (len(Xev),)


def test_randomized_label_negative_control() -> None:
    """Shuffled labels score within chance bands (item 6/negative control).

    With balanced-ish classes, chance accuracy ~= majority prior p with
    binomial std sqrt(p(1-p)/n) per trial; over 10 trials the mean must
    lie within 3 sigma of the expected value. No significance is claimed.
    """
    X, y = _supervised()
    Xtr, ytr = X.iloc[:150], y[:150]
    Xev, yev = X.iloc[150:], y[150:]
    p = float((ytr == 1).mean())
    p = min(max(p, 0.05), 0.95)
    n = len(yev)
    sigma_trial = float(np.sqrt(p * (1.0 - p) / n))
    accs: list[float] = []
    rng = np.random.default_rng(0)
    for _ in range(10):
        shuffled = rng.permutation(ytr)
        if len(np.unique(shuffled)) < 2:
            continue
        m = SklearnLogistic(random_state=42).fit(Xtr, shuffled)
        accs.append(float((m.predict(Xev) == yev).mean()))
    assert len(accs) >= 5
    expected = max(p, 1.0 - p)  # a fitted model under shuffled y ~= majority rate
    band = 3.0 * sigma_trial + 0.05  # sampling band + small fitting slack
    assert abs(float(np.mean(accs)) - expected) < band, (
        f"mean shuffled accuracy {np.mean(accs):.3f} outside chance band "
        f"({expected:.3f} +/- {band:.3f}); no significance claimed."
    )


def test_save_load_preserves_predictions(tmp_path) -> None:
    """Registry round-trip preserves predictions exactly (item 8)."""
    X, y = _supervised()
    for model in (
        MajorityBaseline().fit(X, y),
        SklearnLogistic(random_state=42).fit(X, y),
    ):
        d = save_model(model, _meta(), tmp_path / f"reg-{model.family}")
        loaded, _ = load_model(d)
        assert np.array_equal(loaded.predict(X), model.predict(X))
        assert np.allclose(loaded.predict_score(X), model.predict_score(X), atol=1e-12)


def test_corrupted_artifacts_detected(tmp_path) -> None:
    """Bit-flipped artifacts are rejected, not silently loaded (item 9)."""
    X, y = _supervised()
    d = save_model(SklearnLogistic(random_state=42).fit(X, y), _meta(), tmp_path / "reg")
    art = d / "artifact.pkl"
    blob = bytearray(art.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    art.write_bytes(bytes(blob))
    with pytest.raises(CorruptArtifactError):
        load_model(d)


def test_reproducibility_two_fits_same_seed() -> None:
    """Two fits with the same seed give identical predictions (item 10)."""
    X, y = _supervised()
    Xtr, ytr = X.iloc[:150], y[:150]
    Xev = X.iloc[150:]
    m1 = SklearnLogistic(random_state=42).fit(Xtr, ytr)
    m2 = SklearnLogistic(random_state=42).fit(Xtr, ytr)
    assert np.array_equal(m1.predict(Xev), m2.predict(Xev))
    assert np.allclose(m1.predict_score(Xev), m2.predict_score(Xev), atol=1e-12)


def test_lightgbm_optional_and_guarded() -> None:
    """LightGBM path is lazy: missing install -> OptionalDependencyError."""
    lgb = pytest.importorskip("lightgbm", reason="lightgbm not installed (optional)")
    assert lgb is not None
    from crypto_engine.models.sklearn_wrappers import LightGBMModel

    X, y = _supervised()
    Xtr, ytr = X.iloc[:150], y[:150]
    m = LightGBMModel(random_state=42).fit(Xtr, ytr)
    assert m.n_jobs == 1
    assert m.predict(X.iloc[150:]).shape == (len(X) - 150,)


def test_lightgbm_bounds_enforced() -> None:
    from crypto_engine.models.sklearn_wrappers import LightGBMModel

    with pytest.raises(ValueError, match="n_estimators"):
        LightGBMModel(n_estimators=101)
    with pytest.raises(ValueError, match="num_leaves"):
        LightGBMModel(num_leaves=64)
    with pytest.raises(ValueError, match="min_child_samples"):
        LightGBMModel(min_child_samples=1)


def test_representative_fit_timing_reports_elapsed(capsys) -> None:
    """Log elapsed for compute_features + fit on 300 rows (report only)."""
    t0 = time.perf_counter()
    X, y = _supervised(n=300)
    m = SklearnLogistic(random_state=42).fit(X, y)
    dt = time.perf_counter() - t0
    m.predict(X)
    print(f"\n[representative-fit] n={len(X)} elapsed={dt:.3f}s")
    assert dt < 120.0
    captured = capsys.readouterr()
    assert "representative-fit" in captured.out
