# Q20 Collection Continuation Runbook

**Phase:** Q20 — Extended Prospective Collection Continuation & Governance
**Collection ID:** collect-52cefa0dda64
**Freeze ID:** freeze-aaa4dd57057a
**Decision:** Q20_CONTINUATION_READY_WITH_WARNINGS (human continuation approval PENDING)
**Scope:** Read-only continuation procedures; no scheduler activation without authorization; no trading
**Credential values:** None printed or stored in this document

---

## 1. Current Collection Evidence

Assessment time: 2026-11-15T19:28:13Z. Collection started: 2026-11-13T06:15:46.915986+00:00. Seven-day deadline: 2026-11-20T06:15:46.915986+00:00 (~4.06 days remaining; threshold not yet elapsed).

| Metric | Value |
|---|---|
| Accepted observations | 15 |
| Expected intervals | 69 |
| Coverage fraction | 0.2174 (21.74%) |
| Remaining observations to 168 | 153 |
| Raw ingestion events | 17 |
| Normalized events | 17 |
| Unique observations | 15 |
| Duplicate count | 2 (expected dedup behavior) |
| Conflict count | 0 |
| Rejection count | 70 (all historical latency rejections) |
| Ledger integrity | valid, 17 events |
| Ingestion status | HEALTHY |
| Provenance status | HEALTHY |
| Collection status | ACCUMULATING |
| Evaluation blocked | true |

Evidence conclusion: Subsystems are HEALTHY; the blocker is observation volume and coverage against a growing denominator, not collection failure. All 70 rejections are historical latency rejections; Q18 retry hardening (max_retries=3, max_duration_ms=60000, exponential backoff 1s-8s, RateLimitExceeded retried) addresses this class going forward.

---

## 2. Recalculated Coverage Requirements

Q5.2 prospective validation requires all three simultaneously:

| Requirement | Threshold | Current | Status |
|---|---|---|---|
| Observations | >= 168 | 15 | Not met (153 more needed) |
| Coverage fraction | >= 0.95 (95%) | 0.2174 | Not met |
| Elapsed duration | >= 7 days | ~2 days | Met at 2026-11-20T06:15:46Z |

Coverage formula (growing denominator, from `collector.py`): `coverage = accepted_count / expected_intervals`, where `expected_intervals` counts exchange-aligned 1h candles whose open time is strictly after `collection_started_at_utc` and whose close time is at or before now. The denominator grows by 1 every hour; rejected intervals count against coverage and never leave the denominator.

Recalculated feasibility:

| Quantity | Value |
|---|---|
| n_coverage (observations needed for 95% coverage) | 1011 |
| n_required | max(153, 1011) = 1011 |
| Hours needed from assessment | 1011 h |
| Days needed | 42.1 d |
| Missed interval effect | T >= 1011 + 20*M hours |
| Earliest eligibility (M=0) | 2026-12-28T07:51:53+00:00 |

Each missed interval adds ~20 hours because coverage must rebuild against a growing denominator. Zero missed intervals is the planning target.

---

## 3. Earliest Possible Eligibility Date

- Assessment time: 2026-11-15T19:28:13Z
- Required near-perfect hourly collections: 1011
- Earliest eligibility (zero misses): 2026-12-28T07:51:53+00:00

| Missed intervals (M) | Total hours T | Approx. date (UTC) |
|---|---|---|
| 0 | 1011 | 2026-12-28T07:51:53Z |
| 1 | 1031 | ~2026-12-29T11:51:53Z |
| 5 | 1111 | ~2027-01-02T07:51:53Z |
| 10 | 1211 | ~2027-01-07T07:51:53Z |
| 25 | 1511 | ~2027-01-21T07:51:53Z |

Dates beyond zero misses are approximate; each miss re-derives T = 1011 + 20*M from assessment time. The freeze has no expiration, so there is no deadline-driven cutover, but delays delay downstream Q5.2 validation.

---

## 4. Governance Continuation Decision

Decision: Q20_CONTINUATION_READY_WITH_WARNINGS.

| Governance item | Value |
|---|---|
| Seven days is a minimum floor, not a fixed window | true |
| Collection can continue beyond seven days | true |
| Freeze remains valid, no expiration | true |
| ACCUMULATING is an allowed state | true |
| DATA_INVALID / insufficient evidence is recoverable | true |
| No governance rule prohibits extension | true |
| Q6 preflight is final eligibility gate | true |
| Human continuation approval | PENDING_HUMAN_APPROVAL |

Continuation is governance-permitted and evidence-supported, but **human continuation approval for extending collection beyond 7 days is PENDING** and no recorded human approval exists. Do not act on extension beyond the seven-day window until human approval is recorded.

---

## 5. Step-by-Step Continuation Procedures

### Preconditions (verify before every collection cycle)

1. Confirm `ledger_integrity` is valid with 0 duplicates and 0 conflicts.
2. Confirm `ingestion_status` and `provenance_status` are HEALTHY.
3. Confirm host clock is synchronized to UTC (clock drift causes latency rejections).
4. Confirm no credential values are being printed or logged.
5. Confirm human continuation approval has been recorded if acting beyond the seven-day window.

### Collection cadence

| Role | Time (UTC) | Rationale |
|---|---|---|
| Primary | HH:08 every hour | 3 min after candle finalization (:05 post-close), inside the 900s ingestion-latency deadline (:15) |
| Retry | HH:12 every hour | Fallback if primary collection fails or is rejected |

Each collection targets the exchange-aligned 1h candle that closed at HH:00. Collection must complete with ingestion latency < 900s (before :15) for the observation to be accepted.

Collection rules:
- Accepted candle open time must be strictly after `collection_started_at_utc` (2026-11-13T06:15:46.915986+00:00).
- Candle close time must be at or before now (fully finalized).
- Ingestion latency must be under 900s.
- Do not collect candles outside the prospective window; do not backfill; do not fabricate observations.

### After each collection, confirm

1. `observation_count` increased (at least one new accepted observation).
2. New candle latency < 900s.
3. Candle finalized with > 300s margin after close.
4. `duplicates == 0` and `conflicts == 0`.
5. `ledger_integrity`, `ingestion_status`, `provenance_status` remain HEALTHY.

### Monitoring after each window

```powershell
uv run python scripts/q20_prospective_monitor.py
```

Confirm: `collection_status`, `accepted_observations`, `expected_intervals`, `coverage_fraction`, `evaluation_blocked`, subsystem health, duplicate/conflict counts, rejection count. Exit code 0 means the report files were read; it does not mean collection is eligible.

### Eligibility declaration

Declare Q5_2_PROSPECTIVE_VALIDATED = true only when all three thresholds hold simultaneously:
- observations >= 168
- coverage >= 95%
- elapsed duration >= 7 days

Earliest projection: 2026-12-28T07:51:53Z at zero misses.

### What is NOT in scope

- No scheduler activation or Windows scheduled tasks without explicit authorization.
- No backfill of historical candles outside the prospective window.
- No fabricated or synthetic observations.
- No live trading, real-money orders, or paper trading.
- No model retraining, refitting, calibration, tuning, threshold selection, or feature selection.
- No API/dashboard write endpoints or public exposure.
- No credential rotation or secret handling in this document.

---

## 6. Recovery Procedures

### R1 — Missed primary window (HH:08)

Use the HH:12 retry window for the same candle. As long as collection completes before ingestion latency exceeds 900s and the candle is finalized, the observation can still be accepted. A fully missed hour counts as a missed interval (M += 1) and adds ~20 hours to required time (T >= 1011 + 20*M).

### R2 — Transient Binance failure

The Q18 adapter retry policy handles transient failures automatically:
- max_retries = 3 (4 total attempts)
- max_duration_ms = 60000
- Backoff: exponential, multiplier=1, min=1s, max=8s
- Stop condition: stop_after_attempt | stop_after_delay (whichever first)
- RateLimitExceeded (HTTP 429) retried

If retries exhaust, wait for the next valid window (HH:08 or HH:12) and collect the next candle. Do not backfill the missed candle.

### R3 — Persistent connectivity failure

1. Check the Binance exchange status page for ongoing incidents.
2. Confirm host clock is synchronized to UTC.
3. Retry at the next valid window.
4. If failure persists across multiple windows, escalate (see Section 7).

### R4 — Ingestion latency > 900s (rejections)

1. Confirm host clock synchronized to UTC.
2. Collect earlier in the window (closer to :08 primary) to reduce latency.
3. Investigate network latency between host and Binance public endpoints.
4. Rejections count against coverage; minimize them to avoid extending eligibility.

### R5 — Subsystem unhealthy (ledger / ingestion / provenance)

1. Stop collection immediately.
2. Verify ledger chain: storage must verify HEALTHY with 0 duplicates, 0 conflicts.
3. Do not collect until all subsystems return HEALTHY.
4. If the chain is broken or conflicts appear, escalate to the integration lead; do not attempt repair without authorization.

### R6 — evaluation_blocked remains true

Continue hourly collection at HH:08 / HH:12. Re-check after each window. Eligibility requires all three thresholds simultaneously (obs >= 168, coverage >= 95%, duration >= 7 days). Track misses against T >= 1011 + 20*M.

---

## 7. Escalation Criteria

Escalate to the quant-lead / integration lead when any of the following occurs:

- Binance public connectivity fails persistently across multiple collection windows (check exchange status page first).
- Ingestion latency rejections persist despite collecting within :05-:15 post-close and a synchronized UTC clock.
- Ledger chain is broken, duplicates > 0, or conflicts > 0 (do not collect; do not self-repair).
- `evaluation_blocked` remains true after exhausting a sustained run of valid windows, or the operational burden of ~42 days of near-perfect collection becomes unsustainable.
- Human continuation approval cannot be obtained for extension beyond 7 days.

Do not force collection, backfill, fabricate observations, activate a scheduler, or enable Windows scheduled tasks to unblock evaluation.

---

## 8. Remaining Human Approvals

| Approval | Status | Owner |
|---|---|---|
| Human continuation approval for extending collection beyond 7 days | PENDING | Human stakeholder / quant-lead |
| Windows scheduled task activation (if desired later) | NOT AUTHORIZED | Requires explicit user authorization |
| Q5_2_PROSPECTIVE_VALIDATED declaration | false | Declared only when all three thresholds hold simultaneously |

No approval has been recorded for any of the above. This runbook is readiness documentation only.

---

## References

- Q20 machine-readable report: `docs/validation/results/q20_collection_continuation.json`
- Q20 human-readable report: `docs/validation/results/q20_collection_continuation.md`
- Q20 prospective monitoring: `docs/operations/Q20_PROSPECTIVE_MONITORING.md`
- Q20 scheduler instructions: `docs/operations/Q20_SCHEDULER_INSTRUCTIONS.md`
- Monitor script: `scripts/q20_prospective_monitor.py`
- Scheduler script: `scripts/q20_prospective_scheduler.ps1`
- Collector formula: `src/crypto_engine/research/prospective/collector.py`
- Q18/Q19 reports: `docs/validation/results/q18_prospective_integrity_recovery.*`, `docs/validation/results/q19_prospective_feasibility.*`

---

*End of Q20 collection continuation runbook.*
