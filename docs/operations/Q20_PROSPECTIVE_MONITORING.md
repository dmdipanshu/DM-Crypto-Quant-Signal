# Q20 Prospective Collection Monitoring

**Phase:** Q20 — Extended Prospective Collection Continuation & Governance
**Collection ID:** collect-52cefa0dda64
**Freeze ID:** freeze-aaa4dd57057a
**Script:** `scripts/q20_prospective_monitor.py`
**Mode:** Read-only; no credential output; no storage or scheduling mutation
**Credential values:** None printed or stored in this document

---

## 1. Monitor Script Usage

### Run the monitor

```powershell
uv run python scripts/q20_prospective_monitor.py
```

### What it does

The monitor reads current collection state (ledger, storage reader, ingestion and provenance subsystem status) and emits 21 metrics plus 8 warning-condition evaluations. It performs only read operations: it does not write to storage, does not trigger collection, does not register or modify any Windows scheduled task, and does not read or print credentials or secrets.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Monitor ran successfully and emitted metrics/warnings |
| 1 | Monitor error (e.g., unreadable state files) |

Exit code 0 means the report files were read; it does **not** mean collection is eligible. Interpret metrics and warnings to determine state.

### Recommended cadence

Run after each collection window (HH:08 primary / HH:12 retry UTC) and record `accepted_observations`, `expected_intervals`, `coverage_fraction`, `evaluation_blocked`, and subsystem health. Track trends over time to detect stalls and coverage regression.

---

## 2. All Metrics Explained (21)

| # | Metric | Meaning |
|---|---|---|
| 1 | `collection_status` | Current collection lifecycle state (e.g., ACCUMULATING) |
| 2 | `accepted_observations` | Count of uniquely accepted prospective observations |
| 3 | `expected_intervals` | Count of exchange-aligned 1h candles open > started, close <= now |
| 4 | `coverage_fraction` | `accepted_observations / expected_intervals` (growing denominator) |
| 5 | `evaluation_blocked` | true while Q5.2 eligibility thresholds are not all met |
| 6 | `raw_ingestion_events` | Raw events read from ingestion/storage |
| 7 | `normalized_events` | Normalized events after dedup/validation |
| 8 | `unique_observations` | Deduplicated unique observation count |
| 9 | `duplicate_count` | Duplicate events detected by dedup (expected behavior) |
| 10 | `conflict_count` | Conflicting observations detected (must be 0) |
| 11 | `rejection_count` | Rejected observations (latency-driven rejections) |
| 12 | `ledger_integrity_valid` | Ledger chain verification result (true/false) |
| 13 | `ledger_event_count` | Number of events in the ledger chain |
| 14 | `provenance_status` | Provenance subsystem health (HEALTHY / degraded) |
| 15 | `ingestion_status` | Ingestion subsystem health (HEALTHY / degraded) |
| 16 | `hours_since_collection_started` | Elapsed hours since `collection_started_at_utc` |
| 17 | `minutes_since_last_collection` | Minutes since the last collection attempt |
| 18 | `minutes_since_primary_window` | Minutes since the last HH:08 primary window |
| 19 | `minutes_until_seven_day_deadline` | Minutes remaining until the seven-day deadline |
| 20 | `remaining_duration_days` | Days remaining until the seven-day deadline |
| 21 | `observations_needed_to_168` | Remaining accepted observations to reach 168 |

Derived planning values (from Q20 feasibility recalculation): n_coverage = 1011, n_required = 1011, hours_needed = 1011 h (~42.1 d), earliest eligibility 2026-12-28T07:51:53+00:00 at zero misses, missed interval effect T >= 1011 + 20*M.

---

## 3. All Warning Conditions Explained (8)

| # | Warning condition | Trigger | Meaning |
|---|---|---|---|
| 1 | `evaluation_blocked == true` | Q5.2 thresholds not all met | Evaluation remains blocked; continue collection |
| 2 | `collection_status != ACCUMULATING` | Collection not in ACCUMULATING state | Collection may be stalled or stopped |
| 3 | `ledger_integrity_valid == false` OR `duplicate_count > 0` OR `conflict_count > 0` | Ledger chain unhealthy | Stop collection; do not self-repair; escalate |
| 4 | `provenance_status != HEALTHY` OR `ingestion_status != HEALTHY` | Subsystem degraded | Pause collection until subsystems return HEALTHY |
| 5 | No successful ingestion for more than two collection cycles | `accepted_observations` flat across >= 2 windows | Possible collection stall; investigate connectivity/latency |
| 6 | `seven_day_threshold_elapsed == true` without eligibility | Deadline passed but obs/coverage unmet | Duration floor met; extended collection required (~42 more days) |
| 7 | `rejection_count` increasing | New latency rejections appearing | Ingestion latency > 900s; collect closer to HH:08, sync UTC clock |
| 8 | `coverage_fraction` not improving toward 95% | Coverage flat or regressing | Missed intervals or rejections eroding coverage; each miss adds ~20 h |

---

## 4. Response Procedures for Each Warning

### W1 — evaluation_blocked == true

1. Continue hourly collection at HH:08 (primary) / HH:12 (retry).
2. Re-run the monitor after each window.
3. Eligibility requires all three simultaneously: observations >= 168, coverage >= 95%, elapsed duration >= 7 days.
4. Earliest eligibility: 2026-12-28T07:51:53Z at zero misses; each missed interval adds ~20 hours.

### W2 — collection_status != ACCUMULATING

1. Confirm no collection was intentionally stopped.
2. Check monitor output for the last successful collection timestamp (`minutes_since_last_collection`).
3. If stale, check Binance exchange status page and host UTC clock synchronization.
4. Resume at the next valid window (HH:08 or HH:12). Do not backfill.

### W3 — ledger integrity unhealthy (invalid / duplicates > 0 / conflicts > 0)

1. Stop collection immediately.
2. Verify ledger chain independently; it must be valid with 0 duplicates, 0 conflicts.
3. Do not collect until all subsystems return HEALTHY.
4. Escalate to the integration lead; do not attempt repair without authorization.

### W4 — provenance or ingestion subsystem not HEALTHY

1. Pause collection.
2. Re-run the monitor to confirm the unhealthy status persists.
3. Check host clock synchronization to UTC and network connectivity to Binance public endpoints.
4. Resume only when both subsystems return HEALTHY.

### W5 — No successful ingestion for more than two collection cycles

1. Confirm `accepted_observations` has not increased across >= 2 consecutive windows.
2. Check Binance exchange status page for incidents.
3. Confirm host clock synchronized to UTC.
4. Retry at the next valid window (HH:08 / HH:12).
5. If failure persists across multiple windows, escalate (persistent connectivity failure).

### W6 — Seven-day threshold elapsed without eligibility

1. Confirm deadline (2026-11-20T06:15:46Z) has passed and duration floor met.
2. Confirm obs >= 168 and coverage >= 95% are still unmet.
3. Continue extended collection per Phase C governance (duration is a minimum floor; freeze has no expiration).
4. Record human continuation approval before acting on extension beyond 7 days (currently PENDING).
5. Plan for ~42 more days of near-perfect collection (1011 hourly accepts), zero missed intervals target.

### W7 — rejection_count increasing

1. Confirm host clock synchronized to UTC (clock drift causes latency rejections).
2. Collect earlier in the window, closer to :08 primary, to keep ingestion latency < 900s (before :15).
3. Investigate network latency between host and Binance public endpoints.
4. Rejections count against coverage; minimize them to avoid extending eligibility.

### W8 — coverage_fraction not improving toward 95%

1. Cross-check with W5/W7 (stalls and rejections erode coverage).
2. Verify no missed intervals; each miss adds ~20 hours (T >= 1011 + 20*M).
3. Prioritize zero missed intervals at HH:08 / HH:12.
4. Re-project earliest eligibility after each window using current coverage trajectory.

---

## 5. Read-Only Guarantees

- The monitor script performs read-only operations over collection state files and subsystem status.
- It does not write to storage, append to the ledger, create or modify observations, or alter collection state.
- It does not register, create, modify, or delete any Windows scheduled task (`SignalForge-Q20-Prospective-Collection`).
- It does not trigger collection runs; invocation is purely observational.
- It reads no credentials, tokens, or secrets and prints no credential values (`no_credential_output: true`).
- Logs and output contain no secrets.
- Scheduler dry-run is not available on this system (`dry_run_tested: false`); PowerShell is unavailable for `scripts/q20_prospective_scheduler.ps1` dry-run testing.

---

## 6. References

- Q20 machine-readable report: `docs/validation/results/q20_collection_continuation.json`
- Q20 human-readable report: `docs/validation/results/q20_collection_continuation.md`
- Q20 collection continuation runbook: `docs/operations/Q20_COLLECTION_CONTINUATION_RUNBOOK.md`
- Q20 scheduler instructions: `docs/operations/Q20_SCHEDULER_INSTRUCTIONS.md`
- Monitor script: `scripts/q20_prospective_monitor.py`
- Scheduler script: `scripts/q20_prospective_scheduler.ps1`
- Collector formula: `src/crypto_engine/research/prospective/collector.py`

---

*End of Q20 prospective monitoring documentation.*
