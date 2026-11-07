# ADR-0009 — Read-only, loopback-first API and dashboard

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, risk reviewer
- **Supersedes:** none

## Context

The API and dashboard exist to inspect recorded evidence. They have no
legitimate reason to mutate state, and a write surface would create a remote
attack path to a research system that runs on the operator's desktop.

The current implementation is already GET-only and loopback-only by default.

## Decision

Preserve and institutionalize:

- **GET-only** route table, asserted at build time; startup fails on a
  non-GET, non-docs route.
- **Loopback binding** (`127.0.0.1`) by default; non-loopback requires an
  explicit flag and a warning.
- **No authentication** in the local-only deployment; remote mode is refused
  until a compensating control is designed and approved.
- **No mutations** through HTTP: no training, fetching, backtesting, paper
  stepping, configuration changes or file access.
- **Bounded pagination** and bounded scans.
- **Sanitized errors** with a request id; no tracebacks, paths or env values.
- **Escaped HTML** in the dashboard; no raw model or AI HTML/JS.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Full CRUD API | Creates a remote mutation surface for no research benefit |
| Auth + public hosting | Out of scope; invites credential and exposure risk |
| Dashboard calling a separate service | Adds a process and a network hop for no gain |
| Disable the UI entirely | Loses a genuinely useful read-only view |

## Trade-offs

Accepted:

- Some operations remain CLI-only.
- No remote access convenience.
- Public hosting is impossible without a new decision.

Avoided:

- A remotely reachable mutation endpoint.
- Credential handling in a single-user local tool.
- Silent data exposure through verbose errors.

## Consequences

- The API surface is auditable by a route table test.
- The dashboard cannot become a back door to the database.
- Any future remote mode needs its own ADR and security review.
- Rate limiting is unnecessary locally but pagination caps are still required
  to bound memory.

## Revisit conditions

- The operator needs remote access from another device.
- A legitimate need for a write endpoint appears (e.g. a job trigger); it must
  be designed as an explicit authorization flow, not a generic write.
- A hosted multi-user product is proposed, which would require a full security
  architecture.
