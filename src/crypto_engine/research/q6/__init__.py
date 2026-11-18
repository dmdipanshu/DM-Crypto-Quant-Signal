"""Q6 prospective strategy evaluation and quantitative edge validation.

Evaluates previously developed and frozen strategy candidates against
genuinely prospective (post-freeze) market observations. All operations
are read-only with respect to prospective source data: no retraining,
no recalibration, no threshold tuning, no model promotion.

The evaluation answers whether any frozen strategy demonstrates genuine
economic edge on unseen data, answering:
  1. Positive net returns after costs?
  2. Outperform an appropriate benchmark?
  3. Survive realistic fees, spread, slippage, funding?
  4. Stable across time?
  5. Statistically distinguishable from noise?
  6. Survive adverse market regimes?
  7. Acceptable drawdown?
  8. Reproducible?
  9. Prospective dataset independent of model development?
  10. Enough evidence for human review?

A technically successful evaluation does NOT mean automatic promotion.
The most this framework can return is EDGE_DETECTED_NOT_ROBUST or
VALIDATION_PASSED_PENDING_HUMAN_REVIEW. Human approval and execution
authorization are always false.
"""

from crypto_engine.research.q6.accounting import EconomicAccounting
from crypto_engine.research.q6.benchmark import BenchmarkComparator
from crypto_engine.research.q6.config import (
    CandidateLock,
    CandidateLockError,
    Q6EvaluationPolicy,
    compute_lock_hash,
)
from crypto_engine.research.q6.evaluation import Q6EvaluationEngine
from crypto_engine.research.q6.governance import (
    BLOCKED,
    EDGE_DETECTED_NOT_ROBUST,
    EDGE_NOT_FOUND,
    INSUFFICIENT_EVIDENCE,
    Q6_STATUSES,
    REJECTED,
    VALIDATION_PASSED_PENDING_HUMAN_REVIEW,
    assess_q6_readiness,
    historical_q4_rejection_preserved,
    no_prospective_data_leakage,
    q6_cannot_auto_promote,
)
from crypto_engine.research.q6.historical_adapter import (
    HISTORICAL_SCHEMA_VERSION,
    HistoricalDataAdapter,
)
from crypto_engine.research.q6.historical_manifest import (
    DEFAULT_HISTORICAL_HISTORY_PATH,
    DEFAULT_HISTORICAL_MANIFEST_PATH,
    build_historical_manifest,
    create_or_reuse_historical_manifest,
    load_historical_manifest_history,
    verify_historical_manifest,
)
from crypto_engine.research.q6.historical_pipeline import (
    HISTORICAL_EVALUATION_SCHEMA_VERSION,
    HistoricalValidationPipeline,
)
from crypto_engine.research.q6.historical_storage import (
    DEFAULT_HISTORICAL_STORAGE_ROOT,
    HistoricalStorage,
)
from crypto_engine.research.q6.manifest import EvaluationManifest
from crypto_engine.research.q6.preflight import PreflightBlockedError, run_preflight
from crypto_engine.research.q6.promotion import PromotionGate
from crypto_engine.research.q6.regime import RegimeAnalyzer
from crypto_engine.research.q6.robustness import RobustnessAnalyzer
from crypto_engine.research.q6.statistics import StatisticalValidator

__all__ = [
    "BLOCKED",
    "DEFAULT_HISTORICAL_HISTORY_PATH",
    "DEFAULT_HISTORICAL_MANIFEST_PATH",
    "DEFAULT_HISTORICAL_STORAGE_ROOT",
    "EDGE_DETECTED_NOT_ROBUST",
    "EDGE_NOT_FOUND",
    "HISTORICAL_EVALUATION_SCHEMA_VERSION",
    "HISTORICAL_MANIFEST_SCHEMA_VERSION",
    "HISTORICAL_SCHEMA_VERSION",
    "INSUFFICIENT_EVIDENCE",
    "Q6_STATUSES",
    "REJECTED",
    "VALIDATION_PASSED_PENDING_HUMAN_REVIEW",
    "BenchmarkComparator",
    "CandidateLock",
    "CandidateLockError",
    "EconomicAccounting",
    "EvaluationManifest",
    "HistoricalDataAdapter",
    "HistoricalStorage",
    "HistoricalValidationPipeline",
    "PreflightBlockedError",
    "PromotionGate",
    "Q6EvaluationEngine",
    "Q6EvaluationPolicy",
    "RegimeAnalyzer",
    "RobustnessAnalyzer",
    "StatisticalValidator",
    "assess_q6_readiness",
    "build_historical_manifest",
    "compute_lock_hash",
    "create_or_reuse_historical_manifest",
    "historical_q4_rejection_preserved",
    "load_historical_manifest_history",
    "no_prospective_data_leakage",
    "q6_cannot_auto_promote",
    "run_preflight",
    "verify_historical_manifest",
]
