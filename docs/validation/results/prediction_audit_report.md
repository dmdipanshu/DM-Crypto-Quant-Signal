# Prediction Audit Report - Q6 Frozen Inference

- **dataset_id**: hist-accel-7c0a166d4f93
- **dataset_type**: HISTORICAL_ACCELERATED_VALIDATION
- **n_candles**: 169
- **dataset_hash**: 2d66e31e11e808331a39332e3c6a89c2bc3e65117e40655907f6c7c042e79707
- **generated_at_utc**: 2026-11-13T14:12:51.661808+00:00

## Governance
- official_prospective_evidence: false
- q5_2_eligible: false
- promotion_eligible: false
- q4_decision_preserved: true
- q4_decision: NO_STRATEGY_ELIGIBLE
- promotion_gate: BLOCKED

## Per-Candidate Results

| candidate | status | long | short | abstain | eligible | trade_count | net_return_bp | candidate_lock_hash | model_artifact_hash |
|---|---|---:|---:|---:|---:|---:|---|---|---|
| logistic | EVALUATED | 5 | 45 | 119 | 50 | 2 | -299.69399721340267 | 4c96f4c1b658a9a7... | logistic-ee482c-3e3324f2-20261028T132236 |
| trend | EVALUATED | 11 | 39 | 119 | 50 | 2 | 100.30600278659767 | 7009d9659b164506... | trend-baseline-deterministic |
| majority | NOT_EVALUABLE | 0 | 0 | 0 | 0 | None | None | ... | majority-baseline-no-labels |
| lightgbm | NOT_EVALUABLE | 0 | 0 | 0 | 0 | None | None | ... | lightgbm-artifact-not-on-disk |

## Errors
- logistic: none
- trend: none
- majority: ['NOT_EVALUABLE: no training labels available to determine majority class']
- lightgbm: ['NOT_EVALUABLE: no LightGBM artifact on disk']
