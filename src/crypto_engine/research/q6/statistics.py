"""Q6 statistical validation module.

Implements bootstrap confidence intervals, permutation testing, and
statistical sufficiency checks for prospective evaluation results.

Accounts for autocorrelation and overlapping positions - does not apply
independent-sample assumptions blindly to time-series returns.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _block_bootstrap(
    returns: np.ndarray,
    *,
    block_size: int,
    n_replicates: int,
    seed: int,
) -> np.ndarray:
    """Block bootstrap for serially dependent returns.

    Uses moving block bootstrap to preserve autocorrelation structure.
    """
    rng = np.random.default_rng(seed)
    n = len(returns)
    if n < block_size:
        block_size = max(1, n)

    n_blocks = math.ceil(n / block_size)
    bootstrap_means = np.zeros(n_replicates)

    for i in range(n_replicates):
        # Sample blocks with replacement
        block_starts = rng.integers(0, n - block_size + 1, size=n_blocks)
        bootstrap_sample = np.concatenate(
            [returns[start : start + block_size] for start in block_starts]
        )[:n]
        bootstrap_means[i] = float(np.mean(bootstrap_sample))

    return bootstrap_means


def compute_bootstrap_confidence_interval(
    *,
    returns_bp: list[float],
    confidence_level: float = 0.95,
    n_replicates: int = 1000,
    seed: int = 42,
    block_size: int | None = None,
) -> dict[str, Any]:
    """Compute bootstrap confidence interval for mean return.

    Uses block bootstrap to account for serial dependence in time-series
    returns. Does not apply independent-sample assumptions blindly.

    Returns dict with:
      - mean: sample mean
      - ci_lower, ci_upper: confidence interval bounds
      - confidence_level
      - n_replicates
      - block_size
      - sufficient_sample: whether n >= min_sample_size
    """
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n < 2:
        return {
            "mean": None,
            "ci_lower": None,
            "ci_upper": None,
            "confidence_level": confidence_level,
            "n_replicates": n_replicates,
            "block_size": None,
            "sufficient_sample": False,
            "reason": "Insufficient sample (n < 2)",
        }

    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1)) if n > 1 else 0.0

    # Determine block size (rule of thumb: n^(1/3))
    if block_size is None:
        block_size = max(1, round(n ** (1.0 / 3.0)))

    if n < block_size:
        block_size = n

    bootstrap_means = _block_bootstrap(
        returns, block_size=block_size, n_replicates=n_replicates, seed=seed
    )

    alpha = 1.0 - confidence_level
    lower_percentile = 100 * (alpha / 2)
    upper_percentile = 100 * (1 - alpha / 2)

    ci_lower = float(np.percentile(bootstrap_means, lower_percentile))
    ci_upper = float(np.percentile(bootstrap_means, upper_percentile))

    min_sample_size = 30
    sufficient_sample = n >= min_sample_size

    return {
        "mean": mean,
        "std": std,
        "ci_lower": ci_lower,
        "ci_upper": ci_upper,
        "confidence_level": confidence_level,
        "n_replicates": n_replicates,
        "block_size": block_size,
        "sufficient_sample": sufficient_sample,
        "sample_size": n,
        "reason": None if sufficient_sample else f"Sample size {n} < minimum {min_sample_size}",
    }


def compute_permutation_test(
    *,
    strategy_returns_bp: list[float],
    benchmark_returns_bp: list[float] | None = None,
    n_replicates: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Permutation test for strategy vs benchmark excess return.

    Tests the null hypothesis that strategy returns come from the same
    distribution as benchmark returns (or zero if no benchmark).

    Returns p-value and test statistic.
    """
    strategy = np.asarray(strategy_returns_bp, dtype=float)

    if benchmark_returns_bp is not None:
        benchmark = np.asarray(benchmark_returns_bp, dtype=float)
        n_benchmark = len(benchmark)
        if n_benchmark == 0:
            benchmark = None

    if benchmark_returns_bp is None or len(benchmark_returns_bp) == 0:
        # Test against zero
        observed_stat = float(np.mean(strategy))
        pooled = strategy
        rng = np.random.default_rng(seed)
        perm_stats = np.zeros(n_replicates)
        for i in range(n_replicates):
            permuted = rng.permutation(pooled)
            perm_stats[i] = float(np.mean(permuted))
        # Two-sided p-value
        p_value = float(np.mean(np.abs(perm_stats) >= np.abs(observed_stat)))
        return {
            "observed_statistic": observed_stat,
            "p_value": p_value,
            "null_hypothesis": "strategy mean return = 0",
            "n_replicates": n_replicates,
            "seed": seed,
            "significant_at_005": p_value < 0.05,
        }

    # Test strategy vs benchmark
    observed_stat = float(np.mean(strategy) - np.mean(benchmark))
    pooled = np.concatenate([strategy, benchmark])
    n1 = len(strategy)
    rng = np.random.default_rng(seed)
    perm_stats = np.zeros(n_replicates)
    for i in range(n_replicates):
        permuted = rng.permutation(pooled)
        perm_strategy = permuted[:n1]
        perm_benchmark = permuted[n1:]
        perm_stats[i] = float(np.mean(perm_strategy) - np.mean(perm_benchmark))

    p_value = float(np.mean(np.abs(perm_stats) >= np.abs(observed_stat)))
    return {
        "observed_statistic": observed_stat,
        "p_value": p_value,
        "null_hypothesis": "strategy returns = benchmark returns",
        "n_replicates": n_replicates,
        "seed": seed,
        "significant_at_005": p_value < 0.05,
    }


def compute_probability_of_positive_expectancy(
    *,
    returns_bp: list[float],
    n_replicates: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Compute probability that true expectancy is positive.

    Uses bootstrap to estimate P(mean_return > 0).
    """
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n < 2:
        return {
            "probability_positive": None,
            "observed_mean_bp": None,
            "n_replicates": n_replicates,
            "sufficient_sample": False,
        }

    observed_mean = float(np.mean(returns))
    bootstrap_means = _block_bootstrap(
        returns,
        block_size=max(1, round(n ** (1.0 / 3.0))),
        n_replicates=n_replicates,
        seed=seed,
    )
    prob_positive = float(np.mean(bootstrap_means > 0))

    min_sample_size = 30
    sufficient_sample = n >= min_sample_size

    return {
        "probability_positive": prob_positive,
        "observed_mean_bp": observed_mean,
        "n_replicates": n_replicates,
        "sufficient_sample": sufficient_sample,
        "sample_size": n,
        "reason": None if sufficient_sample else f"Sample size {n} < minimum {min_sample_size}",
    }


def compute_sharpe_ratio(
    *,
    returns_bp: list[float],
    risk_free_bp_per_period: float = 0.0,
    periods_per_year: int = 8760,  # Hourly bars per year
) -> dict[str, Any]:
    """Compute Sharpe ratio for strategy returns.

    Returns None if insufficient data or zero volatility.
    """
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n < 2:
        return {"sharpe_ratio": None, "sufficient_sample": False}

    mean_return = float(np.mean(returns))
    std_return = float(np.std(returns, ddof=1))

    if std_return == 0 or not math.isfinite(std_return):
        return {
            "sharpe_ratio": None,
            "sufficient_sample": n >= 30,
            "reason": "Zero volatility",
        }

    # Annualized Sharpe
    excess_return = mean_return - risk_free_bp_per_period
    sharpe = float(excess_return / std_return * math.sqrt(periods_per_year))

    min_sample_size = 30
    sufficient_sample = n >= min_sample_size

    return {
        "sharpe_ratio": sharpe,
        "mean_return_bp": mean_return,
        "std_return_bp": std_return,
        "periods_per_year": periods_per_year,
        "sufficient_sample": sufficient_sample,
        "sample_size": n,
        "reason": None if sufficient_sample else f"Sample size {n} < minimum {min_sample_size}",
    }


def compute_sortino_ratio(
    *,
    returns_bp: list[float],
    risk_free_bp_per_period: float = 0.0,
    periods_per_year: int = 8760,
) -> dict[str, Any]:
    """Compute Sortino ratio (downside deviation instead of total std)."""
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n < 2:
        return {"sortino_ratio": None, "sufficient_sample": False}

    mean_return = float(np.mean(returns))
    downside_returns = returns[returns < risk_free_bp_per_period]
    downside_std = float(np.std(downside_returns, ddof=1)) if len(downside_returns) > 1 else 0.0

    if downside_std == 0 or not math.isfinite(downside_std):
        return {
            "sortino_ratio": None,
            "sufficient_sample": n >= 30,
            "reason": "Zero downside deviation",
        }

    excess_return = mean_return - risk_free_bp_per_period
    sortino = float(excess_return / downside_std * math.sqrt(periods_per_year))

    min_sample_size = 30
    sufficient_sample = n >= min_sample_size

    return {
        "sortino_ratio": sortino,
        "mean_return_bp": mean_return,
        "downside_std_bp": downside_std,
        "periods_per_year": periods_per_year,
        "sufficient_sample": sufficient_sample,
        "sample_size": n,
        "reason": None if sufficient_sample else f"Sample size {n} < minimum {min_sample_size}",
    }


def compute_calmar_ratio(
    *,
    returns_bp: list[float],
    max_drawdown_bp: float,
    periods_per_year: int = 8760,
) -> dict[str, Any]:
    """Compute Calmar ratio (annualized return / max drawdown)."""
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n < 2 or max_drawdown_bp <= 0:
        return {
            "calmar_ratio": None,
            "sufficient_sample": n >= 30,
        }

    mean_return = float(np.mean(returns))
    annualized_return = mean_return * periods_per_year
    calmar = float(annualized_return / max_drawdown_bp)

    min_sample_size = 30
    sufficient_sample = n >= min_sample_size

    return {
        "calmar_ratio": calmar,
        "annualized_return_bp": annualized_return,
        "max_drawdown_bp": max_drawdown_bp,
        "sufficient_sample": sufficient_sample,
        "sample_size": n,
    }


class StatisticalValidator:
    """Statistical validation for prospective evaluation results."""

    def __init__(
        self,
        *,
        bootstrap_n_replicates: int = 1000,
        permutation_n_replicates: int = 1000,
        bootstrap_seed: int = 42,
        permutation_seed: int = 42,
        min_sample_size: int = 30,
    ) -> None:
        self.bootstrap_n_replicates = bootstrap_n_replicates
        self.permutation_n_replicates = permutation_n_replicates
        self.bootstrap_seed = bootstrap_seed
        self.permutation_seed = permutation_seed
        self.min_sample_size = min_sample_size

    def validate(
        self,
        *,
        strategy_returns_bp: list[float],
        benchmark_returns_bp: list[float] | None = None,
        max_drawdown_bp: float = 0.0,
    ) -> dict[str, Any]:
        """Run complete statistical validation suite.

        Returns dict with all statistical tests and sufficiency flags.
        """
        n = len(strategy_returns_bp)
        sufficient = n >= self.min_sample_size

        ci = compute_bootstrap_confidence_interval(
            returns_bp=strategy_returns_bp,
            confidence_level=0.95,
            n_replicates=self.bootstrap_n_replicates,
            seed=self.bootstrap_seed,
        )

        perm_test = compute_permutation_test(
            strategy_returns_bp=strategy_returns_bp,
            benchmark_returns_bp=benchmark_returns_bp,
            n_replicates=self.permutation_n_replicates,
            seed=self.permutation_seed,
        )

        prob_positive = compute_probability_of_positive_expectancy(
            returns_bp=strategy_returns_bp,
            n_replicates=self.bootstrap_n_replicates,
            seed=self.bootstrap_seed,
        )

        sharpe = compute_sharpe_ratio(
            returns_bp=strategy_returns_bp,
            periods_per_year=8760,
        )

        sortino = compute_sortino_ratio(
            returns_bp=strategy_returns_bp,
            periods_per_year=8760,
        )

        calmar = compute_calmar_ratio(
            returns_bp=strategy_returns_bp,
            max_drawdown_bp=max_drawdown_bp,
            periods_per_year=8760,
        )

        # Statistical significance: CI excludes zero AND permutation p < 0.05
        ci_excludes_zero = (
            ci["ci_lower"] is not None
            and ci["ci_upper"] is not None
            and (ci["ci_lower"] > 0 or ci["ci_upper"] < 0)
        )
        statistically_significant = ci_excludes_zero and perm_test.get("significant_at_005", False)

        return {
            "sample_size": n,
            "sufficient_sample": sufficient,
            "bootstrap_ci": ci,
            "permutation_test": perm_test,
            "probability_positive_expectancy": prob_positive,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "ci_excludes_zero": ci_excludes_zero,
            "statistically_significant": statistically_significant,
            "overall_sufficient": sufficient and ci["sufficient_sample"],
        }


__all__ = [
    "StatisticalValidator",
    "compute_bootstrap_confidence_interval",
    "compute_calmar_ratio",
    "compute_permutation_test",
    "compute_probability_of_positive_expectancy",
    "compute_sharpe_ratio",
    "compute_sortino_ratio",
]
