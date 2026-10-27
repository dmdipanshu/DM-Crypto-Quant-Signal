# DM Crypto Quant Signal — SignalForge (Phase 5 authorized)

Standalone crypto quant signal engine. Phase 5 adds the canonical signal
decision pipeline — schema-validated `SignalRecord` v1.0, deterministic
model-to-decision policy, calibration-aware handling, expected-edge vs cost
gating, deterministic risk gates (fail-closed), advisory-only AI context,
proposed level validation, a hash-chained audit history, and `sf signal`
CLI with offline replay — on top of the Phase 1–4 foundation. The engine
abstains (`NO_TRADE`) whenever mandatory evidence is missing, and it places
no orders, holds no positions, and tracks no live P&L.

> Phase 5 only. The full event-driven backtester, paper trading, live trading,
> portfolio accounting, and API/dashboard are
> **planned, not implemented in Phase 5**.

## Quickstart (Windows)

```powershell
py -3.12 -m pip install uv
uv sync
uv run sf health
uv run sf config-show
uv run pytest
uv run ruff check .
```

## Layout

```text
config/default.toml      validated defaults (no secrets; [ai] disabled, [signal]/[risk] set)
src/crypto_engine/       engine: data, features, labels, models, registry,
                         calibration, validation, ai_providers (8 adapters),
                         agents (8 analysts), signals (schema/policy/costs/levels/history),
                         risk (deterministic gates)
tests/                   pytest suite (unit, integration, leakage; all offline)
docs/                    architecture, methodology, AI_PROVIDERS, install, security
memory/                  project memory, state, next steps, session log
.opencode/agents|skills|commands  agent roles, procedures, workflows
```

## Configuration

See `config/default.toml` (exchange, data symbols/timeframes, logging, model,
registry, calibration, validation, ai, signal, risk). AI providers are all
disabled by default; risk gates fail closed by default; keys live only in
env/`.env` (see `.env.example`). TOML never holds secrets. Provider SDKs are
bring-your-own (`pip install openai …`) — the sandbox has no package-registry
network, so there are no ai-* extras.

## Status

Phase 5 in progress — canonical signal engine, deterministic risk gates,
audit history, `sf signal` CLI (all verified; engine abstains safely by
default). See `memory/CURRENT_STATE.md` and `memory/NEXT_STEPS.md` for
verified state.
No 90% target claimed; no out-of-sample profitability claimed; no AI
predictive-value claim; no order placement or live readiness claimed.

## License

MIT (see `DECISIONS.md` for stack/license notes, incl. VectorBT deferral).
