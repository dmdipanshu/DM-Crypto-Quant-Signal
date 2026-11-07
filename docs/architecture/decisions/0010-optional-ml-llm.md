# ADR-0010 — Optional ML and LLM, never on the critical path

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, ML engineer, AI provider engineer
- **Supersedes:** none
- **Related:** ADR-0014 (universal AI gateway)

## Context

The system must produce baseline signals without ML, GPU, large downloads or
external AI. ML and LLM capabilities are useful experiments but are also the
easiest way to make the project non-reproducible, expensive and dependent on
third parties.

## Decision

Treat both as **optional adapters behind ports**:

- Baseline signal generation uses deterministic, explainable rules and, where a
  model is used, a lightweight local model trained on local data.
- No ML dependency is required for install, test or baseline signal generation.
- No LLM is required for any decision.
- If an LLM is enabled, it may only produce advisory text attached to
  diagnostics. It cannot alter direction, score, position size or risk.
- Provider SDKs are optional and lazily imported; a missing SDK produces an
  actionable error naming the install command.
- Every optional component is off by default and absent from the baseline
  import graph.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| LLM in the decision path | Non-deterministic, unverifiable, prompt-injectable |
| Mandatory ML dependency | Breaks the resource envelope and offline testability |
| No ML/LLM at all | Discards legitimate research value; the requirement allows optional |
| Vendored models | Large downloads, licensing and supply-chain risk |

## Trade-offs

Accepted:

- Optional code needs absence tests.
- Users who enable providers must install extras and manage keys.
- LLM value is limited to explanation, not prediction.

Avoided:

- Vendor lock-in.
- Secret handling in core code.
- Non-deterministic signals.
- A 4 GB machine trying to host a large model.

## Consequences

- `signals/` and `risk/` cannot import AI adapters (enforced).
- AI failures degrade to "context unavailable", never to a changed decision.
- Model artifacts are checksummed and trusted-local-only.
- Baseline tests run with every optional package absent.

## Revisit conditions

- A deterministic, locally runnable model demonstrably improves validated
  out-of-sample results and fits the envelope.
- A provider offers a verifiable, schema-constrained interface that can be
  treated as a data source rather than an authority.
