"""Advisory-only paper trading state (Phase 6 Track B)."""

from __future__ import annotations

from crypto_engine.paper_trading.compare import (
    DivergenceReport,
    TradeDivergence,
    compare_account_to_backtest,
    compare_to_backtest,
)
from crypto_engine.paper_trading.loop import (
    StepResult,
    default_gates_ctx,
    make_gates_ctx_fn,
    paper_portfolio,
    step,
)
from crypto_engine.paper_trading.state import (
    ACCOUNT_FILENAME,
    BAR_WINDOW,
    MAX_EQUITY_POINTS,
    MAX_PROCESSED_IDS,
    MAX_REJECTIONS,
    MAX_TRADES_CLOSED,
    CorruptStateError,
    PaperAccount,
    account_hash,
    equity,
    flatten,
    halt,
    init_account,
    is_processed,
    load_account,
    mark_processed,
    record_rejection,
    save_account,
)

__all__ = [
    "ACCOUNT_FILENAME",
    "BAR_WINDOW",
    "MAX_EQUITY_POINTS",
    "MAX_PROCESSED_IDS",
    "MAX_REJECTIONS",
    "MAX_TRADES_CLOSED",
    "CorruptStateError",
    "DivergenceReport",
    "PaperAccount",
    "StepResult",
    "TradeDivergence",
    "account_hash",
    "compare_account_to_backtest",
    "compare_to_backtest",
    "default_gates_ctx",
    "equity",
    "flatten",
    "halt",
    "init_account",
    "is_processed",
    "load_account",
    "make_gates_ctx_fn",
    "mark_processed",
    "paper_portfolio",
    "record_rejection",
    "save_account",
    "step",
]
