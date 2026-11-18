"""Research-only quantitative evaluation and promotion governance.

This package has no exchange, order, paper-ledger, or AI-provider dependency.
"""

from crypto_engine.research.execution import evaluate_fixed_horizon_strategy
from crypto_engine.research.governance import (
    assess_holdout_integrity,
    evaluate_promotion_eligibility,
)

__all__ = [
    "assess_holdout_integrity",
    "evaluate_fixed_horizon_strategy",
    "evaluate_promotion_eligibility",
]
