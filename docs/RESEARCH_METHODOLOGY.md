# RESEARCH_METHODOLOGY.md — SignalForge (Phase 7, final)

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

> Phases 1–6 delivered: calibration, reliability reporting, purged walk-forward,
> holdout protocol with access ledger, experiment registry (DSR, Holm/Bonferroni,
> no fabricated PBO), cost/robustness analysis, the advisory-only AI layer
> (ablation: inconclusive), the canonical signal decision policy with
> deterministic risk gates and audit history, and the event-safe backtester plus
> advisory-only paper sandbox (next-bar fills, absolute SL/TP levels, same-bar
> ties exit STOP_LOSS, full cost legs, reproducible ledger, duplicate protection,
> halt, divergence-vs-frozen-backtest comparison).
> Phase 7 adds no models or strategies — only read-only presentation (GET-only
> `/api/v1`, server-rendered dashboard) over the recorded evidence, with
> explicit empty/unavailable states and no fabricated rows.
> A backtest is a historical simulation, not live evidence; paper fills are
> hypothetical, not executions; a dashboard value is a recorded artifact, not a
> recommendation. No profitability, accuracy, or deployment claim is made.
