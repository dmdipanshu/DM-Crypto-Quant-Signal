---
name: backtesting-validation
description: Enforce causal timing, realistic costs, and robust validation
---

# Backtesting Validation

## Procedure

1. Confirm decision timestamps; use only information available at each timestamp.
2. Execute close-based signals no earlier than the next bar unless documented otherwise.
3. Model all costs: fees, spread, slippage, funding; use conservative ambiguous fills.
4. Walk forward with purges/embargoes where observations overlap; keep the final
   holdout locked until the last evaluation.
5. Verify accounting with small deterministic fixtures before trusting any metric.

## Validation standards

- Timing tests prove no lookahead; accounting fixtures reconcile exactly.
- Report accuracy, win rate, and net profitability separately; claim profitability
  only with valid out-of-sample support net of costs.
- Investigate suspicious performance (leakage/cost audit) instead of optimizing around it.

## Failure handling

- Missing/invalid inputs → explicit error or documented exclusion, never silent fill.
- Suspicious outperformance → freeze tuning, audit leakage and costs, report first.

## Completion requirements

Handoff states: fixtures run, costs modeled, holdout discipline kept, metrics
separated, limitations and unresolved risks listed. (Planned Phase 3; not
implemented in Phase 1.)
