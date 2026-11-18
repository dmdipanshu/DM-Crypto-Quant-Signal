# Phase 10 — Deployment Checklist

SignalForge Phase 10 deployment readiness. All items are **advisory-only, read-only, no live orders**. Single-process, 4 GB laptop safe.

## Pre-Deployment

### Environment
- [ ] Python 3.12 installed (`py -3.12 --version`)
- [ ] `uv` installed (`uv --version`)
- [ ] Virtual environment created at `.venv/` (`uv sync`)
- [ ] `uv.lock` present and committed (pinned dependencies, no auto-update)
- [ ] No new dependencies added beyond `pyproject.toml`
- [ ] `config/default.toml` present and parses cleanly (`python -c "import tomllib; tomllib.load(open('config/default.toml','rb'))"`)
- [ ] `.env` present with no real secrets (all values empty or placeholder)
- [ ] `.env` NOT committed to git (check `.gitignore`)
- [ ] `SIGNALFORGE_API_TOKEN` env var set if API auth is required
- [ ] `SIGNALFORGE_ALLOW_FAIL_OPEN` NOT set to "1" (fail-open disabled by default)

### Code
- [ ] `uv run ruff check .` passes with zero errors
- [ ] `uv run pytest` passes (all tests, no skips counted as passes)
- [ ] `uv run sf health` returns all components HEALTHY or DEGRADED (no UNHEALTHY)
- [ ] `uv run sf config-show` produces valid config dump
- [ ] No `pickle` imports in `src/crypto_engine/registry/store.py`
- [ ] No real API keys in `.env`, TOML, or source code
- [ ] Meta Muse provider disabled (`enabled = false` in config/default.toml)
- [ ] AI egress allowlist non-empty even when disabled

### Data & Artifacts
- [ ] Frozen model artifact present in `models/artifacts/` (logistic only; LightGBM NOT_EVALUABLE)
- [ ] Model artifact SHA-256 verified against `record.json`
- [ ] Prospective data snapshot present in `data/prospective/`
- [ ] No retraining, tuning, or calibration performed
- [ ] No strategy promotion flags set (`NO_STRATEGY_ELIGIBLE`, `ai_can_promote=false`)

### Security
- [ ] API token auth configured (`SIGNALFORGE_API_TOKEN`) if exposing API
- [ ] API bound to loopback only (`127.0.0.1:8000`, `allow_remote=false`)
- [ ] CORS origins empty (`cors_origins=[]`)
- [ ] Fail-open gated behind `SIGNALFORGE_ALLOW_FAIL_OPEN=1`
- [ ] Metric label redaction active (secret-like values redacted to `REDACTED`)
- [ ] Health check endpoints read-only (no mutation via HTTP)

## Deployment

### Install
- [ ] `uv sync` installs all dependencies without network (uses committed `uv.lock`)
- [ ] `uv run sf health` succeeds after install
- [ ] `uv run sf backup create` succeeds and produces a backup
- [ ] `uv run sf backup verify <backup_dir>` passes

### Runtime
- [ ] API starts on `127.0.0.1:8000` only
- [ ] `GET /health/components` returns all components healthy
- [ ] `GET /metrics` returns Prometheus-format output
- [ ] `GET /api/v1/*` returns 401 when `SIGNALFORGE_API_TOKEN` is set and no token provided
- [ ] `GET /api/v1/*` returns 200 when valid Bearer token provided
- [ ] No live order endpoints exist (verified by code review)
- [ ] No training/fetch endpoints exposed via HTTP

## Post-Deployment

### Verification
- [ ] `uv run pytest` still passes after deployment
- [ ] `uv run sf backup list backups/` shows at least one backup
- [ ] `uv run sf health` shows no UNHEALTHY components
- [ ] Monitoring/metrics collection functioning (if enabled)

### Rollback plan
- [ ] Rollback runbook reviewed (see `q10_rollback_runbook.md`)
- [ ] Most recent backup identified and verified
- [ ] Restore procedure tested in dry-run mode (`sf backup restore --dry-run`)

## Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Deployment owner | | | |
| Security reviewer | | | |
| QA / Release | | | |

**Note:** Deployment does NOT authorize live trading, real-money orders, model retraining, or strategy promotion. All signal outputs are advisory-only.
