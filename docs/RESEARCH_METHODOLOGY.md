# RESEARCH_METHODOLOGY.md — SignalForge (Phase 3)

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

> Phase 3 implements calibration (sigmoid/isotonic, event `P(y == positive_class)`),
> reliability reporting (bins, Brier, log-loss, ECE, coverage), purged walk-forward
> with embargo, a locked-holdout protocol with access ledger, an experiment registry
> (DSR, Holm/Bonferroni, no fabricated PBO), and research-level cost/robustness
> analysis (fee/slippage stress, block bootstrap, MC). The cost proxy is a
> research approximation, NOT a fill-level backtest; full execution remains Phase 6.
