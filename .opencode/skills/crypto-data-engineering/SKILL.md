---
name: crypto-data-engineering
description: Build UTC-safe, provenance-preserving cryptocurrency data pipelines
---

# Crypto Data Engineering

## Procedure

1. Fetch via CCXT public endpoints with bounded pagination, timeouts, retries
   (tenacity), and rate limiting.
2. Normalize all timestamps to UTC; enforce monotonicity; reject duplicates and
   invalid candles.
3. Record provenance per fetch: exchange, symbol, timeframe, params, fetch time,
   row count, checksum.
4. Detect stale/missing observations; preserve raw availability boundaries.
5. Persist immutable parquet snapshots; never silently replace a research snapshot.

## Validation standards

- Schema checks (UTC, OHLCV ranges, monotonic, no duplicates) gate every write.
- Offline tests cover gaps, duplicates, stale data, invalid candles, pagination edges.
- Exchange-specific limitations documented (rate limits, history depth, quirks).

## Failure handling

- Timeout/rate-limit → bounded retry with backoff, then explicit error.
- Stale/missing → flag and exclude with reason. Never fabricate data.

## Completion requirements

Handoff states: adapters, provenance records, snapshot paths, boundary tests run
(exact commands + counts), limitations and unresolved data risks.
