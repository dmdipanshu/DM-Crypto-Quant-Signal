"""Phase 5 Track A signal fixtures (offline, deterministic, <=50 rows).

Small fake BaseModel subclass + tiny fitted logistic on a <=50-row
fixture; calibrator fitted on a later split. No network.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from crypto_engine.models.base import BaseModel


class StubBinaryModel(BaseModel):
    """Deterministic stub binary model (fixed scores/labels, no learning)."""

    family: str = "stub"

    def __init__(
        self,
        feature_names: list[str] | None = None,
        score: float = 0.9,
        label: int = 1,
        classes: list[int] | None = None,
        random_state: int = 42,
    ) -> None:
        super().__init__(feature_names, random_state)
        self._score = float(score)
        self._label = int(label)
        self._classes = [int(c) for c in (classes if classes is not None else [-1, 1])]
        self._params: dict[str, Any] = {"score": self._score, "label": self._label}

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        y: np.ndarray,
        feature_names: list[str] | None = None,
    ) -> StubBinaryModel:
        _ = np.asarray(y)
        self._store_feature_names(X, feature_names)
        self._is_fitted = True
        return self

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        arr, _ = self._as_columns(X)
        return np.full(arr.shape[0], self._label, dtype=np.int64)

    def predict_score(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        arr, _ = self._as_columns(X)
        return np.full(arr.shape[0], self._score, dtype=float)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "params": dict(self._params),
            "feature_names": list(self._feature_names or []),
            "random_state": self.random_state,
            "is_fitted": self._is_fitted,
            "classes": list(self._classes),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StubBinaryModel:
        params = dict(payload.get("params", {}))
        obj = cls(
            feature_names=list(payload.get("feature_names", []) or None or []),
            score=float(params.get("score", 0.9)),
            label=int(params.get("label", 1)),
        )
        obj._classes = [int(c) for c in payload.get("classes", [-1, 1])]
        obj._is_fitted = bool(payload.get("is_fitted", False))
        return obj


def make_feature_frame(n: int = 40, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
    """Tiny deterministic binary feature frame (n <= 50)."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 2))
    frame = pd.DataFrame(x, columns=["a", "b"])
    y = np.where(x[:, 0] > 0, 1, -1).astype(np.int64)
    return frame, y


def make_fitted_logistic(n: int = 40, seed: int = 0):
    """Fit SklearnLogistic on a tiny frame (binary +-1 labels)."""
    from crypto_engine.models.sklearn_wrappers import SklearnLogistic

    frame, y = make_feature_frame(n=n, seed=seed)
    return SklearnLogistic(feature_names=["a", "b"]).fit(frame, y), frame, y


def make_calibrated_pair(seed: int = 0):
    """Return (base, calibrator, calibrator_meta, row) for policy tests.

    Base fitted on rows 0..23, calibrator on rows 24..39 (later split),
    decision row = last row. Deterministic, no network.
    """
    from scipy.special import expit

    from crypto_engine.calibration.calibrators import CalibratedClassifier
    from crypto_engine.models.base import feature_schema_hash

    rng = np.random.default_rng(seed)
    x = rng.normal(size=(50, 2))
    frame = pd.DataFrame(x, columns=["a", "b"])
    y = np.where(x[:, 0] > 0, 1, -1).astype(np.int64)
    from crypto_engine.models.sklearn_wrappers import SklearnLogistic

    base = SklearnLogistic(feature_names=["a", "b"]).fit(frame.iloc[:25], y[:25])
    scores_cal = base.predict_score(frame.iloc[25:])
    cal = CalibratedClassifier(base, method="sigmoid").fit(scores_cal, y[25:])
    payload = cal.to_dict()
    meta = {
        "model_family": payload["model_family"],
        "version": payload["model_version"],
        "feature_hash": payload["feature_schema_hash"],
        "positive_class": payload["positive_class"],
    }
    _ = expit  # keep scipy import used
    _ = feature_schema_hash
    row = {c: float(frame.iloc[-1][c]) for c in ["a", "b"]}
    return base, cal, meta, row


__all__ = ["StubBinaryModel", "make_calibrated_pair", "make_feature_frame", "make_fitted_logistic"]


# --- Phase 5 Track B additions (append-only): tiny snapshot/model/portfolio --
# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


def make_snapshot_dir(
    storage_root,
    n: int = 5,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    seed: int = 0,
    start_ms: int = 1_700_000_000_000,
):
    """Persist a tiny (<=50-row) snapshot dir under ``storage_root``.

    Deterministic 1h candles; returns ``(snap_dir, candles)``. Offline.
    """
    from crypto_engine.data.validation import normalize_candles
    from crypto_engine.datasets.snapshots import save_snapshot

    price = 40_000.0 + seed * 100.0
    rows: list[list] = []
    for i in range(n):
        o = price + i * 10.0
        rows.append([start_ms + i * 3_600_000, o, o + 50.0, o - 40.0, o + 5.0, 10.0 + i])
    candles = normalize_candles(rows)
    snap_dir = save_snapshot(
        candles,
        {"requested_start": None, "requested_end": None, "warnings": [], "missing_count": 0},
        storage_root,
        exchange="binance",
        symbol=symbol,
        timeframe=timeframe,
    )
    return snap_dir, candles


def make_registry_model_dir(registry_root, seed: int = 0):
    """Fit a tiny TrendBaseline and persist it via registry ``save_model``.

    The model uses real snapshot feature names (``ret_1``/``ret_4`` ratio
    geometry) so CLI schema checks match tiny snapshots and tail
    perturbations change the decision inputs. Returns
    ``(model_dir, model, record)``. JSON-family artifact: safe to
    reload offline via ``load_model`` (no pickle).
    """
    import numpy as np
    import pandas as pd

    from crypto_engine.models.baselines import TrendBaseline
    from crypto_engine.registry.store import save_model

    rng = np.random.default_rng(seed)
    frame = pd.DataFrame({"ret_1": rng.normal(size=40), "ret_4": rng.normal(size=40)})
    y = np.where(frame["ret_1"].to_numpy() > 0, 1, -1).astype(np.int64)
    model = TrendBaseline(ratio_col="ret_1").fit(frame, y)
    meta = {
        "dataset_snapshot_id": "fixture-snap",
        "dataset_checksum": f"fixture{seed:04d}",
        "label_config": {"pt_mult": 2.0, "sl_mult": 1.0},
        "label_hash": "fixturelbl",
        "train_start": "2024-01-01T00:00:00+00:00",
        "train_end": "2024-01-05T00:00:00+00:00",
    }
    model_dir = save_model(model, meta, registry_root)
    from crypto_engine.registry.store import load_model as _load

    _, record = _load(model_dir)
    return model_dir, model, record


def make_portfolio(status: str = "valid") -> dict | None:
    """Caller-supplied portfolio snapshots: valid / breached / missing.

    ``missing`` returns None (never invented). ``breached`` trips drawdown
    and exposure limits. All values deterministic.
    """
    if status == "missing":
        return None
    base = {
        "exposure_pct": 4.0,
        "symbol_exposure_pct": 3.0,
        "day_start_equity": 100_000.0,
        "current_equity": 99_000.0,
        "strategy_enabled": True,
        "circuit_open": False,
    }
    if status == "breached":
        base = {
            "exposure_pct": 50.0,
            "symbol_exposure_pct": 25.0,
            "day_start_equity": 100_000.0,
            "current_equity": 90_000.0,
            "strategy_enabled": False,
            "circuit_open": True,
        }
    if status != "valid" and status != "breached":
        raise ValueError(f"unknown portfolio status {status!r}.")
    return base
