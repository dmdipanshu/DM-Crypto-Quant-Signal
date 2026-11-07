# ADR-0015 — Parquet, SQLite WAL and JSON as separate storage roles

- **Status:** Proposed (architecture freeze, design only)
- **Date:** 2026-11-07
- **Deciders:** Operator, integration lead, market-data engineer
- **Supersedes:** ADR-0005 (in part — SQLite becomes the canonical
  transactional store; JSON is confined to metadata and manifests)
- **Related:** ADR-0004 (immutable Parquet snapshots), ADR-0011 (4 GB
  envelope)
- **Specified in:** `docs/architecture/STORAGE_OWNERSHIP.md`

## Context

ADR-0005 treated SQLite as an optional convenience and kept JSON as the
default for small state. That is safe but leaves the target without a clear
canonical home for transactional state, and it invites three failure modes:

1. candles or other bulk market data migrating into SQLite because it is
   convenient;
2. transactional state accumulating in JSON files that must be fully read and
   rewritten;
3. the same state being written to two places with no single source of truth.

The operator has now fixed three distinct storage roles and requires storage
ownership to remain explicit.

## Decision

Freeze three roles, one per kind of state:

| Role | Technology | Canonical for |
|---|---|---|
| Historical market data | Parquet + JSON manifest | OHLCV, funding, derived research datasets, immutable snapshots |
| Transactional application state | SQLite in WAL mode | portfolio, positions, paper trades, ledger, scheduler jobs/events, operational state |
| Metadata and manifests | JSON | dataset manifests, provenance, SHA256, validation/research/model/verification metadata, configuration metadata |

Rules:

- Market-data identity stays `exchange + symbol + timeframe + open_time_utc`.
- Parquet is never used for mutable counters; SQLite is never used for bulk
  candles; JSON is never used for large datasets or per-row state.
- Each path has exactly one writer (`DEPENDENCY_RULES.md` DR-11).
- SQLite connections set `journal_mode=WAL`, `synchronous=NORMAL`,
  `busy_timeout`, `foreign_keys=ON`, and use short transactions.
- The SQLite file lives on a local filesystem; WAL is not safe on a network
  share or inside a synchronising folder. If that cannot be guaranteed, the
  JSON fallback is used with the limitation recorded.
- No dual-write: a migration writes the new store, verifies it, records the
  cutover, and keeps a JSON export for inspection.
- Published snapshots remain immutable; replacement creates a new snapshot id
  with a recorded `replaces_snapshot_id`.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| All JSON | Full-file reads, no transactions, scan cost grows with state |
| All SQLite (including candles) | Poor columnar scans, bloat, migration burden, competes with the 4 GB budget |
| All Parquet | No transactions, no filtered queries, no mutable state |
| PostgreSQL | Server process, credentials, operational burden; violates the envelope |
| DuckDB | Extra dependency and a second query engine for a single-user tool |
| Keep ADR-0005 as-is | Leaves transactional ownership ambiguous and invites dual sources of truth |
| Dual-write JSON + SQLite | Two authorities, silent divergence, impossible to audit |

## Trade-offs

Accepted:

- Two state technologies plus metadata files.
- WAL sidecar files (`-wal`, `-shm`) must be handled by backup and ignore rules.
- Schema migrations become necessary once SQLite is introduced.
- A local-disk constraint on the database location.

Avoided:

- Bulk data in a transactional database.
- Mutable state in immutable-file formats.
- Unbounded JSON growth.
- Silent divergence between two stores.
- A server database on a laptop.

## Consequences

- Storage ownership is auditable per path.
- Candles remain citable, copyable, checksummed artifacts.
- Transactional state gains atomic multi-row updates and filtered reads.
- JSON remains the human-inspectable format for provenance and metadata.
- `sqlite3` is standard library, so no dependency is added.
- No database, schema or migration is created by this ADR; implementation
  requires a separate authorization.

## Revisit conditions

- WAL proves unreliable on the target filesystem (sync client, network share).
- A query pattern needs analytics that SQLite cannot serve within the budget.
- JSON metadata grows into a dataset and must be split.
- A future deployment requires a different transactional backend; the port
  must absorb it without changing application code.
