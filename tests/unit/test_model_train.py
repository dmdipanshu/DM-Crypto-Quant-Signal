"""Unit tests for the Phase 8 training pipeline (offline, deterministic)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_engine.config.settings import load_settings
from crypto_engine.training.pipeline import TrainRequest, train_model
from tests.fixtures.model_train_fixtures import (
    make_monotone_snapshot_dir,
    make_oscillating_snapshot_dir,
)


def _settings():
    return load_settings()


def _request(tmp_path: Path, snap_dir: Path, **kw) -> TrainRequest:
    return TrainRequest(
        snapshot_dir=snap_dir,
        registry_root=tmp_path / "registry",
        train_frac=0.6,
        valid_frac=0.2,
        **kw,
    )


def test_train_end_to_end_reports_all_sections(tmp_path: Path) -> None:
    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)
    summary = train_model(_request(tmp_path, snap), _settings())
    for key in (
        "snapshot",
        "features",
        "labels",
        "split",
        "model",
        "validation_exploratory",
        "holdout_final",
        "artifact",
        "calibration",
        "disclaimer",
    ):
        assert key in summary, key
    assert summary["calibration"]["status"] == "uncalibrated"
    assert "profitab" in summary["disclaimer"]
    assert summary["artifact"]["reload_verified"] is True
    assert summary["split"]["train_rows"] >= 10
    assert summary["holdout_final"]["n"] >= 5
    # Majority baseline reported on identical splits.
    assert "majority_baseline" in summary["holdout_final"]
    assert summary["holdout_final"]["model"]["accuracy"] is not None


def test_train_uses_snapshot_manifest_provenance(tmp_path: Path) -> None:
    from crypto_engine.datasets.snapshots import load_snapshot

    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)
    _, manifest = load_snapshot(snap)
    summary = train_model(_request(tmp_path, snap), _settings())
    assert summary["snapshot"]["snapshot_id"] == manifest.snapshot_id
    assert summary["snapshot"]["checksum_sha256"] == manifest.checksum_sha256
    assert summary["artifact"]["label_hash"]
    assert summary["artifact"]["feature_schema_hash"]


def test_train_observability_at_cutoffs(tmp_path: Path) -> None:
    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)
    summary = train_model(_request(tmp_path, snap), _settings())
    # Every training label's outcome must be observable no later than the cutoff.
    assert summary["split"]["train_observable_max"] <= summary["split"]["train_cutoff"]
    assert summary["split"]["train_cutoff"] <= summary["split"]["valid_range"][0]
    # Purge/embargo accounting is explicit, never silent.
    assert summary["split"]["purged_rows"] >= 0
    assert summary["split"]["embargoed_rows"] >= 0


def test_single_class_snapshot_rejected(tmp_path: Path) -> None:
    snap = make_monotone_snapshot_dir(tmp_path / "storage", n=200)
    with pytest.raises(ValueError, match="single-class"):
        train_model(_request(tmp_path, snap), _settings())


def test_insufficient_snapshot_rejected(tmp_path: Path) -> None:
    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=8)
    with pytest.raises(ValueError, match=r"insufficient data|minimum|empty|usable"):
        train_model(_request(tmp_path, snap), _settings())


def test_malformed_snapshot_surfaced(tmp_path: Path) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "candles.parquet").write_bytes(b"not parquet")
    (bad / "manifest.json").write_text("{}")
    with pytest.raises(Exception) as excinfo:
        train_model(_request(tmp_path, bad), _settings())
    assert str(excinfo.value).strip(), "errors must carry an actionable message"


def test_training_only_scaling(tmp_path: Path) -> None:
    import json

    from crypto_engine.datasets.snapshots import load_snapshot
    from crypto_engine.features.engineering import compute_features
    from crypto_engine.labels.triple_barrier import label_triple_barrier, valid_mask
    from crypto_engine.training.splits import chronological_split

    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)
    summary = train_model(_request(tmp_path, snap), _settings())
    model_dir = Path(summary["artifact"]["model_dir"])
    record = json.loads((model_dir / "record.json").read_text(encoding="utf-8"))
    assert record["model_id"] == summary["artifact"]["model_id"]

    # Independently rebuild the training rows and verify the stored scaler
    # mean equals the *training-only* mean (not the pooled mean).
    frame, _ = load_snapshot(snap)
    ohlcv = frame.set_index(pd.to_datetime(frame["timestamp"], utc=True)).sort_index()
    feats = compute_features(ohlcv, chunk_rows=5000)
    names = summary["features"]["names"]
    assert names, "no features selected"
    finite = np.isfinite(feats[names].to_numpy(dtype=float)).all(axis=1)
    usable = feats[names].loc[finite]
    labels = label_triple_barrier(ohlcv)
    mask = valid_mask(labels)
    idx = usable.index.intersection(labels.index[mask])
    X = usable.loc[idx]
    split = chronological_split(
        pd.DatetimeIndex(X.index),
        pd.Series(pd.to_datetime(labels.loc[idx, "observable_at"], utc=True), index=X.index),
        train_frac=0.6,
        valid_frac=0.2,
        embargo_bars=int(_settings().validation.embargo_bars),
    )
    train_mean = X.to_numpy(dtype=float)[np.asarray(split.train_idx)][:, :4].mean(axis=0)
    pooled_mean = X.to_numpy(dtype=float)[:, :4].mean(axis=0)
    np.testing.assert_allclose(summary["model"]["scaler_mean_head"], train_mean, rtol=1e-9)
    assert not np.allclose(train_mean, pooled_mean), "fixture must distinguish train vs pooled"


def test_registry_no_overwrite_and_reload(tmp_path: Path) -> None:
    import json

    from crypto_engine.registry.store import load_model

    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)
    summary = train_model(_request(tmp_path, snap), _settings())
    model_dir = Path(summary["artifact"]["model_dir"])
    assert (model_dir / "record.json").is_file()
    assert (model_dir / "artifact.json").is_file() or any(model_dir.iterdir())
    record = json.loads((model_dir / "record.json").read_text(encoding="utf-8"))
    assert record["dataset_snapshot_id"] == summary["snapshot"]["snapshot_id"]
    assert record["label_hash"] == summary["artifact"]["label_hash"]
    assert record["status"] == "trained"
    model, reread = load_model(model_dir)
    assert model.is_fitted
    assert reread.artifact_sha256 == record["artifact_sha256"]


def test_no_overwrite_error_path_is_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import crypto_engine.registry.store as store

    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)

    def _boom(model, record_meta, registry_root):
        raise FileExistsError(f"Model already exists (no overwrite): {registry_root}/fake-id")

    monkeypatch.setattr(store, "save_model", _boom)
    with pytest.raises(FileExistsError, match="never"):
        train_model(_request(tmp_path, snap), _settings())


def test_snapshot_discovery_by_symbol_timeframe(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    make_oscillating_snapshot_dir(storage, n=400, symbol="BTC/USDT", timeframe="1h")
    req = TrainRequest(
        symbol="BTC/USDT",
        timeframe="1h",
        storage_root=storage,
        registry_root=tmp_path / "registry",
    )
    summary = train_model(req, _settings())
    assert summary["snapshot"]["symbol"] == "BTC/USDT"
    assert summary["snapshot"]["timeframe"] == "1h"


def test_snapshot_discovery_missing_is_actionable(tmp_path: Path) -> None:
    req = TrainRequest(
        symbol="NOPE/USDT",
        timeframe="1h",
        storage_root=tmp_path / "empty",
        registry_root=tmp_path / "registry",
    )
    with pytest.raises(FileNotFoundError, match="no snapshot matches"):
        train_model(req, _settings())


