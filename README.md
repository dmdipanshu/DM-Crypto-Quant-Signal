# DM Crypto Quant Signal — SignalForge (Phase 2 authorized)

Standalone crypto quant signal engine. Phase 2 implements causal features,
triple-barrier labels, lightweight ML baselines, and a local model registry
on top of the Phase 1 foundation (config, market data, snapshots, CLI, tests).

> Phase 2 only. AI providers, production signals, risk execution, full backtesting,
> paper trading, and API/dashboard are **planned, not implemented in Phase 2**.

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
config/default.toml      validated defaults (no secrets)
src/crypto_engine/       engine (built by engine track; Phase 1 interfaces only)
tests/                   pytest suite
docs/                    architecture, methodology, install, security, phasedocs
memory/                  project memory, state, next steps, session log
.opencode/agents|skills|commands  agent roles, procedures, workflows
```

## Configuration

See `config/default.toml` (exchange, data symbols/timeframes, logging).
Secrets go in `.env` (copy `.env.example`); TOML never holds secrets.

## Status

Phase 2 in progress — causal features, triple-barrier labels, baselines, registry.
See `memory/CURRENT_STATE.md` and `memory/NEXT_STEPS.md` for verified state.
No 90% target claimed; no out-of-sample profitability claimed.

## License

MIT (see `DECISIONS.md` for stack/license notes, incl. VectorBT deferral).
