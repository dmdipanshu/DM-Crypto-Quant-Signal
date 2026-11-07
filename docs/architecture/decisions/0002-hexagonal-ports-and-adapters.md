# ADR-0002 — Hexagonal ports and adapters

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, integration lead
- **Supersedes:** none

## Context

The current code imports `ccxt`, `pyarrow`, filesystem paths and JSON stores
directly from business modules. That makes offline testing harder, hides I/O
side effects, and lets an adapter shape leak into domain decisions.

The project needs to change storage backends (JSON metadata to a SQLite WAL
transactional store), add optional providers, and prove that research cannot
reach execution.

## Decision

Use Ports and Adapters:

- `ports/` declares `typing.Protocol` interfaces.
- `domain/` and `application/` depend only on those protocols.
- `market_data/`, `storage/`, `execution/`, `scheduler/`, `notifications/`
  and `observability/` implement them.
- `bootstrap.py` is the only composition root.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Direct imports with dependency injection only | Injection without interfaces still couples callers to concrete types |
| Abstract base classes | Heavier than needed; `Protocol` gives structural typing with no inheritance |
| Service locator | Hides dependencies and makes tests order-dependent |
| Framework DI container | Extra dependency and indirection for a single-process app |

## Trade-offs

Accepted:

- Slightly more files and indirection.
- Protocols must be kept honest by conformance tests.

Avoided:

- Vendor lock-in at the storage and exchange layer.
- Tests that need a network or a real database.
- Accidental writes from read paths.

## Consequences

- Application code is testable with in-memory fakes.
- Adapters can be swapped without touching use cases.
- Every port must have at least one fake and one real adapter, or an explicit
  "not implemented" marker.
- Architecture tests can enforce the direction mechanically.

## Revisit conditions

- A protocol becomes a god interface with unrelated methods.
- Structural typing causes runtime surprises that a nominal type would catch.
- The number of ports exceeds what one developer can maintain.
