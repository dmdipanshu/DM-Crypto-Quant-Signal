"""scikit-learn wrappers with causal scaling discipline (Phase 2).

``StandardScaler`` is fitted on training ``X`` only; eval data is
transformed with the stored mean/var and the scaler is never refit on
eval. All estimators run single-threaded (``n_jobs=1``) with a fixed
``random_state`` for determinism (4 GB laptop constraint).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from crypto_engine.models.base import (
    BaseModel,
    InformativeError,
    OptionalDependencyError,
)


class SklearnLogistic(BaseModel):
    """L2 logistic regression with train-only standardization.

    Defaults: ``C=1.0``, ``solver="lbfgs"``, ``max_iter=200``,
    ``n_jobs=1``. Deterministic given ``random_state``.
    """

    family: str = "logistic"

    def __init__(
        self,
        feature_names: list[str] | None = None,
        random_state: int = 42,
        C: float = 1.0,
        max_iter: int = 200,
        class_weight: str | dict[int, float] | None = None,
    ) -> None:
        super().__init__(feature_names, random_state)
        self.C: float = float(C)
        self.max_iter: int = int(max_iter)
        self.n_jobs: int = 1
        self.class_weight: str | dict[int, float] | None = class_weight
        self._scaler_mean: np.ndarray | None = None
        self._scaler_scale: np.ndarray | None = None
        self._classes: list[int] = []
        self._coef: np.ndarray | None = None
        self._intercept: np.ndarray | None = None

    def _estimator(self):  # type: ignore[no-untyped-def]
        import warnings

        from sklearn.linear_model import LogisticRegression

        kwargs: dict[str, Any] = {
            "C": self.C,
            "solver": "lbfgs",  # single-threaded solver; n_jobs has no effect
            "max_iter": self.max_iter,
            "random_state": self.random_state,
        }
        if self.class_weight is not None:
            kwargs["class_weight"] = self.class_weight
        try:
            with warnings.catch_warnings():
                # sklearn>=1.8 deprecates n_jobs (removal in 1.10); pin single
                # thread while the parameter still exists.
                warnings.simplefilter("ignore", FutureWarning)
                return LogisticRegression(**kwargs, n_jobs=self.n_jobs)
        except TypeError:
            return LogisticRegression(**kwargs)

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> SklearnLogistic:
        from sklearn.preprocessing import StandardScaler

        columns = self._store_feature_names(X, feature_names)
        arr = (
            X[columns].to_numpy(dtype=float)
            if isinstance(X, pd.DataFrame)
            else np.asarray(X, dtype=float)
        )
        if arr.ndim != 2:
            raise InformativeError(f"logistic: expected 2D X, got shape {arr.shape}.")
        labels = np.asarray(y).ravel()
        if labels.size != arr.shape[0]:
            raise InformativeError(f"logistic: X rows ({arr.shape[0]}) != y size ({labels.size}).")
        classes = [int(c) for c in np.unique(labels)]
        if len(classes) < 2:
            raise InformativeError(
                f"logistic: need >= 2 classes to fit; saw classes={classes} "
                f"(n={labels.size}). Use MajorityBaseline for single-class data."
            )
        scaler = StandardScaler()
        scaled = scaler.fit_transform(arr)
        est = self._estimator()
        import warnings

        with warnings.catch_warnings():
            # sklearn>=1.8 emits FutureWarning for n_jobs at fit time.
            warnings.simplefilter("ignore", FutureWarning)
            est.fit(scaled, labels)
        # Persist only plain arrays (no pickled callables in state dict).
        self._scaler_mean = np.asarray(scaler.mean_, dtype=float)
        self._scaler_scale = np.asarray(scaler.scale_, dtype=float)
        self._classes = [int(c) for c in est.classes_]
        coef = np.asarray(est.coef_, dtype=float)
        self._coef = coef.reshape(len(self._classes), -1) if coef.ndim == 1 else coef
        if self._coef.shape[0] == 1 and len(self._classes) == 2:
            # Binary lbfgs stores one row; keep as-is (predict maps via classes).
            pass
        self._intercept = np.asarray(est.intercept_, dtype=float).ravel()
        self._is_fitted = True
        return self

    def _scale(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        arr, _ = self._as_columns(X)
        assert self._scaler_mean is not None and self._scaler_scale is not None
        return (arr - self._scaler_mean) / self._scaler_scale

    def _decision(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        from scipy.special import expit

        z = self._scale(X)
        assert self._coef is not None and self._intercept is not None
        if len(self._classes) == 2:
            w = self._coef.reshape(-1)
            if w.shape[0] != z.shape[1]:
                # Binary coef stored per-class (reloaded); use positive-class row.
                w = self._coef[1]
            margin = z @ w + float(self._intercept.flat[0])
            return expit(margin)  # P(class == classes[1]); UNCALIBRATED
        margins = z @ self._coef.T + self._intercept
        shifted = margins - margins.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        return exp / exp.sum(axis=1, keepdims=True)

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Argmax over UNCALIBRATED scores mapped back to class labels."""
        self._require_fitted()
        out = self._decision(X)
        if out.ndim == 1:
            picked = (out >= 0.5).astype(int)
            return np.asarray([self._classes[i] for i in picked], dtype=np.int64)
        idx = np.argmax(out, axis=1)
        return np.asarray([self._classes[i] for i in idx], dtype=np.int64)

    def predict_score(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """UNCALIBRATED positive-class/marginal score in ``[0, 1]``.

        Raw logistic output without any calibration step; ranking-only,
        never a calibrated probability. Do not use for sizing or risk.
        """
        self._require_fitted()
        out = self._decision(X)
        if out.ndim == 1:
            return np.clip(out, 0.0, 1.0).astype(float)
        return np.clip(out.max(axis=1), 0.0, 1.0).astype(float)

    def scaler_stats(self) -> dict[str, list[float]]:
        """Return stored train-only scaler ``mean``/``scale`` (for tests)."""
        self._require_fitted()
        assert self._scaler_mean is not None and self._scaler_scale is not None
        return {
            "mean": list(map(float, self._scaler_mean)),
            "scale": list(map(float, self._scaler_scale)),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "params": {
                "C": self.C,
                "max_iter": self.max_iter,
                "n_jobs": self.n_jobs,
                "class_weight": self.class_weight,
            },
            "feature_names": list(self._feature_names or []),
            "random_state": self.random_state,
            "is_fitted": self._is_fitted,
            "classes": list(self._classes),
            "scaler_mean": None
            if self._scaler_mean is None
            else list(map(float, self._scaler_mean)),
            "scaler_scale": None
            if self._scaler_scale is None
            else list(map(float, self._scaler_scale)),
            "coef": None if self._coef is None else self._coef.tolist(),
            "intercept": None if self._intercept is None else list(map(float, self._intercept)),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SklearnLogistic:
        params = dict(payload.get("params", {}))
        obj = cls(
            feature_names=list(payload.get("feature_names", [])) or None,
            random_state=int(payload.get("random_state", 42)),
            C=float(params.get("C", 1.0)),
            max_iter=int(params.get("max_iter", 200)),
        )
        obj._classes = [int(c) for c in payload.get("classes", [])]
        mean = payload.get("scaler_mean")
        scale = payload.get("scaler_scale")
        obj._scaler_mean = None if mean is None else np.asarray(mean, dtype=float)
        obj._scaler_scale = None if scale is None else np.asarray(scale, dtype=float)
        coef = payload.get("coef")
        obj._coef = None if coef is None else np.asarray(coef, dtype=float)
        icept = payload.get("intercept")
        obj._intercept = None if icept is None else np.asarray(icept, dtype=float).ravel()
        obj._is_fitted = bool(payload.get("is_fitted", False))
        return obj


class LightGBMModel(BaseModel):
    """Optional LightGBM classifier (lazy import; core tests skip if absent).

    Bounds (4 GB laptop): ``n_estimators <= 100`` (default 50),
    ``num_leaves <= 31`` (default 15), ``min_child_samples >= 20``,
    ``n_jobs=1``, fixed ``random_state`` + ``deterministic=True``.
    """

    family: str = "lightgbm"

    def __init__(
        self,
        feature_names: list[str] | None = None,
        random_state: int = 42,
        n_estimators: int = 50,
        num_leaves: int = 15,
        min_child_samples: int = 20,
        class_weight: str | dict[int, float] | None = None,
    ) -> None:
        super().__init__(feature_names, random_state)
        if int(n_estimators) < 1 or int(n_estimators) > 100:
            raise ValueError(f"n_estimators must be within 1..100, got {n_estimators!r}")
        if int(num_leaves) < 2 or int(num_leaves) > 31:
            raise ValueError(f"num_leaves must be within 2..31, got {num_leaves!r}")
        if int(min_child_samples) < 20:
            raise ValueError(
                f"min_child_samples must be >= 20 (4GB guard), got {min_child_samples!r}"
            )
        self.n_estimators = int(n_estimators)
        self.num_leaves = int(num_leaves)
        self.min_child_samples = int(min_child_samples)
        self.n_jobs: int = 1
        self.class_weight: str | dict[int, float] | None = class_weight
        self._classes: list[int] = []
        self._booster_blob: str | None = None  # model_to_string payload

    @staticmethod
    def _require_lgb():  # type: ignore[no-untyped-def]
        try:
            import lightgbm as lgb
        except ImportError as exc:
            raise OptionalDependencyError(
                "LightGBM is not installed. Install the optional ML extras with "
                "`uv sync --extra ml` (adds lightgbm) or use SklearnLogistic."
            ) from exc
        return lgb

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> LightGBMModel:
        lgb = self._require_lgb()
        columns = self._store_feature_names(X, feature_names)
        arr = (
            X[columns].to_numpy(dtype=float)
            if isinstance(X, pd.DataFrame)
            else np.asarray(X, dtype=float)
        )
        raw_labels = np.asarray(y).ravel()
        if arr.ndim != 2:
            raise InformativeError(f"lightgbm: expected 2D X, got {arr.shape}.")
        if raw_labels.size != arr.shape[0]:
            raise InformativeError(
                f"lightgbm: X rows ({arr.shape[0]}) != y size ({raw_labels.size})."
            )
        try:
            numeric_labels = raw_labels.astype(float)
        except (TypeError, ValueError) as exc:
            raise InformativeError("lightgbm: class labels must be finite integers.") from exc
        if not np.isfinite(numeric_labels).all() or not np.equal(
            numeric_labels, np.trunc(numeric_labels)
        ).all():
            raise InformativeError("lightgbm: class labels must be finite integers.")
        classes_array = np.unique(numeric_labels).astype(np.int64)
        classes = [int(value) for value in classes_array]
        if len(classes) < 2:
            raise InformativeError(
                f"lightgbm: need >= 2 classes; saw classes={classes}. "
                "Use MajorityBaseline for single-class data."
            )
        # LightGBM multiclass does not accept a class_weight hyperparameter,
        # so we implement balanced training-fold-only class weighting as
        # per-sample weights. Weights are computed from the training fold's
        # class frequencies only, so no holdout information leaks in.
        encoded_labels = np.searchsorted(classes_array, numeric_labels).astype(np.int32)
        class_counts = np.bincount(encoded_labels.astype(np.int64))
        if str(self.class_weight).lower() == "balanced":
            n_total = float(class_counts.sum())
            k = float(class_counts.size)
            sample_weights = n_total / (k * class_counts[encoded_labels.astype(np.int64)])
        elif isinstance(self.class_weight, dict):
            weight_map = {
                int(c): float(self.class_weight[c]) for c in classes if c in self.class_weight
            }
            default_weight = 1.0
            sample_weights = np.asarray(
                [weight_map.get(int(c), default_weight) for c in encoded_labels],
                dtype=float,
            )
        else:
            sample_weights = np.ones(encoded_labels.shape[0], dtype=float)
        train = lgb.Dataset(
            arr,
            label=encoded_labels,
            weight=sample_weights,
        )
        params = {
            "objective": "multiclass" if len(classes) > 2 else "binary",
            "num_class": len(classes) if len(classes) > 2 else None,
            "num_leaves": self.num_leaves,
            "min_child_samples": self.min_child_samples,
            "verbose": -1,
            "num_threads": 1,
            "deterministic": True,
            "seed": self.random_state,
        }
        params = {k: v for k, v in params.items() if v is not None}
        booster = lgb.train(params, train, num_boost_round=self.n_estimators)
        self._classes = classes
        self._booster_blob = booster.model_to_string()
        self._is_fitted = True
        return self

    def _booster(self):  # type: ignore[no-untyped-def]
        lgb = self._require_lgb()
        self._require_fitted()
        if self._booster_blob is None:
            raise InformativeError("lightgbm: no stored booster; refit first.")
        import io

        with io.StringIO(self._booster_blob) as fh:
            return lgb.Booster(model_str=fh.read())

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        arr, _ = self._as_columns(X)
        proba = np.asarray(self._booster().predict(arr))
        if proba.ndim == 1:
            picked = (proba >= 0.5).astype(int)
            return np.asarray([self._classes[i] for i in picked], dtype=np.int64)
        idx = np.argmax(proba, axis=1)
        return np.asarray([self._classes[i] for i in idx], dtype=np.int64)

    def predict_score(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """UNCALIBRATED raw booster output in ``[0, 1]`` (not calibrated)."""
        arr, _ = self._as_columns(X)
        proba = np.asarray(self._booster().predict(arr), dtype=float)
        if proba.ndim == 1:
            return np.clip(proba, 0.0, 1.0)
        return np.clip(proba.max(axis=1), 0.0, 1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "params": {
                "n_estimators": self.n_estimators,
                "num_leaves": self.num_leaves,
                "min_child_samples": self.min_child_samples,
                "n_jobs": self.n_jobs,
                "class_weight": self.class_weight,
            },
            "feature_names": list(self._feature_names or []),
            "random_state": self.random_state,
            "is_fitted": self._is_fitted,
            "classes": list(self._classes),
            "booster": self._booster_blob,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LightGBMModel:
        params = dict(payload.get("params", {}))
        obj = cls(
            feature_names=list(payload.get("feature_names", [])) or None,
            random_state=int(payload.get("random_state", 42)),
            n_estimators=int(params.get("n_estimators", 50)),
            num_leaves=int(params.get("num_leaves", 15)),
            min_child_samples=int(params.get("min_child_samples", 20)),
        )
        obj._classes = [int(c) for c in payload.get("classes", [])]
        obj._booster_blob = payload.get("booster")
        obj._is_fitted = bool(payload.get("is_fitted", False))
        obj.class_weight = payload.get("params", {}).get("class_weight")
        return obj


__all__ = ["LightGBMModel", "SklearnLogistic"]