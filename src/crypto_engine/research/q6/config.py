"""Q6 configuration, candidate lock, and evaluation policy.

All thresholds are predeclared and must NOT be tuned against prospective
outcomes. They define when a candidate can reach EDGE_DETECTED_NOT_ROBUST
or VALIDATION_PASSED_PENDING_HUMAN_REVIEW.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_Q6_MANIFEST_PATH = None  # Set at runtime
DEFAULT_Q6_HISTORY_PATH = None  # Set at runtime

Q6_SCHEMA_VERSION = "q6-evaluation-manifest.v1"
Q6_EVALUATION_SCHEMA_VERSION = "q6-evaluation-result.v1"

#: Default evaluation policy thresholds (predeclared, not tuned).
DEFAULT_MIN_TRADES = 100
DEFAULT_MIN_PROFIT_FACTOR = 1.10
DEFAULT_MAX_DRAWDOWN_BP = 5000.0  # 50% max drawdown in bp
DEFAULT_MIN_POSITIVE_REGIME_FRACTION = 0.50
DEFAULT_REQUIRED_STRESS_MULTIPLIER = 2.0
DEFAULT_MAX_PREDICTION_COLLAPSE_SHARE = 0.90
DEFAULT_MIN_FOLDS = 3
DEFAULT_MIN_SAMPLE_SIZE_FOR_STATISTICS = 30
DEFAULT_BOOTSTRAP_SEED = 42
DEFAULT_PERMUTATION_SEED = 42
DEFAULT_PERMUTATION_N_REPLICATES = 1000
DEFAULT_BOOTSTRAP_N_REPLICATES = 1000


@dataclass(frozen=True, slots=True)
class Q6EvaluationPolicy:
    """Predeclared evaluation policy for Q6 prospective evaluation.

    These thresholds govern when a candidate can advance through the
    evaluation pipeline. They must not be tuned against prospective results.
    """

    min_trades: int = DEFAULT_MIN_TRADES
    min_profit_factor: float = DEFAULT_MIN_PROFIT_FACTOR
    max_drawdown_bp: float = DEFAULT_MAX_DRAWDOWN_BP
    min_positive_regime_fraction: float = DEFAULT_MIN_POSITIVE_REGIME_FRACTION
    required_stress_multiplier: float = DEFAULT_REQUIRED_STRESS_MULTIPLIER
    max_prediction_collapse_share: float = DEFAULT_MAX_PREDICTION_COLLAPSE_SHARE
    min_folds: int = DEFAULT_MIN_FOLDS
    min_sample_size_for_statistics: int = DEFAULT_MIN_SAMPLE_SIZE_FOR_STATISTICS
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED
    permutation_seed: int = DEFAULT_PERMUTATION_SEED
    permutation_n_replicates: int = DEFAULT_PERMUTATION_N_REPLICATES
    bootstrap_n_replicates: int = DEFAULT_BOOTSTRAP_N_REPLICATES

    def __post_init__(self) -> None:
        if self.min_trades < 1:
            raise ValueError("min_trades must be >= 1")
        if not self.min_profit_factor > 0.0:
            raise ValueError("min_profit_factor must be > 0")
        if self.max_drawdown_bp is not None and self.max_drawdown_bp <= 0:
            raise ValueError("max_drawdown_bp must be > 0")
        if not 0.0 < self.min_positive_regime_fraction <= 1.0:
            raise ValueError("min_positive_regime_fraction must be in (0, 1]")
        if not 1.0 <= self.required_stress_multiplier <= 5.0:
            raise ValueError("required_stress_multiplier must be in [1, 5]")
        if not 0.0 < self.max_prediction_collapse_share <= 1.0:
            raise ValueError("max_prediction_collapse_share must be in (0, 1]")
        if self.min_folds < 1:
            raise ValueError("min_folds must be >= 1")
        if self.min_sample_size_for_statistics < 1:
            raise ValueError("min_sample_size_for_statistics must be >= 1")


@dataclass(frozen=True, slots=True)
class CandidateLock:
    """Immutable lock on a previously developed strategy candidate.

    The candidate definitions must be demonstrably frozen before prospective
    outcomes are examined. If candidate provenance or configuration hashes
    are missing, the candidate cannot be evaluated and returns
    CANDIDATE_LOCK_MISSING.
    """

    candidate_id: str
    model_name: str
    model_artifact_hash: str
    model_config_hash: str
    feature_config_hash: str
    target_config_hash: str
    strategy_config_hash: str
    entry_exit_rules: dict[str, Any]
    thresholds: dict[str, Any]
    calibration_status: str
    cost_assumptions: dict[str, Any]
    risk_limits: dict[str, Any]
    candidate_creation_timestamp: str
    associated_research_freeze_id: str
    associated_prospective_collection_id: str
    lock_timestamp: str
    lock_hash: str

    def is_valid(self) -> bool:
        """Return True if all required lock fields are present and non-empty."""
        required_fields = [
            self.candidate_id,
            self.model_name,
            self.model_artifact_hash,
            self.model_config_hash,
            self.feature_config_hash,
            self.target_config_hash,
            self.strategy_config_hash,
            self.associated_research_freeze_id,
            self.associated_prospective_collection_id,
        ]
        return all(bool(f) for f in required_fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "model_name": self.model_name,
            "model_artifact_hash": self.model_artifact_hash,
            "model_config_hash": self.model_config_hash,
            "feature_config_hash": self.feature_config_hash,
            "target_config_hash": self.target_config_hash,
            "strategy_config_hash": self.strategy_config_hash,
            "entry_exit_rules": self.entry_exit_rules,
            "thresholds": self.thresholds,
            "calibration_status": self.calibration_status,
            "cost_assumptions": self.cost_assumptions,
            "risk_limits": self.risk_limits,
            "candidate_creation_timestamp": self.candidate_creation_timestamp,
            "associated_research_freeze_id": self.associated_research_freeze_id,
            "associated_prospective_collection_id": self.associated_prospective_collection_id,
            "lock_timestamp": self.lock_timestamp,
            "lock_hash": self.lock_hash,
        }


class CandidateLockError(Exception):
    """Raised when a candidate lock is missing or invalid."""


def compute_lock_hash(candidate: CandidateLock) -> str:
    """Compute deterministic hash over candidate lock fields."""
    import hashlib
    import json

    payload = json.dumps(
        {
            "candidate_id": candidate.candidate_id,
            "model_name": candidate.model_name,
            "model_artifact_hash": candidate.model_artifact_hash,
            "model_config_hash": candidate.model_config_hash,
            "feature_config_hash": candidate.feature_config_hash,
            "target_config_hash": candidate.target_config_hash,
            "strategy_config_hash": candidate.strategy_config_hash,
            "entry_exit_rules": candidate.entry_exit_rules,
            "thresholds": candidate.thresholds,
            "calibration_status": candidate.calibration_status,
            "cost_assumptions": candidate.cost_assumptions,
            "risk_limits": candidate.risk_limits,
        },
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "DEFAULT_BOOTSTRAP_N_REPLICATES",
    "DEFAULT_BOOTSTRAP_SEED",
    "DEFAULT_MAX_DRAWDOWN_BP",
    "DEFAULT_MAX_PREDICTION_COLLAPSE_SHARE",
    "DEFAULT_MIN_FOLDS",
    "DEFAULT_MIN_POSITIVE_REGIME_FRACTION",
    "DEFAULT_MIN_PROFIT_FACTOR",
    "DEFAULT_MIN_SAMPLE_SIZE_FOR_STATISTICS",
    "DEFAULT_MIN_TRADES",
    "DEFAULT_PERMUTATION_N_REPLICATES",
    "DEFAULT_PERMUTATION_SEED",
    "DEFAULT_Q6_HISTORY_PATH",
    "DEFAULT_Q6_MANIFEST_PATH",
    "DEFAULT_REQUIRED_STRESS_MULTIPLIER",
    "Q6_EVALUATION_SCHEMA_VERSION",
    "Q6_SCHEMA_VERSION",
    "CandidateLock",
    "CandidateLockError",
    "Q6EvaluationPolicy",
    "compute_lock_hash",
]
