"""Prospective holdout governance for Q5.

Integrates with src/crypto_engine/research/governance.py to manage the
prospective holdout lifecycle:

CONTAMINATED -> UNAVAILABLE -> ACCUMULATING -> ELIGIBLE_FOR_EVALUATION -> EVALUATED

The historical holdout remains permanently CONTAMINATED.
The prospective dataset begins as ACCUMULATING and requires explicit
evaluation to reach EVALUATED.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from crypto_engine.research.governance import (
    PROSPECTIVE_STATUSES,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def assess_prospective_holdout_readiness(
    *,
    freeze_timestamp: str | datetime,
    collection_started_at: str | datetime,
    observation_count: int,
    min_observation_count: int,
    coverage_fraction: float,
    min_coverage_fraction: float,
    data_quality_passed: bool,
    first_seen_provenance_valid: bool,
    post_collection_start: bool,
    no_backfill: bool,
    no_development_inspection: bool,
    evaluation_requested: bool = False,
) -> dict[str, Any]:
    """Assess whether the prospective holdout is ready for evaluation.

    Transitions:
      - CONTAMINATED: if historical holdout is contaminated (always true)
      - UNAVAILABLE: if provenance missing or data not post-collection-start
      - ACCUMULATING: if data exists but quality/coverage not met
      - ELIGIBLE_FOR_EVALUATION: if all requirements pass, awaiting evaluation
      - EVALUATED: only if evaluation_requested is True (explicit action)

    Returns a dict with the prospective state and reasons.
    """
    freeze = _parse_ts(freeze_timestamp, "freeze_timestamp")
    collection_start = _parse_ts(collection_started_at, "collection_started_at")
    historical_contaminated = True

    # Zero accepted observations means NOT_STARTED. Collection start anchor is not
    # established and no elapsed-time requirements are evaluated.
    if observation_count <= 0:
        return {
            "prospective_status": "NOT_STARTED",
            "state": "NOT_STARTED",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "No accepted observations; collection_started_at_utc is not established (NOT_STARTED)."
            ],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    # Build the state using the governance module's function
    # We evaluate based on collection-readiness criteria
    if not post_collection_start:
        return {
            "prospective_status": "UNAVAILABLE",
            "state": "UNAVAILABLE",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "Observations are not genuinely post-collection-start; "
                "cannot establish prospective holdout."
            ],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": collection_start.isoformat() if collection_start else None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    if not first_seen_provenance_valid:
        return {
            "prospective_status": "UNAVAILABLE",
            "state": "UNAVAILABLE",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "First-seen provenance is not valid; "
                "cannot assume observations are genuinely prospective."
            ],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": collection_start.isoformat() if collection_start else None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    if not data_quality_passed:
        return {
            "prospective_status": "ACCUMULATING",
            "state": "ACCUMULATING",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "Data-quality checks have not passed; prospective holdout is accumulating."
            ],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": collection_start.isoformat() if collection_start else None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    if observation_count < min_observation_count:
        return {
            "prospective_status": "ACCUMULATING",
            "state": "ACCUMULATING",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                f"Observation count {observation_count} is below minimum "
                f"{min_observation_count}; prospective holdout is accumulating."
            ],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": collection_start.isoformat() if collection_start else None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    if coverage_fraction < min_coverage_fraction:
        return {
            "prospective_status": "ACCUMULATING",
            "state": "ACCUMULATING",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                f"Coverage fraction {coverage_fraction:.2%} is below minimum "
                f"{min_coverage_fraction:.2%}; prospective holdout is accumulating."
            ],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": collection_start.isoformat() if collection_start else None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    if not no_backfill:
        return {
            "prospective_status": "CONTAMINATED",
            "state": "CONTAMINATED",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": ["Historical backfill detected; prospective holdout is contaminated."],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": collection_start.isoformat() if collection_start else None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    if not no_development_inspection:
        return {
            "prospective_status": "CONTAMINATED",
            "state": "CONTAMINATED",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": ["Prospective outcomes were used in development; holdout is contaminated."],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": collection_start.isoformat() if collection_start else None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    # All requirements pass -> ELIGIBLE_FOR_EVALUATION
    if not evaluation_requested:
        return {
            "prospective_status": "ELIGIBLE_FOR_EVALUATION",
            "state": "ELIGIBLE_FOR_EVALUATION",
            "eligible_for_evaluation": True,
            "evaluated": False,
            "reasons": [
                "All prospective requirements satisfied; awaiting explicit "
                "evaluation action after the research freeze."
            ],
            "freeze_timestamp": freeze.isoformat() if freeze else None,
            "collection_started_at": collection_start.isoformat() if collection_start else None,
            "observation_count": observation_count,
            "min_observation_count": min_observation_count,
            "coverage_fraction": coverage_fraction,
            "min_coverage_fraction": min_coverage_fraction,
            "data_quality_passed": data_quality_passed,
            "first_seen_provenance_valid": first_seen_provenance_valid,
            "no_backfill": no_backfill,
            "no_development_inspection": no_development_inspection,
            "evaluation_requested": evaluation_requested,
            "historical_holdout_contaminated": historical_contaminated,
            "assessed_at_utc": _utc_now_iso(),
        }

    # Explicit evaluation performed -> EVALUATED
    return {
        "prospective_status": "EVALUATED",
        "state": "EVALUATED",
        "eligible_for_evaluation": True,
        "evaluated": True,
        "reasons": [
            "Explicit evaluation performed after the research freeze with all "
            "provenance requirements satisfied."
        ],
        "freeze_timestamp": freeze.isoformat() if freeze else None,
        "collection_started_at": collection_start.isoformat() if collection_start else None,
        "observation_count": observation_count,
        "min_observation_count": min_observation_count,
        "coverage_fraction": coverage_fraction,
        "min_coverage_fraction": min_coverage_fraction,
        "data_quality_passed": data_quality_passed,
        "first_seen_provenance_valid": first_seen_provenance_valid,
        "no_backfill": no_backfill,
        "no_development_inspection": no_development_inspection,
        "evaluation_requested": evaluation_requested,
        "historical_holdout_contaminated": historical_contaminated,
        "assessed_at_utc": _utc_now_iso(),
    }


def _parse_ts(value: str | datetime, field: str) -> datetime | None:
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


def historical_holdout_is_contaminated() -> dict[str, Any]:
    """Return the permanent historical holdout contamination status."""
    return {
        "state": "CONTAMINATED",
        "eligible_for_evaluation": False,
        "evaluated": False,
        "reasons": [
            "Historical holdout is permanently classified CONTAMINATED; "
            "it cannot become eligible due to later pipeline runs."
        ],
    }


__all__ = [
    "PROSPECTIVE_STATUSES",
    "assess_prospective_holdout_readiness",
    "historical_holdout_is_contaminated",
]
