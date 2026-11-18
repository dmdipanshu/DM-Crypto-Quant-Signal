"""Q6 market regime analysis module.

Analyzes strategy performance by chronological subperiods, rolling windows,
and market regimes (bullish, bearish, sideways, high/low volatility).

Regime classification is predefined and not redefined after inspecting
candidate performance.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def classify_regime(
    *,
    returns_bp: list[float],
    volatility_bp: list[float] | None = None,
    window: int = 24,
) -> list[str]:
    """Classify each period into a market regime.

    Regimes:
      - bullish: positive trend, normal volatility
      - bearish: negative trend, normal volatility
      - sideways: flat trend, normal volatility
      - high_volatility: high volatility regardless of trend
      - low_volatility: low volatility regardless of trend

    Classification is based on rolling statistics and is predefined -
    not redefined after inspecting candidate performance.
    """
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)
    if n == 0:
        return []

    if volatility_bp is None:
        # Estimate volatility as rolling std of returns
        vol = np.zeros(n)
        for i in range(n):
            start = max(0, i - window + 1)
            vol[i] = np.std(returns[start : i + 1])
    else:
        vol = np.asarray(volatility_bp, dtype=float)

    vol_median = float(np.median(vol))
    vol_high_threshold = vol_median * 1.5
    vol_low_threshold = vol_median * 0.5

    regimes = []
    for i in range(n):
        # Rolling mean return for trend
        start = max(0, i - window + 1)
        rolling_mean = float(np.mean(returns[start : i + 1]))

        if vol[i] >= vol_high_threshold:
            regimes.append("high_volatility")
        elif vol[i] <= vol_low_threshold:
            regimes.append("low_volatility")
        elif rolling_mean > 0:
            regimes.append("bullish")
        elif rolling_mean < 0:
            regimes.append("bearish")
        else:
            regimes.append("sideways")

    return regimes


def compute_regime_performance(
    *,
    returns_bp: list[float],
    regimes: list[str],
) -> dict[str, Any]:
    """Compute performance metrics for each market regime.

    Returns per-regime: observation count, trade count, net return,
    profit factor, drawdown, exposure, robustness.
    """
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n != len(regimes):
        raise ValueError("returns_bp and regimes must have same length")

    unique_regimes = sorted(set(regimes))
    regime_results: dict[str, Any] = {}

    for regime in unique_regimes:
        mask = np.array([r == regime for r in regimes])
        regime_returns = returns[mask]
        n_regime = int(mask.sum())

        if n_regime == 0:
            continue

        wins = regime_returns[regime_returns > 0]
        losses = regime_returns[regime_returns < 0]
        gross_profit = float(wins.sum()) if wins.size else 0.0
        gross_loss = float(-losses.sum()) if losses.size else 0.0
        net_return = float(regime_returns.sum())

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        win_rate = float((regime_returns > 0).mean()) if n_regime > 0 else None
        expectancy = float(regime_returns.mean()) if n_regime > 0 else None

        # Max drawdown within regime
        equity = 10_000.0
        peak = equity
        max_dd = 0.0
        for value in regime_returns:
            equity = max(0.0, equity * (1.0 + float(value) / 10_000.0))
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

        regime_results[regime] = {
            "observation_count": n_regime,
            "trade_count": n_regime,
            "net_return_bp": net_return,
            "gross_profit_bp": gross_profit,
            "gross_loss_bp": gross_loss,
            "profit_factor": profit_factor,
            "win_rate": win_rate,
            "expectancy_bp": expectancy,
            "max_drawdown_bp": float(max_dd),
            "fraction_of_total": n_regime / n if n > 0 else 0.0,
        }

    return regime_results


def compute_rolling_performance(
    *,
    returns_bp: list[float],
    window: int = 24,
) -> list[dict[str, Any]]:
    """Compute rolling window performance metrics.

    Returns one entry per window position with net return, profit factor,
    and drawdown.
    """
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n < window:
        return []

    results = []
    for i in range(window - 1, n):
        window_returns = returns[i - window + 1 : i + 1]
        wins = window_returns[window_returns > 0]
        losses = window_returns[window_returns < 0]
        gross_profit = float(wins.sum()) if wins.size else 0.0
        gross_loss = float(-losses.sum()) if losses.size else 0.0
        net_return = float(window_returns.sum())

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        win_rate = float((window_returns > 0).mean())

        # Max drawdown within window
        equity = 10_000.0
        peak = equity
        max_dd = 0.0
        for value in window_returns:
            equity = max(0.0, equity * (1.0 + float(value) / 10_000.0))
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

        results.append(
            {
                "window_end_index": i,
                "window_size": window,
                "net_return_bp": net_return,
                "profit_factor": profit_factor,
                "win_rate": win_rate,
                "max_drawdown_bp": float(max_dd),
            }
        )

    return results


def compute_chronological_subperiods(
    *,
    returns_bp: list[float],
    n_subperiods: int = 4,
) -> list[dict[str, Any]]:
    """Split returns into chronological subperiods and compute per-subperiod metrics.

    Useful for detecting performance decay or regime-specific behavior.
    """
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n < n_subperiods:
        n_subperiods = n

    if n == 0:
        return []

    subperiod_size = n // n_subperiods
    remainder = n % n_subperiods

    results = []
    start = 0
    for i in range(n_subperiods):
        size = subperiod_size + (1 if i < remainder else 0)
        end = start + size
        sub_returns = returns[start:end]

        wins = sub_returns[sub_returns > 0]
        losses = sub_returns[sub_returns < 0]
        gross_profit = float(wins.sum()) if wins.size else 0.0
        gross_loss = float(-losses.sum()) if losses.size else 0.0
        net_return = float(sub_returns.sum())

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        win_rate = float((sub_returns > 0).mean()) if len(sub_returns) > 0 else None

        equity = 10_000.0
        peak = equity
        max_dd = 0.0
        for value in sub_returns:
            equity = max(0.0, equity * (1.0 + float(value) / 10_000.0))
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

        results.append(
            {
                "subperiod_index": i,
                "start_index": start,
                "end_index": end,
                "observation_count": len(sub_returns),
                "net_return_bp": net_return,
                "profit_factor": profit_factor,
                "win_rate": win_rate,
                "max_drawdown_bp": float(max_dd),
            }
        )
        start = end

    return results


class RegimeAnalyzer:
    """Market regime analysis for prospective evaluation."""

    def __init__(
        self,
        *,
        window: int = 24,
        n_subperiods: int = 4,
    ) -> None:
        self.window = window
        self.n_subperiods = n_subperiods

    def analyze(
        self,
        *,
        returns_bp: list[float],
        volatility_bp: list[float] | None = None,
    ) -> dict[str, Any]:
        """Run complete regime analysis.

        Returns dict with regime classification, per-regime performance,
        rolling performance, and chronological subperiods.
        """
        regimes = classify_regime(
            returns_bp=returns_bp,
            volatility_bp=volatility_bp,
            window=self.window,
        )

        regime_performance = compute_regime_performance(
            returns_bp=returns_bp,
            regimes=regimes,
        )

        rolling = compute_rolling_performance(
            returns_bp=returns_bp,
            window=self.window,
        )

        subperiods = compute_chronological_subperiods(
            returns_bp=returns_bp,
            n_subperiods=self.n_subperiods,
        )

        # Count regimes
        regime_counts: dict[str, int] = {}
        for r in regimes:
            regime_counts[r] = regime_counts.get(r, 0) + 1

        # Check if strategy is robust across regimes
        # A strategy is regime-robust if positive net return in majority of regimes
        positive_regimes = sum(
            1
            for rp in regime_performance.values()
            if rp["net_return_bp"] is not None and rp["net_return_bp"] > 0
        )
        total_regimes = len(regime_performance)
        regime_robustness = positive_regimes / total_regimes if total_regimes > 0 else 0.0

        return {
            "regime_classification": regimes,
            "regime_counts": regime_counts,
            "regime_performance": regime_performance,
            "rolling_performance": rolling,
            "chronological_subperiods": subperiods,
            "regime_robustness": regime_robustness,
            "positive_regime_count": positive_regimes,
            "total_regime_count": total_regimes,
            "robust_across_regimes": regime_robustness >= 0.5,
        }


__all__ = [
    "RegimeAnalyzer",
    "classify_regime",
    "compute_chronological_subperiods",
    "compute_regime_performance",
    "compute_rolling_performance",
]
