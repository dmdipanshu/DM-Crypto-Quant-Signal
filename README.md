# DM Crypto Quant Signal — SignalForge (Phase 1)

Standalone crypto quant signal engine. Phase 1 builds the foundation:
validated config, market-data layer, dataset snapshots, CLI, tests, and docs.

> Phase 1 only. Models, AI providers, signal generation, backtests, paper trading,
> and API/dashboard implementation are **planned, not implemented in Phase 1**.

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

Phase 1 in progress — foundation only. See `memory/CURRENT_STATE.md` and
`memory/NEXT_STEPS.md` for verified state and handoff.

## License

MIT (see `DECISIONS.md` for stack/license notes, incl. VectorBT deferral).
