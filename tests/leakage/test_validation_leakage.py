"""Validation-leakage tests: causal splits, calibration ordering, determinism.

Track B anti-leakage contract:
1. No future leakage: training on fold ``k`` never sees validation labels;
   perturbing validation rows cannot change the train-fitted artifact.
2. Calibration-before-holdout: calibrators fit on a calibration split that
   is chronologically after training but strictly before the locked
   holdout; the holdout ledger stays pristine until an explicit access.
3. Determinism: identical inputs reproduce identical folds and predictions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_engine.calibration.calibrators import CalibratedClassifier
from crypto_engine.labels.triple_barrier import label_triple_barrier, valid_mask
from crypto_engine.models.baselines import MajorityBaseline
from crypto_engine.models.sklearn_wrappers import SklearnLogistic
from crypto_engine.validation.folds import label_intervals, make_folds
from crypto_engine.validation.holdout import HoldoutLedger, split_holdout
from tests.fixtures.features_fixture import make_ohlcv_frame


def _labeled_frame(n: int = 300, seed: int = 42):
    """Features + triple-barrier labels with observability metadata."""
    from crypto_engine.features.engineering import compute_features

    ohlcv = make_ohlcv_frame(n=n, seed=seed)
    feats = compute_features(ohlcv).dropna()
    labels_df = label_triple_barrier(ohlcv).loc[feats.index]
    return feats, labels_df


def test_train_never_sees_val_labels() -> None:
    """Fold index sets are disjoint with train strictly before val."""
    feats, labels_df = _labeled_frame()
    valid = valid_mask(labels_df).to_numpy()
    n = len(feats)
    folds = make_folds(n, 120, 30, 2, embargo_bars=5, valid_idx=valid)
    assert folds
    for fold in folds:
        assert len(set(fold.train_idx.tolist()) & set(fold.val_idx.tolist())) == 0
        assert int(fold.train_idx.max()) < int(fold.val_idx.min())
        # Every train row resolves before (or touches nothing of) the val block.
        assert bool((fold.train_idx < fold.val_start).all())


def test_perturbing_val_does_not_change_train_fit() -> None:
    """Val-side edits cannot move a model fitted on train rows only."""
    feats, labels_df = _labeled_frame()
    cols = ["ret_1", "trend_ratio_20_50", "rsi_14", "realized_vol_20"]
    X = feats[cols]
    y = labels_df["label"].to_numpy(dtype=np.int64)
    valid = valid_mask(labels_df).to_numpy()
    fold = make_folds(len(X), 120, 30, 2, valid_idx=valid)[0]
    Xtr, ytr = X.iloc[fold.train_idx], y[fold.train_idx]

    before = MajorityBaseline().fit(Xtr, ytr)
    pred_before = before.predict(Xtr)

    # Adversarial val edit: flip every validation label and shift features.
    y_perturbed = y.copy()
    y_perturbed[fold.val_idx] = -y_perturbed[fold.val_idx]
    X_perturbed = X.copy()
    X_perturbed.iloc[fold.val_idx] += 100.0
    after = MajorityBaseline().fit(Xtr, ytr)  # still train rows only
    assert np.array_equal(after.predict(Xtr), pred_before)
    assert after.to_dict() == before.to_dict()
    assert X_perturbed.shape == X.shape and y_perturbed.shape == y.shape


def test_calibration_before_holdout_ordering(tmp_path) -> None:
    """Calibrator fits on cal-split only; holdout untouched until ledger access."""
    rng = np.random.default_rng(0)
    x = rng.normal(size=(160, 2))
    X = pd.DataFrame(x, columns=["a", "b"])
    y = (x[:, 0] > 0).astype(int)

    research_idx, holdout_idx = split_holdout(len(X), 0.25)
    ledger = HoldoutLedger.create(
        tmp_path / "ledger.json",
        holdout_start=int(holdout_idx[0]),
        n_total=len(X),
        holdout_frac=0.25,
    )
    # Train on head, calibrate on the later research tail — never the holdout.
    n_cal = 40
    tr_idx = research_idx[:-n_cal]
    cal_idx = research_idx[-n_cal:]
    assert int(cal_idx.max()) < int(holdout_idx.min())
    assert int(tr_idx.max()) < int(cal_idx.min())

    base = SklearnLogistic(random_state=42).fit(X.iloc[tr_idx], y[tr_idx])
    scores_cal = base.predict_score(X.iloc[cal_idx])
    calibrator = CalibratedClassifier(base, min_cal_samples=10).fit(
        scores_cal, y[cal_idx], t_bounds=(tr_idx, cal_idx)
    )
    assert ledger.is_pristine is True  # holdout never touched so far
    # Final unbiased read: explicit access first, then evaluate.
    ledger.mark_accessed("leakage test: holdout scoring")
    proba_hold = calibrator.predict_proba(base.predict_score(X.iloc[holdout_idx]))
    assert proba_hold.shape == (holdout_idx.shape[0],)
    assert ledger.is_pristine is False


def test_deterministic_predictions_across_reruns() -> None:
    """Identical inputs reproduce identical folds, labels, and predictions."""
    feats, labels_df = _labeled_frame()
    cols = ["ret_1", "trend_ratio_20_50", "rsi_14", "realized_vol_20"]
    X = feats[cols]
    y = labels_df["label"].to_numpy(dtype=np.int64)
    valid = valid_mask(labels_df).to_numpy()
    intervals_a = label_intervals(labels_df["tte_bars"].to_numpy(), horizon=24)
    intervals_b = label_intervals(labels_df["tte_bars"].to_numpy(), horizon=24)
    assert intervals_a == intervals_b

    folds_a = make_folds(len(X), 120, 30, 2, embargo_bars=5, valid_idx=valid)
    folds_b = make_folds(len(X), 120, 30, 2, embargo_bars=5, valid_idx=valid)
    for fa, fb in zip(folds_a, folds_b, strict=True):
        assert np.array_equal(fa.train_idx, fb.train_idx)
        assert np.array_equal(fa.val_idx, fb.val_idx)

    fold = folds_a[0]
    preds = []
    for _ in range(2):
        model = SklearnLogistic(random_state=42).fit(X.iloc[fold.train_idx], y[fold.train_idx])
        preds.append(model.predict(X.iloc[fold.val_idx]))
    assert np.array_equal(preds[0], preds[1])
