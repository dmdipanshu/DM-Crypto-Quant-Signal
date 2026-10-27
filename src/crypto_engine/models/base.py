"""Model interface: spec, abstract base, shared errors and schema helpers.

Phase 2, 4 GB laptop: single-process, ``n_jobs=1`` everywhere, small
fixtures, no copies of full matrices in hot paths (column views/slices).
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel as PydanticModel
from pydantic import Field


class NotFittedError(RuntimeError):
    """Raised when ``predict``/``predict_score`` is called before ``fit``."""


class SchemaMismatchError(ValueError):
    """Raised when predict-time columns differ from the fitted schema."""


class InformativeError(ValueError):
    """Raised for data problems that need an actionable message (e.g. classes)."""


class OptionalDependencyError(ImportError):
    """Raised when an optional backend (e.g. LightGBM) is not installed."""


class CorruptArtifactError(ValueError):
    """Raised when a stored artifact fails checksum or structural checks."""


class UntrainedModelError(RuntimeError):
    """Raised when a loaded artifact reports ``is_fitted is False``."""


class ModelSpec(PydanticModel):
    """Static description of a model family + its fitted schema."""

    family: str
    params: dict[str, Any] = Field(default_factory=dict)
    feature_names: list[str] = Field(default_factory=list)
    label_config_hash: str = ""
    random_state: int = 42


def feature_schema_hash(feature_names: list[str]) -> str:
    """Deterministic sha256 over the ordered feature-name list."""
    payload = json.dumps(list(feature_names), sort_keys=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def short_params_hash(params: dict[str, Any], length: int = 6) -> str:
    """Short deterministic hash of sorted params JSON (for model ids)."""
    payload = json.dumps(params, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


class BaseModel(ABC):
    """Abstract model interface for Phase 2 baselines.

    Contract:

    * :meth:`fit` stores ``feature_names`` and sets :attr:`is_fitted`.
    * :meth:`predict` returns integer labels; raises :class:`NotFittedError`
      before fit and :class:`SchemaMismatchError` on column mismatch.
    * :meth:`predict_score` returns UNCALIBRATED float scores in ``[0, 1]``
      (proba-like, never claimed calibrated; do not use for sizing/risk).
    * :meth:`to_dict` / :meth:`from_dict` round-trip params only
      (never pickle raw callables).
    """

    family: str = "base"

    def __init__(self, feature_names: list[str] | None = None, random_state: int = 42) -> None:
        self._feature_names: list[str] | None = list(feature_names) if feature_names else None
        self.random_state: int = int(random_state)
        self._is_fitted: bool = False

    @property
    def is_fitted(self) -> bool:
        """Whether :meth:`fit` has completed successfully."""
        return self._is_fitted

    @property
    def feature_names_(self) -> list[str] | None:
        """Fitted feature schema (``None`` before :meth:`fit`)."""
        return list(self._feature_names) if self._feature_names is not None else None

    @abstractmethod
    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> BaseModel:
        """Fit on training data only; return ``self``."""

    @abstractmethod
    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Predict integer labels (raises if unfitted or schema mismatches)."""

    @abstractmethod
    def predict_score(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Return UNCALIBRATED scores in ``[0, 1]`` (not calibrated)."""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serialize params + fitted state to a JSON-safe dict."""

    @classmethod
    @abstractmethod
    def from_dict(cls, payload: dict[str, Any]) -> BaseModel:
        """Rebuild a model from :meth:`to_dict` output."""

    def spec(self, label_config_hash: str = "") -> ModelSpec:
        """Return a :class:`ModelSpec` for this (possibly fitted) model."""
        return ModelSpec(
            family=self.family,
            params=self.to_dict().get("params", {}),
            feature_names=list(self._feature_names or []),
            label_config_hash=label_config_hash,
            random_state=self.random_state,
        )

    # -- shared helpers -------------------------------------------------
    def _store_feature_names(
        self, X: pd.DataFrame | np.ndarray, feature_names: list[str] | None
    ) -> list[str]:
        """Resolve and store the fitted column schema."""
        if isinstance(X, pd.DataFrame):
            resolved = list(feature_names) if feature_names else list(X.columns)
        elif feature_names:
            resolved = list(feature_names)
        elif self._feature_names:
            resolved = list(self._feature_names)
        else:
            raise InformativeError(
                "ndarray X requires explicit feature_names "
                "(fitted schema unknown); pass feature_names to fit()."
            )
        self._feature_names = resolved
        return resolved

    def _require_fitted(self) -> None:
        if not self._is_fitted:
            raise NotFittedError(f"{self.family}: predict called before fit; call fit() first.")

    def _check_schema(self, X: pd.DataFrame | np.ndarray) -> None:
        """Reject missing/misordered columns with :class:`SchemaMismatchError`."""
        if self._feature_names is None:
            raise NotFittedError(f"{self.family}: no fitted schema; call fit() first.")
        if isinstance(X, pd.DataFrame):
            got = list(X.columns)
            if got != self._feature_names:
                raise SchemaMismatchError(
                    f"{self.family}: schema mismatch: expected {self._feature_names}, got {got}."
                )
        else:
            arr = np.asarray(X)
            if arr.ndim != 2 or arr.shape[1] != len(self._feature_names):
                raise SchemaMismatchError(
                    f"{self.family}: expected 2D array with {len(self._feature_names)} "
                    f"columns {self._feature_names}, got shape {arr.shape}."
                )

    def _as_columns(self, X: pd.DataFrame | np.ndarray) -> tuple[np.ndarray, list[str]]:
        """Return ``(2D float array, column names)``; validates schema."""
        self._require_fitted()
        self._check_schema(X)
        assert self._feature_names is not None
        if isinstance(X, pd.DataFrame):
            return X[self._feature_names].to_numpy(dtype=float), self._feature_names
        return np.asarray(X, dtype=float), self._feature_names


__all__ = [
    "BaseModel",
    "CorruptArtifactError",
    "InformativeError",
    "ModelSpec",
    "NotFittedError",
    "OptionalDependencyError",
    "SchemaMismatchError",
    "UntrainedModelError",
    "feature_schema_hash",
    "short_params_hash",
]
