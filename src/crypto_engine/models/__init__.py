"""Model package: interfaces, baselines, sklearn wrappers."""

from __future__ import annotations

from crypto_engine.models.base import (
    BaseModel,
    CorruptArtifactError,
    InformativeError,
    ModelSpec,
    NotFittedError,
    OptionalDependencyError,
    SchemaMismatchError,
    UntrainedModelError,
    feature_schema_hash,
    short_params_hash,
)
from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline
from crypto_engine.models.sklearn_wrappers import LightGBMModel, SklearnLogistic

__all__ = [
    "BaseModel",
    "CorruptArtifactError",
    "InformativeError",
    "LightGBMModel",
    "MajorityBaseline",
    "ModelSpec",
    "NotFittedError",
    "OptionalDependencyError",
    "SchemaMismatchError",
    "SklearnLogistic",
    "TrendBaseline",
    "UntrainedModelError",
    "feature_schema_hash",
    "short_params_hash",
]
