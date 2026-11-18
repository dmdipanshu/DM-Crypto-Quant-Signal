"""Fail-closed provenance and strategy promotion gates for research artifacts.

The research engine can recommend a human review only. This module does not
write to production registries, paper state, signals, risk state, or execution.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def _timestamp(value: str | datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


#: Holdout integrity statuses.
#:
#: ``pristine``        â€” chronologically separated, zero access, frozen model/params.
#: ``contaminated``    â€” overlaps training, was accessed, influenced selection,
#:                       or model/params were not frozen.
#: ``not_yet_available`` â€” no freeze timestamp set yet, or no post-freeze data
#:                       exists in the dataset (prospective validation impossible).
#: ``unknown``         â€” insufficient provenance to assess (legacy fallback).
#:
#: Prospective holdout lifecycle states (Q2.2.1):
#: ``CONTAMINATED``         â€” historical holdout permanently contaminated; never
#:                            becomes eligible due to later pipeline runs.
#: ``UNAVAILABLE``          â€” no ingestion provenance or first_seen_at available.
#: ``ACCUMULATING``         â€” post-freeze data exists but not yet cleared for
#:                            evaluation (provenance/quality checks pending).
#: ``ELIGIBLE_FOR_EVALUATION`` â€” all provenance and quality requirements pass;
#:                               awaiting explicit evaluation action.
#: ``EVALUATED``            â€” explicit evaluation performed after the freeze.
HOLDTOUT_STATUSES = ("pristine", "contaminated", "not_yet_available", "unknown")

#: Prospective holdout lifecycle states.
PROSPECTIVE_STATUSES = (
    "CONTAMINATED",
    "UNAVAILABLE",
    "ACCUMULATING",
    "ELIGIBLE_FOR_EVALUATION",
    "EVALUATED",
)


def assess_holdout_integrity(
    *,
    holdout_start: str | datetime | None = None,
    holdout_end: str | datetime | None = None,
    dataset_start: str | datetime | None = None,
    dataset_end: str | datetime | None = None,
    training_end: str | datetime | None = None,
    training_cutoff: str | datetime | None = None,
    research_freeze_timestamp: str | datetime | None = None,
    access_count: int = 0,
    model_frozen: bool = False,
    parameters_frozen: bool = False,
    previously_inspected: bool = False,
    influenced_feature_or_threshold_selection: bool = False,
) -> dict[str, Any]:
    """Return a conservative holdout state with full provenance metadata.

    ``pristine`` is returned only when there is genuinely post-freeze data,
    the training cutoff ends before the holdout starts, the access count is
    zero, and model/parameters were frozen. Missing provenance or a missing
    freeze timestamp is ``not_yet_available``; it is never treated as pristine.

    Legacy callers that omit ``research_freeze_timestamp`` and
    ``dataset_end`` fall back to the previous behaviour based purely on
    ``training_end`` vs ``holdout_start`` plus access/freeze flags.
    """
    count = int(access_count)
    if count < 0:
        raise ValueError("access_count must be >= 0")

    start = _timestamp(holdout_start, "holdout_start")
    end = _timestamp(holdout_end, "holdout_end")
    ds = _timestamp(dataset_start, "dataset_start")
    de = _timestamp(dataset_end, "dataset_end")
    te = _timestamp(training_end, "training_end")
    cutoff = _timestamp(training_cutoff, "training_cutoff") if training_cutoff is not None else te
    freeze = _timestamp(research_freeze_timestamp, "research_freeze_timestamp")

    reasons: list[str] = []
    evaluation_status = "not_assessed"

    # ---- Legacy mode (no freeze timestamp): previous behaviour ----
    if freeze is None:
        if start is None or cutoff is None:
            status = "unknown"
            reasons.append("Holdout start or training cutoff is missing; chronology is unverified.")
        elif cutoff >= start:
            status = "contaminated"
            reasons.append(
                "Training period overlaps or reaches the holdout boundary; "
                "the holdout is contaminated."
            )
        elif count > 0:
            status = "consumed"
            reasons.append(f"Holdout has already been accessed {count} time(s).")
        elif not model_frozen or not parameters_frozen:
            status = "contaminated"
            reasons.append("Model version or parameters were not frozen.")
        else:
            status = "pristine"
        evaluation_status = "legacy_assessed"
        return {
            "status": status,
            "pristine": status == "pristine",
            "holdout_start": start.isoformat() if start else None,
            "holdout_end": end.isoformat() if end else None,
            "dataset_start": ds.isoformat() if ds else None,
            "dataset_end": de.isoformat() if de else None,
            "training_cutoff": cutoff.isoformat() if cutoff else None,
            "research_freeze_timestamp": None,
            "access_count": count,
            "model_frozen": bool(model_frozen),
            "parameters_frozen": bool(parameters_frozen),
            "previously_inspected": bool(previously_inspected),
            "influenced_selection": bool(influenced_feature_or_threshold_selection),
            "reasons": reasons,
            "evaluation_status": evaluation_status,
            "eligible_for_prospective_validation": False,
        }

    # ---- New mode (freeze timestamp set): prospective validation ----
    if de is None or de <= freeze:
        # No data collected after the freeze -> cannot prospectively validate.
        status = "not_yet_available"
        reasons.append(
            "No dataset observations exist after the research freeze timestamp; "
            "prospective holdout validation is not yet possible."
        )
        evaluation_status = "no_prospective_data"
        return {
            "status": status,
            "pristine": False,
            "holdout_start": start.isoformat() if start else None,
            "holdout_end": end.isoformat() if end else None,
            "dataset_start": ds.isoformat() if ds else None,
            "dataset_end": de.isoformat() if de else None,
            "training_cutoff": cutoff.isoformat() if cutoff else None,
            "research_freeze_timestamp": freeze.isoformat(),
            "access_count": count,
            "model_frozen": bool(model_frozen),
            "parameters_frozen": bool(parameters_frozen),
            "previously_inspected": bool(previously_inspected),
            "influenced_selection": bool(influenced_feature_or_threshold_selection),
            "reasons": reasons,
            "evaluation_status": evaluation_status,
            "eligible_for_prospective_validation": False,
        }

    # There IS post-freeze data; now check contamination of the holdout itself.
    if start is None or cutoff is None:
        status = "unknown"
        reasons.append("Holdout start or training cutoff is missing; chronology is unverified.")
        evaluation_status = "assessed_unknown_provenance"
    elif cutoff >= start:
        status = "contaminated"
        reasons.append(
            "Training period overlaps or reaches the holdout boundary; the holdout is contaminated."
        )
    elif count > 0:
        status = "contaminated"
        reasons.append(f"Holdout has already been accessed {count} time(s).")
    elif previously_inspected:
        status = "contaminated"
        reasons.append("Holdout was previously inspected by research.")
    elif influenced_feature_or_threshold_selection:
        status = "contaminated"
        reasons.append("Holdout observations influenced feature or threshold selection.")
    elif not model_frozen or not parameters_frozen:
        status = "contaminated"
        reasons.append("Model version or parameters were not frozen.")
    else:
        status = "pristine"
        evaluation_status = "assessed_pristine"

    return {
        "status": status,
        "pristine": status == "pristine",
        "holdout_start": start.isoformat() if start else None,
        "holdout_end": end.isoformat() if end else None,
        "dataset_start": ds.isoformat() if ds else None,
        "dataset_end": de.isoformat() if de else None,
        "training_cutoff": cutoff.isoformat() if cutoff else None,
        "research_freeze_timestamp": freeze.isoformat(),
        "access_count": count,
        "model_frozen": bool(model_frozen),
        "parameters_frozen": bool(parameters_frozen),
        "previously_inspected": bool(previously_inspected),
        "influenced_selection": bool(influenced_feature_or_threshold_selection),
        "reasons": reasons,
        "evaluation_status": evaluation_status,
        "eligible_for_prospective_validation": status == "pristine",
    }


def assess_prospective_holdout(
    *,
    dataset_end: str | datetime | None,
    research_freeze_timestamp: str | datetime | None,
    dataset_start: str | datetime | None = None,
    previously_inspected: bool = False,
    influenced_selection: bool = False,
) -> dict[str, Any]:
    """Assess whether a genuinely prospective holdout can exist.

    Returns a dict with ``status`` in ``pristine`` / ``contaminated`` /
    ``not_yet_available`` and ``eligible_for_prospective_validation``.
    """
    return assess_holdout_integrity(
        dataset_start=dataset_start,
        dataset_end=dataset_end,
        research_freeze_timestamp=research_freeze_timestamp,
        access_count=0,
        model_frozen=True,
        parameters_frozen=True,
        previously_inspected=previously_inspected,
        influenced_feature_or_threshold_selection=influenced_selection,
    )


@dataclass(frozen=True, slots=True)
class PromotionThresholds:
    """Predeclared economic/reproducibility thresholds for human review.

    A candidate must satisfy *every* condition to reach
    ``ELIGIBLE_FOR_HUMAN_REVIEW``; any failure yields
    ``NO_STRATEGY_ELIGIBLE``. Thresholds are predeclared and must not be
    tuned against the final holdout.
    """

    min_trades: int = 100
    min_profit_factor: float = 1.10
    min_positive_fold_fraction: float = 0.75
    required_stress_multiplier: float = 2.0
    min_balanced_accuracy_vs_baseline: float = 0.0
    max_drawdown_bp: float | None = None
    max_prediction_collapse_share: float | None = 0.90
    min_folds: int = 3

    def __post_init__(self) -> None:
        if self.min_trades < 1:
            raise ValueError("min_trades must be >= 1")
        for name, value in (
            ("min_profit_factor", self.min_profit_factor),
            ("min_positive_fold_fraction", self.min_positive_fold_fraction),
            ("required_stress_multiplier", self.required_stress_multiplier),
        ):
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{name} must be finite and > 0")
        if self.min_positive_fold_fraction > 1:
            raise ValueError("min_positive_fold_fraction must be <= 1")
        if self.max_drawdown_bp is not None and (
            not math.isfinite(float(self.max_drawdown_bp)) or float(self.max_drawdown_bp) <= 0
        ):
            raise ValueError("max_drawdown_bp must be finite and > 0")
        if self.max_prediction_collapse_share is not None:
            v = float(self.max_prediction_collapse_share)
            if not math.isfinite(v) or v <= 0 or v > 1:
                raise ValueError("max_prediction_collapse_share must be within (0, 1]")
        if self.min_folds < 1:
            raise ValueError("min_folds must be >= 1")


def evaluate_promotion_eligibility(
    *,
    strategy_metrics: dict[str, Any],
    fold_net_returns_bp: list[float],
    holdout_integrity: dict[str, Any],
    prospective_holdout_validated: bool,
    thresholds: PromotionThresholds | None = None,
    baseline_balanced_accuracy: float | None = None,
    model_balanced_accuracy: float | None = None,
    prediction_collapse: dict[str, Any] | None = None,
    critical_defects: list[str] | None = None,
    n_folds: int | None = None,
) -> dict[str, Any]:
    """Apply deterministic fail-closed gates; never authorize execution.

    The most this function can return is ``ELIGIBLE_FOR_HUMAN_REVIEW``.
    Human approval and execution authorization are always false.
    """
    rules = thresholds or PromotionThresholds()
    reasons: list[str] = []

    # --- Holdout integrity (mandatory) ---
    holdout_state = str(holdout_integrity.get("status", "unknown"))
    eval_status = str(holdout_integrity.get("evaluation_status", "not_assessed"))
    if holdout_state != "pristine":
        reasons.append(f"Holdout integrity is {holdout_state!r}, not pristine.")
    if eval_status != "assessed_pristine":
        reasons.append("Holdout is not assessed as pristine.")
    if not prospective_holdout_validated:
        reasons.append("No genuinely prospective, untouched holdout has been validated.")

    # --- Critical defects (mandatory) ---
    defects = list(critical_defects or [])
    if defects:
        for d in defects:
            reasons.append(f"Critical defect: {d}")

    # --- Model beats baseline (mandatory when baseline supplied) ---
    if baseline_balanced_accuracy is not None and model_balanced_accuracy is not None:
        if not math.isfinite(model_balanced_accuracy) or not math.isfinite(
            baseline_balanced_accuracy
        ):
            reasons.append("Balanced accuracy values are non-finite.")
        elif (
            model_balanced_accuracy
            <= baseline_balanced_accuracy + rules.min_balanced_accuracy_vs_baseline
        ):
            reasons.append(
                "Model balanced accuracy does not exceed the baseline by the required margin."
            )

    # --- Trade count ---
    try:
        trade_count = int(strategy_metrics.get("trade_count", 0))
    except (TypeError, ValueError):
        trade_count = 0
    if trade_count < rules.min_trades:
        reasons.append(f"Trade count {trade_count} is below minimum {rules.min_trades}.")

    # --- Profit factor ---
    pf_raw = strategy_metrics.get("profit_factor")
    try:
        profit_factor = float(pf_raw) if pf_raw is not None else math.nan
    except (TypeError, ValueError):
        profit_factor = math.nan
    if not math.isfinite(profit_factor) or profit_factor < rules.min_profit_factor:
        reasons.append(f"Profit factor must be finite and >= {rules.min_profit_factor:.2f}.")

    # --- Drawdown ---
    dd_raw = strategy_metrics.get("max_drawdown_bp") or strategy_metrics.get(
        "max_drawdown_bp_account"
    )
    if rules.max_drawdown_bp is not None and dd_raw is not None:
        try:
            drawdown = float(dd_raw)
        except (TypeError, ValueError):
            drawdown = math.nan
        if not math.isfinite(drawdown) or drawdown > rules.max_drawdown_bp:
            reasons.append(
                f"Max drawdown {drawdown:.2f} bp exceeds limit {rules.max_drawdown_bp:.2f} bp."
            )

    # --- Prediction collapse ---
    collapse_share = None
    if prediction_collapse is not None:
        try:
            collapse_share = float(prediction_collapse.get("max_class_share"))
        except (TypeError, ValueError):
            collapse_share = None
    if (
        rules.max_prediction_collapse_share is not None
        and collapse_share is not None
        and collapse_share >= rules.max_prediction_collapse_share
    ):
        reasons.append(
            f"Prediction collapse: max class share {collapse_share:.2%} exceeds "
            f"limit {rules.max_prediction_collapse_share:.2%}."
        )

    # --- Fold robustness ---
    values: list[float] = []
    invalid_fold_value = False
    for raw_value in fold_net_returns_bp:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            invalid_fold_value = True
            continue
        if not math.isfinite(value):
            invalid_fold_value = True
            continue
        values.append(value)
    positive_fraction = (sum(v > 0 for v in values) / len(values)) if values else 0.0
    if invalid_fold_value:
        reasons.append("Walk-forward fold returns contain invalid or non-finite values.")
    if n_folds is not None and int(n_folds) < rules.min_folds:
        reasons.append(f"Fold count {int(n_folds)} is below minimum {rules.min_folds}.")
    if not values or positive_fraction < rules.min_positive_fold_fraction:
        reasons.append(
            "Positive walk-forward fold fraction is below "
            f"{rules.min_positive_fold_fraction:.0%} or unavailable."
        )
    if invalid_fold_value:
        reasons.append("Walk-forward fold returns contain invalid values.")

    # --- Cost stress at required multiplier ---
    stress = strategy_metrics.get("cost_stress", [])
    stress_row = None
    if isinstance(stress, list):
        for row in stress:
            if not isinstance(row, dict):
                continue
            try:
                multiplier = float(row.get("cost_multiplier", -1))
            except (TypeError, ValueError):
                continue
            if math.isclose(
                multiplier,
                rules.required_stress_multiplier,
                rel_tol=0.0,
                abs_tol=1e-9,
            ):
                stress_row = row
                break
    try:
        stress_return = float(stress_row["total_net_return_bp"]) if stress_row else math.nan
    except (KeyError, TypeError, ValueError):
        stress_return = math.nan
    if not math.isfinite(stress_return) or stress_return <= 0:
        reasons.append(
            f"Net return at {rules.required_stress_multiplier:.1f}x costs must be positive."
        )

    eligible = not reasons
    return {
        "status": "ELIGIBLE_FOR_HUMAN_REVIEW" if eligible else "NO_STRATEGY_ELIGIBLE",
        "reasons": reasons,
        "trade_count": trade_count,
        "profit_factor": profit_factor if math.isfinite(profit_factor) else None,
        "positive_fold_fraction": positive_fraction,
        "stress_multiplier": rules.required_stress_multiplier,
        "stress_net_return_bp": stress_return if math.isfinite(stress_return) else None,
        "human_approval_required": True,
        "human_authorized": False,
        "execution_authorized": False,
        "ai_promotion_authority": False,
    }


def assess_prospective_holdout_state(
    *,
    freeze_timestamp: str | datetime | None,
    observation_event_timestamp: str | datetime | None,
    observation_ingestion_timestamp: str | datetime | None,
    observation_first_seen_at: str | datetime | None,
    observation_provenance_available: bool = False,
    observation_used_in_development: bool = False,
    data_quality_passed: bool = False,
    evaluation_performed: bool = False,
    historical_holdout_contaminated: bool = True,
) -> dict[str, Any]:
    """Assess a prospective holdout observation with full timestamp provenance.

    Distinguishes five timestamps:
      - dataset event timestamp: when the market event occurred
      - dataset ingestion timestamp: when the data was ingested
      - first_seen_at: when the observation was first seen by the system
      - research freeze timestamp: when research was frozen
      - evaluation timestamp: when the holdout was explicitly evaluated

    A genuinely prospective observation must satisfy ALL of:
      - event timestamp is after the research freeze
      - first_seen_at is after the research freeze
      - observation provenance is available
      - observation was not used during feature/target/model/threshold development
      - data-quality checks pass

    States:
      - CONTAMINATED: historical holdout permanently contaminated
      - UNAVAILABLE: no ingestion provenance or first_seen_at
      - ACCUMULATING: post-freeze data exists but not yet cleared
      - ELIGIBLE_FOR_EVALUATION: all requirements pass, awaiting evaluation
      - EVALUATED: explicit evaluation performed after the freeze
    """
    freeze = _timestamp(freeze_timestamp, "freeze_timestamp") if freeze_timestamp else None
    event_ts = (
        _timestamp(observation_event_timestamp, "observation_event_timestamp")
        if observation_event_timestamp
        else None
    )
    ingestion_ts = (
        _timestamp(observation_ingestion_timestamp, "observation_ingestion_timestamp")
        if observation_ingestion_timestamp
        else None
    )
    first_seen = (
        _timestamp(observation_first_seen_at, "observation_first_seen_at")
        if observation_first_seen_at
        else None
    )

    # Historical holdout is permanently contaminated.
    if historical_holdout_contaminated:
        return {
            "prospective_status": "CONTAMINATED",
            "state": "CONTAMINATED",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "Historical holdout is permanently classified CONTAMINATED; "
                "it cannot become eligible due to later pipeline runs."
            ],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
            "observation_ingestion_timestamp": ingestion_ts.isoformat() if ingestion_ts else None,
            "observation_first_seen_at": first_seen.isoformat() if first_seen else None,
            "provenance_available": observation_provenance_available,
            "used_in_development": observation_used_in_development,
            "data_quality_passed": data_quality_passed,
            "evaluation_performed": evaluation_performed,
        }

    # No freeze timestamp -> cannot assess prospectively.
    if freeze is None:
        return {
            "prospective_status": "UNAVAILABLE",
            "state": "UNAVAILABLE",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": ["No research freeze timestamp set; prospective assessment impossible."],
            "freeze_timestamp": None,
            "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
            "observation_ingestion_timestamp": ingestion_ts.isoformat() if ingestion_ts else None,
            "observation_first_seen_at": first_seen.isoformat() if first_seen else None,
            "provenance_available": observation_provenance_available,
            "used_in_development": observation_used_in_development,
            "data_quality_passed": data_quality_passed,
            "evaluation_performed": evaluation_performed,
        }

    # No ingestion provenance or first_seen_at -> UNAVAILABLE.
    if ingestion_ts is None or first_seen is None:
        return {
            "prospective_status": "UNAVAILABLE",
            "state": "UNAVAILABLE",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "Missing ingestion provenance or first_seen_at; "
                "cannot assume observation is genuinely prospective."
            ],
            "freeze_timestamp": freeze.isoformat(),
            "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
            "observation_ingestion_timestamp": ingestion_ts.isoformat() if ingestion_ts else None,
            "observation_first_seen_at": first_seen.isoformat() if first_seen else None,
            "provenance_available": observation_provenance_available,
            "used_in_development": observation_used_in_development,
            "data_quality_passed": data_quality_passed,
            "evaluation_performed": evaluation_performed,
        }

    # Event timestamp must be after the freeze.
    if event_ts is not None and event_ts <= freeze:
        return {
            "prospective_status": "UNAVAILABLE",
            "state": "UNAVAILABLE",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "Observation event timestamp is not after the research freeze; "
                "not genuinely prospective."
            ],
            "freeze_timestamp": freeze.isoformat(),
            "observation_event_timestamp": event_ts.isoformat(),
            "observation_ingestion_timestamp": ingestion_ts.isoformat(),
            "observation_first_seen_at": first_seen.isoformat(),
            "provenance_available": observation_provenance_available,
            "used_in_development": observation_used_in_development,
            "data_quality_passed": data_quality_passed,
            "evaluation_performed": evaluation_performed,
        }

    # first_seen_at must be after the freeze.
    if first_seen <= freeze:
        return {
            "prospective_status": "UNAVAILABLE",
            "state": "UNAVAILABLE",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "Observation first_seen_at is not after the research freeze; "
                "not genuinely prospective."
            ],
            "freeze_timestamp": freeze.isoformat(),
            "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
            "observation_ingestion_timestamp": ingestion_ts.isoformat(),
            "observation_first_seen_at": first_seen.isoformat(),
            "provenance_available": observation_provenance_available,
            "used_in_development": observation_used_in_development,
            "data_quality_passed": data_quality_passed,
            "evaluation_performed": evaluation_performed,
        }

    # Provenance must be available.
    if not observation_provenance_available:
        return {
            "prospective_status": "UNAVAILABLE",
            "state": "UNAVAILABLE",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "Observation provenance is not available; cannot assume genuine prospectiveness."
            ],
            "freeze_timestamp": freeze.isoformat(),
            "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
            "observation_ingestion_timestamp": ingestion_ts.isoformat(),
            "observation_first_seen_at": first_seen.isoformat(),
            "provenance_available": False,
            "used_in_development": observation_used_in_development,
            "data_quality_passed": data_quality_passed,
            "evaluation_performed": evaluation_performed,
        }

    # Must not have been used in development.
    if observation_used_in_development:
        return {
            "prospective_status": "CONTAMINATED",
            "state": "CONTAMINATED",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "Observation was used during feature/target/model/threshold "
                "development; holdout is contaminated."
            ],
            "freeze_timestamp": freeze.isoformat(),
            "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
            "observation_ingestion_timestamp": ingestion_ts.isoformat(),
            "observation_first_seen_at": first_seen.isoformat(),
            "provenance_available": observation_provenance_available,
            "used_in_development": True,
            "data_quality_passed": data_quality_passed,
            "evaluation_performed": evaluation_performed,
        }

    # Data quality must pass.
    if not data_quality_passed:
        return {
            "prospective_status": "ACCUMULATING",
            "state": "ACCUMULATING",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": ["Data-quality checks have not passed; observation is accumulating."],
            "freeze_timestamp": freeze.isoformat(),
            "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
            "observation_ingestion_timestamp": ingestion_ts.isoformat(),
            "observation_first_seen_at": first_seen.isoformat(),
            "provenance_available": observation_provenance_available,
            "used_in_development": observation_used_in_development,
            "data_quality_passed": False,
            "evaluation_performed": evaluation_performed,
        }

    # All requirements pass -> ELIGIBLE_FOR_EVALUATION.
    if not evaluation_performed:
        return {
            "prospective_status": "ELIGIBLE_FOR_EVALUATION",
            "state": "ELIGIBLE_FOR_EVALUATION",
            "eligible_for_evaluation": True,
            "evaluated": False,
            "reasons": [
                "All prospective requirements satisfied; awaiting explicit "
                "evaluation action after the research freeze."
            ],
            "freeze_timestamp": freeze.isoformat(),
            "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
            "observation_ingestion_timestamp": ingestion_ts.isoformat(),
            "observation_first_seen_at": first_seen.isoformat(),
            "provenance_available": observation_provenance_available,
            "used_in_development": observation_used_in_development,
            "data_quality_passed": data_quality_passed,
            "evaluation_performed": False,
        }

    # Explicit evaluation performed -> EVALUATED.
    return {
        "prospective_status": "EVALUATED",
        "state": "EVALUATED",
        "eligible_for_evaluation": True,
        "evaluated": True,
        "reasons": [
            "Explicit evaluation performed after the research freeze with all "
            "provenance requirements satisfied."
        ],
        "freeze_timestamp": freeze.isoformat(),
        "observation_event_timestamp": event_ts.isoformat() if event_ts else None,
        "observation_ingestion_timestamp": ingestion_ts.isoformat(),
        "observation_first_seen_at": first_seen.isoformat(),
        "provenance_available": observation_provenance_available,
        "used_in_development": observation_used_in_development,
        "data_quality_passed": data_quality_passed,
        "evaluation_performed": True,
    }


__all__ = [
    "HOLDTOUT_STATUSES",
    "PROSPECTIVE_STATUSES",
    "PromotionThresholds",
    "assess_holdout_integrity",
    "assess_prospective_holdout",
    "assess_prospective_holdout_state",
    "evaluate_promotion_eligibility",
]
