"""Platt-scaling / isotonic calibration over UNCALIBRATED base-model scores.

Event definition
----------------
The calibrated output estimates ``P(y == positive_class)`` where, for binary
base models, ``positive_class`` is the base model's ``classes[1]`` (the same
convention as ``SklearnLogistic.predict_score``, which returns
``P(class == classes[1])``). Multiclass base models are rejected: binary
only (one-vs-rest wrapping is out of scope for Phase 3).

Limitations
-----------
* The mapping is calibrated only with respect to the calibration sample; a
  good fit on ``(scores_cal, y_cal)`` is not a guarantee of future
  calibration (regime shift, drift, and small-sample noise all apply).
* Isotonic regression is a flexible non-parametric fit: it needs materially
  more calibration samples than Platt scaling to avoid overfitting the
  calibration split (rule of thumb: hundreds of samples; ``min_cal_samples``
  is a floor, not a sufficiency claim).
* The caller must supply scores from a CHRONOLOGICALLY LATER split than the
  base-model training data. ``fit`` enforces this only when ``t_bounds`` is
  given (``min(t_cal) > max(t_train)`` required); pass it whenever timestamps
  are available.

4 GB laptop: single-threaded sklearn estimators, no matrix copies beyond a
1D score view, deterministic given fixed inputs (isotonic is deterministic;
Platt uses ``lbfgs`` with a fixed ``random_state``).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from crypto_engine.models.base import (
    BaseModel,
    CorruptArtifactError,
    InformativeError,
    NotFittedError,
    UntrainedModelError,
    feature_schema_hash,
)

_CALIBRATOR_NAME = "CalibratedClassifier"
_SCHEMA_VERSION = "cal-v1"


def _sklearn_version() -> str:
    import sklearn

    return str(sklearn.__version__)


def _as_1d_float(values: Any, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float).ravel()
    return arr


def _check_no_nan(arr: np.ndarray, name: str) -> None:
    if arr.size and bool(np.isnan(arr).any()):
        raise InformativeError(f"calibration: {name} contains NaN; reject (no silent fills).")


def _bound_value(bound: Any) -> tuple[float, float]:
    """Reduce one side of ``t_bounds`` to ``(min, max)`` floats when possible."""
    arr = np.asarray(bound)
    if arr.size == 0:
        raise InformativeError("calibration: t_bounds side is empty; cannot verify ordering.")
    try:
        flat = arr.ravel().astype(float)
    except (TypeError, ValueError) as exc:
        raise InformativeError(
            "calibration: t_bounds entries must be numeric timestamps "
            f"(got dtype {arr.dtype}); pass epoch numbers."
        ) from exc
    return float(flat.min()), float(flat.max())


def _check_time_ordering(t_bounds: tuple[Any, Any]) -> tuple[list[float], list[float]]:
    """Enforce calibration timestamps strictly after training timestamps.

    ``t_bounds`` is ``(t_train, t_cal)`` where each side is either a scalar
    (``t_train_max`` / ``t_cal_min``) or an array of timestamps. Requires
    ``min(t_cal) > max(t_train)``; raises :class:`InformativeError` on
    overlap (train/cal overlap would leak in-sample information into the
    calibration map).
    """
    if not isinstance(t_bounds, tuple) or len(t_bounds) != 2:
        raise InformativeError(
            "calibration: t_bounds must be a (t_train, t_cal) tuple of "
            "timestamp scalars/arrays; got "
            f"{type(t_bounds).__name__}."
        )
    t_train, t_cal = t_bounds
    cal_min, cal_max = _bound_value(t_cal)
    train_min, train_max = _bound_value(t_train)
    if not cal_min > train_max:
        raise InformativeError(
            "calibration: calibration timestamps must be strictly later than "
            f"training timestamps (min(t_cal)={cal_min} <= max(t_train)={train_max}); "
            "refit with a chronologically later calibration split."
        )
    return [train_min, train_max], [cal_min, cal_max]


def _base_classes(base: BaseModel) -> list[int]:
    try:
        payload = base.to_dict()
    except Exception:
        return []
    classes = payload.get("classes", [])
    try:
        return [int(c) for c in classes]
    except (TypeError, ValueError):
        return []


class CalibratedClassifier:
    """Map UNCALIBRATED base-model scores to calibrated ``P(event)``.

    NOT a :class:`BaseModel` subclass: it wraps any fitted base model
    exposing ``predict_score``/``to_dict``/``is_fitted`` and learns a 1D
    score-to-probability map on a held-out calibration split.
    """

    is_calibrated = True

    def __init__(
        self,
        base_model: BaseModel,
        method: str = "sigmoid",
        min_cal_samples: int = 20,
        positive_class: int | None = None,
        model_version: str = "",
        dataset_snapshot: str = "",
        random_state: int = 42,
    ) -> None:
        """Bind a base model; call :meth:`fit` before predicting.

        Args:
            base_model: Fitted binary base model (must expose ``predict_score``).
            method: ``"sigmoid"`` (Platt scaling) or ``"isotonic"``.
            min_cal_samples: Floor on calibration rows; smaller samples raise
                instead of fitting a noisy map.
            positive_class: Event label; defaults to the base model's
                ``classes[1]`` resolved at :meth:`fit` time.
            model_version: Provenance tag recorded in :meth:`to_dict`.
            dataset_snapshot: Provenance tag recorded in :meth:`to_dict`.
            random_state: Seed for the Platt logistic fit (determinism).
        """
        if method not in ("sigmoid", "isotonic"):
            raise InformativeError(
                f"calibration: unknown method {method!r}; use 'sigmoid' or 'isotonic'."
            )
        if int(min_cal_samples) < 2:
            raise InformativeError(
                f"calibration: min_cal_samples must be >= 2 (got {min_cal_samples!r})."
            )
        self.base_model = base_model
        self.method: str = method
        self.min_cal_samples: int = int(min_cal_samples)
        self.positive_class: int | None = None if positive_class is None else int(positive_class)
        self.model_version: str = str(model_version)
        self.dataset_snapshot: str = str(dataset_snapshot)
        self.random_state: int = int(random_state)
        self._is_fitted: bool = False
        self._negative_class: int | None = None
        self._coef: float | None = None
        self._intercept: float | None = None
        self._iso_x: np.ndarray | None = None
        self._iso_y: np.ndarray | None = None
        self._train_range: list[float] | None = None
        self._cal_range: list[float] | None = None
        self._model_family: str = ""
        self._feature_names: list[str] = []
        self._sklearn_version: str = _sklearn_version()

    @property
    def is_fitted(self) -> bool:
        """Whether :meth:`fit` has completed successfully."""
        return self._is_fitted

    def _require_base_fitted(self) -> None:
        fitted = bool(getattr(self.base_model, "is_fitted", False))
        if not fitted:
            raise UntrainedModelError(
                "calibration: base model is not fitted; fit the base model "
                "on training data before calibrating."
            )

    def fit(
        self,
        scores_cal: np.ndarray,
        y_cal: np.ndarray,
        positive_class: int | None = None,
        t_bounds: tuple[Any, Any] | None = None,
    ) -> CalibratedClassifier:
        """Learn the score-to-probability map on a held-out calibration split.

        Args:
            scores_cal: 1D UNCALIBRATED base-model scores for the calibration
                split (later in time than base-model training rows).
            y_cal: 1D class labels for the calibration split (same length).
            positive_class: Event label override; defaults to the
                constructor value, else the base model's ``classes[1]``.
            t_bounds: Optional ``(t_train, t_cal)`` timestamp scalars/arrays;
                when given, ``min(t_cal) > max(t_train)`` is enforced.

        Raises:
            UntrainedModelError: Base model is not fitted.
            InformativeError: Multiclass data, ``< min_cal_samples`` rows,
                single-class ``y_cal``, NaN scores, or time overlap.
        """
        self._require_base_fitted()
        scores = _as_1d_float(scores_cal, "scores_cal")
        labels = np.asarray(y_cal).ravel()
        if scores.shape[0] != labels.shape[0]:
            raise InformativeError(
                "calibration: scores_cal "
                f"(n={scores.shape[0]}) and y_cal (n={labels.shape[0]}) differ in length."
            )
        _check_no_nan(scores, "scores_cal")
        if labels.size == 0:
            raise InformativeError("calibration: y_cal is empty; nothing to fit.")
        if np.isnan(np.asarray(labels, dtype=float)).any():
            raise InformativeError("calibration: y_cal contains NaN; reject.")

        base_classes = _base_classes(self.base_model)
        if len(base_classes) > 2:
            raise InformativeError(
                f"calibration: binary only; base model reports classes={base_classes}. "
                "One-vs-rest multiclass wrapping is out of scope for Phase 3."
            )
        seen = [int(c) for c in np.unique(labels)]
        if len(seen) > 2:
            raise InformativeError(
                f"calibration: binary only; y_cal has classes={seen}. "
                "One-vs-rest multiclass wrapping is out of scope for Phase 3."
            )

        resolved = (
            int(positive_class)
            if positive_class is not None
            else (self.positive_class if self.positive_class is not None else None)
        )
        if resolved is None:
            if len(base_classes) == 2:
                resolved = int(base_classes[1])
            elif len(seen) == 2:
                resolved = int(sorted(seen)[1])
            else:
                raise InformativeError(
                    "calibration: cannot resolve positive_class (need base "
                    "classes or 2-class y_cal); pass positive_class explicitly."
                )
        if len(base_classes) == 2 and resolved != int(base_classes[1]):
            raise InformativeError(
                f"calibration: positive_class={resolved} disagrees with base "
                f"model classes[1]={base_classes[1]}; event must be P(y == classes[1])."
            )
        self.positive_class = int(resolved)
        negatives = [c for c in seen if c != self.positive_class]
        if len(base_classes) == 2:
            self._negative_class = int(base_classes[0])
        else:
            self._negative_class = int(negatives[0]) if negatives else int(1 - self.positive_class)

        y_event = (labels == self.positive_class).astype(int)
        if int(y_event.sum()) == 0 or int(y_event.sum()) == int(y_event.size):
            raise InformativeError(
                f"calibration: y_cal has a single class for event={self.positive_class} "
                f"(classes={seen}, n={labels.size}); calibration needs both outcomes."
            )
        if int(labels.size) < self.min_cal_samples:
            raise InformativeError(
                f"calibration: only n={labels.size} calibration rows, below "
                f"min_cal_samples={self.min_cal_samples}; collect a larger "
                "chronologically-later split instead of fitting a noisy map."
            )

        if t_bounds is not None:
            self._train_range, self._cal_range = _check_time_ordering(t_bounds)
        else:
            self._train_range, self._cal_range = None, None

        try:
            fam = self.base_model.to_dict().get("family", "")
            self._model_family = str(fam)
        except Exception:
            self._model_family = type(self.base_model).__name__
        fnames = list(getattr(self.base_model, "feature_names_", None) or [])
        if not fnames:
            try:
                fnames = list(self.base_model.to_dict().get("feature_names", []) or [])
            except Exception:
                fnames = []
        self._feature_names = [str(c) for c in fnames]

        if self.method == "sigmoid":
            self._fit_sigmoid(scores, y_event)
        else:
            self._fit_isotonic(scores, y_event)
        self._is_fitted = True
        return self

    def _fit_sigmoid(self, scores: np.ndarray, y_event: np.ndarray) -> None:
        import warnings

        from sklearn.linear_model import LogisticRegression

        kwargs: dict[str, Any] = {"max_iter": 1000, "random_state": self.random_state}
        try:
            with warnings.catch_warnings():
                # sklearn>=1.8 deprecates n_jobs (removal in 1.10); pin single
                # thread while the parameter still exists.
                warnings.simplefilter("ignore", FutureWarning)
                est = LogisticRegression(**kwargs, n_jobs=1)  # type: ignore[call-arg]
        except TypeError:
            est = LogisticRegression(**kwargs)
        X = scores.reshape(-1, 1)  # single 1D feature; no copy of full matrices
        with warnings.catch_warnings():
            # sklearn>=1.8 warns that n_jobs has no effect at fit time.
            warnings.simplefilter("ignore", FutureWarning)
            est.fit(X, y_event)
        coef = np.asarray(est.coef_, dtype=float).ravel()
        icept = np.asarray(est.intercept_, dtype=float).ravel()
        self._coef = float(coef[0])
        self._intercept = float(icept[0])
        self._iso_x = None
        self._iso_y = None

    def _fit_isotonic(self, scores: np.ndarray, y_event: np.ndarray) -> None:
        from sklearn.isotonic import IsotonicRegression

        est = IsotonicRegression(out_of_bounds="clip")
        est.fit(scores, y_event.astype(float))
        self._iso_x = np.asarray(est.X_thresholds_, dtype=float)
        # sklearn>=1.7 exposes the fitted levels as y_thresholds_ (f_ is the
        # interp1d callable); store plain arrays for exact reload replay.
        self._iso_y = np.asarray(est.y_thresholds_, dtype=float)
        self._coef = None
        self._intercept = None

    def _require_fitted(self) -> None:
        if not self._is_fitted:
            raise NotFittedError("calibration: predict called before fit; call fit() first.")

    def predict_proba(self, scores: np.ndarray) -> np.ndarray:
        """Return calibrated ``P(y == positive_class)`` in ``[0, 1]``."""
        self._require_fitted()
        arr = _as_1d_float(scores, "scores")
        _check_no_nan(arr, "scores")
        if self.method == "sigmoid":
            assert self._coef is not None and self._intercept is not None
            z = self._coef * arr + self._intercept
            out = 1.0 / (1.0 + np.exp(-z))
        else:
            assert self._iso_x is not None and self._iso_y is not None
            out = np.interp(arr, self._iso_x, self._iso_y)
        return np.clip(out, 0.0, 1.0).astype(float)

    def predict(self, scores: np.ndarray, threshold: float = 0.5) -> np.ndarray:
        """Threshold calibrated probabilities back to class labels."""
        proba = self.predict_proba(scores)
        assert self.positive_class is not None and self._negative_class is not None
        picked = (proba >= float(threshold)).astype(int)
        return np.asarray([self.positive_class if i else self._negative_class for i in picked])

    def to_dict(self) -> dict[str, Any]:
        """Serialize the calibration map to a JSON-safe dict (no pickles)."""
        self._require_fitted()
        assert self.positive_class is not None and self._negative_class is not None
        payload: dict[str, Any] = {
            "schema": _SCHEMA_VERSION,
            "calibrator": _CALIBRATOR_NAME,
            "method": self.method,
            "positive_class": int(self.positive_class),
            "negative_class": int(self._negative_class),
            "model_family": self._model_family,
            "feature_names": list(self._feature_names),
            "feature_schema_hash": feature_schema_hash(self._feature_names),
            "train_range": None if self._train_range is None else list(self._train_range),
            "cal_range": None if self._cal_range is None else list(self._cal_range),
            "model_version": self.model_version,
            "dataset_snapshot": self.dataset_snapshot,
            "sklearn_version": self._sklearn_version,
            "min_cal_samples": int(self.min_cal_samples),
            "is_fitted": True,
        }
        if self.method == "sigmoid":
            payload["coef"] = float(self._coef)  # type: ignore[arg-type]
            payload["intercept"] = float(self._intercept)  # type: ignore[arg-type]
        else:
            assert self._iso_x is not None and self._iso_y is not None
            payload["iso_x"] = [float(v) for v in self._iso_x]
            payload["iso_y"] = [float(v) for v in self._iso_y]
        return payload

    @classmethod
    def from_dict(
        cls, payload: dict[str, Any], base_model: BaseModel | None = None
    ) -> CalibratedClassifier:
        """Rebuild a fitted calibrator; verifies the feature-schema hash.

        Reloaded maps reproduce :meth:`predict_proba` within 1e-12 (sigmoid
        stores the scalar coef/intercept; isotonic stores the fitted
        threshold arrays and reuses identical interpolation).

        Raises:
            CorruptArtifactError: Schema/version/hash mismatch.
        """
        if not isinstance(payload, dict):
            raise CorruptArtifactError("calibration: payload must be a dict.")
        if payload.get("calibrator") != _CALIBRATOR_NAME:
            raise CorruptArtifactError(
                f"calibration: not a {_CALIBRATOR_NAME} payload "
                f"(got {payload.get('calibrator')!r})."
            )
        if payload.get("schema") != _SCHEMA_VERSION:
            raise CorruptArtifactError(
                f"calibration: schema {payload.get('schema')!r} != {_SCHEMA_VERSION!r}."
            )
        method = payload.get("method")
        if method not in ("sigmoid", "isotonic"):
            raise CorruptArtifactError(f"calibration: unknown stored method {method!r}.")
        fnames = [str(c) for c in (payload.get("feature_names") or [])]
        expected_hash = feature_schema_hash(fnames)
        if payload.get("feature_schema_hash") != expected_hash:
            raise CorruptArtifactError(
                "calibration: feature_schema_hash mismatch; artifact corrupt or edited."
            )
        if base_model is None:

            class _MapOnly:
                is_fitted = True

                def predict_score(self, X: Any) -> np.ndarray:  # pragma: no cover
                    raise NotImplementedError

                def to_dict(self) -> dict[str, Any]:
                    return {
                        "family": str(payload.get("model_family", "")),
                        "feature_names": list(fnames),
                        "classes": [
                            int(payload["negative_class"]),
                            int(payload["positive_class"]),
                        ],
                    }

            base_any: Any = _MapOnly()
        else:
            base_any = base_model
        obj = cls(
            base_model=base_any,
            method=str(method),
            min_cal_samples=int(payload.get("min_cal_samples", 20)),
            positive_class=int(payload["positive_class"]),
            model_version=str(payload.get("model_version", "")),
            dataset_snapshot=str(payload.get("dataset_snapshot", "")),
        )
        obj._negative_class = int(payload["negative_class"])
        obj._model_family = str(payload.get("model_family", ""))
        obj._feature_names = fnames
        obj._sklearn_version = str(payload.get("sklearn_version", ""))
        obj._train_range = payload.get("train_range")
        obj._cal_range = payload.get("cal_range")
        if method == "sigmoid":
            try:
                obj._coef = float(payload["coef"])
                obj._intercept = float(payload["intercept"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptArtifactError(
                    "calibration: sigmoid payload missing numeric coef/intercept."
                ) from exc
        else:
            try:
                obj._iso_x = np.asarray(payload["iso_x"], dtype=float)
                obj._iso_y = np.asarray(payload["iso_y"], dtype=float)
            except (KeyError, TypeError, ValueError) as exc:
                raise CorruptArtifactError(
                    "calibration: isotonic payload missing numeric iso_x/iso_y."
                ) from exc
            if obj._iso_x.size == 0 or obj._iso_x.shape != obj._iso_y.shape:
                raise CorruptArtifactError("calibration: isotonic arrays empty/shape mismatch.")
        if not bool(payload.get("is_fitted", False)):
            raise CorruptArtifactError("calibration: payload reports is_fitted=False.")
        obj._is_fitted = True
        return obj


__all__ = ["CalibratedClassifier"]
