# CONTRIBUTING.md — SignalForge (Phase 1)

## Environment

- Python 3.12 via `py -3.12`; `uv` manages the isolated `.venv/`. Never use system Python.
- `uv sync` after pulling or changing `pyproject.toml`.

## Workflow

1. Read `AGENTS.md` and `memory/` first.
2. Work only within your assigned paths; keep changes small and typed.
3. Windows-safe paths (`pathlib`); no secrets in code/config/git.
4. Run before handoff: `uv run pytest`, `uv run ruff check .`,
   `uv run sf health`, `uv run sf config-show`.
5. Update `memory/` only with verified facts (exact commands + results).

## Standards

- Typed public interfaces, validated config, explicit errors, bounded side effects.
- Tests for every data boundary and risk gate; deterministic fixtures.
- Research integrity: no fabrication, no forced targets; separate accuracy /
  win rate / profitability; `NO_TRADE` is valid.
- Phase 1 only — do not add models, backtests, AI providers, paper trading,
  or dashboard implementation without phase approval.

## Commits

Small, scoped commits with factual messages. Never commit `.env`, data under
`data/`, artifacts under `models/`, or generated reports.
