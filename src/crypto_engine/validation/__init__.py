"""Phase 3 Track B validation layer: purged walk-forward + holdout + metrics.

Research-level only. This package is NOT a backtester: trade-cost numbers
are documented cost-aware proxies (see :mod:`metrics`), and execution,
portfolio, and fill-level concerns belong to Phase 6.
"""

from __future__ import annotations

from crypto_engine.validation.experiments import (
    ExperimentRegistry,
    bonferroni_threshold,
    deflated_sharpe,
    experiment_id,
    holm_thresholds,
    pbo_note,
    selection_warning,
    validate_experiment,
)
from crypto_engine.validation.folds import (
    Fold,
    InsufficientDataError,
    exclusion_table,
    label_intervals,
    make_folds,
)
from crypto_engine.validation.holdout import (
    HoldoutLedger,
    adequacy_report,
    split_holdout,
)
from crypto_engine.validation.metrics import (
    aggregate_folds,
    baseline_comparison,
    classification_metrics,
    proxy_summary,
    score_metrics,
    summarize_folds,
    trade_proxy_returns,
)
from crypto_engine.validation.robustness import (
    block_bootstrap_ci,
    fee_slippage_grid,
    leave_one_fold_out,
    permutation_mc,
)

__all__ = [
    "ExperimentRegistry",
    "Fold",
    "HoldoutLedger",
    "InsufficientDataError",
    "adequacy_report",
    "aggregate_folds",
    "baseline_comparison",
    "block_bootstrap_ci",
    "bonferroni_threshold",
    "classification_metrics",
    "deflated_sharpe",
    "exclusion_table",
    "experiment_id",
    "fee_slippage_grid",
    "holm_thresholds",
    "label_intervals",
    "leave_one_fold_out",
    "make_folds",
    "pbo_note",
    "permutation_mc",
    "proxy_summary",
    "score_metrics",
    "selection_warning",
    "split_holdout",
    "summarize_folds",
    "trade_proxy_returns",
    "validate_experiment",
]
