"""Probability calibration wrappers (Phase 3 Track A)."""

from __future__ import annotations

from crypto_engine.calibration.calibrators import CalibratedClassifier
from crypto_engine.calibration.reliability import (
    brier_score,
    classification_report_small,
    coverage_at_thresholds,
    expected_calibration_error,
    log_loss_clipped,
    reliability_table,
)

__all__ = [
    "CalibratedClassifier",
    "brier_score",
    "classification_report_small",
    "coverage_at_thresholds",
    "expected_calibration_error",
    "log_loss_clipped",
    "reliability_table",
]
