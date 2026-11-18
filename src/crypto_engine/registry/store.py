"""Lightweight local model registry (Phase 2).

All artifacts are serialized with JSON only; pickle is no longer used.
Pickle can execute arbitrary code on load and is considered unsafe for
deserialization, so no artifact family uses it. Corrupt or tampered files
are rejected with :exc:`CorruptArtifactError` (checksum mismatch) before
deserialization where detectable; checksum verification only detects
accidental corruption/tampering of trusted files.

Conventions mirror Phase 1 snapshots: atomic writes (tmp file +
``os.replace``), deterministic ids, no overwrite of existing ids.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel as PydanticModel
from pydantic import Field

from crypto_engine import __version__ as package_version
from crypto_engine.models.base import (
    BaseModel,
    CorruptArtifactError,
    UntrainedModelError,
    feature_schema_hash,
    short_params_hash,
)

RECORD_FILENAME = "record.json"
JSON_ARTIFACT = "artifact.json"
PICKLE_ARTIFACT = "artifact.pkl"
#: Deprecated: kept for backward compatibility only. Pickle is no longer used;
#: all artifacts are JSON (see module docstring). Do not use for new code.

#: Families serialized as plain JSON (no pickle involved).
JSON_FAMILIES = ("trend", "majority")


class ModelRecord(PydanticModel):
    """Provenance + integrity manifest stored alongside each model."""

    model_id: str
    version: str = "0.1.0"
    family: str
    params: dict[str, Any] = Field(default_factory=dict)
    dataset_snapshot_id: str = ""
    dataset_checksum: str = ""
    feature_names: list[str] = Field(default_factory=list)
    feature_schema_hash: str = ""
    label_config: dict[str, Any] = Field(default_factory=dict)
    label_hash: str = ""
    train_start: str = ""
    train_end: str = ""
    created_at: str = ""
    random_state: int = 42
    package_version: str = package_version
    sklearn_version: str = "builtin"
    artifact_path: str = ""
    artifact_sha256: str = ""
    status: Literal["trained", "evaluated", "failed"] = "trained"
    interface: str = "predict/predict_score"
    limitations: str = ""
    known_incompatibilities: list[str] = Field(default_factory=list)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sklearn_version() -> str:
    try:
        import sklearn

        return str(sklearn.__version__)
    except ImportError:
        return "not-installed"


def make_model_id(family: str, params: dict[str, Any], dataset_checksum: str) -> str:
    """Deterministic id: ``family-shortparams-datasetsha[:8]-timestamp``."""
    short = short_params_hash(params)
    data = (dataset_checksum or "nodata")[:8]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    safe_family = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in family)
    return f"{safe_family}-{short}-{data}-{stamp}"


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(payload)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _serialize_model(model: BaseModel) -> tuple[str, bytes]:
    """Return ``(filename, payload)``; JSON for all model families."""
    payload = json.dumps(model.to_dict(), indent=2, sort_keys=True).encode("utf-8")
    return JSON_ARTIFACT, payload


def _deserialize_model(family: str, blob: bytes) -> BaseModel:
    from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline
    from crypto_engine.models.sklearn_wrappers import SklearnLogistic

    try:
        data = json.loads(blob.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CorruptArtifactError(f"JSON artifact decode failed: {exc}") from exc
    if not isinstance(data, dict):
        raise CorruptArtifactError("JSON artifact payload is not a model dict.")
    fam = str(data.get("family", family))
    if fam == "lightgbm":
        from crypto_engine.models.sklearn_wrappers import LightGBMModel

        return LightGBMModel.from_dict(data)
    if fam == "trend":
        return TrendBaseline.from_dict(data)
    if fam == "majority":
        return MajorityBaseline.from_dict(data)
    return SklearnLogistic.from_dict(data)


def save_model(
    model: BaseModel,
    record_meta: dict[str, Any],
    registry_root: Path | str,
) -> Path:
    """Persist a fitted model + ``record.json`` manifest atomically.

    Args:
        model: Fitted model (``is_fitted`` must be ``True``).
        record_meta: Provenance fields: ``dataset_snapshot_id``,
            ``dataset_checksum``, ``label_config``, ``label_hash``,
            ``train_start``/``train_end`` ISO strings, ``status``,
            ``limitations``, ``known_incompatibilities``, ``version``,
            ``label_config_hash`` (optional alias of ``label_hash``).
        registry_root: Registry base directory.

    Returns:
        Path to the created model directory.

    Raises:
        UntrainedModelError: If the model is not fitted.
        FileExistsError: If the deterministic model id dir already exists
            (no overwrite; pass a new registry root or bump provenance).
    """
    if not model.is_fitted:
        raise UntrainedModelError(
            f"Refusing to register unfitted {model.family} model; call fit() first."
        )
    meta = dict(record_meta)
    params = dict(model.to_dict().get("params", {}))
    dataset_checksum = str(meta.get("dataset_checksum", ""))
    model_id = make_model_id(model.family, params, dataset_checksum)
    model_dir = Path(registry_root) / model_id
    if model_dir.exists():
        raise FileExistsError(f"Model already exists (no overwrite): {model_dir}")

    feature_names = list(model.feature_names_ or [])
    artifact_name, payload = _serialize_model(model)
    digest = hashlib.sha256(payload).hexdigest()
    label_hash = str(meta.get("label_hash", meta.get("label_config_hash", "")))
    record = ModelRecord(
        model_id=model_id,
        version=str(meta.get("version", "0.1.0")),
        family=model.family,
        params=params,
        dataset_snapshot_id=str(meta.get("dataset_snapshot_id", "")),
        dataset_checksum=dataset_checksum,
        feature_names=feature_names,
        feature_schema_hash=feature_schema_hash(feature_names),
        label_config=dict(meta.get("label_config", {})),
        label_hash=label_hash,
        train_start=str(meta.get("train_start", "")),
        train_end=str(meta.get("train_end", "")),
        created_at=_utc_now_iso(),
        random_state=int(getattr(model, "random_state", 42)),
        package_version=package_version,
        sklearn_version=_sklearn_version() if model.family not in JSON_FAMILIES else "builtin",
        artifact_path=artifact_name,
        artifact_sha256=digest,
        status=meta.get("status", "trained"),
        limitations=str(meta.get("limitations", "Scores are UNCALIBRATED; ranking only.")),
        known_incompatibilities=list(meta.get("known_incompatibilities", [])),
    )

    model_dir.mkdir(parents=True, exist_ok=False)
    try:
        _atomic_write_bytes(model_dir / artifact_name, payload)
        _atomic_write_bytes(
            model_dir / RECORD_FILENAME, record.model_dump_json(indent=2).encode("utf-8")
        )
    except BaseException:
        for leftover in model_dir.glob("*.tmp"):
            with contextlib.suppress(OSError):
                leftover.unlink()
        raise
    return model_dir


def load_model(model_dir: Path | str) -> tuple[BaseModel, ModelRecord]:
    """Load ``(model, record)`` verifying checksum and fitted state.

    Raises:
        CorruptArtifactError: Missing files, checksum mismatch, schema
            drift between record and artifact, or undecodable payload.
        UntrainedModelError: Artifact round-trips but reports unfitted.
    """
    path = Path(model_dir)
    record_path = path / RECORD_FILENAME
    if not record_path.is_file():
        raise CorruptArtifactError(f"Missing record.json in {path}")
    try:
        record = ModelRecord.model_validate(json.loads(record_path.read_text(encoding="utf-8")))
    except ValueError as exc:
        raise CorruptArtifactError(f"record.json invalid in {path}: {exc}") from exc

    artifact_path = path / (record.artifact_path or JSON_ARTIFACT)
    if not artifact_path.is_file():
        raise CorruptArtifactError(f"Missing artifact {artifact_path} in {path}")
    blob = artifact_path.read_bytes()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != record.artifact_sha256:
        raise CorruptArtifactError(
            f"Checksum mismatch for {artifact_path}: expected {record.artifact_sha256}, "
            f"got {actual}."
        )
    model = _deserialize_model(record.family, blob)
    if not model.is_fitted:
        raise UntrainedModelError(f"Artifact in {path} reports is_fitted=False.")
    schema_now = feature_schema_hash(list(model.feature_names_ or []))
    if schema_now != record.feature_schema_hash:
        raise CorruptArtifactError(
            f"Feature schema drift in {path}: record={record.feature_schema_hash}, "
            f"artifact={schema_now}."
        )
    return model, record


def list_models(registry_root: Path | str) -> list[ModelRecord]:
    """List all valid manifests under ``registry_root`` (skips corrupt)."""
    base = Path(registry_root)
    records: list[ModelRecord] = []
    if not base.is_dir():
        return records
    for record_path in sorted(base.glob(f"*/{RECORD_FILENAME}")):
        try:
            records.append(
                ModelRecord.model_validate(json.loads(record_path.read_text(encoding="utf-8")))
            )
        except (OSError, ValueError):
            continue
    return records


__all__ = [
    "JSON_ARTIFACT",
    "JSON_FAMILIES",
    "PICKLE_ARTIFACT",
    "RECORD_FILENAME",
    "ModelRecord",
    "list_models",
    "load_model",
    "make_model_id",
    "save_model",
]
