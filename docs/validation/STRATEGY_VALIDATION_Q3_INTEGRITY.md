# Strategy Validation — Q3 Integrity

Research freeze timestamp: 2026-11-12T19:11:47.293700+00:00
Generated: 2026-11-12T19:47:00+00:00

## Purpose

Q3 revised validates strategy performance using barrier-aligned execution
with corrected gross-vs-net accounting. The simulator executes the same
triple-barrier geometry that produced the Q2.2.1 training targets. All
economic metrics are net of transaction costs, with gross and net reported
separately at both the trade and fold levels.

## Methodology Correction

Q3 revised uses barrier-aligned execution: entry at next-bar open, barriers
from causal vol x 2.0/1.0, horizon 24 bars, conservative same-bar collision
(stop first). This resolves the Q2.1/Q3 methodology gap where models were
trained on triple-barrier labels but evaluated with a fixed-horizon
next-open exit.

## Corrected Gross-vs-Net Accounting (BUG 1 fix)

For every model and walk-forward fold, the following are independently
calculated and preserved:

- gross_profit_bp, gross_loss_bp, gross_return_bp
- total_trading_costs_bp, fees_bp, spread_bp, slippage_bp, funding_bp
- net_profit_bp, net_loss_bp, net_return_bp
- gross_profit_factor, net_profit_factor
- trade_count, win_rate, maximum_drawdown, exposure, average_trade_return_bp

Gross results are calculated before transaction costs. Net returns are
calculated after all costs. Each cost component is applied exactly once
per round trip. Gross returns are never labelled as net returns.

Zero-trade folds return None for rate/ratio metrics and 0.0 for sums.
Zero-loss profit factor is None (undefined, not infinite).

## Cost-Adjusted Robustness

Economic robustness is calculated separately from predictive stability.
For each model and fold:

- gross positive-fold count / net positive-fold count
- gross positive-fold fraction / net positive-fold fraction
- gross average fold return / net average fold return
- net return standard deviation / worst net fold / best net fold
- 1x cost performance / 2x cost performance

The denominator is the number of actually evaluated folds, not the
configured number of folds. A fold with no qualifying evaluation is not
reported as a positive fold.

## Protocol

- Research rows: 15,129 (total), 14,986 (usable), 15,085 (complete labels)
- Folds: 5 (actual-duration purge + 24-bar embargo)
- Class weight: balanced (training fold only)
- Costs (bp): fee=10, spread=5, slippage=5, funding=0

## Model Results

All models evaluated under barrier-aligned simulation with corrected
accounting show NO_STRATEGY_ELIGIBLE under the fail-closed promotion gate.

| Model | Trades | Gross (bp) | Net (bp) | Costs (bp) | Net PF | Gross PF | Max DD (bp) | Exposure | Integrity OK |
|-------|--------|------------|----------|------------|--------|----------|-------------|----------|--------------|
| majority | 1,105 | -841.71 | -39,516.71 | 38,675.00 | 0.1188 | 0.9695 | 9,814.61 | 0.1985 | No |
| trend | 1,105 | -473.58 | -39,148.58 | 38,675.00 | 0.1982 | 0.9826 | 9,807.52 | 0.1985 | No |
| logistic | 932 | -1,992.49 | -34,612.49 | 32,620.00 | 0.2239 | 0.9149 | 9,729.92 | 0.1738 | No |
| lightgbm | 1,062 | -645.93 | -37,815.93 | 37,170.00 | 0.2068 | 0.9754 | 9,777.70 | 0.1946 | No |

## Per-Fold Economic Metrics (lightgbm example)

| Fold | Trades | Gross (bp) | Net (bp) | Costs (bp) |
|------|--------|------------|----------|------------|
| 0 | 209 | 1,100.00 | -6,215.00 | 7,315.00 |
| 1-4 | (see Q3 JSON) | ... | ... | ... |

Note: Fold-level profit factor, drawdown, and per-trade cost details are
exposed only where calculated and persisted in the Q3 JSON artifact. Fields
not present in `fold_strategy_results` are not claimed.

## Holdout Integrity

- Historical holdout status: contaminated (permanent)
- Prospective holdout validated: No
- Promotion blocked: Yes
- research_freeze_timestamp: 2026-11-12T19:11:47.293700+00:00

## Decision

No strategy is production-eligible. A genuinely prospective holdout,
positive net performance under cost stress, and independent human review
remain required. No model, paper or execution authority is granted.

## Limitations

- All models show negative net returns under barrier-aligned simulation.
- Historical holdout is contaminated and the prospective holdout does not
  yet exist.
- Valid research artifacts do not imply a promotion-eligible strategy.
