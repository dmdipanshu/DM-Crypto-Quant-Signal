# NEXT_STEPS.md — SignalForge handoff

## Immediate: user review (Phase 5 gate)

1. Review this Phase 5 report; explicitly approve or reject opening Phase 6.
2. Do NOT start the event-driven backtester, paper trading (Phase 6), or
   API/dashboard (Phase 7) until approval.

## If Phase 6 approved, proposed scope

- Event-safe backtesting engine (next-bar execution, fees/spread/slippage/
  funding, conservative ambiguous fills, reproducible trade ledger).
- Paper-trading loop consuming canonical `SignalRecord`s, compared against
  frozen backtest expectations; advisory-only, no live orders.

## Gates already met (verified 2026-10-27)

- [x] Canonical schema (v1.0, deterministic ids, closed enums, NO_TRADE first-class)
- [x] Decision policy (fixed veto order, calibration matching, edge gating, determinism)
- [x] Costs/levels (bp units, mixup guard, conservative tick rounding)
- [x] Risk gates A→B→C→D (fail-closed, no invented state, AI never overrides)
- [x] Audit history (hash chain, idempotent, tamper-detecting, secret-free)
- [x] `sf signal` CLI (offline default, replay deterministic, clear errors)
- [x] `uv sync` clean, `pytest` 346 passed/1 skipped (lightgbm optional), ruff clean
- [ ] Explicit user approval to open Phase 6

## Out of scope (stop and ask)

Event backtester, paper/live trading, portfolio accounting, API/dashboard,
new AI providers, large searches, unapproved global config changes.
