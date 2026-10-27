# RESEARCH_METHODOLOGY.md — SignalForge (Phase 6)

## Principles

- **Causal**: only information available at each decision timestamp may be used.
  Close-based signals execute no earlier than the next bar unless documented otherwise.
- **No forced targets**: never optimize toward a mandated accuracy (e.g., "90%").
  Report inconclusive or negative evidence honestly.
- **Separation**: exploratory observations vs confirmatory evidence; in-sample
  tuning vs locked out-of-sample/holdout validation.

## Required design elements

State before tuning: hypothesis, target, horizon, baseline, feature availability
times, sample size, costs (fees, spread, slippage, funding), and validation
procedure (walk-forward, purges/embargoes where observations overlap).

## Metrics — do not conflate

- **Accuracy**: fraction of predictions correct.
- **Win rate**: fraction of trades profitable (gross, before full costs unless stated).
- **Profitability**: net P&L after all costs. Claim only with valid out-of-sample
  support net of costs.

`NO_TRADE` / abstention is a valid outcome on insufficient evidence.

## Validation

Locked final holdouts, purge/embargo overlapping windows, deterministic accounting
fixtures. Investigate suspicious performance instead of optimizing around it.

> Phases 1–5 delivered: calibration, reliability reporting, purged walk-forward,
> holdout protocol with access ledger, experiment registry (DSR, Holm/Bonferroni,
> no fabricated PBO), cost/robustness analysis, the advisory-only AI layer
> (ablation: inconclusive), and the canonical signal decision policy with
> deterministic risk gates and audit history.
> Phase 6 adds the event-safe backtester (signals fill no earlier than the next
> bar open; absolute SL/TP levels; same-bar ties exit STOP_LOSS; timeout and
> END_OF_DATA conventions documented; full cost legs; reproducible ledger) and
> the advisory-only paper sandbox (persistent state, duplicate protection, halt,
> divergence-vs-frozen-backtest comparison).
> A backtest is a historical simulation, not live evidence; paper fills are
> hypothetical, not executions. No profitability or accuracy claim is made.
