---
name: trading-risk-controls
description: Build deterministic, testable risk gates and abstention rules
---

# Trading Risk Controls

## Procedure

1. Validate independently of AI: data freshness, liquidity, spread, volatility,
   model readiness, expected net edge, exposure, drawdown, cooldown, circuit
   breakers, kill switches.
2. Return `NO_TRADE` on invalid or insufficient evidence — a valid, expected outcome.
3. Test every hard limit with deterministic fixtures, including boundary values.
4. Never allow model output to bypass a hard gate.

## Validation standards

- Each gate has an explicit threshold, a test proving it trips, and a logged reason.
- Gates evaluated in a fixed order; failures abstain, never degrade silently.

## Failure handling

- Invalid inputs or breached limits → `NO_TRADE` with reason. Missing gate
  coverage → report as a blocker, not a pass.

## Completion requirements

Handoff states: gate definitions + thresholds, trip tests run, abstention paths
verified, unresolved risk gaps. (Planned Phase 3/6; not implemented in Phase 1.)
