# Historical Validation Comparison (12-month BTC/USDT 1h)

Dataset ID: hist-accel-12m-27eb09322722
Dataset Type: HISTORICAL_ACCELERATED_VALIDATION
Storage Root: data\historical_accelerated_12m
N Candles: 8760
Dataset Hash: 82dd207696045c9a6758a5e02dc6937c983756a2f8b4fdfe184800c983d2480b
Official Prospective Evidence: False
Q5.2 Eligible: False
Promotion Eligible: False
Q4 Decision Preserved: True
Q4 Decision: NO_STRATEGY_ELIGIBLE
Promotion Gate: BLOCKED
Generated At UTC: 2026-11-13T15:49:33.176687+00:00

## Candidates

| Candidate | Status | Net Return (bp) | Trade Count | Long | Short | Abstain | Promotion Gate |
|---|---|---|---|---|---|---|---|
| logistic-ee482c | EVALUATED | -19453.763771163594 | 469 | 3534 | 5107 | 119 | BLOCKED |
| trend-baseline | EVALUATED | -15003.488395143042 | 469 | 4281 | 4360 | 119 | BLOCKED |
| majority-baseline | NOT_EVALUABLE | None | None | 0 | 0 | 0 | BLOCKED |
| lightgbm | NOT_EVALUABLE | None | None | 0 | 0 | 0 | BLOCKED |

All candidates evaluated via frozen inference on historical accelerated data. No retraining, tuning, or calibration. Historical validation does not imply prospective evidence; promotion is BLOCKED and the Q4 NO_STRATEGY_ELIGIBLE decision is preserved.
