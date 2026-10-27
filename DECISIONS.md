# DECISIONS.md — SignalForge Decision Log (Phase 1)

Append-only. Never rewrite history; supersede with new dated entries.

## 2026-10-27 — Phase 1 stack

- **Python 3.12 + uv, isolated `.venv/`**: reproducible, Windows-friendly, avoids
  system-Python pollution and OneDrive permission issues. `uv.lock` is tracked.
- **pydantic v2 + pydantic-settings**: validated config with typed errors.
- **pandas + numpy + scipy + pyarrow**: market-data frames, stats, parquet snapshots.
- **ccxt v4**: public read-only exchange data (Binance default), rate-limited, bounded retries.
- **typer + rich**: `sf` CLI with readable output.
- **tenacity**: bounded retry/backoff for network calls.
- **pyyaml**: future config/docs tooling; minimal use in Phase 1.
- **tomli**: conditional dep for Python < 3.11 only; 3.12 uses stdlib `tomllib`.
- **pytest + ruff (dev)**: tests and lint, run via `uv run`.
- **2026-10-27 (integration amendment)**: `pytest>=8` + `ruff>=0.4` promoted to
  main `dependencies` (kept also in `[dev]` extra) so plain `uv sync` installs
  them and documented `uv run pytest` / `uv run ruff check .` work — engine
  track found plain sync missed them; lead fixed and re-verified (`uv sync`
  EXIT 0, `pytest` 37 passed, `ruff check` clean).
- **ML/API deps (scikit-learn, lightgbm, optuna, fastapi, uvicorn) as optional extras only**:
  not installed or imported in Phase 1; prevents scope creep and heavy installs.

## 2026-10-27 — No VectorBT until license verified

- VectorBT (all variants) deferred: license terms must be verified compatible
  (including redistribution/commercial implications) before adoption.
- Phase 1 backtest needs are met by deterministic in-house accounting fixtures;
  revisit in Phase 3 with a license review recorded here.

## 2026-10-27 — Isolated venv, tracked lockfile

- `.venv/` git-ignored; `uv.lock` tracked for reproducible installs.
- `data/raw/`, `data/processed/`, `models/artifacts/`, `reports/generated/`,
  `*.parquet` (except test fixtures) ignored — large regenerable outputs stay out of git.

## 2026-10-27 — Secrets in env, never TOML/git

- `config/default.toml` holds non-secret defaults only; `.env.example` documents
  placeholder keys; `.env` is ignored and never committed.

## 2026-10-27 - Phase 2 stack and resource bounds

- scikit-learn>=1.3 promoted to main dependencies (Windows wheels for 3.12/3.13, lightweight, no GPU); lightgbm/optuna stay in [ml] optional, lazy-guarded, never required for core tests.
- ModelConfig enforces n_jobs=1 (le=1), max_rows_train=2000 default with explicit logging when capped, chunk_rows=5000; RegistryConfig path models/artifacts (git-ignored), allow_overwrite=false.
- Model scores documented UNCALIBRATED; no calibration claim until Phase 3 evidence.
- Pickle artifacts trusted-local-only with sha256 verification; JSON for rule-based models; corruption/tampering rejected.
- Representative probe: 300-row OHLCV -> 181 feature rows x4 cols + logistic fit ~12.2s cold / ~55 MiB Python-alloc peak (tracemalloc, not RSS guarantee).
- No 90% or profitability claim; randomized-label control within chance bands only.

