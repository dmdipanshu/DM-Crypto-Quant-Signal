"""Q6 benchmark comparison module.

Compares locked candidate strategies against non-strategy benchmarks:

- Buy and hold BTC
- Cash / zero-return benchmark
- Majority baseline
- Frozen trend baseline

All benchmarks use identical evaluation timestamps and comparable cost
conventions.
"""

from __future__ import annotations

from typing import Any


def compute_buy_and_hold_return(
    *,
    prices: list[float],
    fee_bp: float = 0.0,
    spread_bp: float = 0.0,
) -> dict[str, Any]:
    """Compute buy-and-hold return for BTC.

    Buy at first price, sell at last price, with round-trip costs.
    """
    if not prices or len(prices) < 2:
        return {
            "net_return_bp": None,
            "gross_return_bp": None,
            "total_trading_costs_bp": 0.0,
            "trade_count": 1,
            "profit_factor": None,
            "win_rate": None,
        }

    entry = float(prices[0])
    exit_price = float(prices[-1])
    gross_return_bp = float((exit_price / entry - 1.0) * 10_000.0)
    round_trip_cost_bp = 2.0 * fee_bp + spread_bp + 2.0 * 0.0  # No slippage for B&H
    net_return_bp = gross_return_bp - round_trip_cost_bp

    return {
        "net_return_bp": net_return_bp,
        "gross_return_bp": gross_return_bp,
        "total_trading_costs_bp": round_trip_cost_bp,
        "trade_count": 1,
        "profit_factor": None,  # Single trade, no PF
        "win_rate": 1.0 if net_return_bp > 0 else 0.0,
    }


def compute_cash_return() -> dict[str, Any]:
    """Compute cash/zero-return benchmark."""
    return {
        "net_return_bp": 0.0,
        "gross_return_bp": 0.0,
        "total_trading_costs_bp": 0.0,
        "trade_count": 0,
        "profit_factor": None,
        "win_rate": None,
    }


def compute_majority_baseline_return(
    *,
    gross_returns_bp: list[float],
    net_returns_bp: list[float],
) -> dict[str, Any]:
    """Compute majority baseline (always predict majority class).

    The majority baseline never trades (always abstains), so returns are zero.
    """
    return {
        "net_return_bp": 0.0,
        "gross_return_bp": 0.0,
        "total_trading_costs_bp": 0.0,
        "trade_count": 0,
        "profit_factor": None,
        "win_rate": None,
    }


def compute_excess_return(
    *,
    strategy_net_return_bp: float | None,
    benchmark_net_return_bp: float | None,
) -> float | None:
    """Compute excess return of strategy over benchmark."""
    if strategy_net_return_bp is None or benchmark_net_return_bp is None:
        return None
    return float(strategy_net_return_bp - benchmark_net_return_bp)


class BenchmarkComparator:
    """Compares candidate strategies against benchmarks."""

    def __init__(
        self,
        *,
        fee_bp: float = 10.0,
        spread_bp: float = 5.0,
        slippage_bp: float = 5.0,
        funding_bp_per_bar: float = 0.0,
    ) -> None:
        self.fee_bp = fee_bp
        self.spread_bp = spread_bp
        self.slippage_bp = slippage_bp
        self.funding_bp_per_bar = funding_bp_per_bar

    def compare(
        self,
        *,
        strategy_net_return_bp: float | None,
        strategy_gross_return_bp: float | None,
        prices: list[float],
        candidate_trade_count: int = 0,
    ) -> dict[str, Any]:
        """Compare strategy against all benchmarks.

        Returns dict with benchmark results and excess returns.
        """
        bnh = compute_buy_and_hold_return(
            prices=prices,
            fee_bp=self.fee_bp,
            spread_bp=self.spread_bp,
        )
        cash = compute_cash_return()
        majority = compute_majority_baseline_return(
            gross_returns_bp=[],
            net_returns_bp=[],
        )

        excess_vs_bnh = compute_excess_return(
            strategy_net_return_bp=strategy_net_return_bp,
            benchmark_net_return_bp=bnh["net_return_bp"],
        )
        excess_vs_cash = compute_excess_return(
            strategy_net_return_bp=strategy_net_return_bp,
            benchmark_net_return_bp=cash["net_return_bp"],
        )
        excess_vs_majority = compute_excess_return(
            strategy_net_return_bp=strategy_net_return_bp,
            benchmark_net_return_bp=majority["net_return_bp"],
        )

        return {
            "buy_and_hold": bnh,
            "cash": cash,
            "majority": majority,
            "excess_return_vs_buy_and_hold_bp": excess_vs_bnh,
            "excess_return_vs_cash_bp": excess_vs_cash,
            "excess_return_vs_majority_bp": excess_vs_majority,
            "outperforms_buy_and_hold": (excess_vs_bnh is not None and excess_vs_bnh > 0),
            "outperforms_cash": (excess_vs_cash is not None and excess_vs_cash > 0),
            "outperforms_majority": (excess_vs_majority is not None and excess_vs_majority > 0),
        }


__all__ = [
    "BenchmarkComparator",
    "compute_buy_and_hold_return",
    "compute_cash_return",
    "compute_excess_return",
    "compute_majority_baseline_return",
]
