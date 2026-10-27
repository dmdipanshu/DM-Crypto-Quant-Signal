# PROJECT_MEMORY.md — SignalForge durable memory

## Architecture

- Standalone engine `SignalForge`; pipeline: exchange → market data → features →
  models → signals → risk gates → results; dashboard/API consume canonical results.
- 20 components (see `docs/ARCHITECTURE.md`); Phases 1–2 implement data→models;
  Phase 3 adds calibration + walk-forward/holdout validation (no production signals).
- Phase 4: AI interface + analysts + ablation (quant works with AI disabled).

## Invariants

- Phase 4 only unless a new phase is explicitly approved.
- UTC-normalized market data with provenance; no silent fills; `NO_TRADE` valid.
- Deterministic risk gates independent of models/AI; no live orders.
- Secrets in env/`.env` only; never TOML/code/git. Accuracy ≠ win rate ≠ profitability.

## Conventions

- Python 3.12, uv isolated `.venv/`; typed modular code; Windows-safe `pathlib`.
- `uv sync` → `uv run sf health` → `uv run sf config-show` → `uv run pytest` → `uv run ruff check .`
- Phase 2: causal features + triple-barrier labels + baselines + registry; 4 GB
  bounds (`n_jobs=1`, `chunk_rows`, `max_rows_train=2000`); scores uncalibrated;
  pickle trusted-local-only.

## Verified commands (lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync` — clean (73 packages), `uv.lock` tracked
- [x] `uv run sf health` — status ok, package 0.1.0, storage writable
- [x] `uv run sf config-show` — validated binance/BTC+ETH+SOL/1h+4h dump
- [x] `uv run sf data --help` / `uv run sf data status` — 0 snapshots, no crash
- [x] `uv run pytest` — 37 passed in ~5s, no network
- [x] `uv run ruff check .` — all checks passed
- [x] `uv run ruff format --check .` — 63 files formatted-clean

## Verified commands (Phase 2 lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync` — clean, scikit-learn 1.9.1 installed (Windows wheels)
- [x] `uv run pytest` — 98 passed, 1 skipped (lightgbm optional) in ~13–28 s
- [x] `uv run pytest -rs` — skip reason: lightgbm not installed (legitimate)
- [x] `uv run ruff check .` — clean (after 1 unused-var fix in test_models.py)
- [x] `uv run ruff format --check .` — clean (5 files reformatted by lead)
- [x] `uv run sf health` / `sf config-show` — model/registry sections present

## Verified commands (Phase 3 lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync` — clean (no dep changes; sklearn 1.9.1 retained)
- [x] `uv run pytest` — 165 passed, 1 skipped (lightgbm optional) in ~14–16 s
- [x] `uv run pytest -rs` — skip reason: lightgbm not installed (legitimate)
- [x] `uv run ruff check .` — clean
- [x] `uv run ruff format --check .` — 98 files clean
- [x] `uv run sf health` / `sf config-show` — calibration/validation sections present

## Verified commands (Phase 4 lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync --offline` — clean, 73 packages (no network for re-lock; no new deps)
- [x] `uv run pytest` — 254 passed, 1 skipped (lightgbm optional) in ~16 s
- [x] `uv run pytest -rs` — skip reason: lightgbm not installed (legitimate)
- [x] `uv run ruff check .` — clean (lead fixed 9x RUF012 ClassVar + B027 noqa + 11 auto-fixes)
- [x] `uv run ruff format --check .` — 122 files clean (lead formatted 11 files)
- [x] `uv run sf health` / `sf config-show` — `[ai]` section present, providers empty/disabled
