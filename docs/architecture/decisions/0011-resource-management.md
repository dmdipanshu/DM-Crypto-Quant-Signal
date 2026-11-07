# ADR-0011 — Design to the 4 GB memory envelope

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, integration lead
- **Supersedes:** none
- **Related:** `RESOURCE_BUDGET.md` §2 and §11 (open item closed 2026-11-07)

## Context

`AGENTS.md` mandates a 4 GB laptop envelope. The architecture brief mentions
approximately 8 GB. The two figures conflict. Designing to the larger number
would produce software that fails on the smaller machine, while designing to
the smaller number works on both.

## Decision

Adopt the **stricter 4 GB envelope** as the design budget and treat 8 GB as
headroom. Enforce it through:

- one process and one job at a time;
- `n_jobs=1` everywhere;
- bounded pagination, row caps and chunked reads;
- no full-matrix copies;
- explicit errors on cap breach;
- optional modules not constructed when disabled.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Design to 8 GB | Fails the documented operating constraint |
| Design adaptively at runtime | Complexity, unpredictable behaviour, hard to test |
| Ignore memory and rely on swap | Unacceptable latency and disk churn |
| Require more RAM | Not a decision the software can make |

## Trade-offs

Accepted:

- Smaller default sample sizes.
- Longer runtimes because of single-threading.
- Some analyses must be run in slices.

Avoided:

- Out-of-memory crashes mid-run.
- Silent row dropping.
- A dependency on a beefier machine.

## Consequences

- Every bounded operation reports when it hits its cap.
- Tests use small fixtures by default.
- The 8 GB figure, if confirmed, permits raising caps in one documented step.
- Performance claims require measurement, not extrapolation.

## Resolution (2026-11-07)

The operator confirmed the interpretation: the host has roughly 8 GB of
physical RAM, and **4 GB is the hard budget for SignalForge's own processing**.
It is not an operating-system limit, and the remaining memory is headroom for
Windows, the browser, tooling and other workloads. The stricter value governs;
the open item is closed and no cap is raised.

## Revisit conditions

- The operator confirms 8 GB as the true floor and accepts the change.
- A workload cannot satisfy its purpose within the envelope.
- A memory leak is found; fix it rather than raising the cap.
