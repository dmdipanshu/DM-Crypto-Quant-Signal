---
name: multi-provider-ai
description: Implement optional AI providers with strict schemas and safe fallback
---

# Multi-Provider AI

## Procedure

1. Implement the common provider interface: model identity, timeout, retry,
   rate-limit, budget, fallback, usage reporting.
2. Load secrets from the environment only; never TOML/code/git.
3. Validate closed structured outputs against strict schemas.
4. Downgrade safely on failure: fallback provider or abstention with logged reason.
5. Enforce hard limits: AI cannot invent market facts, set risk limits, override
   hard gates, or create orders.

## Validation standards

- Timeout/retry/budget/fallback behavior tested with deterministic fakes.
- Schema violations and provider outages produce safe degradation, verified by tests.

## Failure handling

- Error, timeout, schema violation, budget breach → fallback or abstention.
  Never fabricate a substitute answer or silent success.

## Completion requirements

Handoff states: interface, providers wired, fallback/usage tests run, trust-boundary
notes, unresolved risks. (Planned Phase 4; not implemented in Phase 1.)
