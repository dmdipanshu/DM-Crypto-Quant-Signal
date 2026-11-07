# ADR-0007 — Evidence-based strategy promotion governance

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator (final authority), quant researcher, independent reviewer
- **Supersedes:** none

## Context

A backtest is a historical simulation, not evidence of future profitability.
The project's own history includes a cycle in which no strategy demonstrated
sufficient out-of-sample evidence and `candidates_for_paper_testing` remained
empty. That is a **historical record, not a current filesystem artifact**; the
runs that produced it are not present in this checkout. Promotion must
therefore be a deliberate, auditable exception rather than an automatic
consequence of a good-looking metric.

## Decision

A strategy becomes production-eligible only through a manual review with ten
mandatory gates:

1. Reproducibility from dataset hash, parameters and code version.
2. Causality and leakage tests pass.
3. Positive holdout result net of stress costs.
4. Robustness across folds, regimes and parameters.
5. Break-even cost above realistic trading cost.
6. Sufficient independent sample size.
7. Stability over time.
8. Written hypothesis, limits and failure modes.
9. Independent read-only reproduction.
10. Explicit human authorization.

The pipeline never promotes. It may only produce a recommendation and a
report. Promotion is recorded with the authorizing identity and the evidence
hash.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Automatic promotion on metric threshold | Optimizes for a number, not for evidence; invites overfitting |
| Committee vote | No committee exists; adds ceremony without independence |
| Promote on validation-set performance | Validation is for tuning; holds no out-of-sample weight |
| No promotion at all | Too strict; prevents any future use of a genuinely validated strategy |

## Trade-offs

Accepted:

- Slow, manual, occasionally subjective review.
- Requires a second reviewer, which is a real cost for a solo developer
  (mitigated by requiring a reproducible, scripted review).

Avoided:

- Shipping overfit strategies.
- Metric gaming.
- Silent changes to a promoted strategy.

## Consequences

- Every promoted strategy carries an evidence bundle.
- Demotion is possible and recorded.
- `candidates_for_paper_testing = []` remains the correct state until a
  strategy clears all gates.
- Documentation of negative results is mandatory, not optional.

## Revisit conditions

- A validated strategy clears every gate and the review process proves
  workable.
- The review burden becomes disproportionate to the research volume.
- New evidence types (e.g. long live-paper history) justify an additional gate.
