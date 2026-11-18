# Prospective Data Governance — Q5

Generated: 2026-11-18T15:12:23.649530+00:00

## Holdout States

- Historical Holdout: CONTAMINATED (permanent)
- Prospective Holdout: ACCUMULATING

## Governance Rules

- Historical holdout remains permanently CONTAMINATED
- Prospective dataset begins as ACCUMULATING
- Does not automatically become eligible due to new candles
- Requires explicit authorized evaluation action for EVALUATED
- Q5 does not train, calibrate, or optimize models using prospective data

## Evaluation Eligibility Requirements

- Original freeze remains valid: YES
- Collection epoch is immutable: YES
- Data is genuinely post-collection-start: YES
- All first-seen provenance is valid: YES
- Data-quality requirements pass: False
- Collection coverage meets policy: 0.78% >= 0.95
- Minimum observation count met: 1 >= 168
- No development or tuning process has inspected prospective outcomes: YES
- No historical backfill has contaminated the collection: YES

## Dataset Isolation

- Prospective dataset is separate from canonical training data
- Prospective dataset is separate from historical validation data
- Prospective dataset is separate from calibration datasets
- Prospective dataset is separate from model training inputs
- Prospective dataset is separate from feature selection inputs
- Prospective dataset is separate from backtesting inputs
- Prospective dataset is separate from strategy threshold optimization
- Dataset-purpose metadata: PROSPECTIVE_HOLDOUT
- Ordinary model-training and tuning commands reject prospective data

## AI Authority

- AI advisory remains disabled by default
- AI cannot control collection integrity
- AI cannot control holdout status
- AI cannot control evaluation eligibility

## Q4 Compatibility

- Q4 decision: NO_STRATEGY_ELIGIBLE (unchanged)
- Q5 does not modify Q2, Q3, or Q4 results
- Q5 does not weaken the fail-closed promotion gate
