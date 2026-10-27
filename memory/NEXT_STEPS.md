# NEXT_STEPS.md — SignalForge handoff

## Immediate: user review (Phase 6 gate)

1. Review this Phase 6 report; explicitly approve or reject opening Phase 7.
2. Do NOT start API/dashboard implementation (Phase 7) until approval.

## If Phase 7 approved, proposed scope

- Read-only API over canonical records (signals, backtest ledgers, paper
  state, audit history) reusing validated schemas; no order placement.
- Dashboard consuming canonical result schemas only, never engine internals;
  no fabricated or stale predictions shown as current.

## Gates already met (verified 2026-10-28)

- [x] Event-safe backtester (next-bar fills, absolute levels, conservative ties)
- [x] Realistic costs (fee/spread/slippage/funding legs, bp units, mixup guard)
- [x] Reproducible ledger (deterministic ids, fee math reconciles, run-twice identical)
- [x] Approved-only trading (NOT_APPROVED skip; gates + engine double-enforced)
- [x] Paper sandbox (persistent checksummed state, duplicate protection, halt/flatten)
- [x] Divergence comparison (match/diverged verdicts with tolerances)
- [x] `sf backtest` / `sf paper` CLI (offline, zero-trade valid, clear errors)
- [x] `uv sync` clean, `pytest` 431 passed/1 skipped (lightgbm optional), ruff clean
- [x] No ccxt/order/trade API imports in backtesting or paper_trading paths
- [ ] Explicit user approval to open Phase 7

## Out of scope (stop and ask)

Live orders, real-money trading, portfolio accounting beyond the paper
sandbox, API/dashboard, new AI providers, large searches, unapproved global
config changes.
