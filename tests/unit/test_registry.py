"""Unit tests for the lightweight local model registry (offline)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from crypto_engine.features.engineering import compute_features
from crypto_engine.models.base import CorruptArtifactError, UntrainedModelError
from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline
from crypto_engine.models.sklearn_wrappers import SklearnLogistic
from crypto_engine.registry.store import RECORD_FILENAME, list_models, load_model, save_model
from tests.fixtures.features_fixture import make_ohlcv_frame


def _trained() -> tuple[pd.DataFrame, np.ndarray]:
    df = make_ohlcv_frame(n=200, seed=7)
    feats = compute_features(df).dropna()
    cols = ["ret_1", "trend_ratio_20_50", "rsi_14", "realized_vol_20"]
    X = feats[cols]
    y = np.where(X["trend_ratio_20_50"].to_numpy() > 0, 1, -1).astype(np.int64)
    return X, y


def _meta() -> dict:
    return {
        "dataset_snapshot_id": "snap-1",
        "dataset_checksum": "deadbeef1234",
        "label_config": {"pt_mult": 2.0, "sl_mult": 1.0},
        "label_hash": "lblhash",
        "train_start": "2024-01-01T00:00:00+00:00",
        "train_end": "2024-01-05T00:00:00+00:00",
        "limitations": "test artifact",
    }


def test_save_load_roundtrip_manifest_fields(tmp_path) -> None:
    X, y = _trained()
    model = SklearnLogistic(random_state=42).fit(X, y)
    d = save_model(model, _meta(), tmp_path / "reg")
    loaded, record = load_model(d)
    assert record.model_id == d.name
    assert record.family == "logistic"
    assert record.params["C"] == 1.0
    assert record.dataset_snapshot_id == "snap-1"
    assert record.dataset_checksum == "deadbeef1234"
    assert record.feature_names == list(X.columns)
    assert len(record.feature_schema_hash) == 64
    assert record.label_config == {"pt_mult": 2.0, "sl_mult": 1.0}
    assert record.label_hash == "lblhash"
    assert record.artifact_sha256 and len(record.artifact_sha256) == 64
    assert record.status == "trained"
    assert record.interface == "predict/predict_score"
    assert np.array_equal(loaded.predict(X), model.predict(X))


def test_rule_based_json_artifact(tmp_path) -> None:
    X, y = _trained()
    d = save_model(TrendBaseline().fit(X, y), _meta(), tmp_path / "reg")
    assert (d / "artifact.json").is_file()
    loaded, _ = load_model(d)
    assert isinstance(loaded, TrendBaseline)


def test_checksum_mismatch_detected(tmp_path) -> None:
    X, y = _trained()
    d = save_model(MajorityBaseline().fit(X, y), _meta(), tmp_path / "reg")
    art = d / "artifact.json"
    art.write_bytes(art.read_bytes() + b" ")
    with pytest.raises(CorruptArtifactError, match=r"[Cc]hecksum"):
        load_model(d)


def test_missing_artifact_detected(tmp_path) -> None:
    X, y = _trained()
    d = save_model(MajorityBaseline().fit(X, y), _meta(), tmp_path / "reg")
    (d / "artifact.json").unlink()
    with pytest.raises(CorruptArtifactError, match=r"[Mm]issing artifact"):
        load_model(d)


def test_missing_record_detected(tmp_path) -> None:
    X, y = _trained()
    d = save_model(MajorityBaseline().fit(X, y), _meta(), tmp_path / "reg")
    (d / RECORD_FILENAME).unlink()
    with pytest.raises(CorruptArtifactError, match=r"[Mm]issing record"):
        load_model(d)


def test_tampered_record_rejected(tmp_path) -> None:
    X, y = _trained()
    d = save_model(MajorityBaseline().fit(X, y), _meta(), tmp_path / "reg")
    art = d / "artifact.json"
    blob = bytearray(art.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    art.write_bytes(bytes(blob))
    with pytest.raises(CorruptArtifactError):
        load_model(d)


def test_no_overwrite_enforced(tmp_path, monkeypatch) -> None:
    import crypto_engine.registry.store as store

    X, y = _trained()
    root = tmp_path / "reg"
    model = MajorityBaseline().fit(X, y)
    monkeypatch.setattr(store, "make_model_id", lambda *a, **k: "fixed-id-0001")
    save_model(model, _meta(), root)
    with pytest.raises(FileExistsError):
        save_model(model, _meta(), root)


def test_untrained_rejected_on_save_and_load(tmp_path) -> None:
    X, _ = _trained()
    with pytest.raises(UntrainedModelError):
        save_model(TrendBaseline(), _meta(), tmp_path / "reg")
    # Craft an unfitted JSON artifact + matching record manually.
    import hashlib
    import json

    from crypto_engine.models.base import feature_schema_hash

    cols = list(X.columns)
    blob = json.dumps(TrendBaseline(feature_names=cols).to_dict()).encode()
    model_dir = tmp_path / "reg" / "trend-abc123-deadbeef-20240101T000000"
    model_dir.mkdir(parents=True)
    (model_dir / "artifact.json").write_bytes(blob)
    record = {
        "model_id": model_dir.name,
        "family": "trend",
        "params": {"ratio_col": "trend_ratio_20_50", "clip": 0.02},
        "feature_names": cols,
        "feature_schema_hash": feature_schema_hash(cols),
        "artifact_path": "artifact.json",
        "artifact_sha256": hashlib.sha256(blob).hexdigest(),
    }
    (model_dir / RECORD_FILENAME).write_text(json.dumps(record))
    with pytest.raises(UntrainedModelError):
        load_model(model_dir)


def test_list_models(tmp_path) -> None:
    X, y = _trained()
    root = tmp_path / "reg"
    save_model(TrendBaseline().fit(X, y), _meta(), root)
    save_model(MajorityBaseline().fit(X, y), {**_meta(), "dataset_checksum": "other"}, root)
    records = list_models(root)
    assert len(records) == 2
    assert {r.family for r in records} == {"trend", "majority"}
    assert list_models(tmp_path / "empty") == []

