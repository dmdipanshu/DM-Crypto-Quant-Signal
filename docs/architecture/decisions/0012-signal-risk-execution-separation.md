# ADR-0012 — Separate signal, risk approval and execution authorization

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, risk reviewer, integration lead
- **Supersedes:** none

## Context

The current implementation applies risk gates by mutating the `SignalRecord`
(`apply_gate_veto`). That is workable but conflates three distinct decisions:

1. What the evidence suggests (signal generation).
2. Whether the risk rules permit acting on it (risk approval).
3. Whether an execution may actually occur (authorization).

Conflating them makes it hard to answer "was this rejected because the signal
was weak or because the portfolio was at its limit?" and makes it easier for a
future execution path to treat a published signal as pre-approved.

## Decision

Produce three separate, linked artifacts:

| Artifact | Producer | Content |
|---|---|---|
| `SignalRecord` | `signals/` | Direction, score, diagnostics, dataset ref |
| `RiskDecision` | `risk/` | Approved/rejected, gate trace, limits, signal id |
| `ExecutionAuthorization` | `application/paper.py` | Operator confirmation, risk decision id, paper-only scope |

Rules:

- A published research signal is stage 1 only.
- A paper fill requires all three.
- No live execution artifact exists.
- `NO_TRADE` can originate at any stage and is always recorded with a reason
  code from a closed vocabulary.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Keep mutating the signal record | Loses the distinction between weak evidence and risk rejection |
| A single combined record | Simpler, but hides the decision boundary and invites implicit approval |
| Risk checked only at execution | Too late to explain a published signal; risk context must be visible |
| No execution authorization artifact | The confirmation flag becomes invisible in the audit trail |

## Trade-offs

Accepted:

- More records and more links to maintain.
- Existing consumers of a single signal record need an adapter or a joined
  view.

Avoided:

- A signal being treated as an order intent.
- Ambiguous audit trails.
- Risk logic embedded in the signal policy.

## Consequences

- The audit trail shows three timestamps and three actors (system, risk,
  operator).
- Risk can be re-evaluated without regenerating the signal.
- The paper path is explicit and testable; the live path does not exist.
- Migration must preserve the current signal schema for existing readers.

## Revisit conditions

- The three artifacts prove redundant in practice and a joined view suffices.
- A future execution venue requires additional authorization metadata.
