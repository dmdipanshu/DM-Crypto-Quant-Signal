# DM Crypto Quant Signal — SignalForge (Phase 4 authorized)

Standalone crypto quant signal engine. Phase 4 adds a provider-independent AI
layer — 8 optional adapters (stdlib urllib, SDKs bring-your-own), bounded
retry/fallback/budgets, closed-schema validation, 8 specialist analysts, a
context-score contract, and a 4-arm ablation framework reusing the Phase 3
experiment registry — on top of the Phase 1–3 foundation (config, market data,
snapshots, CLI, features, labels, baselines, calibration, walk-forward/holdout
validation). The quant engine works fully with AI disabled (default).

> Phase 4 only. Production signals, AI-made trading decisions, execution/orders,
> the full backtester, paper/live trading, and API/dashboard are
> **planned, not implemented in Phase 4**.

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
config/default.toml      validated defaults (no secrets; [ai] all disabled)
src/crypto_engine/       engine: data, features, labels, models, registry,
                         calibration, validation, ai_providers (8 adapters),
                         agents (8 analysts + orchestrator), ablation
tests/                   pytest suite (unit, integration, leakage; all offline)
docs/                    architecture, methodology, AI_PROVIDERS, install, security
memory/                  project memory, state, next steps, session log
.opencode/agents|skills|commands  agent roles, procedures, workflows
```

## Configuration

See `config/default.toml` (exchange, data symbols/timeframes, logging, model,
registry, calibration, validation, ai). AI providers are all disabled by
default; keys live only in env/`.env` (see `.env.example`). TOML never holds
secrets. Provider SDKs are bring-your-own (`pip install openai …`) — the sandbox
has no package-registry network, so there are no ai-* extras.

## Status

Phase 4 in progress — AI interface, analysts, ablation (verdict: inconclusive on
mocked data, honestly reported). See `memory/CURRENT_STATE.md` and
`memory/NEXT_STEPS.md` for verified state.
No 90% target claimed; no out-of-sample profitability claimed; no AI
predictive-value claim (ablation found none on available data).

## License

MIT (see `DECISIONS.md` for stack/license notes, incl. VectorBT deferral).
