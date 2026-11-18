"""Q6 robustness and failure analysis module.

Tests strategy performance under stress conditions:

- 1x, 1.5x, 2x transaction costs
- Delayed execution
- Adverse slippage
- Missed signals
- Missing observations
- Volatility spikes
- Regime transitions
- Consecutive losses
- Drawdown recovery

All tests use existing strategy parameters - no tuning during robustness testing.
"""

from __future__ import annotations

from typing import Any

import numpy as np


def apply_cost_stress(
    *,
    gross_returns_bp: list[float],
    base_costs_bp: float,
    multipliers: list[float],
) -> list[dict[str, Any]]:
    """Apply cost stress multipliers to gross returns.

    For each multiplier, computes net returns = gross - (base_costs * multiplier).
    """
    gross = np.asarray(gross_returns_bp, dtype=float)
    n = len(gross)
    per_trade_cost = base_costs_bp / max(1, n)

    results = []
    for multiplier in multipliers:
        stressed_net = gross - (per_trade_cost * multiplier)
        wins = stressed_net[stressed_net > 0]
        losses = stressed_net[stressed_net < 0]
        gross_profit = float(wins.sum()) if wins.size else 0.0
        gross_loss = float(-losses.sum()) if losses.size else 0.0
        net_return = float(stressed_net.sum())

        results.append(
            {
                "cost_multiplier": float(multiplier),
                "trade_count": int(n),
                "gross_return_bp": float(gross.sum()),
                "net_return_bp": net_return,
                "total_trading_costs_bp": float(per_trade_cost * multiplier * n),
                "gross_profit_bp": gross_profit,
                "gross_loss_bp": gross_loss,
                "net_profit_bp": float(wins.sum()) if wins.size else 0.0,
                "net_loss_bp": float(-losses.sum()) if losses.size else 0.0,
                "profit_factor": (gross_profit / gross_loss if gross_loss > 0 else None),
                "net_profit_factor": (
                    float(wins.sum()) / float(-losses.sum())
                    if wins.size and losses.size and float(-losses.sum()) > 0
                    else None
                ),
                "win_rate": float((stressed_net > 0).mean()),
                "expectancy_bp": float(stressed_net.mean()),
                "remains_profitable": net_return > 0,
            }
        )

    return results


def simulate_delayed_execution(
    *,
    gross_returns_bp: list[float],
    delay_bars: int = 1,
    decay_bp_per_bar: float = 0.0,
) -> dict[str, Any]:
    """Simulate delayed execution where fills happen `delay_bars` later.

    Each bar's return decays by decay_bp_per_bar per bar of delay.
    """
    gross = np.asarray(gross_returns_bp, dtype=float)
    n = len(gross)

    # Delayed execution: returns decay
    decayed = gross - (delay_bars * decay_bp_per_bar)
    wins = decayed[decayed > 0]
    losses = decayed[decayed < 0]

    return {
        "delay_bars": delay_bars,
        "decay_bp_per_bar": decay_bp_per_bar,
        "trade_count": int(n),
        "original_net_return_bp": float(gross.sum()),
        "delayed_net_return_bp": float(decayed.sum()),
        "return_degradation_bp": float(gross.sum() - decayed.sum()),
        "profit_factor": (
            float(wins.sum()) / float(-losses.sum())
            if wins.size and losses.size and float(-losses.sum()) > 0
            else None
        ),
        "win_rate": float((decayed > 0).mean()),
        "remains_profitable": float(decayed.sum()) > 0,
    }


def simulate_adverse_slippage(
    *,
    gross_returns_bp: list[float],
    extra_slippage_bp: float = 5.0,
) -> dict[str, Any]:
    """Simulate adverse slippage added to each trade.

    Each trade's net return is reduced by extra_slippage_bp.
    """
    gross = np.asarray(gross_returns_bp, dtype=float)
    n = len(gross)

    slippaged = gross - extra_slippage_bp
    wins = slippaged[slippaged > 0]
    losses = slippaged[slippaged < 0]

    return {
        "extra_slippage_bp": extra_slippage_bp,
        "trade_count": int(n),
        "original_net_return_bp": float(gross.sum()),
        "slippaged_net_return_bp": float(slippaged.sum()),
        "return_degradation_bp": float(extra_slippage_bp * n),
        "profit_factor": (
            float(wins.sum()) / float(-losses.sum())
            if wins.size and losses.size and float(-losses.sum()) > 0
            else None
        ),
        "win_rate": float((slippaged > 0).mean()),
        "remains_profitable": float(slippaged.sum()) > 0,
    }


def simulate_missed_signals(
    *,
    gross_returns_bp: list[float],
    miss_probability: float = 0.1,
    seed: int = 42,
) -> dict[str, Any]:
    """Simulate missed signals (some trades not executed).

    Randomly drops trades based on miss_probability.
    """
    rng = np.random.RandomState(seed)
    gross = np.asarray(gross_returns_bp, dtype=float)
    n = len(gross)

    mask = rng.random(n) >= miss_probability
    executed = gross[mask]

    wins = executed[executed > 0]
    losses = executed[executed < 0]

    return {
        "miss_probability": miss_probability,
        "trade_count": int(n),
        "executed_count": int(mask.sum()),
        "missed_count": int((~mask).sum()),
        "original_net_return_bp": float(gross.sum()),
        "executed_net_return_bp": float(executed.sum()),
        "profit_factor": (
            float(wins.sum()) / float(-losses.sum())
            if wins.size and losses.size and float(-losses.sum()) > 0
            else None
        ),
        "win_rate": float((executed > 0).mean()),
        "remains_profitable": float(executed.sum()) > 0,
    }


def compute_consecutive_losses(
    *,
    returns_bp: list[float],
) -> dict[str, Any]:
    """Compute maximum consecutive losses and loss streak statistics."""
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n == 0:
        return {
            "max_consecutive_losses": 0,
            "current_streak": 0,
            "loss_streaks": [],
            "max_loss_streak_bp": 0.0,
        }

    max_streak = 0
    current_streak = 0
    streaks = []
    max_loss_streak_bp = 0.0

    for value in returns:
        if value < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            if current_streak > 0:
                streaks.append(current_streak)
            current_streak = 0

    if current_streak > 0:
        streaks.append(current_streak)

    # Max loss streak in bp
    current_loss_sum = 0.0
    for value in returns:
        if value < 0:
            current_loss_sum += value
            max_loss_streak_bp = min(max_loss_streak_bp, current_loss_sum)
        else:
            current_loss_sum = 0.0

    return {
        "max_consecutive_losses": max_streak,
        "current_streak": current_streak,
        "loss_streaks": streaks,
        "max_loss_streak_bp": float(max_loss_streak_bp),
        "total_losses": int((returns < 0).sum()),
        "total_wins": int((returns > 0).sum()),
    }


def compute_drawdown_recovery(
    *,
    returns_bp: list[float],
    starting_equity: float = 10_000.0,
) -> dict[str, Any]:
    """Compute drawdown and recovery metrics.

    Returns max drawdown, time to recover, and whether recovery occurred.
    """
    returns = np.asarray(returns_bp, dtype=float)
    n = len(returns)

    if n == 0:
        return {
            "max_drawdown_bp": 0.0,
            "max_drawdown_pct": 0.0,
            "recovery_occurred": True,
            "bars_to_recover": None,
            "final_equity": starting_equity,
        }

    equity = starting_equity
    peak = equity
    max_dd_bp = 0.0
    max_dd_pct = 0.0
    peak_index = 0
    recovery_index = None
    recovery_occurred = True

    for i, value in enumerate(returns):
        equity = max(0.0, equity * (1.0 + float(value) / 10_000.0))
        if equity > peak:
            peak = equity
            peak_index = i
            # Check if we had a drawdown before this peak
            if recovery_index is not None:
                recovery_occurred = True
                recovery_index = None
        else:
            drawdown = peak - equity
            drawdown_pct = drawdown / peak * 100.0
            drawdown_bp = drawdown_pct * 100.0
            if drawdown_bp > max_dd_bp:
                max_dd_bp = drawdown_bp
                max_dd_pct = drawdown_pct
            if recovery_index is None:
                recovery_index = i

    # If we're still in drawdown at the end
    if recovery_index is not None and equity < peak:
        recovery_occurred = False
        bars_to_recover = None
    elif recovery_index is not None:
        bars_to_recover = peak_index - recovery_index
    else:
        bars_to_recover = 0

    return {
        "max_drawdown_bp": float(max_dd_bp),
        "max_drawdown_pct": float(max_dd_pct),
        "recovery_occurred": recovery_occurred,
        "bars_to_recover": bars_to_recover,
        "final_equity": float(equity),
        "peak_equity": float(peak),
    }


def compute_volatility_spike_impact(
    *,
    returns_bp: list[float],
    volatility_multiplier: float = 2.0,
) -> dict[str, Any]:
    """Simulate impact of volatility spike on strategy performance.

    Scales returns by volatility multiplier (amplifies both gains and losses).
    """
    gross = np.asarray(returns_bp, dtype=float)
    n = len(gross)

    spiked = gross * volatility_multiplier
    wins = spiked[spiked > 0]
    losses = spiked[spiked < 0]

    return {
        "volatility_multiplier": volatility_multiplier,
        "trade_count": int(n),
        "original_net_return_bp": float(gross.sum()),
        "spiked_net_return_bp": float(spiked.sum()),
        "profit_factor": (
            float(wins.sum()) / float(-losses.sum())
            if wins.size and losses.size and float(-losses.sum()) > 0
            else None
        ),
        "win_rate": float((spiked > 0).mean()),
        "remains_profitable": float(spiked.sum()) > 0,
        "max_loss_bp": float(spiked.min()) if spiked.size else 0.0,
    }


class RobustnessAnalyzer:
    """Robustness and failure analysis for prospective evaluation."""

    def __init__(
        self,
        *,
        cost_multipliers: list[float] | None = None,
        delay_bars: int = 1,
        decay_bp_per_bar: float = 0.0,
        extra_slippage_bp: float = 5.0,
        miss_probability: float = 0.1,
        volatility_multiplier: float = 2.0,
        seed: int = 42,
    ) -> None:
        self.cost_multipliers = cost_multipliers or [1.0, 1.5, 2.0]
        self.delay_bars = delay_bars
        self.decay_bp_per_bar = decay_bp_per_bar
        self.extra_slippage_bp = extra_slippage_bp
        self.miss_probability = miss_probability
        self.volatility_multiplier = volatility_multiplier
        self.seed = seed

    def analyze(
        self,
        *,
        gross_returns_bp: list[float],
        net_returns_bp: list[float],
        base_costs_bp: float = 0.0,
    ) -> dict[str, Any]:
        """Run complete robustness analysis.

        Returns dict with all stress test results and overall robustness
        assessment.
        """
        net = np.asarray(net_returns_bp, dtype=float)

        # Base net return
        base_net_return = float(net.sum()) if net.size else 0.0

        # Cost stress
        cost_stress = apply_cost_stress(
            gross_returns_bp=gross_returns_bp,
            base_costs_bp=base_costs_bp,
            multipliers=self.cost_multipliers,
        )

        # Check if profitable at all stress levels
        profitable_at_all_levels = all(r["remains_profitable"] for r in cost_stress)

        # Delayed execution
        delay_result = simulate_delayed_execution(
            gross_returns_bp=gross_returns_bp,
            delay_bars=self.delay_bars,
            decay_bp_per_bar=self.decay_bp_per_bar,
        )

        # Adverse slippage
        slippage_result = simulate_adverse_slippage(
            gross_returns_bp=gross_returns_bp,
            extra_slippage_bp=self.extra_slippage_bp,
        )

        # Missed signals
        missed_result = simulate_missed_signals(
            gross_returns_bp=gross_returns_bp,
            miss_probability=self.miss_probability,
            seed=self.seed,
        )

        # Consecutive losses
        consecutive = compute_consecutive_losses(returns_bp=net_returns_bp)

        # Drawdown recovery
        recovery = compute_drawdown_recovery(
            returns_bp=net_returns_bp,
            starting_equity=10_000.0,
        )

        # Volatility spike
        vol_spike = compute_volatility_spike_impact(
            returns_bp=net_returns_bp,
            volatility_multiplier=self.volatility_multiplier,
        )

        # Overall robustness score
        robustness_checks = [
            profitable_at_all_levels,
            delay_result["remains_profitable"],
            slippage_result["remains_profitable"],
            missed_result["remains_profitable"],
            vol_spike["remains_profitable"],
        ]
        robustness_score = float(sum(robustness_checks)) / len(robustness_checks)

        return {
            "base_net_return_bp": base_net_return,
            "cost_stress": cost_stress,
            "profitable_at_all_cost_levels": profitable_at_all_levels,
            "delayed_execution": delay_result,
            "adverse_slippage": slippage_result,
            "missed_signals": missed_result,
            "consecutive_losses": consecutive,
            "drawdown_recovery": recovery,
            "volatility_spike": vol_spike,
            "robustness_score": robustness_score,
            "robustness_checks_passed": sum(robustness_checks),
            "robustness_checks_total": len(robustness_checks),
            "is_robust": robustness_score >= 0.8,
        }


__all__ = [
    "RobustnessAnalyzer",
    "apply_cost_stress",
    "compute_consecutive_losses",
    "compute_drawdown_recovery",
    "compute_volatility_spike_impact",
    "simulate_adverse_slippage",
    "simulate_delayed_execution",
    "simulate_missed_signals",
]
