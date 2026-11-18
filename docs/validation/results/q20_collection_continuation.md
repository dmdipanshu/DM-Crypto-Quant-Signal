# Q20 Extended Prospective Collection Continuation & Governance

**Phase:** Q20
**Topic:** Extended Prospective Collection Continuation & Governance
**Decision:** Q20_CONTINUATION_READY_WITH_WARNINGS
**Collection ID:** collect-52cefa0dda64
**Freeze ID:** freeze-aaa4dd57057a
**Collection started at UTC:** 2026-11-13T06:15:46.915986+00:00
**Seven-day deadline UTC:** 2026-11-20T06:15:46.915986+00:00
**Assessment time UTC:** 2026-11-15T19:28:13Z
**Status:** Collection continuation ready with warnings; Q5.2 prospective validation NOT yet eligible; production not ready.

Credential values: None printed or stored in this document.

---

## Executive Summary

Q20 reassesses Q5.2 prospective validation feasibility for collection `collect-52cefa0dda64` (BTC/USDT 1h, Binance public spot, frozen model, read-only) after Q18 reliability recovery and Q19 feasibility analysis.

Current state at assessment time (2026-11-15T19:28:13Z): 15 accepted observations, 69 expected intervals, 0.2174 (21.74%) coverage, collection status ACCUMULATING, evaluation blocked. All subsystems are HEALTHY (ledger with 17 verified events, ingestion, provenance); 2 duplicates (expected dedup behavior), 0 conflicts; 70 rejections, all historical latency rejections (>900s ingestion latency).

The original seven-day deadline (2026-11-20T06:15:46Z) has NOT yet elapsed (~4.06 days remain), but it cannot satisfy Q5.2 eligibility: maximum 168 observations cannot be reached and maximum coverage cannot reach 95% within the window. Only the duration floor is approaching.

Per Phase C governance, duration is a minimum eligibility floor, not a fixed expiry; the research freeze (`freeze-aaa4dd57057a`) has no expiration; collection can continue beyond seven days; ACCUMULATING is an allowed state; recovery is permitted. Decision: Q20_CONTINUATION_READY_WITH_WARNINGS — continuation is governance-permitted and evidence-supported, but human continuation approval for extending beyond 7 days is PENDING and no recorded human approval exists yet.

Recalculated feasibility: 153 more observations are needed to reach 168, but coverage drives the requirement: 1011 more near-perfect hourly collections (~42.1 days) with zero missed intervals are needed to reach 95% coverage against a growing denominator. Each missed interval adds 20 hours (T >= 1011 + 20*M). Earliest eligibility is projected at 2026-12-28T07:51:53+00:00 assuming zero misses from assessment time.

No live orders, real-money trading, retraining, tuning, calibration, backtesting, paper trading, or write endpoints are implemented. This report is validation-only and does not authorize production use or live trading.

---

## Current Prospective State

| Metric | Value |
|---|---|
| Accepted observations | 15 |
| Expected intervals | 69 |
| Coverage fraction | 0.2174 (21.74%) |
| Remaining observations to 168 | 153 |
| Raw ingestion events | 17 |
| Normalized events | 17 |
| Unique observations | 15 |
| Duplicate count | 2 |
| Conflict count | 0 |
| Rejection count | 70 |
| Evaluation blocked | true |
| Collection status | ACCUMULATING |
| Seven-day threshold elapsed | false |
| Remaining duration to deadline | 4.06 days |
| Ledger integrity | valid (17 events) |
| Ingestion status | HEALTHY |
| Provenance status | HEALTHY |

The blocker is insufficient prospective observation volume and low coverage, not a subsystem failure. Ledger, ingestion, and provenance are HEALTHY. All 70 rejections were caused by historical ingestion latency exceeding the 900s maximum; the Q18 adapter retry policy resolves this class of failure going forward. The 2 duplicates reflect expected dedup behavior in the storage reader (17 raw vs 15 unique).

---

## Exact Coverage Formula (from collector.py)

Quoted from `src/crypto_engine/research/prospective/collector.py` (coverage calculation using unique observations):

```text
# Expected intervals: count of exchange-aligned candles whose open time
# is strictly after collection_started_at_utc and whose close time is
# at or before now (fully finalized).
now_ms = int(datetime.now(UTC).timestamp() * 1000)
collection_start_ms = int(
    datetime.fromisoformat(result["collection_started_at_utc"]).timestamp() * 1000
)

expected_intervals = 0
for _symbol in self.symbols:
    for timeframe in self.timeframes:
        interval_ms = timeframe_to_ms(timeframe)
        # First exchange-aligned candle whose open time is strictly
        # after collection_started_at_utc
        first_open_ms = ((collection_start_ms // interval_ms) + 1) * interval_ms
        # Count intervals from first_open_ms to now_ms (inclusive of
        # intervals that have fully closed by now)
        t = first_open_ms
        while t + interval_ms <= now_ms:
            expected_intervals += 1
            t += interval_ms

actual_count = unique_observation_count
coverage = actual_count / expected_intervals if expected_intervals > 0 else 0.0
```

Coverage formula: **growing_denominator** — `coverage = accepted_count / expected_intervals`, where `expected_intervals` is the count of exchange-aligned candles whose open time is strictly after `collection_started_at_utc` and whose close time is at or before now. The denominator grows by 1 every hour, so coverage is a ratio against an ever-increasing expected count; rejected intervals count against coverage and never leave the denominator.

Verification at assessment time: 15 accepted / 69 expected = 0.21739... -> 0.2174 (21.74%). Matches `collector.py` `round(coverage, 4)`.

---

## Phase A Evidence Verification Results

| Check | Result | Evidence |
|---|---|---|
| Current state verified | PASS | 15 accepted obs, 69 expected intervals, 0.2174 coverage, ACCUMULATING, evaluation_blocked=true |
| Coverage formula verified | PASS | Formula quoted from collector.py; 15/69 = 0.2174 matches stored coverage_fraction |
| Count feasibility recalculated | PASS | 153 more obs needed to reach 168; coverage drives at 1011 |
| Coverage feasibility recalculated | PASS | n_coverage = 1011 from growing-denominator recurrence |
| Original deadline feasibility | FAIL | obs>=168 impossible within window; coverage>=95% impossible within window |
| Extended collection governance verified | PASS | Phase C: duration is minimum floor, no freeze expiration, continuation permitted |
| Collection integrity verified | PASS | Ledger valid (17 events), 0 conflicts, 2 expected duplicates, subsystems HEALTHY |
| Eligibility date recalculated | PASS | 2026-12-28T07:51:53+00:00 (1011 hours after assessment, zero misses) |
| Q5.2 prospective validated | FAIL | 15/168 obs, 21.74%/95% coverage; not eligible |
| Q4 governance preserved | PASS | Frozen model, read-only public data, no trading endpoints |
| Security regression passed | PASS | 1408 passed, 0 failed, 3 skipped; no credential values logged |

---

## Phase B Mathematical Calculations

### Baseline deadlines

- Collection started: 2026-11-13T06:15:46.915986+00:00
- Seven-day deadline: 2026-11-20T06:15:46.915986+00:00
- Assessment time: 2026-11-15T19:28:13Z
- Hours remaining to deadline: ~97.4 h (~4.06 days)

### Requirements (Q5.2 prospective)

- Minimum observations: 168
- Minimum coverage fraction: 0.95 (95%)
- Minimum elapsed duration: 7 days

### By the original seven-day deadline

| Quantity | Value | Derivation |
|---|---|---|
| Remaining duration | ~4.06 days | Deadline - assessment |
| Max collectable intervals remaining | ~97 | One collection attempt per hourly interval remaining |
| Max accepted by deadline | ~112 | 15 already accepted + ~97 new |
| Observation requirement (>=168) | false | Max ~112 < 168 |
| Coverage requirement (>=95%) | false | Denominator grows each hour; 21.74% now, unreachable |
| Duration requirement (>=7 days) | true | 7 days elapse at the deadline |
| All requirements met | false | Observation and coverage fail |

Conclusion: The original seven-day deadline cannot satisfy Q5.2 eligibility. Only duration is met.

### Extended collection feasibility (recalculated)

| Quantity | Value |
|---|---|
| Remaining observations to 168 | 153 |
| n_coverage (observations needed for 95% coverage) | 1011 |
| n_required | 1011 |
| Hours needed from assessment | 1011 h |
| Days needed | 42.1 d |
| Missed interval effect formula | T >= 1011 + 20*M |
| Earliest eligibility date UTC (M=0) | 2026-12-28T07:51:53+00:00 |

Missed interval examples (total hours T from assessment):
- M=0: 1011 h (~42.1 d) -> 2026-12-28T07:51:53Z
- M=1: 1031 h (~43.0 d)
- M=5: 1111 h (~46.3 d)
- M=10: 1211 h (~50.5 d)
- M=25: 1511 h (~63.0 d)

Each missed interval forces roughly one extra day of collection because coverage must climb back to 95% against a growing denominator; every missed hour both removes an acceptance opportunity and adds one to the denominator.

### Rationale for n_coverage = 1011

To reach 95% coverage with a growing denominator starting from 15 accepted / 69 expected, every newly accepted hour must offset denominator growth. Solving the recurrence `accepted / expected >= 0.95` with one acceptance per hour: (15 + t) / (69 + t) >= 0.95 -> t >= 1011, i.e. 1011 hours of near-perfect collection (~42.1 days) from the assessment time.

---

## Phase C Governance Decision (CONTINUATION_READY_WITH_WARNINGS)

| Governance item | Value |
|---|---|
| Duration semantics | minimum_floor_not_fixed_window |
| Collection can continue beyond seven days | true |
| Freeze remains valid / has expiration | true / false |
| ACCUMULATING is an allowed state | true |
| DATA_INVALID / insufficient evidence is recoverable | true |
| No governance rule prohibits extension | true |
| Q6 preflight is final eligibility gate | true |
| Human continuation approval | PENDING_HUMAN_APPROVAL |
| Decision | Q20_CONTINUATION_READY_WITH_WARNINGS |

Rationale: The seven-day window is a minimum eligibility floor, not a hard expiry. The research freeze has no expiration, so collection may continue until observation count, coverage fraction, and elapsed duration are all satisfied simultaneously. ACCUMULATING reflects ongoing, healthy collection; evaluation remains blocked until thresholds are met. Continuation requires recorded human approval before extension beyond 7 days is actioned; this report documents readiness but does not grant it.

---

## Phase D Reliability & Security Regression

| Item | Value |
|---|---|
| Collection cadence (primary) | HH:08 UTC |
| Collection cadence (retry) | HH:12 UTC |
| Scheduler script | scripts/q20_prospective_scheduler.ps1 |
| Scheduler task name | SignalForge-Q20-Prospective-Collection |
| Single-instance protection | true |
| Idempotent no-op | true |
| Bounded retries | 2 |
| Scheduler registered | false |
| Dry-run tested | false (PowerShell unavailable on this system) |
| Monitoring script | scripts/q20_prospective_monitor.py |
| Monitoring metrics | 21 |
| Warning conditions | 8 |
| Monitoring read-only | true |
| Monitor credential output | none |

Security regression results: 1408 passed, 0 failed, 3 skipped. Q18 adapter tests 12 passed; Q19 feasibility tests 14 passed; Q5 prospective tests 37 passed. Q18 retry intact; Q19 calculations correct. No NVIDIA API key in user scope; no META_MUSE_API_KEY env var found; credentials not inspected in logs; deployment config present; API auth fail-closed; observability operational.

---

## Phase E Test Results

| Metric | Value |
|---|---|
| Total passed | 1408 |
| Total failed | 0 |
| Total skipped | 3 |
| Q18 adapter tests | 12 passed |
| Q19 feasibility tests | 14 passed |
| Q5 prospective tests | 37 passed |
| All tests pass | true |

Skips are pre-existing environmental skips, not failures. Zero credential values appear in test output or reports.

---

## Readiness Flags Table

| Flag | Value |
|---|---|
| COLLECTION_CONTINUATION_GOVERNANCE_VERIFIED | true |
| HUMAN_CONTINUATION_APPROVAL | PENDING_HUMAN_APPROVAL |
| CURRENT_EVIDENCE_RECALCULATED | true |
| COVERAGE_REQUIREMENT_RECALCULATED | true |
| ELIGIBILITY_DATE_RECALCULATED | true |
| SCHEDULER_DRY_RUN_PASSED | false |
| SCHEDULER_REGISTERED | false |
| COLLECTION_MONITORING_OPERATIONAL | true |
| COLLECTION_INTEGRITY_VERIFIED | true |
| Q5_2_PROSPECTIVE_VALIDATED | false |
| Q4_GOVERNANCE_PRESERVED | true |
| PRODUCTION_READY | false |
| LIVE_TRADING_AUTHORIZED | false |

---

## Warnings and Blockers

1. **Human continuation approval is PENDING** — no recorded human approval exists for extending collection beyond 7 days. Continuation cannot be actioned without it.
2. **Original 7-day deadline (~4.06 days remaining) cannot satisfy obs>=168 or coverage>=95%.** Only duration is met by 2026-11-20T06:15:46Z.
3. **Extended collection requires ~42 more days of near-perfect collection** (~1011 hourly accepts) before Q5.2 eligibility.
4. **Each missed interval adds 20 hours** to required time (formula: T >= 1011 + 20*M).
5. **Earliest eligibility: 2026-12-28T07:51:53Z** assuming zero missed intervals from assessment time.
6. **No successful ingestion for more than two collection cycles** — monitor for stalls via `scripts/q20_prospective_monitor.py`.
7. **Storage reader shows 17 raw vs 15 unique (2 duplicates)** — expected dedup behavior, not corruption.
8. **70 rejected observations**, all historical latency rejections.
9. **SCHEDULER_DRY_RUN_PASSED is false** — PowerShell not available on this system for dry-run testing; scheduler not registered.

Blockers: HUMAN_CONTINUATION_APPROVAL = PENDING_HUMAN_APPROVAL; Q5_2_PROSPECTIVE_VALIDATED = false; PRODUCTION_READY = false; LIVE_TRADING_AUTHORIZED = false.

---

## Not Implemented

- live orders
- real-money trading
- model retraining / refitting / calibration / tuning
- threshold selection
- feature selection
- backtest execution
- paper trading
- portfolio accounting
- API/dashboard write endpoints
- public exposure
- Windows scheduled task activation (requires explicit authorization)

---

## Files Changed

| File | Change |
|---|---|
| scripts/q20_prospective_scheduler.ps1 | Q20, new |
| scripts/q20_prospective_monitor.py | Q20, new |
| tests/unit/research/q19/test_q19_coverage_feasibility.py | Q19 test fixed — stale assertion corrected |
| docs/validation/results/q20_collection_continuation.json | Q20, new |
| docs/validation/results/q20_collection_continuation.md | Q20, new |
| docs/operations/Q20_COLLECTION_CONTINUATION_RUNBOOK.md | Q20, new |
| docs/operations/Q20_PROSPECTIVE_MONITORING.md | Q20, new |

---

## References

- Q20 machine-readable report: `docs/validation/results/q20_collection_continuation.json`
- Q19 feasibility report: `docs/validation/results/q19_prospective_feasibility.md` / `.json`
- Q18 integrity recovery report: `docs/validation/results/q18_prospective_integrity_recovery.md`
- Q20 collection continuation runbook: `docs/operations/Q20_COLLECTION_CONTINUATION_RUNBOOK.md`
- Q20 prospective monitoring: `docs/operations/Q20_PROSPECTIVE_MONITORING.md`
- Q20 scheduler instructions: `docs/operations/Q20_SCHEDULER_INSTRUCTIONS.md`
- Collection continuity procedures: `docs/operations/PROSPECTIVE_COLLECTION_CONTINUITY.md`
- Monitor script: `scripts/q20_prospective_monitor.py`
- Scheduler script: `scripts/q20_prospective_scheduler.ps1`
- Collector formula: `src/crypto_engine/research/prospective/collector.py`

---

*End of Q20 report.*
