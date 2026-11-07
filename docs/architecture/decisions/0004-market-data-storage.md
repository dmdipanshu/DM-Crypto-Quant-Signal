# ADR-0004 — Immutable Parquet snapshot storage

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, market-data engineer
- **Supersedes:** none

## Context

Research results are only meaningful if the exact input bytes can be
identified and re-read. The current system already writes Parquet snapshots
with a JSON manifest and a SHA256 checksum, and the two surviving snapshots
verify cleanly.

The system must never silently overwrite a research dataset, and it must
remain readable on a machine with limited RAM.

## Decision

Keep **immutable Parquet snapshots** as the canonical historical dataset:

```text
data/processed/<exchange>/<symbol>/<timeframe>/<snapshot_id>/
  candles.parquet
  manifest.json
```

- `snapshot_id` is deterministic from provenance plus content checksum.
- `manifest.json` records the checksum, row count, quality verdict and
  provenance.
- Writes are atomic: temp directory then rename.
- An existing directory is re-verified, never overwritten.
- Loading recomputes the checksum and refuses on mismatch.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| CSV | Larger, slower, weaker typing, easier to corrupt silently |
| SQLite as the candle store | Good for metadata, poor for large columnar scans and portability of a dataset |
| HDF5 | Extra dependency, weaker Windows story, no clear benefit here |
| Cloud object store | No network dependency wanted for research; adds credentials |
| Single monolithic Parquet per symbol | Makes per-range versioning and immutability awkward |

## Trade-offs

Accepted:

- Directory proliferation over time.
- A manifest file must stay in sync with the Parquet file (guarded by checksum).
- No partial-range update in place.

Avoided:

- Silent mutation of research inputs.
- Database migration burden for historical data.
- Vendor lock-in.

## Consequences

- Dataset identity is content-addressed enough to be cited in reports.
- Retention is manual and auditable.
- A SQLite index may later accelerate discovery, but it is a cache, not the
  source of truth.
- Schema changes require a new schema version and a new snapshot lineage.

## Revisit conditions

- A single symbol/timeframe exceeds practical file sizes for the hardware.
- Row-group reads become the bottleneck.
- A legitimate need arises for in-place correction of vendor errors (which
  must still produce a new snapshot, not a mutation).
