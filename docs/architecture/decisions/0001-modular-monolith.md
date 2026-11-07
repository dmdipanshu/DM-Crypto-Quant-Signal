# ADR-0001 — Modular monolith over microservices

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator (final), integration lead
- **Supersedes:** none

## Context

SignalForge is developed by one person on a Windows machine with limited RAM.
It must ingest public market data, compute features, run strategies, evaluate
risk, backtest, validate and serve a read-only UI. There is no team to operate
a service fleet and no traffic that requires horizontal scaling.

The current codebase is already a single process and a single deployable. A
split would be a new cost, not a correction.

## Decision

Keep one deployable application with one process (plus one optional
foreground server process). Separate concerns by **modules and enforced import
rules**, not by network boundaries.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Microservices per subsystem | Multiplies processes, ports, deployment and failure modes; each service adds memory overhead the machine cannot spare |
| Serverless functions | Cold starts, vendor coupling, no natural fit for long CPU-bound backtests |
| Plugin architecture with dynamic loading | Adds discovery complexity and weakens static import checking |
| Single-file script | No boundaries, untestable, already outgrown |

## Trade-offs

Accepted:

- One bad module can affect the whole process (mitigated by import rules and
  error isolation).
- Scaling out later requires deliberate work (mitigated by ports).
- Independent deployment of a subsystem is not possible.

Avoided:

- Distributed transactions, service discovery, network retries, multiple
  deployment pipelines, and the memory cost of several runtimes.

## Consequences

- `DEPENDENCY_RULES.md` becomes load-bearing and must be tested.
- Optional modules must be genuinely optional at construction time.
- A future split remains possible at port boundaries without a rewrite.
- Deployment stays a single `uv sync` and one command.

## Revisit conditions

- Multiple operators need independent deployments.
- A workload genuinely needs separate hardware (e.g. GPU inference).
- Ingestion throughput exceeds what a single process can serve.
- The module graph can no longer be kept acyclic.

Any revisit requires a new ADR, not a silent drift.
