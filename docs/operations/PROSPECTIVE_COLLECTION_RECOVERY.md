# Q5.2 Prospective Collection Recovery

**Phase:** Q18 — Prospective Collection Recovery  
**Collection ID:** collect-52cefa0dda64  
**Scope:** Read-only recovery procedures; no trading, no scheduler activation without authorization  
**Credential values:** None printed or stored in this document

## Purpose and Scope

This document describes the recovery procedure for the Q5.2 prospective collection
(collect-52cefa0dda64) after monitor output indicated collection is blocked.

**In scope:**
- Read-only status checks via `scripts/monitor_prospective_collection.py`
- Ledger chain integrity verification via `storage.verify_ledger_chain()`
- Binance exchange connectivity checks via `CcxtAdapter.fetch_range` with retry policy
- Manual collection within the valid window (HH:08 UTC primary, HH:12 UTC retry)
- Verification that a new observation is accepted
- Re-running the monitor to confirm progress

**Not in scope:**
- No scheduler activation (do not enable or restart the collection scheduler without explicit authorization)
- No backfill of historical candles outside the prospective window
- No fabricated or synthetic observations
- No live trading, real-money orders, or paper trading
- No model retraining, refitting, calibration, tuning, or threshold selection
- No credential rotation or secret handling

## Current State Summary

Values below are from the last `monitor_prospective_collection.py` run.

| Field | Value |
|---|---|
| Collection ID | collect-52cefa0dda64 |
| Collection status | DATA_INVALID |
| Observation count | 1 (minimum required: 168) |
| Expected intervals | (per quality report) |
| Coverage fraction | 1.72% (minimum required: 95%) |
| Collection started at UTC | (from manifest) |
| Seven-day deadline UTC | (collection_started_at + 7 days) |
| Remaining duration | 4.53 days |
| Evaluation blocked | true |
| Duplicates | 0 |
| Conflicts | 0 |

### What is Healthy

- `ledger_integrity` = HEALTHY — the append-only ledger chain is intact
- `ingestion_status` = HEALTHY — ingestion pipeline is functioning
- `provenance_status` = HEALTHY — first-seen provenance is preserved
- `duplicates` = 0 — no duplicate timestamps detected
- `conflicts` = 0 — no conflicting candle records

The blocker is insufficient prospective observations (1 of 168 required) and low
coverage (1.72% of 95% required), which keeps `evaluation_blocked=true`. The
ledger, ingestion, and provenance subsystems are not the cause of the block.

## Recovery Procedure

Run these steps in order. Stop and escalate if any step fails persistently.

### Step 1 — Check current status

Run the read-only monitor script to confirm the latest state:

```powershell
uv run python scripts/monitor_prospective_collection.py
```

Confirm `collection_status`, `observation_count`, `coverage_fraction`,
`evaluation_blocked`, and `remaining_duration_days`. Record the output for the
recovery record. Exit code 0 means the report files were read successfully; it
does not mean collection is eligible.

### Step 2 — Verify ledger chain integrity

From a Python REPL or one-off script, run:

```python
from crypto_engine.research.prospective.storage import ProspectiveStorage

storage = ProspectiveStorage(<storage_root> / "binance" / "spot" / <symbol> / <timeframe>)
result = storage.verify_ledger_chain()
assert result["status"] == "HEALTHY", result
```

The ledger chain must verify as HEALTHY before collection proceeds. If the
chain is broken, do not collect; escalate to the integration lead.

### Step 3 — Check Binance exchange connectivity

Verify the adapter can reach Binance public Spot endpoints:

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

- Success: `result.candles` is non-empty or a clean empty window is returned.
- Transient failure (`ccxt.NetworkError`, `ExchangeNotAvailable`, `RequestTimeout`,
  `DDoSProtection`, `RateLimitExceeded`): retried per the policy below; if
  exhausted, `DataFetchError` is raised.
- Permanent failure (`BadSymbol`, `NotSupported`): mapped to
  `UnsupportedEndpointError` and not retried.

Only `fetch_ohlcv` failures go through retry. There is no `exchangeInfo` code
path in this adapter.

### Step 4 — Collect within the valid window

Collect manually during the valid hourly window:

- Primary collection time: `HH:08 UTC`
- Retry collection time: `HH:12 UTC`

Run `ProspectiveCollector.collect_once()` (or the equivalent collection entry
point) once per window. Do not run the scheduler; invoke collection manually
and observe the result. Each accepted candle must:

- Open strictly after the collection epoch start (`collection_started_at_utc`)
- Be fully finalized (close time + 300s margin has passed)
- Have ingestion latency under 900s

### Step 5 — Verify new observation accepted

After collection, confirm:

- At least one new observation was stored (`observation_count` increased)
- `latency < 900s` for the new candle
- Candle finalized with `> 300s` margin after close
- `duplicates == 0` and `conflicts == 0` still hold

### Step 6 — Re-run monitor to confirm progress

```powershell
uv run python scripts/monitor_prospective_collection.py
```

Confirm `observation_count` increased and `coverage_fraction` improved. If
`evaluation_blocked` remains true, repeat Steps 3–5 in subsequent valid
windows until the 7-day deadline or until blocked persistently.

## Retry Policy Summary (Phase D / Q18)

Applied in `CcxtAdapter._fetch_page` (`src/crypto_engine/data/adapter.py`):

| Setting | Value |
|---|---|
| `max_retries` | 3 (4 total attempts) |
| `max_duration_ms` | 60000 (60s total retry duration boundary via `stop_after_delay`) |
| Backoff | `wait_exponential(multiplier=1, min=1, max=8)` → 1s, 2s, 4s, 8s capped |
| Stop condition | `stop_after_attempt | stop_after_delay` — whichever comes first |
| Retried exceptions | `ccxt.NetworkError`, `ccxt.ExchangeNotAvailable`, `ccxt.RequestTimeout`, `ccxt.DDoSProtection`, `ccxt.RateLimitExceeded` (HTTP 429) |
| Not retried | `BadSymbol`, `NotSupported` → `UnsupportedEndpointError`; all other → `DataFetchError` |
| Rate limiting | ccxt internal rate limiter (`enableRateLimit=True`); no Retry-After header parsing |

## What is NOT in Scope

- No scheduler activation
- No backfill
- No fabricated observations
- No live trading
- No retraining

## Escalation

- If Binance `exchangeInfo` or public connectivity fails persistently: check the
  Binance exchange status page before retrying; confirm no ongoing exchange
  incident.
- If latency rejections persist (`ingestion latency > 900s`): investigate
  network latency and local clock drift; confirm the host clock is synchronized
  to UTC.
- If `evaluation_blocked` remains true after exhausting valid windows or the
  7-day deadline approaches: escalate to the quant-lead / integration lead.
  Do not force collection, backfill, or fabricate observations to unblock.

## References

- Monitor script: `scripts/monitor_prospective_collection.py`
- Adapter: `src/crypto_engine/data/adapter.py` — `CcxtAdapter`
- Collector: `src/crypto_engine/research/prospective/collector.py` — `ProspectiveCollector`
- Storage/ledger: `crypto_engine.research.prospective.storage.ProspectiveStorage`
- Q18 failure handling: `docs/operations/BINANCE_TRANSIENT_FAILURE_HANDLING.md`
