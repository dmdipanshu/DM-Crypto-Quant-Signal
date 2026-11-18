"""Q6 promotion and human review gate.

Independent deterministic promotion gating. A candidate must satisfy
configured requirements for:

- valid prospective holdout
- valid candidate lock
- sufficient observations
- minimum trade count
- positive net expectancy
- positive performance after costs
- acceptable drawdown
- benchmark comparison
- fold or chronological stability
- statistical validity
- regime robustness
- cost stress
- data integrity

No automatic promotion is permitted. A passing Q6 evaluation only permits
a human review request.
"""

from __future__ import annotations

from typing import Any

from crypto_engine.research.q6.config import Q6EvaluationPolicy


class PromotionGateResult:
    """Result of promotion gate evaluation."""

    def __init__(
        self,
        *,
        status: str,
        eligible_for_human_review: bool,
        human_authorized: bool,
        execution_authorized: bool,
        reasons: list[str],
        checks: dict[str, Any],
    ) -> None:
        self.status = status
        self.eligible_for_human_review = eligible_for_human_review
        self.human_authorized = human_authorized
        self.execution_authorized = execution_authorized
        self.reasons = reasons
        self.checks = checks

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "eligible_for_human_review": self.eligible_for_human_review,
            "human_authorized": self.human_authorized,
            "execution_authorized": self.execution_authorized,
            "reasons": self.reasons,
            "checks": self.checks,
        }


# Q6 promotion gate statuses
BLOCKED = "BLOCKED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
EDGE_NOT_FOUND = "EDGE_NOT_FOUND"
EDGE_DETECTED_NOT_ROBUST = "EDGE_DETECTED_NOT_ROBUST"
VALIDATION_PASSED_PENDING_HUMAN_REVIEW = "VALIDATION_PASSED_PENDING_HUMAN_REVIEW"
REJECTED = "REJECTED"

PROMOTION_STATUSES = (
    BLOCKED,
    INSUFFICIENT_EVIDENCE,
    EDGE_NOT_FOUND,
    EDGE_DETECTED_NOT_ROBUST,
    VALIDATION_PASSED_PENDING_HUMAN_REVIEW,
    REJECTED,
)


class PromotionGate:
    """Deterministic promotion gate for Q6 prospective evaluation.

    Never grants automatic promotion. Maximum outcome is
    VALIDATION_PASSED_PENDING_HUMAN_REVIEW which requires explicit
    human approval.
    """

    def __init__(
        self,
        *,
        policy: Q6EvaluationPolicy | None = None,
    ) -> None:
        self.policy = policy or Q6EvaluationPolicy()

    def evaluate(
        self,
        *,
        # Preflight conditions
        preflight_passed: bool,
        holdout_eligible: bool,
        data_quality_passed: bool,
        # Candidate lock
        candidate_lock_valid: bool,
        candidate_lock_hash: str | None,
        # Evaluation results
        trade_count: int,
        net_return_bp: float | None,
        gross_return_bp: float | None,
        profit_factor: float | None,
        max_drawdown_bp: float | None,
        win_rate: float | None,
        expectancy_bp: float | None,
        # Benchmark comparison
        outperforms_buy_and_hold: bool | None,
        outperforms_cash: bool | None,
        outperforms_majority: bool | None,
        # Stability
        regime_robustness: float | None,
        chronological_stable: bool | None,
        # Statistical
        statistically_significant: bool | None,
        sufficient_sample: bool | None,
        # Cost stress
        profitable_at_all_cost_levels: bool | None,
        # Data integrity
        data_integrity_ok: bool,
        # Cost stress at required multiplier
        stress_passed: bool | None,
    ) -> PromotionGateResult:
        """Evaluate promotion gate for a candidate.

        Returns PromotionGateResult with status and authorization flags.
        """
        reasons: list[str] = []
        checks: dict[str, Any] = {}

        # ---- Step 1: Preflight ----
        if not preflight_passed:
            reasons.append("Q6 preflight failed - prospective data not eligible")
            checks["preflight"] = False
            return PromotionGateResult(
                status=BLOCKED,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["preflight"] = True

        # ---- Step 2: Holdout eligible ----
        if not holdout_eligible:
            reasons.append("Prospective holdout not ELIGIBLE_FOR_EVALUATION")
            checks["holdout_eligible"] = False
            return PromotionGateResult(
                status=BLOCKED,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["holdout_eligible"] = True

        # ---- Step 3: Data quality ----
        if not data_quality_passed:
            reasons.append("Data quality checks failed")
            checks["data_quality"] = False
            return PromotionGateResult(
                status=BLOCKED,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["data_quality"] = True

        # ---- Step 4: Candidate lock ----
        if not candidate_lock_valid:
            reasons.append("Candidate lock missing or invalid (CANDIDATE_LOCK_MISSING)")
            checks["candidate_lock"] = False
            return PromotionGateResult(
                status=BLOCKED,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["candidate_lock"] = True
        checks["candidate_lock_hash"] = candidate_lock_hash

        # ---- Step 5: Sufficient observations ----
        if trade_count < self.policy.min_trades:
            reasons.append(f"Trade count {trade_count} below minimum {self.policy.min_trades}")
            checks["min_trades"] = False
            return PromotionGateResult(
                status=INSUFFICIENT_EVIDENCE,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["min_trades"] = True

        # ---- Step 6: Sufficient sample for statistics ----
        if not sufficient_sample:
            reasons.append("Insufficient sample size for statistical validation")
            checks["sufficient_sample"] = False
            return PromotionGateResult(
                status=INSUFFICIENT_EVIDENCE,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["sufficient_sample"] = True

        # ---- Step 7: Positive net expectancy ----
        if expectancy_bp is None or expectancy_bp <= 0:
            reasons.append(f"Non-positive net expectancy: {expectancy_bp} bp")
            checks["positive_expectancy"] = False
            return PromotionGateResult(
                status=EDGE_NOT_FOUND,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["positive_expectancy"] = True

        # ---- Step 8: Positive performance after costs ----
        if net_return_bp is None or net_return_bp <= 0:
            reasons.append(f"Non-positive net return after costs: {net_return_bp} bp")
            checks["positive_net_after_costs"] = False
            return PromotionGateResult(
                status=EDGE_NOT_FOUND,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["positive_net_after_costs"] = True

        # ---- Step 9: Acceptable drawdown ----
        if max_drawdown_bp is not None and max_drawdown_bp > self.policy.max_drawdown_bp:
            reasons.append(
                f"Max drawdown {max_drawdown_bp} bp exceeds limit {self.policy.max_drawdown_bp} bp"
            )
            checks["acceptable_drawdown"] = False
            return PromotionGateResult(
                status=EDGE_NOT_FOUND,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["acceptable_drawdown"] = True

        # ---- Step 10: Benchmark comparison ----
        if not (outperforms_buy_and_hold or outperforms_cash or outperforms_majority):
            reasons.append("Strategy does not outperform any benchmark")
            checks["benchmark_comparison"] = False
            return PromotionGateResult(
                status=EDGE_NOT_FOUND,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["benchmark_comparison"] = True

        # ---- Step 11: Statistical validity ----
        if not statistically_significant:
            reasons.append("Results not statistically significant")
            checks["statistical_validity"] = False
            return PromotionGateResult(
                status=EDGE_NOT_FOUND,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["statistical_validity"] = True

        # ---- Step 12: Regime robustness ----
        if regime_robustness is not None and regime_robustness < 0.5:
            reasons.append(f"Regime robustness {regime_robustness:.0%} below 50% threshold")
            checks["regime_robustness"] = False
            return PromotionGateResult(
                status=EDGE_DETECTED_NOT_ROBUST,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["regime_robustness"] = True

        # ---- Step 13: Chronological stability ----
        if chronological_stable is not None and not chronological_stable:
            reasons.append("Chronological stability check failed")
            checks["chronological_stability"] = False
            return PromotionGateResult(
                status=EDGE_DETECTED_NOT_ROBUST,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["chronological_stability"] = True

        # ---- Step 14: Cost stress at required multiplier ----
        if not profitable_at_all_cost_levels:
            reasons.append(
                f"Not profitable at {self.policy.required_stress_multiplier}x cost stress"
            )
            checks["cost_stress"] = False
            return PromotionGateResult(
                status=EDGE_DETECTED_NOT_ROBUST,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["cost_stress"] = True

        # ---- Step 15: Data integrity ----
        if not data_integrity_ok:
            reasons.append("Data integrity check failed")
            checks["data_integrity"] = False
            return PromotionGateResult(
                status=BLOCKED,
                eligible_for_human_review=False,
                human_authorized=False,
                execution_authorized=False,
                reasons=reasons,
                checks=checks,
            )
        checks["data_integrity"] = True

        # ---- All checks passed ----
        # Maximum outcome: VALIDATION_PASSED_PENDING_HUMAN_REVIEW
        # Human approval required, execution never authorized
        return PromotionGateResult(
            status=VALIDATION_PASSED_PENDING_HUMAN_REVIEW,
            eligible_for_human_review=True,
            human_authorized=False,
            execution_authorized=False,
            reasons=["All mandatory requirements passed - pending human review"],
            checks=checks,
        )


__all__ = [
    "BLOCKED",
    "EDGE_DETECTED_NOT_ROBUST",
    "EDGE_NOT_FOUND",
    "INSUFFICIENT_EVIDENCE",
    "PROMOTION_STATUSES",
    "REJECTED",
    "VALIDATION_PASSED_PENDING_HUMAN_REVIEW",
    "PromotionGate",
    "PromotionGateResult",
]
