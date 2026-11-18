# Quantitative Research Release — Q4 (Revised)

Research freeze timestamp: 2026-11-12T19:11:47.293700+00:00
Generated: 2026-11-12T20:22:07.756546+00:00

## Decision

**NO_STRATEGY_ELIGIBLE**

- Models reviewed: 4
- Models eligible for human review: 0
- Historical holdout integrity: contaminated
- Prospective holdout validated: No
- Freeze status: VALID
- Artifact integrity OK: True
- Accounting integrity OK: True
- Predictive evidence integrity OK: True
- Strategy evidence integrity OK: True
- Freeze integrity OK: True
- Holdout integrity OK: True
- Promotion eligible: False
- AI advisory status: disabled

## Integrity verification

- All integrity checks passed.

## Model research gates

### majority

- Trades: 1105
- Gross return: -841.7117981727562 bp
- Net return: -39516.71179817276 bp
- Total trading costs: 38675.0 bp
- Gross profit factor: 0.9695015607286477
- Net profit factor: 0.11883963507438956
- Max drawdown: 9814.607537011872 bp
- Exposure: 0.1984904661016949
- Artifact integrity OK: True
- Accounting integrity OK: True
- Predictive evidence integrity OK: True
- Strategy evidence integrity OK: True
- Boundary validation OK: True
- Gate: NO_STRATEGY_ELIGIBLE
- Reasons: Holdout integrity is 'contaminated', not pristine.; Holdout is not assessed as pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Prediction collapse: max class share 100.00% exceeds limit 90.00%.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.
- Candidate AI advisory: AI gateway is not configured; deterministic report is unchanged.

### trend

- Trades: 1105
- Gross return: -473.5792734188194 bp
- Net return: -39148.57927341882 bp
- Total trading costs: 38675.0 bp
- Gross profit factor: 0.9826159668315576
- Net profit factor: 0.19820555894732347
- Max drawdown: 9807.523105446553 bp
- Exposure: 0.1984904661016949
- Artifact integrity OK: True
- Accounting integrity OK: True
- Predictive evidence integrity OK: True
- Strategy evidence integrity OK: True
- Boundary validation OK: True
- Gate: NO_STRATEGY_ELIGIBLE
- Reasons: Holdout integrity is 'contaminated', not pristine.; Holdout is not assessed as pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.
- Candidate AI advisory: AI gateway is not configured; deterministic report is unchanged.

### logistic

- Trades: 932
- Gross return: -1992.4922659661472 bp
- Net return: -34612.492265966146 bp
- Total trading costs: 32620.0 bp
- Gross profit factor: 0.9148965299792559
- Net profit factor: 0.22392079845517202
- Max drawdown: 9729.920712877727 bp
- Exposure: 0.1737950211864407
- Artifact integrity OK: True
- Accounting integrity OK: True
- Predictive evidence integrity OK: True
- Strategy evidence integrity OK: True
- Boundary validation OK: True
- Gate: NO_STRATEGY_ELIGIBLE
- Reasons: Holdout integrity is 'contaminated', not pristine.; Holdout is not assessed as pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.
- Candidate AI advisory: AI gateway is not configured; deterministic report is unchanged.

### lightgbm

- Trades: 1062
- Gross return: -645.9341328368872 bp
- Net return: -37815.93413283689 bp
- Total trading costs: 37170.0 bp
- Gross profit factor: 0.9753622754467904
- Net profit factor: 0.20682740875289168
- Max drawdown: 9777.701288909653 bp
- Exposure: 0.1945842161016949
- Artifact integrity OK: True
- Accounting integrity OK: True
- Predictive evidence integrity OK: True
- Strategy evidence integrity OK: True
- Boundary validation OK: True
- Gate: NO_STRATEGY_ELIGIBLE
- Reasons: Holdout integrity is 'contaminated', not pristine.; Holdout is not assessed as pristine.; No genuinely prospective, untouched holdout has been validated.; Profit factor must be finite and >= 1.10.; Positive walk-forward fold fraction is below 75% or unavailable.; Net return at 2.0x costs must be positive.
- Candidate AI advisory: AI gateway is not configured; deterministic report is unchanged.

## AI advisory

AI gateway is not configured; deterministic report is unchanged.

AI cannot select, approve, promote or execute a strategy. Human approval remains mandatory.

No live orders, paper-state mutations, or execution authorization are present.
