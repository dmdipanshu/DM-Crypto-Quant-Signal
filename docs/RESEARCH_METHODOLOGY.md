# RESEARCH_METHODOLOGY.md — SignalForge (Phase 5)

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

> Phases 1–4 implemented: calibration (sigmoid/isotonic, event `P(y == positive_class)`),
> reliability reporting, purged walk-forward with embargo, locked-holdout protocol
> with access ledger, experiment registry (DSR, Holm/Bonferroni, no fabricated PBO),
> cost/robustness analysis, and the advisory-only AI layer (ablation: inconclusive).
> Phase 5 adds the canonical signal decision policy (LONG/SHORT/NEUTRAL/NO_TRADE),
> calibration-aware handling (calibrated probabilities only for their documented
> event), expected-edge gating against explicit cost estimates (edge never inferred
> from confidence), deterministic risk gates, and a reproducible audit history.
> A probability is not an edge; an edge estimate is not a fill; a backtest proxy
> is not live performance. Full execution accounting remains Phase 6.
