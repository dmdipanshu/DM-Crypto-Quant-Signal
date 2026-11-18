# Quantitative Research Release — Q4 Integrity

Research freeze timestamp: 2026-11-12T19:11:47.293700+00:00
Generated: 2026-11-12T19:47:00+00:00

## Decision

**NO_STRATEGY_ELIGIBLE**

No model is eligible for human review. All mandatory integrity and
promotion-gate requirements fail.

## Research Freeze (immutable)

- Status: VALID
- Freeze ID: freeze-aaa4dd57057a
- Created at: 2026-11-12T19:11:47.293700+00:00
- Schema version: research-freeze-manifest.v1
- Commit SHA: 8d7beb729d5f326c14700713f76fe1b858e348db
- Valid: Yes

The freeze timestamp is immutable and is never replaced with the pipeline
execution timestamp or generated_at.

## Holdout Integrity

- Historical holdout status: contaminated
- Permanently contaminated: Yes (cannot be cleared by later pipeline runs)
- Prospective holdout validated: No
- Prospective status: CONTAMINATED

## Separated Integrity Statuses (BUG 4 fix)

Valid research artifacts do not imply a promotion-eligible strategy. Each
integrity dimension is tracked independently:

| Dimension | Status |
|-----------|--------|
| artifact_integrity_ok | False |
| accounting_integrity_ok | False |
| predictive_evidence_integrity_ok | False |
| strategy_evidence_integrity_ok | False |
| freeze_integrity_ok | True |
| holdout_integrity_ok | False |
| promotion_eligible | False |

## Integrity Verification

| Check | Status | Reason |
|-------|--------|--------|
| Freeze manifest | VALID | Manifest loaded, all checks passed |
| Freeze timestamp propagation | OK | created_at_utc=2026-11-12T19:11:47.293700+00:00 used throughout |
| Q2 predictive fold_results | Present | 5 folds per model with accuracy, balanced_accuracy, macro_f1, mcc |
| Q3 strategy fold results | Present | 5 folds per model with gross_return_bp, net_return_bp |
| Q2/Q3 fold boundary compatibility | OK | Matching fold IDs and validation boundaries |
| Gross vs net reconciliation | Verified | costs applied exactly once per round trip |
| No misleading net fields | Verified | gross ≠ net where costs > 0 |
| Cost-adjusted robustness | Present | 1x/2x cost performance, net positive-fold fraction |
| Holdout integrity | FAIL | Historical holdout permanently contaminated |

## Model Research Gates

All models evaluated: 4
Models eligible for human review: 0

| Model | Gross (bp) | Net (bp) | Costs (bp) | Net PF | Gate | Artifact OK | Accounting OK | Pred Evidence OK | Strat Evidence OK |
|-------|------------|----------|------------|--------|------|-------------|---------------|------------------|-------------------|
| majority | -841.71 | -39,516.71 | 38,675.00 | 0.1188 | NO_STRATEGY_ELIGIBLE | No | No | No | No |
| trend | -473.58 | -39,148.58 | 38,675.00 | 0.1982 | NO_STRATEGY_ELIGIBLE | No | No | No | No |
| logistic | -1,992.49 | -34,612.49 | 32,620.00 | 0.2239 | NO_STRATEGY_ELIGIBLE | No | No | No | No |
| lightgbm | -645.93 | -37,815.93 | 37,170.00 | 0.2068 | NO_STRATEGY_ELIGIBLE | No | No | No | No |

## Q4 Promotion Decision

No strategy is eligible. The historical holdout is permanently
contaminated and no genuinely prospective holdout has been validated.
All models fail the fail-closed promotion gate. AI cannot override this
deterministic rejection.

## Authority

- AI can select strategy: False
- AI can override risk: False
- AI can promote: False
- Human approval required: True
- Human authorized: False
- Paper trading authorized: False
- Order execution authorized: False

No live orders, paper-state mutations, or execution authorization are
present.

## Limitations

- Historical holdout is not pristine and cannot be used as prospective
  validation evidence.
- No genuinely prospective, untouched holdout has been validated after
  the frozen methodology/research state.
- Valid research artifacts do not imply a promotion-eligible strategy.
- Barrier-aligned targets and simulator correct prior horizon/execution
  misalignment, but economic edge remains unproven.
- AI output is optional advisory interpretation only; deterministic
  results and gates remain authoritative.
- Candidate metrics are kept separate; no cross-model aggregation or
  synthetic selection is reported.
- No strategy is approved for paper or live execution by this release.
