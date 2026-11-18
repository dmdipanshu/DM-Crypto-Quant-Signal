# Q19 Prospective Validation Feasibility & Collection Optimization

**Phase:** Q19  
**Decision:** Q19_FEASIBILITY_CONFIRMED_WITH_WARNINGS  
**Collection ID:** collect-52cefa0dda64  
**Freeze ID:** freeze-aaa4dd57057a  
**Collection started at UTC:** 2026-11-13T06:15:46.915986+00:00  
**Seven-day deadline UTC:** 2026-11-20T06:15:46.915986+00:00  
**Assessment time UTC:** 2026-11-15T17:58:45Z  
**Status:** Feasibility confirmed with warnings; Q5.2 prospective validation NOT yet eligible; production not ready.

---

## Executive Summary

Q19 assesses the feasibility of Q5.2 prospective validation for collection `collect-52cefa0dda64` (BTC/USDT 1h, Binance public spot, frozen model, read-only).

Current state at assessment time (2026-11-15T17:58:45Z): 1 accepted observation, 58 expected intervals, 1.72% coverage, collection status DATA_INVALID, evaluation blocked. All subsystems are HEALTHY (ledger, ingestion, provenance); 0 duplicates; 0 conflicts; 58 rejections, all latency-driven (>900s ingestion latency).

Phase B shows the original 7-day deadline (2026-11-20T06:15:46Z) cannot satisfy Q5.2 eligibility: maximum 110 accepted observations by deadline (requirement >= 168) and maximum 65.87% coverage (requirement >= 95%). Duration (7 days) is the only requirement met by the deadline.

Per Phase C governance, duration is a minimum floor, not a fixed window; the freeze has no expiration; collection can continue beyond seven days. Decision: CONTINUE_BEYOND_SEVEN_DAYS. Earliest eligibility is projected at 2026-12-30T19:58:45Z, requiring ~1082 near-perfect hourly collections (~45 days) assuming zero missed intervals. Each missed interval adds 20 hours to the required time.

No live orders, real-money trading, retraining, tuning, backtesting, paper trading, or write endpoints are implemented. This report is validation-only and does not authorize production use.

---

## Current Prospective State

| Metric | Value |
|---|---|
| Accepted observations | 1 |
| Expected intervals | 58 |
| Coverage fraction | 0.0172 (1.72%) |
| Evaluation blocked | true |
| Collection status | DATA_INVALID |
| Ledger integrity | HEALTHY |
| Ingestion status | HEALTHY |
| Provenance status | HEALTHY |
| Duplicates | 0 |
| Conflicts | 0 |
| Rejections | 58 |

The blocker is insufficient prospective observation volume and low coverage, not a subsystem failure. Ledger, ingestion, and provenance are HEALTHY. All 58 rejections were caused by ingestion latency exceeding the 900s maximum; the Q18 adapter retry policy (max_duration_ms=60000, 3 retries / 4 attempts, exponential backoff, RateLimitExceeded retried) resolves this class of failure going forward.

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

Verification at assessment time: 1 accepted / 58 expected = 0.01724... -> 0.0172 (1.72%). Matches `collector.py` `round(coverage, 4)`.

---

## Phase A Evidence Verification Results

| Check | Result | Evidence |
|---|---|---|
| Current state verified | PASS | 1 accepted obs, 58 expected intervals, 1.72% coverage, DATA_INVALID, evaluation_blocked=true |
| Coverage formula verified | PASS | Formula quoted from collector.py; 1/58 = 0.0172 matches stored coverage_fraction |
| Count feasibility calculated | PASS | Max 110 accepted by deadline vs 168 required |
| Coverage feasibility calculated | PASS | Max 65.87% coverage by deadline vs 95% required |
| Original deadline feasibility | FAIL | obs>=168 impossible (max 110); coverage>=95% impossible (max 65.87%) |
| Extended collection governance verified | PASS | Phase C: duration is minimum floor, no freeze expiration, CONTINUE_BEYOND_SEVEN_DAYS |
| Collection continuity verified | PASS | Cadence HH:08 primary / HH:12 retry; monitor script scripts/monitor_prospective_continuity.py |
| Eligibility date projected | PASS | 2026-12-30T19:58:45Z (1082 hours after assessment, zero misses) |
| Q5.2 prospective validated | FAIL | 1/168 obs, 1.72%/95% coverage; not eligible |
| Q4 governance preserved | PASS | Frozen model, read-only public data, no trading endpoints |
| Security regression passed | PASS | 1408 passed, 0 failed, 3 skipped; no credential values logged |

---

## Phase B Mathematical Calculations

### Baseline deadlines
- Collection started: 2026-11-13T06:15:46.915986+00:00
- Seven-day deadline: 2026-11-20T06:15:46.915986+00:00
- Assessment time: 2026-11-15T17:58:45Z
- Hours remaining to deadline: 108.28 h (4 days 12 h 17 min 1 s)

### Requirements (Q5.2 prospective)
- Minimum observations: 168
- Minimum coverage fraction: 0.95 (95%)
- Minimum elapsed duration: 7 days

### By the original seven-day deadline

| Quantity | Value | Derivation |
|---|---|---|
| Expected intervals at deadline | 167 | Exchange-aligned 1h candles open > started, close <= deadline |
| New expected intervals now to deadline | 109 | 167 - 58 expected intervals already counted |
| Max collectable intervals | 109 | One collection attempt per hourly interval |
| Max accepted by deadline | 110 | 1 already accepted + 109 new |
| Max coverage by deadline | 0.6587 | 110 / 167 = 0.65868... |
| Observation requirement (>=168) | false | Max 110 < 168 |
| Coverage requirement (>=95%) | false | Max 65.87% < 95% |
| Duration requirement (>=7 days) | true | 7 days elapse exactly at the deadline |
| All requirements met | false | Observation and coverage fail |

Conclusion: The original 7-day deadline cannot satisfy Q5.2 eligibility. Only duration is met.

### Extended collection feasibility (Phase B extended)

| Quantity | Value |
|---|---|
| n_count (observations required) | 167 |
| n_coverage (intervals needed for 95% coverage) | 1082 |
| n_required | 1082 |
| Hours needed | 1082 h |
| Days needed | 45.08 d |
| Missed interval effect formula | T >= 1082 + 20*M |
| Earliest eligibility date UTC (M=0) | 2026-12-30T19:58:45+00:00 |

Missed interval examples (total hours T):
- M=0: 1082 h
- M=1: 1102 h
- M=5: 1182 h
- M=10: 1282 h
- M=25: 1582 h

Each missed interval forces roughly one extra day of collection because coverage must climb back to 95% against a growing denominator; every missed hour both removes an acceptance opportunity and adds one to the denominator.

### Rationale for n_coverage = 1082

To reach 95% coverage with a growing denominator starting from 1 accepted / 58 expected, every newly accepted hour must offset denominator growth. Solving the recurrence `accepted / expected >= 0.95` with one acceptance per hour yields approximately 1082 required accepted intervals after the deadline, i.e. 1082 hours of near-perfect collection (~45.08 days) from the assessment time.

---

## Phase C Governance Decision (CONTINUE_BEYOND_SEVEN_DAYS)

| Governance item | Value |
|---|---|
| Duration semantics | minimum_floor_not_fixed_window |
| Collection can continue beyond seven days | true |
| Freeze has expiration | false |
| Rejected intervals count against coverage | true |
| Coverage can recover naturally | true |
| DATA_INVALID is recoverable | true |
| Decision | CONTINUE_BEYOND_SEVEN_DAYS |

Rationale: The seven-day window is a minimum eligibility floor, not a hard expiry. The research freeze (`freeze-aaa4dd57057a`) has no expiration, so collection may continue until the observation count, coverage fraction, and duration requirements are all satisfied simultaneously. DATA_INVALID reflects current insufficiency, not permanent rejection; ledger/ingestion/provenance are HEALTHY and coverage can recover naturally through continued HH:08 / HH:12 UTC collection.

---

## Phase D Reliability Findings

| Item | Value |
|---|---|
| Adapter max duration | 60000 ms |
| Adapter max retries | 3 |
| Adapter total attempts | 4 |
| Adapter backoff | exponential multiplier=1 min=1s max=8s |
| Adapter stop condition | stop_after_attempt | stop_after_delay |
| RateLimitExceeded retried | true |
| Collection cadence (primary) | HH:08 UTC |
| Collection cadence (retry) | HH:12 UTC |
| Monitoring script | scripts/monitor_prospective_continuity.py |

The Q18 adapter reliability hardening is preserved: bounded retries for transient Binance failures (NetworkError, ExchangeNotAvailable, RequestTimeout, DDoSProtection, RateLimitExceeded/HTTP 429), exponential backoff 1s-8s, stop after attempt or delay (whichever first), no exchangeInfo code path, permanent failures (BadSymbol, NotSupported) not retried. Collection cadence targets the 300s finalization margin after candle close (:05) with the :08 primary and :12 retry slots well inside the 900s ingestion-latency deadline (:15).

---

## Phase E Independent Improvements

| Improvement | Detail |
|---|---|
| Prospective collection health check | Added to `crypto_engine/observability/health.py` |
| Health check read-only | true |

The Q19 health check provides read-only observability of prospective collection state (accepted observations, expected intervals, coverage, evaluation_blocked, subsystem health) without mutating collection, storage, or scheduling state.

---

## Phase F Test Results

| Metric | Value |
|---|---|
| Total passed | 1408 |
| Total failed | 0 |
| Total skipped | 3 |
| Q19 tests passed | 12 |
| All tests pass | true |

Test counts reflect the full suite run at Q19 assessment. Skips are pre-existing environmental skips, not failures. Zero credential values appear in test output or reports.

---

## Readiness Flags Table

| Flag | Value |
|---|---|
| CURRENT_PROSPECTIVE_STATE_VERIFIED | true |
| COVERAGE_FORMULA_VERIFIED | true |
| COUNT_FEASIBILITY_CALCULATED | true |
| COVERAGE_FEASIBILITY_CALCULATED | true |
| ORIGINAL_DEADLINE_FEASIBILITY | false |
| EXTENDED_COLLECTION_GOVERNANCE_VERIFIED | true |
| COLLECTION_CONTINUITY_VERIFIED | true |
| ELIGIBILITY_DATE_PROJECTED | true |
| Q5_2_PROSPECTIVE_VALIDATED | false |
| Q4_GOVERNANCE_PRESERVED | true |
| SECURITY_REGRESSION_PASSED | true |
| PRODUCTION_READY | false |
| LIVE_TRADING_AUTHORIZED | false |

---

## Warnings and Blockers

1. **Original 7-day deadline cannot satisfy obs>=168 (max 110) or coverage>=95% (max 65.87%).** Duration is the only requirement met by 2026-11-20T06:15:46Z.
2. **Extended collection requires ~45 more days of near-perfect collection** (~1082 hourly accepts) before Q5.2 eligibility.
3. **Each missed interval adds 20 hours to required time** (formula: T >= 1082 + 20*M).
4. **Earliest eligibility: 2026-12-30T19:58:45Z** assuming zero missed intervals from assessment time.
5. **META_MUSE_API_KEY provider-side rotation pending** — no provider console access; rotation must be performed externally via the Meta provider console.

Blockers: Q5.2_PROSPECTIVE_VALIDATED = false; PRODUCTION_READY = false; LIVE_TRADING_AUTHORIZED = false.

---

## Recommended Q20 Milestone

**Q20: Prospective Validation Eligibility Achievement**

Objective: Reach Q5.2 prospective validation eligibility (>= 168 observations, >= 95% coverage, >= 7 days elapsed duration) through continued read-only collection.

Plan:
1. Continue collection at HH:08 UTC primary / HH:12 UTC retry for each hourly BTC/USDT candle.
2. Run `scripts/monitor_prospective_continuity.py` after each window to confirm accepted_count, coverage_fraction, evaluation_blocked, and ledger/ingestion/provenance health.
3. Verify ledger chain integrity (0 duplicates, 0 conflicts, HEALTHY) before each collection cycle.
4. Project elapsed days; earliest eligibility 2026-12-30T19:58:45Z at zero misses; each miss adds ~20 hours.
5. Complete META_MUSE_API_KEY provider-side rotation via the Meta provider console (external, no console access in this environment).
6. When all three thresholds are satisfied simultaneously, mark Q5_2_PROSPECTIVE_VALIDATED = true and issue the Q5.2 prospective validation report. No model changes, retraining, tuning, backtesting, paper trading, or live trading in Q20.

Authorization & safety warnings:
- No live orders; no real-money trading; no production use.
- Frozen model; no retraining, refitting, calibration, or tuning.
- Public read-only market data only; no private endpoints, no write operations, no scheduler activation without explicit authorization.
- No Windows scheduled tasks; dry-run/manual invocation only.

---

*End of Q19 report.*
