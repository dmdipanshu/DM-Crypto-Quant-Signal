# ADR-0006 — Separate research and production signal paths

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, quant researcher, risk reviewer
- **Supersedes:** none

## Context

A backtest and a live signal share feature code but not consequences. Mixing
them creates two dangers:

1. Research code gains a path to execution.
2. Production behaviour silently changes when a research experiment is edited.

The current code already separates `backtesting/` from `signals/`, but the
boundary is conventional, not structural.

## Decision

Maintain two explicit paths:

- **Production signal path**: verified recent data → features → regime →
  strategies → scoring → diagnostics → signal record → risk decision →
  publication.
- **Isolated research path**: immutable dataset → splits → walk-forward →
  cost stress → robustness → single holdout → report.

Rules:

- Research may import domain and data contracts.
- Research may **not** import execution, the paper ledger, scheduler mutation
  paths, or credential loading.
- Research output is a report; it cannot write production state.
- A shared strategy is allowed only if it is pure and versioned.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| One code path with a "mode" flag | A flag is one bug away from executing research as production |
| Separate repositories | Duplication and drift; too heavy for one developer |
| Separate processes only | Process isolation does not prevent import coupling |
| Notebook-first research | No reproducibility guarantee, no tests |

## Trade-offs

Accepted:

- Some orchestration duplication between the two paths.
- Strategy modules must be written to be pure and reusable.

Avoided:

- Accidental order submission from a research script.
- Research experiments changing production behaviour.

## Consequences

- Import tests enforce the boundary.
- Reports must record full provenance so the research path is reproducible.
- A strategy needs an explicit version to cross from research to production.
- A promotion review is the only bridge (ADR-0007).

## Revisit conditions

- The two paths diverge so much that sharing a strategy is impractical.
- A regulatory or operational need requires physically separate deployments.
