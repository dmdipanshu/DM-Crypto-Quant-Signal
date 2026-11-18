# Strategy Validation (Revised) - Q3

Generated: 2026-11-12T20:22:06.095308+00:00

## Methodology correction

Q3 revised uses **barrier-aligned execution**: the simulator executes the
**same** triple-barrier geometry that produced the Q2.2 training targets.
Entry at next-bar open, barriers from causal vol x 2.0/1.0,
horizon 24 bars, conservative same-bar collision (stop first).

## Protocol

- Research rows: 14986
- Folds: 5 (actual-duration purge + 24-bar embargo)
- Class weight: balanced (training fold only)
- Costs (bp): `{'fee_bp': 10.0, 'spread_bp': 5.0, 'slippage_bp': 5.0, 'funding_bp_per_bar': 0.0}`

## Model: majority

- OOS decisions: 2995
- Prediction distribution: {'-1': 2995, '0': 0, '1': 0}
- Collapsed: True
- Trades: 1105
- Win rate: 0.2986425339366516
- Expectancy: -35.76173013409299 bp
- Gross profit factor: 0.9695015607286477
- Net profit factor: 0.11883963507438956
- Gross return: -841.71 bp
- Net return: -39516.71 bp
- Total trading costs: 38675.00 bp
- Account return: -98.13%
- Max drawdown: 9814.61 bp
- Exposure: 19.85%
- Evaluated folds: 5
- Gross positive-fold fraction: 40%
- Net positive-fold fraction: 0%
- Net average fold return: -7903.342359634551 bp
- Worst net fold: -9592.201409544443 bp
- Best net fold: -6537.093655774621 bp

| Cost mult | Trades | Gross (bp) | Net (bp) | PF |
|---:|---:|---:|---:|---:|
| 1.0x | 1105 | -841.71 | -39516.71 | 0.11883963507438956 |
| 1.5x | 1105 | -841.71 | -58854.21 | 0.030111530441310458 |
| 2.0x | 1105 | -841.71 | -78191.71 | 0.009985114421484781 |

- Gate: **NO_STRATEGY_ELIGIBLE**
- Reasons: Holdout integrity is 'contaminated', not pristine.; Holdout is not assessed as pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Prediction collapse: max class share 100.00% exceeds limit 90.00%.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.

## Model: trend

- OOS decisions: 2995
- Prediction distribution: {'-1': 1340, '0': 0, '1': 1655}
- Collapsed: False
- Trades: 1105
- Win rate: 0.29411764705882354
- Expectancy: -35.428578527980825 bp
- Gross profit factor: 0.9826159668315576
- Net profit factor: 0.19820555894732347
- Gross return: -473.58 bp
- Net return: -39148.58 bp
- Total trading costs: 38675.00 bp
- Account return: -98.06%
- Max drawdown: 9807.52 bp
- Exposure: 19.85%
- Evaluated folds: 5
- Gross positive-fold fraction: 40%
- Net positive-fold fraction: 0%
- Net average fold return: -7829.715854683767 bp
- Worst net fold: -8624.725961723854 bp
- Best net fold: -7337.899970646013 bp

| Cost mult | Trades | Gross (bp) | Net (bp) | PF |
|---:|---:|---:|---:|---:|
| 1.0x | 1105 | -473.58 | -39148.58 | 0.19820555894732347 |
| 1.5x | 1105 | -473.58 | -58486.08 | 0.08583998154151484 |
| 2.0x | 1105 | -473.58 | -77823.58 | 0.03830299806561852 |

- Gate: **NO_STRATEGY_ELIGIBLE**
- Reasons: Holdout integrity is 'contaminated', not pristine.; Holdout is not assessed as pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.

## Model: logistic

- OOS decisions: 2995
- Prediction distribution: {'-1': 414, '0': 640, '1': 1941}
- Collapsed: False
- Trades: 932
- Win rate: 0.2982832618025751
- Expectancy: -37.13786723816111 bp
- Gross profit factor: 0.9148965299792559
- Net profit factor: 0.22392079845517202
- Gross return: -1992.49 bp
- Net return: -34612.49 bp
- Total trading costs: 32620.00 bp
- Account return: -96.93%
- Max drawdown: 9729.92 bp
- Exposure: 17.38%
- Evaluated folds: 5
- Gross positive-fold fraction: 20%
- Net positive-fold fraction: 0%
- Net average fold return: -6922.49845319323 bp
- Worst net fold: -8336.015451527148 bp
- Best net fold: -5454.9416799780365 bp

| Cost mult | Trades | Gross (bp) | Net (bp) | PF |
|---:|---:|---:|---:|---:|
| 1.0x | 932 | -1992.49 | -34612.49 | 0.22392079845517202 |
| 1.5x | 932 | -1992.49 | -50922.49 | 0.10344784356327046 |
| 2.0x | 932 | -1992.49 | -67232.49 | 0.047192280559661126 |

- Gate: **NO_STRATEGY_ELIGIBLE**
- Reasons: Holdout integrity is 'contaminated', not pristine.; Holdout is not assessed as pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.

## Model: lightgbm

- OOS decisions: 2995
- Prediction distribution: {'-1': 1211, '0': 79, '1': 1705}
- Collapsed: False
- Trades: 1062
- Win rate: 0.2806026365348399
- Expectancy: -35.60822423054321 bp
- Gross profit factor: 0.9753622754467904
- Net profit factor: 0.20682740875289168
- Gross return: -645.93 bp
- Net return: -37815.93 bp
- Total trading costs: 37170.00 bp
- Account return: -97.78%
- Max drawdown: 9777.70 bp
- Exposure: 19.46%
- Evaluated folds: 5
- Gross positive-fold fraction: 60%
- Net positive-fold fraction: 0%
- Net average fold return: -7563.186826567379 bp
- Worst net fold: -9278.057979684785 bp
- Best net fold: -6215.000197220709 bp

| Cost mult | Trades | Gross (bp) | Net (bp) | PF |
|---:|---:|---:|---:|---:|
| 1.0x | 1062 | -645.93 | -37815.93 | 0.20682740875289168 |
| 1.5x | 1062 | -645.93 | -56400.93 | 0.09466194918391983 |
| 2.0x | 1062 | -645.93 | -74985.93 | 0.046201165217624 |

- Gate: **NO_STRATEGY_ELIGIBLE**
- Reasons: Holdout integrity is 'contaminated', not pristine.; Holdout is not assessed as pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.

## Overall decision

No strategy is production-eligible. A genuinely prospective holdout,
positive net performance under cost stress, and independent human review
remain required.

Holdout integrity: **contaminated**
