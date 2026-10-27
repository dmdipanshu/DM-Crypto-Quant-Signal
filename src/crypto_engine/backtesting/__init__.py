"""Event-safe historical backtesting (Phase 6 Track A).

Timing contract (the core invariant): a signal with ``generated_at`` in bar
interval [t] (decision on bar t close) may fill NO EARLIER than bar t+1
open. Entry resolution: ``entry_bar = first bar with open time strictly
greater than generated_at``. No wall-clock use; all event time.

Costs reuse Phase 5 ``CostEstimate`` legs (bp units); ``ExecutionCostModel``
in ``costs.py`` converts them to fill prices/fees. One position at a time,
single symbol/timeframe, bounded bars (4GB cap via ``max_bars``).
"""

from __future__ import annotations

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.backtesting.engine import (
    BacktestResult,
    InsufficientDataError,
    run_backtest,
)
from crypto_engine.backtesting.metrics import (
    expectancy_per_trade,
    max_drawdown,
    profit_factor,
    sharpe,
    win_rate,
)
from crypto_engine.backtesting.types import Fill, Position, Trade, validate_bars

__all__ = [
    "BacktestResult",
    "ExecutionCostModel",
    "Fill",
    "InsufficientDataError",
    "Position",
    "Trade",
    "expectancy_per_trade",
    "max_drawdown",
    "profit_factor",
    "run_backtest",
    "sharpe",
    "validate_bars",
    "win_rate",
]
