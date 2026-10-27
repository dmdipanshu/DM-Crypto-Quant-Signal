# DM Crypto Quant Signal — SignalForge (Phase 3 authorized)

Standalone crypto quant signal engine. Phase 3 implements probability calibration,
reliability reporting, purged walk-forward validation with embargo, a locked
final-holdout protocol, an experiment registry with multiple-testing discipline,
and cost/robustness analysis — on top of the Phase 1–2 foundation (config,
market data, snapshots, CLI, causal features, triple-barrier labels, baselines).

> Phase 3 only. AI providers, production signals, risk execution, the full
> event-driven backtester, paper trading, and API/dashboard are
> **planned, not implemented in Phase 3**.

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
src/crypto_engine/       engine: data, features, labels, models, registry,
                         calibration, validation (walk-forward, holdout, metrics)
tests/                   pytest suite (unit, integration, leakage)
docs/                    architecture, methodology, install, security, phasedocs
memory/                  project memory, state, next steps, session log
.opencode/agents|skills|commands  agent roles, procedures, workflows
```

## Configuration

See `config/default.toml` (exchange, data symbols/timeframes, logging, model,
registry, calibration, validation). Secrets go in `.env` (copy `.env.example`);
TOML never holds secrets.

## Status

Phase 3 in progress — calibration, purged walk-forward + embargo, locked holdout,
experiment registry, cost/robustness. See `memory/CURRENT_STATE.md` and
`memory/NEXT_STEPS.md` for verified state.
No 90% target claimed; no out-of-sample profitability claimed.

## License

MIT (see `DECISIONS.md` for stack/license notes, incl. VectorBT deferral).
