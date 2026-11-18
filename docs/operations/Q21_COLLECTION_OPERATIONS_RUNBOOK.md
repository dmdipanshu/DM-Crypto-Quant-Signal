# Q21 Collection Operations Runbook

**Collection ID:** collect-52cefa0dda64
**Freeze ID:** freeze-aaa4dd57057a
**Status:** ACCUMULATING
**Human Continuation Approval:** PENDING_HUMAN_APPROVAL

## Current Collection Evidence

| Metric | Value |
|---|---|
| Accepted observations | 16 |
| Raw events | 18 |
| Normalized events | 18 |
| Unique observations | 16 |
| Duplicates | 2 |
| Conflicts | 0 |
| Expected intervals | 71 |
| Coverage | 22.54% |
| Rejections | 71 |
| Ledger integrity | HEALTHY |
| Provenance | HEALTHY |
| Collection status | ACCUMULATING |
| Evaluation blocked | true |
| 7-day threshold elapsed | No |
| Remaining duration days | ~4.01 |

## Recalculated Coverage Requirements

- Remaining observations to 168: 152
- N_coverage: ceil((0.95 × 71 − 16) / 0.05) = ceil(1029.0) = 1049
- N_required: max(152, 1049) = 1049
- Hours needed: 1049
- Days needed: 43.7
- Earliest eligibility date: 2026-12-29T23:03:57Z
- Missed interval effect: T >= 1049 + 20×M

## Governance Continuation Decision

**Decision:** CONTINUE_BEYOND_SEVEN_DAYS

Verified governance points:
1. Ledger integrity: HEALTHY
2. Provenance: HEALTHY
3. Zero conflicts detected
4. No duplicate valid observations
5. 7-day threshold not yet elapsed (~4.01 days remaining)
6. Human continuation approval pending
7. Collection status ACCUMULATING with rejections documented

## Step-by-Step Collection Commands

| Step | Command | Purpose |
|---|---|---|
| 1 | `sf collection run --id collect-52cefa0dda64 --once` | Run one collection cycle |
| 2 | `sf collection status --id collect-52cefa0dda64 --freeze freeze-aaa4dd57057a` | Check collection status |
| 3 | `sf validation holdout --id collect-52cefa0dda64 --freeze freeze-aaa4dd57057a --prospective` | Validate prospective holdout readiness |
| 4 | `sf q6 preflight --collection collect-52cefa0dda64 --freeze freeze-aaa4dd57057a` | Run Q6 preflight |
| 5 | `sf collection monitor --id collect-52cefa0dda64 --interval 60s --tail` | Monitor collection |

## Failure Recovery Procedures

| Scenario | Recovery Action |
|---|---|
| Binance unavailable | Pause collection, verify public endpoint connectivity, resume automatically on next cycle; alert if >1 cycle missed |
| HTTP 429 | Apply exponential backoff, respect Retry-After header, pause until backoff completes |
| HTTP 5xx | Retry with jitter up to 3 attempts, then pause collection and alert on persistent failure |
| Collector process interruption | Restart collector from last committed ledger checkpoint; verify lock file released |
| Stale lock file | Verify no collector process holds the lock; remove stale lock file only after confirming no active process |
| Duplicate scheduler invocation | Second invocation exits immediately; first invocation retains lock and continues |
| Missing manifest | Regenerate manifest from ledger snapshot; do not fabricate missing observation data |
| Corrupted ledger | Restore ledger from last verified backup; re-verify provenance before resuming collection |
| Monitoring process failure | Restart monitor from CLI; reattach to active collection ID; confirm telemetry resumes |

## Escalation Criteria

1. Evaluation blocked persists beyond 7-day threshold with no human approval
2. Ledger integrity or provenance degrades to UNHEALTHY
3. Conflicts detected in normalized events exceed 0
4. Collection status not ACCUMULATING after 3 consecutive cycles

## Remaining Human Approvals

1. Human Continuation Approval: PENDING_HUMAN_APPROVAL (gate: continue beyond seven days)
2. Holdout readiness confirmation for prospective validation
3. Q6 preflight sign-off before model comparison

## Governance Restrictions Preserved

- No live orders; real-money trading is not authorized
- No model retraining, refitting, calibration, or tuning
- No threshold selection or feature selection
- No backtest execution, paper trading, or portfolio accounting
- No architecture rewrites; Phase 10 is validation-only on frozen model
- No credential values printed or stored in this document
