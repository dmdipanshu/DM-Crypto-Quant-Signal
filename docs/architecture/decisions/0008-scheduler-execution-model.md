# ADR-0008 — Single-process, in-process scheduler

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, integration lead
- **Supersedes:** none

## Context

Some jobs are periodic: refresh data, publish a signal, produce a health
report. The alternatives are an external scheduler, an always-on daemon, or an
in-process loop. The machine has limited RAM and no desire for background
services.

## Decision

Ship an **optional, in-process, single-process scheduler**:

- started explicitly by an operator command;
- runs one job at a time;
- jobs are ordinary application use cases invoked through a `JobRunner` port;
- state is persisted so a restart does not duplicate a run;
- no cron daemon, no OS service, no message broker;
- disabled by default and absent from the baseline dependency graph.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Windows Task Scheduler | External configuration, hard to version, not reproducible from the repo |
| Always-on daemon | Violates the resource and "no unnecessary background daemons" rule |
| Celery / Redis queue | New services, new failure modes, new memory |
| APScheduler | Reasonable, but adds a dependency for a small need |
| No scheduler | Leaves periodic work manual, which is acceptable but limiting |

## Trade-offs

Accepted:

- Jobs do not run when the process is not running.
- No distributed coordination.
- A long job blocks the next scheduled job (by design).

Avoided:

- A resident background process.
- Duplicate execution from overlapping timers.

## Consequences

- The scheduler can never bypass risk: it calls the same use cases.
- Missed runs are visible and recorded, not silently caught up in a burst.
- Scheduling expressions are configuration, validated at load.
- If the process dies, the next start reconciles state and reports missed runs.

## Revisit conditions

- Jobs must run while the machine is unattended for long periods.
- Multiple schedules must run concurrently.
- The in-process loop interferes with interactive CLI use.
