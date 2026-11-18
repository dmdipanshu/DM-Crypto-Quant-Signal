# Q21 — Collection Operations Activation & Evaluation Readiness

**Decision:** Q21_OPERATIONS_READY_WITH_WARNINGS
**Collection ID:** collect-52cefa0dda64
**Freeze ID:** freeze-aaa4dd57057a
**Assessment Time:** 2026-11-16T06:33:20Z

## Executive Summary

Collection `collect-52cefa0dda64` is in ACCUMULATING status with 16 of 168 required observations accepted (22.54% coverage), all frozen under `freeze-aaa4dd57057a`. Human continuation approval is pending — no approval has been recorded, so evaluation remains blocked. The scheduler dry-run could not be executed because pwsh (PowerShell 7+) is not installed on this Windows host; PowerShell 5.1 and schtasks.exe are available. The full test suite passed (1408 passed, 0 failed, 3 skipped).

## Current Prospective Collection Metrics

| Metric | Value |
|---|---|
| Accepted observations | 16 (needs 152 more for 168) |
| Raw ingestion events | 18 |
| Normalized events | 18 |
| Unique observations | 16 |
| Duplicates | 2 |
| Conflicts | 0 |
| Expected intervals | 71 |
| Coverage fraction | 22.54% (needs 72.46% more for 95%) |
| Rejections | 71 |
| Ledger integrity | HEALTHY (18 events, valid hash chain) |
| Provenance status | HEALTHY |
| Collection status | ACCUMULATING |
| Evaluation blocked | true |
| 7-day threshold elapsed | No (~4.01 days remaining) |
| Earliest eligibility date | 2026-12-29T23:03:57Z |
| Remaining observations to 168 | 152 |
| N_coverage | 1049 |
| N_required | 1049 |
| Hours needed | 1049 |
| Days needed | 43.7 |
| Rejection count | 71 |
| Elapsed duration seconds | 258490 |

## Human Continuation Approval Status

**Status:** PENDING_HUMAN_APPROVAL

No recorded approval exists for continuing collection operations. Evaluation cannot proceed until human approval is recorded against Collection ID `collect-52cefa0dda64` / Freeze ID `freeze-aaa4dd57057a`.

## Windows PowerShell Runtime Verification

| Runtime | Available | Version | Path |
|---|---|---|---|
| powershell.exe 5.1 | Yes | v5.1.26100.9818 | C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe |
| pwsh 7+ | No | Not installed | — |
| schtasks | Yes | — | C:\Windows\system32\schtasks.exe |

## Scheduler Dry-Run Results

**Status:** NOT EXECUTED

pwsh (PowerShell 7+) is not installed, so the scheduler dry-run was not tested. schtasks.exe is available, but the dry-run workflow requires pwsh 7+.

## Collection Reliability Evidence

Verified safety properties:

- Ledger hash chain is valid across all 18 ingestion events (HEALTHY)
- Provenance metadata is complete and consistent (HEALTHY)
- No conflicting observations detected (Conflicts: 0)
- Duplicate handling is deterministic (Duplicates: 2 logged, not accepted)
- Rejections are accounted for in metrics (Rejection count: 71)
- Expected interval coverage is tracked explicitly (71 expected intervals)
- Rejection count is invariant with respect to ledger state

## Monitoring and Failure-Recovery Results

- Monitor script: scripts/q20_prospective_monitor.py (21 metrics, 8 warning conditions)
- Exit code: 0
- Failure scenarios: 4/4 pass

## Evaluation Workflow Readiness

### State Machine

| State | Description | Transition Condition |
|---|---|---|
| ACCUMULATING | Collection gathering observations | Approved observations < 168 |
| ELIGIBLE_FOR_EVALUATION | Collection eligible for evaluation | Observations >= 168 AND coverage >= 95% AND 7-day threshold elapsed AND human approval recorded |
| EVALUATED | Evaluation completed | Preflight passed and evaluation executed |

### Exact CLI Commands

- Collect: `sf collect run --collection-id collect-52cefa0dda64 --freeze-id freeze-aaa4dd57057a`
- Status: `sf collect status --collection-id collect-52cefa0dda64`
- Validate: `sf collect validate --collection-id collect-52cefa0dda64 --freeze-id freeze-aaa4dd57057a`
- Preflight: `sf collect preflight --collection-id collect-52cefa0dda64`
- Evaluate: `sf collect evaluate --collection-id collect-52cefa0dda64`

### Q6 Preflight Status

**Status:** BLOCKED

Evaluation preflight is blocked because human approval is pending and the 7-day threshold has not elapsed.

### Candidate Integrity Findings

- All accepted observations match the frozen manifest under `freeze-aaa4dd57057a`
- No candidate drift detected since freeze
- Ledger integrity verified HEALTY

### Authorization Controls

- Evaluation gated on recorded human continuation approval
- Q6 preflight blocks evaluation until all readiness conditions are met
- No credential values are stored in collection metadata or command arguments

## Tests Passed and Failed Table

| Suite | Passed | Failed | Skipped |
|---|---|---|---|
| Full test suite | 1408 | 0 | 3 |

## Files Changed

5 new files:

1. docs/validation/results/q21_collection_operations_readiness.md
2. scripts/q20_prospective_monitor.py
3. src/crypto_engine/collection/metrics.py
4. src/crypto_engine/collection/ledger.py
5. src/crypto_engine/collection/provenance.py

## Remaining Manual Actions

1. Record human continuation approval for Collection ID `collect-52cefa0dda64` / Freeze ID `freeze-aaa4dd57057a`
2. Resume collection until 168 observations accepted (152 remaining)
3. Wait for 7-day threshold to elapse (~4.01 days remaining; earliest eligibility 2026-12-29T23:03:57Z)
4. Verify coverage reaches 95% (needs 72.46% more from current 22.54%)
5. Install pwsh 7+ if scheduler dry-run is required before Q22
6. Re-run Q6 preflight after all blocking conditions clear
7. Execute evaluation once preflight passes

## Actual Execution Time

Assessment performed at 2026-11-16T06:33:20Z; report generation completed same session.

## Recommended Q22 Milestone

Q22 — Evaluation Activation: complete remaining accumulation (152 observations), satisfy 7-day threshold and 95% coverage, record human approval, clear Q6 preflight, and execute the first frozen-model evaluation against `freeze-aaa4dd57057a`.
