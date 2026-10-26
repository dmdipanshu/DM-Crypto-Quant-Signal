# AI_PROVIDERS.md — SignalForge (Phase 4, planned)

> Planned, not implemented in Phase 1. No provider code exists yet.

## Intent

Optional, interchangeable AI adapters that contextualize evidence. All providers
share one interface: explicit model identity, bounded timeout, retry, rate limit,
budget, fallback, usage reporting, and strict schema-validated outputs.

## Hard limits

AI may never: fabricate market facts, set risk limits, override deterministic
gates, or create live orders. Failures downgrade safely (reduced functionality
or abstention), never silent success.

## Secrets

Loaded from environment only (see `.env.example`). Never in TOML, code, or git.
