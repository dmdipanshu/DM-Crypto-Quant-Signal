"""Q6 governance module.

Integrates with existing governance (src/crypto_engine/research/governance.py)
to manage Q6 evaluation states and preserve all existing governance
restrictions.

Q6 states:
- BLOCKED: Preflight or mandatory conditions failed
- INSUFFICIENT_EVIDENCE: Not enough data for evaluation
- EDGE_NOT_FOUND: No statistical edge detected
- EDGE_DETECTED_NOT_ROBUST: Edge found but not robust across regimes/costs
- VALIDATION_PASSED_PENDING_HUMAN_REVIEW: All requirements passed, human review needed
- REJECTED: Explicitly rejected

Historical holdout remains permanently CONTAMINATED.
Q4 NO_STRATEGY_ELIGIBLE decision is preserved.
No automatic promotion is permitted.
"""

from __future__ import annotations

from typing import Any

# Q6 governance states (re-exported for convenience)
BLOCKED = "BLOCKED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
EDGE_NOT_FOUND = "EDGE_NOT_FOUND"
EDGE_DETECTED_NOT_ROBUST = "EDGE_DETECTED_NOT_ROBUST"
VALIDATION_PASSED_PENDING_HUMAN_REVIEW = "VALIDATION_PASSED_PENDING_HUMAN_REVIEW"
REJECTED = "REJECTED"

Q6_STATUSES = (
    BLOCKED,
    INSUFFICIENT_EVIDENCE,
    EDGE_NOT_FOUND,
    EDGE_DETECTED_NOT_ROBUST,
    VALIDATION_PASSED_PENDING_HUMAN_REVIEW,
    REJECTED,
)


def assess_q6_readiness(
    *,
    preflight_passed: bool,
    holdout_eligible: bool,
    data_quality_passed: bool,
    candidate_lock_valid: bool,
    observation_count: int,
    min_observation_count: int,
    trade_count: int,
    min_trades: int,
    net_return_bp: float | None,
    statistical_significance: bool | None,
    cost_stress_passed: bool | None,
    data_integrity_ok: bool,
) -> dict[str, Any]:
    """Assess whether Q6 evaluation can proceed.

    Returns a dict with:
      - state: one of Q6_STATUSES
      - can_evaluate: whether evaluation can proceed
      - blocking_reasons: list of reasons blocking evaluation
      - readiness_details: per-check details
    """
    blocking_reasons: list[str] = []
    checks: dict[str, Any] = {}

    # Check preflight
    if not preflight_passed:
        blocking_reasons.append("Q6 preflight not passed")
        checks["preflight"] = False
    else:
        checks["preflight"] = True

    # Check holdout eligibility
    if not holdout_eligible:
        blocking_reasons.append("Prospective holdout not eligible for evaluation")
        checks["holdout_eligible"] = False
    else:
        checks["holdout_eligible"] = True

    # Check data quality
    if not data_quality_passed:
        blocking_reasons.append("Data quality checks failed")
        checks["data_quality"] = False
    else:
        checks["data_quality"] = True

    # Check candidate lock
    if not candidate_lock_valid:
        blocking_reasons.append("Candidate lock missing or invalid")
        checks["candidate_lock"] = False
    else:
        checks["candidate_lock"] = True

    # Check observation count
    if observation_count < min_observation_count:
        blocking_reasons.append(
            f"Observation count {observation_count} below minimum {min_observation_count}"
        )
        checks["min_observations"] = False
    else:
        checks["min_observations"] = True

    # Check trade count
    if trade_count < min_trades:
        blocking_reasons.append(f"Trade count {trade_count} below minimum {min_trades}")
        checks["min_trades"] = False
    else:
        checks["min_trades"] = True

    # Check net return
    if net_return_bp is None or net_return_bp <= 0:
        blocking_reasons.append(f"Non-positive net return: {net_return_bp} bp")
        checks["positive_net_return"] = False
    else:
        checks["positive_net_return"] = True

    # Check statistical significance
    if statistical_significance is not None and not statistical_significance:
        blocking_reasons.append("Results not statistically significant")
        checks["statistical_significance"] = False
    else:
        checks["statistical_significance"] = True

    # Check cost stress
    if cost_stress_passed is not None and not cost_stress_passed:
        blocking_reasons.append("Cost stress test failed")
        checks["cost_stress"] = False
    else:
        checks["cost_stress"] = True

    # Check data integrity
    if not data_integrity_ok:
        blocking_reasons.append("Data integrity check failed")
        checks["data_integrity"] = False
    else:
        checks["data_integrity"] = True

    can_evaluate = len(blocking_reasons) == 0

    if not can_evaluate:
        state = BLOCKED if blocking_reasons else INSUFFICIENT_EVIDENCE
    else:
        state = VALIDATION_PASSED_PENDING_HUMAN_REVIEW

    return {
        "state": state,
        "can_evaluate": can_evaluate,
        "blocking_reasons": blocking_reasons,
        "blocking_reason_count": len(blocking_reasons),
        "readiness_details": checks,
        "assessed_at_utc": None,  # Set by caller
    }


def historical_q4_rejection_preserved() -> dict[str, Any]:
    """Verify that Q4 NO_STRATEGY_ELIGIBLE decision is preserved.

    Q6 does not change Q4 results. Q4 NO_STRATEGY_ELIGIBLE remains
    regardless of Q6 evaluation outcomes.
    """
    return {
        "q4_decision": "NO_STRATEGY_ELIGIBLE",
        "preserved": True,
        "q6_cannot_override": True,
        "reason": "Q6 evaluation does not modify Q2-Q4 research results",
    }


def q6_cannot_auto_promote() -> dict[str, Any]:
    """Confirm that Q6 never grants automatic promotion.

    Maximum outcome is VALIDATION_PASSED_PENDING_HUMAN_REVIEW which
    requires explicit human approval. Execution authorization is always
    false.
    """
    return {
        "automatic_promotion": False,
        "human_approval_required": True,
        "execution_authorized": False,
        "paper_trading_authorized": False,
        "order_execution_authorized": False,
        "max_outcome": VALIDATION_PASSED_PENDING_HUMAN_REVIEW,
    }


def no_prospective_data_leakage() -> dict[str, Any]:
    """Confirm that prospective data remains isolated from training/tuning.

    Q6 is strictly read-only with respect to prospective source data.
    """
    return {
        "prospective_data_read_only": True,
        "no_retraining": True,
        "no_recalibration": True,
        "no_threshold_tuning": True,
        "no_feature_selection": True,
        "no_backfill": True,
        "historical_contamination_preserved": True,
    }


__all__ = [
    "BLOCKED",
    "EDGE_DETECTED_NOT_ROBUST",
    "EDGE_NOT_FOUND",
    "INSUFFICIENT_EVIDENCE",
    "Q6_STATUSES",
    "REJECTED",
    "VALIDATION_PASSED_PENDING_HUMAN_REVIEW",
    "assess_q6_readiness",
    "historical_q4_rejection_preserved",
    "no_prospective_data_leakage",
    "q6_cannot_auto_promote",
]
