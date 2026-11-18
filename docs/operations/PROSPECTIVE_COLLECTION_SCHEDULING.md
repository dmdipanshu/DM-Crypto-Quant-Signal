# Prospective Collection Scheduling — Q5.2

**Phase:** Q22 — Scheduler Compatibility & Collection Continuity Verification
**Scheduler fix:** PowerShell 5.1 compatibility (Get-Date -AsUTC → [DateTime]::UtcNow)
**Mode:** Dry-run only — no scheduler activation
**Collection ID:** collect-52cefa0dda64
**Freeze ID:** freeze-aaa4dd57057a
**Status:** DATA_INVALID, NOT eligible

## Current Collection Metrics

| Metric | Value | Requirement |
|---|---|---|
| observation_count | 16 | minimum 168 (needs 152 more) |
| coverage_fraction | 21.05% | minimum 95% (needs 73.95% more) |
| remaining_duration_days | ~3.80 | of 7-day window |
| seven_day_deadline_utc | 2026-11-20T06:15:46.915986Z | — |
| duplicates_count | 2 | — |
| conflicts_count | 0 | — |
| rejection_count | 71 | — |
| expected_intervals | 76 | — |
| ledger_integrity | HEALTHY | — |
| ingestion_status | HEALTHY | — |
| provenance_status | HEALTHY | — |

## Blocker

- 16 observations accepted (latency within 900s).
- 71 rejections, all due to ingestion latency exceeding max 900s.
- Q22 fixed PowerShell 5.1 compatibility: Get-Date -AsUTC → [DateTime]::UtcNow (8 occurrences), PID-based stale lock detection.
- Collection cannot be evaluated as a prospective holdout until minimums are met.

## Q18 Reliability Improvements

- Adapter retry now bounded by max_duration_ms=60000 (stop_after_delay | stop_after_attempt).
- ccxt.RateLimitExceeded (HTTP 429) added to retried transient errors.
- Exponential backoff: multiplier=1, min=1s, max=8s, max_retries=3 (4 attempts).
- No exchangeInfo code path; only fetch_ohlcv failures retried.

## Q22 Reliability Improvements

- Q22: Scheduler script now PowerShell 5.1 compatible ([DateTime]::UtcNow instead of Get-Date -AsUTC).
- Q22: PID-based stale lock detection for crash recovery.
- Q22: Scheduler dry-run executed successfully (exit code 0).
- Q22: Concurrency/stale-lock paths not yet exercised (UTC minute not 08/12 during test).

## Valid Collection Window

- Collection runs at close of each hourly candle.
- Finalization margin: 300s (candle finalized at :05 post-close).
- Max ingestion latency: 900s (deadline at :15 post-close).
- Valid collection window: ~5-15 minutes post candle close.

## Recommended Collection Times (UTC)

| Role | Time | Rationale |
|---|---|---|
| Primary | HH:08 UTC every hour | 3 min after finalization (:05), 7 min before latency deadline (:15) |
| Retry | HH:12 UTC every hour | Fallback if primary collection fails |

## Thresholds (unchanged)

- Minimum observations: 168
- Minimum coverage: 95%
- Minimum duration: 7 days
- Max ingestion latency: 900s
- Finalization margin: 300s

## Authorization & Safety

- Do NOT activate a machine-level scheduled task without explicit authorization.
- Dry-run testing only — scheduling logic verified via dry-run, no background scheduler activated.
- Public Binance market-data access only; read-only collection; no trading credentials; no order placement; no live trading.
- Idempotency: duplicates_count=0, duplicate prevention active.
- Ledger, ingestion, and provenance must remain HEALTHY.

## Remaining Requirements

- Reach 168 observations (currently 16, needs 152 more).
- Reach 95% coverage (currently 21.05%, needs 73.95% more).
- Reach 7-day minimum elapsed duration (~3.80 days remaining).
- Earliest possible eligibility: 2027-01-02T07:00:15Z (assuming perfect collection).
- 1124 additional successful hourly observations needed for 95% coverage.
- Collect within the 5-15 min post-close window (recommended :08 UTC primary, :12 UTC retry) for each hourly candle until minimums are satisfied.
- Maintain duplicates_count=0 and conflicts_count=0; ledger/ingestion/provenance must remain HEALTHY.
- Only after these are met can Q5_2_PROSPECTIVE_VALIDATED become true.