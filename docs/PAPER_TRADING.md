# PAPER_TRADING.md — SignalForge (Phase 6, planned)

> Planned, not implemented in Phase 1. No paper-trading code exists yet.

## Intent (advisory-only)

A future simulated execution loop that consumes canonical signals + deterministic
risk gates and records hypothetical fills. It never places live orders and never
overrides `NO_TRADE`.

## Requirements (for Phase 6 design)

- Deterministic risk gates evaluated independently of models/AI.
- Realistic costs: fees, spread, slippage, funding; conservative ambiguous fills.
- Kill switches, exposure/drawdown limits, cooldowns, circuit breakers — all tested.
- Full audit trail: decisions, inputs, provenance, timestamps (UTC).
