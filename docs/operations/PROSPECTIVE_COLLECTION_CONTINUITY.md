# Prospective Collection Continuity Procedures

**Phase:** Q19 — Prospective Validation Feasibility & Collection Optimization  
**Collection ID:** collect-52cefa0dda64  
**Freeze ID:** freeze-aaa4dd57057a  
**Decision:** CONTINUE_BEYOND_SEVEN_DAYS  
**Scope:** Read-only continuity procedures; no scheduler activation without authorization; no trading  
**Credential values:** None printed or stored in this document

---

## Purpose

Continue the Q5.2 prospective collection (`collect-52cefa0dda64`, BTC/USDT 1h, Binance public spot) beyond the original seven-day deadline until Q5.2 eligibility is met: >= 168 accepted observations, >= 95% coverage fraction, >= 7 days elapsed duration. Duration is a minimum floor, not a fixed window; the freeze has no expiration; DATA_INVALID is recoverable.

Earliest eligibility projection: 2026-12-30T19:58:45Z (~1082 hours of near-perfect collection from assessment, zero misses). Each missed interval adds ~20 hours (T >= 1082 + 20*M).

---

## Collection Cadence

| Role | Time (UTC) | Rationale |
|---|---|---|
| Primary | HH:08 every hour | 3 min after candle finalization (:05 post-close), 7 min before the 900s ingestion-latency deadline (:15) |
| Retry | HH:12 every hour | Fallback if primary collection fails or is rejected |

Each collection targets the exchange-aligned 1h candle that closed at HH:00. The candle is finalized at :05 (300s margin). Collection must complete with ingestion latency < 900s (before :15) for the observation to be accepted.

Collection rules:
- Accepted candle open time must be strictly after `collection_started_at_utc` (2026-11-13T06:15:46.915986+00:00).
- Candle close time must be at or before now (fully finalized).
- Ingestion latency must be under 900s.
- Duplicates: 0 required; conflicts: 0 required; ledger/ingestion/provenance: HEALTHY.

Do not collect candles outside the prospective window, do not backfill, do not fabricate observations.

---

## Monitoring Commands

### Check current collection status

```powershell
uv run python scripts/monitor_prospective_continuity.py
```

Confirm: `collection_status`, `accepted_observations` / `observation_count`, `expected_intervals`, `coverage_fraction`, `evaluation_blocked`, `ledger_integrity`, `ingestion_status`, `provenance_status`, `duplicates_count`, `conflicts_count`, `rejection_count`. Exit code 0 means the report files were read; it does not mean collection is eligible.

### Verify ledger chain integrity

From a Python REPL or one-off script:

```python
from crypto_engine.research.prospective.storage import ProspectiveStorage

storage = ProspectiveStorage(<storage_root> / "binance" / "spot" / <symbol> / <timeframe>)
result = storage.verify_ledger_chain()
assert result["status"] == "HEALTHY", result
```

The ledger chain must verify HEALTHY before collection proceeds. 0 duplicates, 0 conflicts required.

### Check Binance public connectivity

```python
from crypto_engine.data.adapter import CcxtAdapter
from crypto_engine.data.types import FetchParams
from datetime import UTC, datetime

adapter = CcxtAdapter(exchange_name="binance", max_retries=3, max_duration_ms=60000)
params = FetchParams(
    exchange="binance",
    symbol="BTC/USDT",
    timeframe="1h",
    since=datetime.now(UTC),
    until=datetime.now(UTC),
    limit=1,
)
result = adapter.fetch_range(params)
```

- Success: `result.candles` non-empty or a clean empty window.
- Transient failure (NetworkError, ExchangeNotAvailable, RequestTimeout, DDoSProtection, RateLimitExceeded/HTTP 429): retried per policy; if exhausted, `DataFetchError` raised.
- Permanent failure (BadSymbol, NotSupported): `UnsupportedEndpointError`, not retried.
- Only `fetch_ohlcv` failures are retried; there is no `exchangeInfo` code path.

### Run a single collection

Invoke `ProspectiveCollector.collect_once()` (or the equivalent collection entry point) once per valid window. Do not run the scheduler; invoke manually and observe the result.

### After each collection, confirm

- `observation_count` increased (at least one new accepted observation)
- New candle latency < 900s
- Candle finalized with > 300s margin after close
- `duplicates == 0` and `conflicts == 0`
- `ledger_integrity`, `ingestion_status`, `provenance_status` remain HEALTHY

### Re-run monitor

```powershell
uv run python scripts/monitor_prospective_continuity.py
```

Confirm `observation_count` increased and `coverage_fraction` improved. If `evaluation_blocked` remains true, repeat collection in subsequent valid windows.

---

## Recovery Procedures

### R1 — Missed primary window (HH:08)

Use the HH:12 retry window for the same candle. As long as collection completes before ingestion latency exceeds 900s and the candle is finalized, the observation can still be accepted. A fully missed hour counts as a missed interval (M += 1) and adds ~20 hours to required time (T >= 1082 + 20*M).

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
2. Confirm host clock is synchronized to UTC (clock drift causes latency rejections).
3. Retry at the next valid window.
4. If failure persists across multiple windows, escalate (see Escalation Criteria).

### R4 — Ingestion latency > 900s (rejections)

1. Confirm host clock synchronized to UTC.
2. Collect earlier in the window (closer to :08 primary) to reduce latency.
3. Investigate network latency between host and Binance public endpoints.
4. Rejections count against coverage; minimize them to avoid extending eligibility.

### R5 — Subsystem unhealthy (ledger / ingestion / provenance)

1. Stop collection immediately.
2. Verify ledger chain: `storage.verify_ledger_chain()` must be HEALTHY with 0 duplicates, 0 conflicts.
3. Do not collect until all subsystems return HEALTHY.
4. If the chain is broken or conflicts appear, escalate to the integration lead; do not attempt repair without authorization.

### R6 — evaluation_blocked remains true

Continue hourly collection at HH:08 / HH:12. Re-check after each window. Eligibility requires all three thresholds simultaneously:
- observations >= 168
- coverage >= 95%
- elapsed duration >= 7 days

Earliest projection: 2026-12-30T19:58:45Z at zero misses. Track misses against T >= 1082 + 20*M.

---

## Escalation Criteria

Escalate to the quant-lead / integration lead when any of the following occurs:

- Binance public connectivity fails persistently across multiple collection windows (check exchange status page first).
- Ingestion latency rejections persist despite collecting within :05-:15 post-close and a synchronized UTC clock.
- Ledger chain is broken, duplicates > 0, or conflicts > 0 (do not collect; do not self-repair).
- `evaluation_blocked` remains true after exhausting a sustained run of valid windows, or the operational burden of ~45 days of near-perfect collection becomes unsustainable.
- META_MUSE_API_KEY provider-side rotation cannot be completed externally (no provider console access in this environment).

Do not force collection, backfill, fabricate observations, activate a scheduler, or enable Windows scheduled tasks to unblock evaluation.

---

## What Is NOT in Scope

- No scheduler activation or Windows scheduled tasks without explicit authorization.
- No backfill of historical candles outside the prospective window.
- No fabricated or synthetic observations.
- No live trading, real-money orders, or paper trading.
- No model retraining, refitting, calibration, tuning, threshold selection, or feature selection.
- No API/dashboard write endpoints or public exposure.
- No credential rotation or secret handling in this document (rotation performed externally via the Meta provider console).

---

## References

- Q19 report (machine-readable): `docs/validation/results/q19_prospective_feasibility.json`
- Q19 report (human-readable): `docs/validation/results/q19_prospective_feasibility.md`
- Operational feasibility: `docs/operations/PROSPECTIVE_VALIDATION_FEASIBILITY.md`
- Monitor script: `scripts/monitor_prospective_continuity.py`
- Adapter: `src/crypto_engine/data/adapter.py` (CcxtAdapter)
- Collector: `src/crypto_engine/research/prospective/collector.py` (ProspectiveCollector)
- Storage/ledger: `crypto_engine.research.prospective.storage.ProspectiveStorage`
- Health check: `crypto_engine/observability/health.py`
- Q18 recovery: `docs/operations/PROSPECTIVE_COLLECTION_RECOVERY.md`
- Q18 scheduling: `docs/operations/PROSPECTIVE_COLLECTION_SCHEDULING.md`

---

*End of collection continuity procedures.*
