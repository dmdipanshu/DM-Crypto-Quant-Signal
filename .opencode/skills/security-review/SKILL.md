---
name: security-review
description: Review secrets, dependencies, untrusted inputs, and execution boundaries
---

# Security Review

## Procedure

1. Check secret redaction and environment handling (no secrets in TOML/code/git).
2. Review filesystem scope (writes bounded to repo), dependency licenses and risk,
   external-response validation, provider trust, unsafe defaults.
3. Trace accidental live-execution paths; confirm no live orders exist.
4. Prefer read-only evidence gathering; report severity, impact, file references,
   and concrete remediation without hiding unresolved risk.

## Validation standards

- Each check has a verdict (pass / fail / unverified) with evidence path.
- Deterministic risk gates confirmed independent of models/AI.

## Failure handling

- Cannot verify → mark unverified with severity and reason, never as safe.

## Completion requirements

Handoff states: severity-ranked findings with impact + remediation, unresolved
risks carried forward, next gate.
