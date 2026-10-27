"""No-learning and majority-class baselines (Phase 2).

Both implement :class:`BaseModel`. Scores are UNCALIBRATED (proba-like,
never claimed calibrated): suitable only for ranking/diagnostics, never
for position sizing or risk decisions.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from crypto_engine.models.base import BaseModel

#: Candidate momentum-ratio columns, in preference order. The canonical
#: Phase 2 feature is ``trend_ratio_20_50`` (see features/registry.py);
#: the legacy alias ``trend_sma_ratio_20_50`` is accepted for compat.
RATIO_CANDIDATES = ("trend_ratio_20_50", "trend_sma_ratio_20_50")


class TrendBaseline(BaseModel):
    """No-learning trend-following baseline.

    Predicts ``+1`` when the momentum ratio ``> 0`` else ``-1``. Performs
    no fitting: :meth:`fit` only records the feature schema (for schema
    validation at predict time) and the ratio column to use.

    Class imbalance: not applicable (no learning; always emits both signs
    depending on the ratio sign).
    """

    family: str = "trend"

    def __init__(
        self,
        feature_names: list[str] | None = None,
        random_state: int = 42,
        ratio_col: str = "trend_ratio_20_50",
        clip: float = 0.02,
    ) -> None:
        super().__init__(feature_names, random_state)
        self.ratio_col: str = ratio_col
        self.clip: float = float(clip)
        if self.clip <= 0:
            raise ValueError(f"clip must be > 0, got {clip!r}")

    def _ratio_index(self, columns: list[str]) -> int:
        if self.ratio_col in columns:
            return columns.index(self.ratio_col)
        for cand in RATIO_CANDIDATES:
            if cand in columns:
                return columns.index(cand)
        raise ValueError(
            f"TrendBaseline: no ratio column found; checked {(self.ratio_col, *RATIO_CANDIDATES)} against schema {columns}."
        )

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> TrendBaseline:
        _ = np.asarray(y)
        self._store_feature_names(X, feature_names)
        self._is_fitted = True
        return self

    def _ratio(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        arr, columns = self._as_columns(X)
        idx = self._ratio_index(columns)
        return arr[:, idx].astype(float)

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Sign of the momentum ratio: ``+1`` if ``> 0`` else ``-1``."""
        ratio = self._ratio(X)
        return np.where(np.nan_to_num(ratio, nan=0.0) > 0.0, 1, -1).astype(np.int64)

    def predict_score(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """UNCALIBRATED score: clipped ratio mapped to ``[0, 1]``.

        ``score = clip(ratio, -clip, +clip) / (2*clip) + 0.5``. This is a
        monotone rescaling for ranking only; it is NOT a calibrated
        probability and must not drive sizing or risk.
        """
        ratio = self._ratio(X)
        clipped = np.clip(np.nan_to_num(ratio, nan=0.0), -self.clip, self.clip)
        return (clipped / (2.0 * self.clip) + 0.5).astype(float)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "params": {"ratio_col": self.ratio_col, "clip": self.clip},
            "feature_names": list(self._feature_names or []),
            "random_state": self.random_state,
            "is_fitted": self._is_fitted,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TrendBaseline:
        params = dict(payload.get("params", {}))
        obj = cls(
            feature_names=list(payload.get("feature_names", [])) or None,
            random_state=int(payload.get("random_state", 42)),
            ratio_col=str(params.get("ratio_col", "trend_ratio_20_50")),
            clip=float(params.get("clip", 0.02)),
        )
        obj._is_fitted = bool(payload.get("is_fitted", False))
        return obj


class MajorityBaseline(BaseModel):
    """Majority-class benchmark classifier.

    :meth:`fit` records the majority class and the empirical class prior.
    :meth:`predict` always emits the majority class; :meth:`predict_score`
    returns the majority prior (UNCALIBRATED constant).

    Class imbalance: handled explicitly — the prior documents the skew and
    predictions collapse to the majority class. Single-class ``y`` is valid
    (majority = that class, prior = 1.0).
    """

    family: str = "majority"

    def __init__(self, feature_names: list[str] | None = None, random_state: int = 42) -> None:
        super().__init__(feature_names, random_state)
        self.classes_: list[int] = []
        self.majority_: int = 0
        self.prior_: float = 1.0

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> MajorityBaseline:
        arr = np.asarray(X)
        if arr.ndim != 2:
            raise ValueError(f"MajorityBaseline: expected 2D X, got shape {arr.shape}.")
        labels = np.asarray(y).ravel()
        if labels.size == 0:
            raise ValueError("MajorityBaseline: empty y.")
        self._store_feature_names(X, feature_names)
        classes, counts = np.unique(labels, return_counts=True)
        self.classes_ = [int(c) for c in classes]
        best = int(np.argmax(counts))
        self.majority_ = self.classes_[best]
        self.prior_ = float(counts[best] / counts.sum())
        self._is_fitted = True
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        self._require_fitted()
        self._check_schema(X)
        n = len(X) if isinstance(X, pd.DataFrame) else np.asarray(X).shape[0]
        return np.full(n, self.majority_, dtype=np.int64)

    def predict_score(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """UNCALIBRATED constant score = majority prior (not calibrated)."""
        self._require_fitted()
        self._check_schema(X)
        n = len(X) if isinstance(X, pd.DataFrame) else np.asarray(X).shape[0]
        return np.full(n, self.prior_, dtype=float)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "params": {},
            "feature_names": list(self._feature_names or []),
            "random_state": self.random_state,
            "is_fitted": self._is_fitted,
            "classes": list(self.classes_),
            "majority": int(self.majority_),
            "prior": float(self.prior_),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MajorityBaseline:
        obj = cls(
            feature_names=list(payload.get("feature_names", [])) or None,
            random_state=int(payload.get("random_state", 42)),
        )
        obj.classes_ = [int(c) for c in payload.get("classes", [])]
        obj.majority_ = int(payload.get("majority", 0))
        obj.prior_ = float(payload.get("prior", 1.0))
        obj._is_fitted = bool(payload.get("is_fitted", False))
        return obj


__all__ = ["RATIO_CANDIDATES", "MajorityBaseline", "TrendBaseline"]
