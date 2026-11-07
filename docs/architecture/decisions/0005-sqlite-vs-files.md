# ADR-0005 — SQLite for metadata and ledger, files for datasets

- **Status:** Superseded in part by ADR-0015 (SQLite becomes the canonical
  transactional store; JSON is confined to metadata and manifests). The
  reasoning below is retained as history.
- **Date:** 2026-11-07
- **Deciders:** Operator, integration lead
- **Supersedes:** none

## Context

Two very different kinds of state exist:

1. **Bulk columnar market data** — large, append-only, read in ranges, cited by
   checksum.
2. **Small transactional state** — instrument metadata, run records, signal
   index, paper ledger, scheduler state.

The current implementation uses JSON/JSONL files for category 2 and Parquet
for category 1. JSON is simple and portable but has no query language, no
transaction, and requires reading a whole file to answer a filtered question.

## Decision

Use a **hybrid**:

- **Parquet snapshots** remain the canonical dataset store (ADR-0004).
- **SQLite** is the optional backend for metadata, the signal index, the
  paper ledger and scheduler state.
- **JSON remains the default** for small state so the baseline has zero new
  dependencies and stays inspectable.
- The backend is selected in configuration and hidden behind a port.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| All JSON | No transactions, full-file reads, growing scan cost |
| All SQLite (including candles) | Poor fit for large columnar scans; bloats the database |
| PostgreSQL | Server process, credentials, operational burden; violates the envelope |
| DuckDB | Attractive for analytics but adds a large dependency and a second query engine |
| Embedded key-value store | Extra dependency, weaker ad hoc inspection |

## Trade-offs

Accepted:

- Two state technologies and two code paths behind one port.
- Optional backend means both must be tested.

Avoided:

- A server database on a laptop.
- Full-file scans for every filtered query.
- Losing human-readable state in the default configuration.

## Consequences

- `storage/` exposes one interface; the backend is invisible to application
  code.
- SQLite schema evolution needs migration notes before implementation; this
  ADR does not define DDL.
- Single-writer discipline is required; SQLite is used with short transactions
  and no long-held locks (OneDrive-safe).
- JSON files remain the fallback for recovery and inspection.

## Revisit conditions

- JSON scan cost becomes user-visible.
- A query pattern needs joins that files cannot serve.
- SQLite locking causes problems on the target filesystem.
- The optional backend is never used and should be removed.

## Supersession

ADR-0015 (2026-11-07) keeps this ADR's reasoning but changes two points: SQLite
in WAL mode becomes the **canonical** transactional store rather than an
optional convenience, and JSON is confined to metadata, manifests and
provenance records. Parquet remains the canonical historical store (ADR-0004).
