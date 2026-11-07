# ADR-0003 — Domain boundaries and a stdlib-only domain

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Operator, integration lead, risk reviewer
- **Supersedes:** none

## Context

The current modules mix three things: data structures, business rules, and
computation over pandas frames. That makes rules hard to test in isolation and
makes it easy for a numeric library or a framework to become part of a rule.

The requirements state that domain rules must not depend on FastAPI, SQLite,
pandas, ccxt, exchange APIs or dashboard code.

## Decision

Define a `domain/` package that uses the Python standard library only:
`dataclasses`, `enum`, `datetime`, `decimal`, `typing`, `collections`.

Domain owns the vocabulary and invariants:

- instruments, trading pairs, timeframes, candles;
- quality verdicts and reason codes;
- regime and strategy metadata;
- signal proposals/records and score breakdowns;
- risk limits and decisions;
- position sizing and ledger invariants;
- research run and validation result values.

Computation over dataframes lives in `analytics/`, `signals/`, `risk/`,
`portfolio/`, `backtesting/` and `research/` — the domain-services ring. Those
may use numeric libraries but depend on `domain/`, never the reverse.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Pydantic models as the domain | Couples rules to a validation framework and its version |
| pandas objects as domain types | Makes every rule require pandas; slow and brittle in tests |
| One shared `models.py` for everything | Recreates the current tangling |
| No domain layer, rules inside services | Rules become untestable and duplicated |

## Trade-offs

Accepted:

- Mapping code between domain values and dataframes.
- Some duplication between a domain value and its serialized form.

Avoided:

- Framework upgrades breaking rules.
- Rules that cannot run without a heavy dependency stack.

## Consequences

- Domain tests are fast and dependency-free.
- `analytics/` and friends must accept and return domain values at their
  boundaries; internal frames are allowed.
- A serialization layer is required at the edges.
- Import tests enforce the stdlib-only rule.

## Revisit conditions

- A rule genuinely needs vectorized execution to be practical (then move the
  computation, not the rule).
- The mapping layer becomes a source of bugs larger than the coupling it
  prevents.
