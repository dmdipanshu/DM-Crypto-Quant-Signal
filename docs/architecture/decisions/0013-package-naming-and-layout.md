# ADR-0013 — Keep the `crypto_engine` package name and organize layer-first

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, integration lead
- **Supersedes:** none

## Context

The import package is `crypto_engine`; the CLI entry point is `sf`. The
project is called SignalForge. Two layout choices must be made:

1. Rename the package to `signalforge` or keep `crypto_engine`.
2. Organize top-level packages by **layer** or by **functional subsystem**.

The current layout is subsystem-first and flat: `data/`, `features/`,
`models/`, `signals/`, `risk/`, `backtesting/`, `paper_trading/`, `api/`,
`dashboard/`.

## Decision

**Keep `crypto_engine`** as the import package and **organize layer-first**:

```text
domain/  ports/  application/  analytics/  strategies/  signals/  risk/
portfolio/  backtesting/  research/  market_data/  storage/  execution/
scheduler/  notifications/  observability/  config/  cli/  api/  dashboard/
```

Functional packages remain where the user's mental model expects them
(`signals/`, `risk/`, `strategies/`, `research/`, `backtesting/`), but they sit
in the domain-services ring with explicit import rules rather than at the top
level as peers of presentation and infrastructure.

The CLI binary stays `sf`.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Rename to `signalforge` | Churn across imports, packaging, docs, tests and the lockfile for zero behavioural gain |
| Keep the flat subsystem layout | Cannot express layer direction; `api/` and `data/` become peers, which invites wrong-way imports |
| Fully nested by layer with no functional names (`domain/services/a/b`) | Deep paths, poor discoverability, fights the domain vocabulary |
| One package per layer with everything inside | Too coarse; loses the subsystem boundaries that make ownership possible |

## Trade-offs

Accepted:

- Some modules move, so the diff is larger than a rename-free refactor.
- Two organizing axes (layer for the top, function inside the service ring)
  must be explained once and then followed.
- The project name and the import name differ, which is occasionally
  confusing.

Avoided:

- A breaking rename with no user-visible benefit.
- A layout in which infrastructure and presentation are siblings of domain.
- Deeply nested paths that slow navigation.

## Consequences

- `pyproject.toml` keeps `packages = ["src/crypto_engine"]` and the `sf`
  entry point.
- Import rules are expressible as prefix rules (`crypto_engine.domain.*`).
- New modules get an obvious home: choose the layer first, then the subsystem.
- Documentation must state the name/layout decision so it is not re-litigated.

## Revisit conditions

- The project is published as a library under the SignalForge name.
- A second consumer requires a stable public import surface different from the
  internal layout.
- The layer-first split proves to add navigation cost without preventing
  violations.
