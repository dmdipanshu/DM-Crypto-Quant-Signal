# SESSION_LOG.md — SignalForge session history

## 2026-10-27 — Foundation build started (foundation track)

- Workspace audit: `.opencode/` stubs only (10 agents, 12 skills, 4 commands);
  no `src/`, no `tests/`, no config, no commits on `master`.
- Built Phase 1 foundation: `pyproject.toml`, `.gitignore`, `.env.example`,
  `config/default.toml`, root docs, `docs/*`, `memory/*`, expanded all agents/skills.
- Verification deferred per instructions: no `uv sync` / `pytest` run; lead integrates.
- Next: engine track builds `src/` + `tests/`; lead runs checks and updates state.

## 2026-10-27 — Engine build + lead integration (both tracks verified)

- Engine track built `src/crypto_engine/` (settings, types, validation, CcxtAdapter
  with pagination/retries/stale warnings, snapshots with atomic writes + sha256,
  Typer CLI `health`/`config-show`/`data status`/`data fetch`) + `tests/` (37 tests).
- Lead fix: moved `pytest` + `ruff` from `[dev]` extra into main dependencies so
  documented `uv sync` → `uv run pytest` works without `--extra dev`.
- Lead verification (Windows, repo root, EXIT 0 all): `uv sync`; `sf health`;
  `sf config-show`; `sf data --help`/`data status`; `pytest` 37 passed;
  `ruff check` clean; `ruff format --check` 63 files clean.
- State updated: Phase 1 complete pending user review; Phase 2 NOT started.

## 2026-10-27 - Phase 2 authorized and implemented (lead integration)

- Auth: Phase 2 only (features, triple-barrier labels, baselines, registry, anti-leakage). AGENTS.md scope updated with 4GB bounds (n_jobs=1, chunked, max_rows_train=2000).
- Track A (features/labels) verified: 34 new tests, full suite 71 passed at handoff.
- Track B (models/registry) partially survived abort; lead verified and completed: sklearn 1.9.1 to main deps (lightgbm stays optional), ModelConfig/RegistryConfig in settings + default.toml.
- Lead fixes: 1 ruff unused-var (test_models.py), ruff format 5 files. Final: pytest 98 passed/1 skipped (lightgbm optional), ruff check + format clean, sf health/config-show EXIT 0.
- Resource probe (300 rows, tracemalloc, cold import): ~12.2s, ~55 MiB Python-alloc peak; not a process-RSS guarantee.
- No 90% or profitability claim. Phase 3 NOT started; awaiting user review.

## 2026-10-27 - Phase 3 authorized and implemented (lead integration)

- Auth: Phase 3 only (calibration, reliability, purged walk-forward + embargo, locked holdout, experiment registry, DSR, cost/robustness). AGENTS.md scope updated to Phase 3; fixed duplicated section numbers (two ##3); sequential-subagent rule for 4GB.
- Hygiene: normalized trailing blank lines in DECISIONS.md + SESSION_LOG.md; rewrote NEXT_STEPS.md to Phase 3 state (old Phase 2 gate retired, history preserved in git).
- Track A (calibration/reliability/config): CalibratedClassifier (event P(y==positive_class), sigmoid/isotonic, t_bounds, min 20, 1e-12 reload), reliability module, CalibrationConfig + ValidationConfig in settings + default.toml; 26 tests.
- Track B (validation): folds (tail-anchored, purge+embargo, disjoint counts), holdout ledger v1, metrics (tagged raw|calibrated, proxy Sharpe/drawdown warned), robustness (fee/slip grid, block bootstrap, MC, LOOFO), experiments (JSONL registry, DSR, Holm/Bonferroni, pbo_note refuses fabrication); 41 tests.
- Lead verification: uv sync EXIT 0; pytest 165 passed/1 skipped (lightgbm optional); ruff check + format (98 files) clean; sf health/config-show EXIT 0 with new sections.
- Resource probe (300-row OHLCV -> 181 feats, 2 folds, 50 bootstraps): ~0.19s, ~475 KiB tracemalloc peak (not RSS).
- No 90% or profitability claim. Phase 4 NOT started; awaiting user review.
