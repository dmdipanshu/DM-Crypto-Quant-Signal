# Strategy Validation — Q3

Generated: 2026-11-12T15:00:23.891443+00:00

## Protocol

- Research period: 2024-11-05 23:00:00+00:00 to 2026-07-23 08:00:00+00:00
- Purged/embargoed folds: 5
- Horizon: 24 bars
- Decision at close; entry at next open; exit at open after horizon
- Non-overlapping positions; no threshold search
- Costs (bp): `{'fee_bp': 10.0, 'spread_bp': 5.0, 'slippage_bp': 5.0, 'funding_bp_per_bar': 0.0}`
- Historical holdout excluded from this phase

## Model: majority

- OOS decisions: 2995
- Trades: 120
- Win rate: 0.44166666666666665
- Expectancy: -29.75218233022803 bp
- Profit factor: 0.6967210874732789
- Net total: -3570.26 bp
- Max drawdown: 3766.57 bp

| Cost multiplier | Trades | Net total (bp) | Profit factor |
|---:|---:|---:|---:|
| 1.0x | 120 | -3570.26 | 0.6967210874732789 |
| 1.5x | 120 | -5670.26 | 0.5625327946794736 |
| 2.0x | 120 | -7770.26 | 0.453808108106509 |

- Promotion gate: **NOT_ELIGIBLE**
- Gate reasons: Holdout integrity is 'unknown', not pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.

## Model: trend

- OOS decisions: 2995
- Trades: 120
- Win rate: 0.4083333333333333
- Expectancy: -33.59606878086674 bp
- Profit factor: 0.6671188221341766
- Net total: -4031.53 bp
- Max drawdown: 3674.27 bp

| Cost multiplier | Trades | Net total (bp) | Profit factor |
|---:|---:|---:|---:|
| 1.0x | 120 | -4031.53 | 0.6671188221341766 |
| 1.5x | 120 | -6131.53 | 0.5423680212793549 |
| 2.0x | 120 | -8231.53 | 0.4402740213171031 |

- Promotion gate: **NOT_ELIGIBLE**
- Gate reasons: Holdout integrity is 'unknown', not pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.

## Model: logistic

- OOS decisions: 2995
- Trades: 120
- Win rate: 0.44166666666666665
- Expectancy: -29.75218233022803 bp
- Profit factor: 0.6967210874732789
- Net total: -3570.26 bp
- Max drawdown: 3766.57 bp

| Cost multiplier | Trades | Net total (bp) | Profit factor |
|---:|---:|---:|---:|
| 1.0x | 120 | -3570.26 | 0.6967210874732789 |
| 1.5x | 120 | -5670.26 | 0.5625327946794736 |
| 2.0x | 120 | -7770.26 | 0.453808108106509 |

- Promotion gate: **NOT_ELIGIBLE**
- Gate reasons: Holdout integrity is 'unknown', not pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.

## Overall decision

No strategy is production-eligible from this research run. A positive walk-forward result is not sufficient: a new prospective holdout, stress-cost survival, robustness and independent human review remain required.

No order placement or paper-state mutation is enabled.
