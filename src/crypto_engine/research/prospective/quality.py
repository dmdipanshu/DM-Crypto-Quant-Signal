"""Prospective data quality monitoring (Q5).

Computes quality states and coverage metrics from collection results.
"""

from __future__ import annotations

from typing import Any

from crypto_engine.research.prospective.collector import CollectionStatus


def compute_quality_report(
    *,
    collection_result: dict[str, Any],
    collection_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute a comprehensive quality report from a collection result.

    Returns a dict with quality state, coverage, gaps, integrity info,
    and explicit evaluation block reasons.
    """
    quality = collection_result.get("quality_checks", {})
    observations = collection_result.get("observations", [])
    gaps = collection_result.get("gaps", [])
    duplicates = collection_result.get("duplicates", [])
    conflicts = collection_result.get("conflicts", [])
    rejections = collection_result.get("rejections", [])
    errors = collection_result.get("errors", [])

    state = quality.get("state", CollectionStatus.HEALTHY)
    observation_count = quality.get("observation_count", len(observations))

    # Determine if evaluation is blocked. Use the collector's assessment as
    # the base (it checks min observations, coverage, elapsed duration, data
    # quality, provenance, and exchange availability), then add any additional
    # checks specific to this report.
    collector_blocked = quality.get("evaluation_blocked", False)
    collector_block_reasons = list(quality.get("evaluation_block_reasons", []))

    block_reasons: list[str] = list(collector_block_reasons)

    # Additional check: observation count below minimum
    if observation_count == 0 and "No observations collected" not in block_reasons:
        block_reasons.append("No observations collected; cannot evaluate prospective holdout.")

    # Additional check: state-based blocking conditions
    if state in (
        CollectionStatus.DATA_INVALID,
        CollectionStatus.EXCHANGE_UNAVAILABLE,
        CollectionStatus.PROVENANCE_INVALID,
        CollectionStatus.GAP_DETECTED,
    ):
        if state == CollectionStatus.DATA_INVALID:
            block_reasons.append("Data is invalid; cannot evaluate prospective holdout.")
        elif state == CollectionStatus.EXCHANGE_UNAVAILABLE:
            block_reasons.append("Exchange is unavailable; cannot evaluate prospective holdout.")
        elif state == CollectionStatus.PROVENANCE_INVALID:
            block_reasons.append("Provenance is invalid; cannot evaluate prospective holdout.")
        elif state == CollectionStatus.GAP_DETECTED:
            block_reasons.append("Collection gaps detected; cannot evaluate prospective holdout.")

    evaluation_blocked = len(block_reasons) > 0 or collector_blocked

    report = {
        "schema": "prospective-data-quality.v1",
        "collection_id": collection_result.get("collection_id"),
        "collection_started_at_utc": collection_result.get("collection_started_at_utc"),
        "assessed_at_utc": __import__("datetime")
        .datetime.now(__import__("datetime").UTC)
        .isoformat(),
        "state": state,
        "reasons": quality.get("reasons", []),
        "evaluation_blocked": evaluation_blocked,
        "evaluation_block_reasons": block_reasons,
        "observation_count": observation_count,
        "expected_intervals": quality.get("expected_intervals", 0),
        "coverage_fraction": quality.get("coverage_fraction", 0.0),
        "error_count": quality.get("error_count", len(errors)),
        "rejection_count": quality.get("rejection_count", len(rejections)),
        "gap_count": quality.get("gap_count", len(gaps)),
        "duplicate_count": quality.get("duplicate_count", len(duplicates)),
        "conflict_count": quality.get("conflict_count", len(conflicts)),
        "gaps": gaps,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "rejections": rejections,
        "errors": errors,
        "collection_manifest_id": (
            collection_manifest.get("collection_id") if collection_manifest else None
        ),
    }
    return report


def is_quality_state_healthy(state: str) -> bool:
    """Return True if the quality state permits prospective evaluation."""
    return state == CollectionStatus.HEALTHY


__all__ = [
    "CollectionStatus",
    "compute_quality_report",
    "is_quality_state_healthy",
]
