"""Calibration policy regression tests.

Verifies that uncalibrated signals are never treated as eligible when
calibration is required, and that calibrated signals pass when a valid
calibrator is present.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import numpy as np

from crypto_engine.calibration.calibrators import CalibratedClassifier
from crypto_engine.models.base import BaseModel, feature_schema_hash
from crypto_engine.signals.policy import PolicyInputs, decide
from crypto_engine.signals.schema import (
    UNCALIBRATED,
    AiContextSummary,
    CostEstimate,
)


class _FakeModel(BaseModel):
    """Minimal fitted binary model for calibration policy tests."""

    family: str = "testmodel"

    def __init__(self, feature_names=None, classes=(0, 1)):
        super().__init__(feature_names=feature_names)
        self._classes = list(classes)
        self._is_fitted = True

    def fit(self, X, y, feature_names=None):
        self._store_feature_names(X, feature_names)
        self._is_fitted = True
        return self

    def predict(self, X):
        self._require_fitted()
        self._check_schema(X)
        return np.ones(len(X), dtype=np.int64)

    def predict_score(self, X):
        self._require_fitted()
        self._check_schema(X)
        return np.full(len(X), 0.75)

    def to_dict(self):
        return {
            "family": self.family,
            "classes": self._classes,
            "feature_names": list(self._feature_names or []),
            "params": {},
        }

    @classmethod
    def from_dict(cls, payload):
        return cls(feature_names=payload.get("feature_names"), classes=tuple(payload.get("classes", [0, 1])))


def _base_inputs(**over):
    model = _FakeModel(feature_names=["f1", "f2"])
    defaults = dict(
        calibrator=None,
        calibration_required=True,
    )
    defaults.update(over)
    return PolicyInputs(
        feature_row={"f1": 0.5, "f2": 0.3},
        feature_names=["f1", "f2"],
        model=model,
        model_id="m-1",
        model_version="0.1.0",
        model_artifact_hash="abc123",
        threshold_version="v1",
        t_long=0.6,
        t_short=0.4,
        require_edge=False,
        expected_edge_bp=None,
        cost=CostEstimate(fee_bp=10.0, slippage_bp=5.0, source="configured"),
        enabled_directions={"LONG", "SHORT", "NEUTRAL", "NO_TRADE"},
        data_quality="validated",
        generated_at=datetime(2024, 1, 10, 0, 0, 0, tzinfo=UTC).isoformat(),
        valid_until=datetime(2024, 1, 10, 1, 0, 0, tzinfo=UTC).isoformat(),
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1h",
        horizon_bars=24,
        regime="",
        blocked_regimes=set(),
        ai=AiContextSummary(status="disabled"),
        ai_required=False,
        dataset_snapshot_id="snap-1",
        levels=None,
        **defaults,
    )


def test_uncalibrated_no_calibrator_required_vetoed():
    """calibration_required=True with no calibrator -> NO_TRADE(UNCALIBRATED)."""
    inputs = _base_inputs(calibration_required=True, calibrator=None)
    record, _trace = decide(inputs)
    assert record.direction == "NO_TRADE"
    assert record.decision.status == "abstained"
    assert UNCALIBRATED in record.decision.reason_codes
    assert record.prediction.calibration_status == "uncalibrated"


def test_uncalibrated_unfitted_calibrator_vetoed():
    """calibration_required=True with unfitted calibrator -> vetoed."""
    calibrator = MagicMock()
    calibrator.is_fitted = False
    inputs = _base_inputs(calibration_required=True, calibrator=calibrator)
    record, _trace = decide(inputs)
    assert record.direction == "NO_TRADE"
    assert record.decision.status == "abstained"
    assert UNCALIBRATED in record.decision.reason_codes or "CALIBRATION_MISMATCH" in record.decision.reason_codes


def test_uncalibrated_not_required_passes():
    """calibration_required=False with no calibrator -> passes as uncalibrated."""
    inputs = _base_inputs(calibration_required=False, calibrator=None)
    record, _trace = decide(inputs)
    assert record.direction != "NO_TRADE" or record.prediction.calibration_status == "uncalibrated"
    assert record.prediction.calibration_status == "uncalibrated"


def test_calibrated_passes():
    """calibration_required=True with fitted calibrator -> passes calibrated."""
    model = _FakeModel(feature_names=["f1", "f2"])
    calibrator = CalibratedClassifier(
        base_model=model,
        method="sigmoid",
        min_cal_samples=2,
        model_version="0.1.0",
        dataset_snapshot="snap-1",
    )
    # Fit with two-class calibration data
    scores = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
    labels = np.array([0, 0, 0, 1, 1, 1])
    calibrator.fit(scores, labels, t_bounds=((1.0,), (2.0,)))

    inputs = _base_inputs(
        calibration_required=True,
        calibrator=calibrator,
        calibrator_meta={
            "model_family": "testmodel",
            "version": "0.1.0",
            "feature_hash": feature_schema_hash(["f1", "f2"]),
            "positive_class": 1,
        },
    )
    record, _trace = decide(inputs)
    assert record.direction != "NO_TRADE"
    assert record.prediction.calibration_status == "calibrated"


def test_calibration_mismatch_vetoed():
    """calibration_required=True with mismatched calibrator meta -> vetoed."""
    model = _FakeModel(feature_names=["f1", "f2"])
    calibrator = CalibratedClassifier(
        base_model=model,
        method="sigmoid",
        min_cal_samples=2,
        model_version="0.1.0",
        dataset_snapshot="snap-1",
    )
    scores = np.array([0.1, 0.2, 0.7, 0.8])
    labels = np.array([0, 0, 1, 1])
    calibrator.fit(scores, labels, t_bounds=((1.0,), (2.0,)))

    # Wrong model_version in meta
    inputs = _base_inputs(
        calibration_required=True,
        calibrator=calibrator,
        calibrator_meta={
            "model_family": "testmodel",
            "version": "0.2.0",  # mismatch
            "feature_hash": feature_schema_hash(["f1", "f2"]),
            "positive_class": 1,
        },
    )
    record, _trace = decide(inputs)
    assert record.direction == "NO_TRADE"
    assert record.decision.status == "abstained"
    assert "CALIBRATION_MISMATCH" in record.decision.reason_codes or UNCALIBRATED in record.decision.reason_codes


__all__ = [
    "test_calibrated_passes",
    "test_calibration_mismatch_vetoed",
    "test_uncalibrated_no_calibrator_required_vetoed",
    "test_uncalibrated_not_required_passes",
    "test_uncalibrated_unfitted_calibrator_vetoed",
]
