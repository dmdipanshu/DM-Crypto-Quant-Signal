"""Unit tests for frozen-model forward validation (Phase 10, offline)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_engine.validation.forward import (
    build_usable_matrix,
    evaluate_forward,
    historical_majority,
    load_eval_frame,
    resolve_historical_snapshot_id,
    select_eval_rows,
    summarize_forward,
    verify_model_artifact,
    write_forward_report,
)
from tests.fixtures.model_train_fixtures import make_oscillating_snapshot_dir


def _small_frame(root: Path, n: int = 150, seed: int = 0) -> Path:
    """Create a fixture snapshot under pytest's managed ``tmp_path``."""
    return make_oscillating_snapshot_dir(root, n=n, seed=seed)


def _fit_logistic_on(X: pd.DataFrame, feature_names: list[str], y: np.ndarray, seed: int = 0):
    from crypto_engine.models.sklearn_wrappers import SklearnLogistic

    m = SklearnLogistic(random_state=seed)
    m.fit(
        np.asarray(X, dtype=float),
        np.asarray(y, dtype=np.int64).ravel(),
        feature_names=list(feature_names),
    )
    return m


def test_select_eval_rows_strict_cutoff() -> None:
    times = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-09-30T23:00:00Z"),
            pd.Timestamp("2026-10-01T00:00:00Z"),
            pd.Timestamp("2026-10-01T01:00:00Z"),
            pd.Timestamp("2026-10-02T00:00:00Z"),
        ]
    )
    pos, n_excluded, info = select_eval_rows(times, "2026-10-01T00:00:00Z")
    assert list(pos) == [2, 3]
    assert n_excluded == 2
    assert info["n_eligible"] == 2


def test_select_eval_rows_empty_is_actionable() -> None:
    times = pd.date_range("2026-09-01", periods=5, freq="h", tz="UTC")
    with pytest.raises(ValueError, match="no evaluation rows"):
        select_eval_rows(times, "2026-10-01T00:00:00Z")


def test_historical_snapshot_id_verified_or_rejected() -> None:
    """Reported provenance requires an artifact id the loaded manifest confirms."""
    # Agreeing record + manifest -> reported verbatim.
    assert resolve_historical_snapshot_id("snap-1", "abc", "snap-1", "abc") == "snap-1"
    # No recorded id -> explicit unknown, never invented from the manifest.
    assert resolve_historical_snapshot_id("", "", "snap-1", "abc") is None
    assert resolve_historical_snapshot_id(None, None, "snap-1", "abc") is None
    # Recorded id the manifest does not confirm -> rejected.
    with pytest.raises(ValueError, match="provenance mismatch"):
        resolve_historical_snapshot_id("snap-1", "abc", "snap-2", "abc")
    # Recorded id with no manifest id -> unverifiable, rejected.
    with pytest.raises(ValueError, match="has no id"):
        resolve_historical_snapshot_id("snap-1", "abc", "", "abc")
    # Checksum disagreement -> rejected.
    with pytest.raises(ValueError, match="checksum mismatch"):
        resolve_historical_snapshot_id("snap-1", "abc", "snap-1", "def")


def test_incomplete_future_labels_excluded(tmp_path: Path) -> None:
    snap = _small_frame(tmp_path / "storage", 300)
    ohlcv, _ = load_eval_frame(snap)
    from crypto_engine.labels.triple_barrier import TripleBarrierConfig

    X, _y, _obs, counts = build_usable_matrix(ohlcv, TripleBarrierConfig(), chunk_rows=5000)
    assert counts["incomplete_excluded"] > 0
    assert len(X) == counts["usable_rows"]
    assert X.index.is_monotonic_increasing


def test_schema_mismatch_rejected(tmp_path: Path) -> None:
    from crypto_engine.labels.triple_barrier import TripleBarrierConfig
    from crypto_engine.models.base import SchemaMismatchError

    snap = _small_frame(tmp_path / "storage", 300)
    ohlcv, _ = load_eval_frame(snap)
    X, y, _obs, _counts = build_usable_matrix(ohlcv, TripleBarrierConfig(), chunk_rows=5000)
    X = X.rename(columns={X.columns[0]: "not_a_feature"})
    model = _fit_logistic_on(X, list(X.columns), np.asarray(y))
    # Wrong schema vs the model's fitted names must fail loudly at predict.
    with pytest.raises(SchemaMismatchError):
        model.predict(X.rename(columns={X.columns[0]: "bogus"}))


def test_baseline_from_historical_train_only(tmp_path: Path) -> None:
    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=300)
    maj, counts, info = historical_majority(snap, {"side": "both"}, chunk_rows=5000)
    assert isinstance(maj, int)
    assert sum(counts.values()) == info["n_train"]
    assert info["n_train"] >= 10


def test_summarize_matches_hand_count() -> None:
    y_true = np.array([-1, -1, 0, 1, 1])
    y_pred = np.array([-1, 1, 0, 1, -1])
    out = summarize_forward(y_true, y_pred, majority_class=-1)
    assert out["accuracy"] == pytest.approx(3 / 5)
    assert out["majority_baseline_accuracy"] == pytest.approx(2 / 5)
    assert out["predicted_dist"] == {"-1": 2, "0": 1, "1": 2}
    assert out["realized_dist"] == {"-1": 2, "0": 1, "1": 2}
    assert out["confusion_matrix"] == [[1, 0, 1], [0, 1, 0], [1, 0, 1]]
    # Balanced accuracy = mean recall = (1/2 + 1 + 1/2) / 3.
    assert out["balanced_accuracy"] == pytest.approx((0.5 + 1.0 + 0.5) / 3)


def test_summarize_insufficient_is_actionable() -> None:
    with pytest.raises(ValueError, match="too small"):
        summarize_forward(np.array([1, -1]), np.array([1, 1]), majority_class=1)


def test_evaluate_forward_end_to_end(tmp_path: Path) -> None:
    """Full frozen-model forward run on two fixture snapshots (no tuning)."""
    import hashlib

    from crypto_engine.models.sklearn_wrappers import SklearnLogistic
    from crypto_engine.registry.store import save_model

    hist_snap = make_oscillating_snapshot_dir(tmp_path / "hist", n=400, seed=1)
    fwd_snap = make_oscillating_snapshot_dir(tmp_path / "fwd", n=250, seed=7)
    fwd_frame, _ = load_eval_frame(fwd_snap)
    eval_start = str((fwd_frame.index[5]).isoformat())

    from crypto_engine.datasets.snapshots import load_snapshot
    from crypto_engine.features.engineering import compute_features
    from crypto_engine.labels.triple_barrier import TripleBarrierConfig, valid_mask

    frame, hist_manifest = load_snapshot(hist_snap)
    ohlcv = frame.set_index(pd.to_datetime(frame["timestamp"], utc=True)).sort_index()
    feats = compute_features(ohlcv, chunk_rows=5000)
    num = [c for c in feats.columns if pd.api.types.is_numeric_dtype(feats[c])]
    finite = np.isfinite(feats[num].to_numpy(dtype=float)).all(axis=1)
    Xh = feats[num].loc[finite]
    from crypto_engine.labels.triple_barrier import label_triple_barrier

    labels = label_triple_barrier(ohlcv, TripleBarrierConfig())
    mask = valid_mask(labels)
    idx = Xh.index.intersection(labels.index[mask])
    Xh, yh = Xh.loc[idx], labels.loc[idx, "label"].astype(int).to_numpy()
    model = SklearnLogistic(random_state=42)
    model.fit(np.asarray(Xh), yh, feature_names=list(Xh.columns))
    reg = tmp_path / "registry"
    reg.mkdir()
    from crypto_engine.labels.triple_barrier import TripleBarrierConfig, label_config_hash

    _lc = TripleBarrierConfig()
    _lc_dict = _lc.model_dump(mode="json")
    model_dir = save_model(
        model,
        {
            "dataset_snapshot_id": hist_manifest.snapshot_id,
            "dataset_checksum": hist_manifest.checksum_sha256,
            "label_config": _lc_dict,
            "label_hash": label_config_hash(_lc),
            "train_start": "",
            "train_end": "",
        },
        reg,
    )
    before = hashlib.sha256((model_dir / "artifact.pkl").read_bytes()).hexdigest()

    report = evaluate_forward(hist_snap, fwd_snap, model_dir, eval_start)
    assert report["frozen_model"] is True
    assert report["metrics_forward"]["n"] > 0
    # Provenance is reported only after the artifact record and the loaded
    # historical manifest agree (F10-01 regression).
    assert report["historical_snapshot"]["snapshot_id"] == hist_manifest.snapshot_id
    # Eval rows are strictly after the cutoff (forward-only evidence).
    assert report["evaluation_window"]["eval_first_ts"] > eval_start
    assert set(report["metrics_forward"]["predicted_dist"]) <= {"-1", "0", "1"}
    after = hashlib.sha256((model_dir / "artifact.pkl").read_bytes()).hexdigest()
    assert before == after, "evaluation must not modify the artifact"

    # A record whose provenance disagrees with the loaded snapshot is rejected
    # before any report can claim a training dataset.
    bad_reg = tmp_path / "registry_bad"
    bad_reg.mkdir()
    bad_dir = save_model(
        model,
        {
            "dataset_snapshot_id": "some-other-snapshot",
            "dataset_checksum": hist_manifest.checksum_sha256,
            "label_config": _lc_dict,
            "label_hash": label_config_hash(_lc),
            "train_start": "",
            "train_end": "",
        },
        bad_reg,
    )
    with pytest.raises(ValueError, match="provenance mismatch"):
        evaluate_forward(hist_snap, fwd_snap, bad_dir, eval_start)


def test_artifact_unmodified_by_evaluation(tmp_path: Path) -> None:
    import hashlib

    from crypto_engine.registry.store import save_model

    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=300)
    ohlcv, _ = load_eval_frame(snap)
    from crypto_engine.labels.triple_barrier import TripleBarrierConfig

    X, y, obs, _counts = build_usable_matrix(ohlcv, TripleBarrierConfig(), chunk_rows=5000)
    model = _fit_logistic_on(X, list(X.columns), np.asarray(y))
    reg = tmp_path / "registry"
    reg.mkdir()
    from crypto_engine.registry.store import list_models

    assert list_models(reg) == []
    from crypto_engine.training.splits import chronological_split

    split = chronological_split(pd.DatetimeIndex(X.index), obs, train_frac=0.6, valid_frac=0.2)
    model.fit(
        X.iloc[split.train_idx],
        np.asarray(y)[np.asarray(split.train_idx, dtype=int)],
        feature_names=list(X.columns),
    )
    meta = {
        "dataset_snapshot_id": "fixture",
        "dataset_checksum": "abc",
        "label_config": {"side": "both"},
        "label_hash": "lbl",
        "train_start": "",
        "train_end": "",
    }
    model_dir = save_model(model, meta, reg)
    before = hashlib.sha256((model_dir / "artifact.pkl").read_bytes()).hexdigest()
    mtime_before = (model_dir / "artifact.pkl").stat().st_mtime_ns
    loaded, _record = verify_model_artifact(model_dir)
    assert loaded.is_fitted
    assert (model_dir / "artifact.pkl").stat().st_mtime_ns == mtime_before
    assert hashlib.sha256((model_dir / "artifact.pkl").read_bytes()).hexdigest() == before


def test_write_report_never_overwrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import crypto_engine.validation.forward as fw

    class _FrozenDT:
        @staticmethod
        def now(tz=None):
            import datetime as _dt

            return _dt.datetime(2026, 10, 28, 12, 0, 0, tzinfo=_dt.UTC)

    monkeypatch.setattr(fw, "datetime", _FrozenDT)
    report = {"forward_snapshot": {"snapshot_id": "s!nap"}, "n": 1}
    first = write_forward_report(report, tmp_path)
    assert first.is_file()
    assert first.name.startswith("forward_validation_s-nap_")
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_forward_report(report, tmp_path)
    assert first.is_file()
