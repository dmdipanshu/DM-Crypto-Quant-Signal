# PROJECT_MEMORY.md — SignalForge durable memory

## Architecture

- Standalone engine `SignalForge`; pipeline: exchange → market data → features →
  models → signals → risk gates → results; dashboard/API consume canonical results.
- 20 components (see `docs/ARCHITECTURE.md`); Phase 1 implements 1–7 only.

## Invariants

- Phase 1 only unless a new phase is explicitly approved.
- UTC-normalized market data with provenance; no silent fills; `NO_TRADE` valid.
- Deterministic risk gates independent of models/AI; no live orders.
- Secrets in env/`.env` only; never TOML/code/git. Accuracy ≠ win rate ≠ profitability.

## Conventions

- Python 3.12, uv isolated `.venv/`; typed modular code; Windows-safe `pathlib`.
- `uv sync` → `uv run sf health` → `uv run sf config-show` → `uv run pytest` → `uv run ruff check .`

## Verified commands (lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync` — clean (73 packages), `uv.lock` tracked
- [x] `uv run sf health` — status ok, package 0.1.0, storage writable
- [x] `uv run sf config-show` — validated binance/BTC+ETH+SOL/1h+4h dump
- [x] `uv run sf data --help` / `uv run sf data status` — 0 snapshots, no crash
- [x] `uv run pytest` — 37 passed in ~5s, no network
- [x] `uv run ruff check .` — all checks passed
- [x] `uv run ruff format --check .` — 63 files formatted-clean
