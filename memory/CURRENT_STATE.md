# CURRENT_STATE.md — SignalForge verified state

- **Phase**: 1 complete (pending user review) — foundation only. Stop before Phase 2.
- **Implemented**: `pyproject.toml` (+ `uv.lock` tracked), `.gitignore`, `.env.example`,
  `config/default.toml`, root docs (`AGENTS.md`, `README.md`, `CONTRIBUTING.md`,
  `DECISIONS.md`), `docs/*` (6 files), `memory/*` (4 files),
  `.opencode/agents/*` (10) and `.opencode/skills/*/SKILL.md` (12) expanded,
  `src/crypto_engine/` (config/settings, data/types+validation+adapter+errors,
  datasets/snapshots, cli with `health`, `config-show`, `data status`, `data fetch`),
  `tests/` (37 tests: unit config/validation/adapter/snapshots + integration cli/agents-skills).
- **Verification (lead-run 2026-10-27, Windows, repo root)**: `uv sync` EXIT 0,
  `uv run sf health` EXIT 0 (status ok), `uv run sf config-show` EXIT 0,
  `uv run sf data --help` / `data status` EXIT 0 (0 snapshots),
  `uv run pytest` 37 passed in ~5s, `uv run ruff check .` all checks passed,
  `uv run ruff format --check .` 63 files formatted-clean.
- **Limitations**: runtime Python 3.13.12 (pyproject allows >=3.12,<3.14; 3.12.10 present but uv resolved 3.13); `data fetch` live-network path untested by design; no models/AI/signals/backtests/paper/API (correctly out of Phase 1).
