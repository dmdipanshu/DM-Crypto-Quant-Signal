# Production Deployment Runbook

SignalForge Q15 operational deployment runbook. All operations are advisory-only, read-only, no live orders. Single-process, bounded, 4 GB laptop safe. This runbook does NOT authorize live trading, real-money orders, model retraining/tuning/calibration, strategy promotion, or paper trading.

## Status

This document is a recommendation only. Deployment targets, thresholds, and timing below are recommendations, not established policy.

---

## 1. Pre-deployment prerequisites

Verify each item before starting any environment deployment.

### 1.1 Runtime environment

| Prerequisite | Verification command |
|---|---|
| Python 3.12 | `py -3.12 --version` |
| `uv` installed | `uv --version` |
| Virtual environment | `.venv/` exists (`uv sync`) |
| Pinned dependencies | `uv.lock` present and committed |
| No new dependencies | `pyproject.toml` unchanged |

### 1.2 Configuration

| Item | Requirement |
|---|---|
| `config/default.toml` | Present and parses cleanly (`tomllib.load`) |
| `.env` | Present with placeholder/empty secrets only |
| `.env` gitignored | Confirmed via `git check-ignore .env` |
| `SIGNALFORGE_API_TOKEN` | Set if API auth required (staging/prod) |
| `SIGNALFORGE_ALLOW_FAIL_OPEN` | NOT set to `1` (fail-open disabled by default) |
| Meta Muse provider | `enabled = false` in `config/default.toml` |
| AI egress allowlist | Non-empty even when disabled |

### 1.3 Code quality

| Check | Command |
|---|---|
| Lint | `uv run ruff check .` — zero errors |
| Tests | `uv run pytest` — all pass, no skips counted as passes |
| Health | `uv run sf health` — all components HEALTHY or DEGRADED (no UNHEALTHY) |
| Config dump | `uv run sf config-show` — valid output |
| No pickle in store | No `pickle` imports in `src/crypto_engine/registry/store.py` |
| No real keys | No real API keys in `.env`, TOML, or source code |

### 1.4 Data and artifacts

| Item | Requirement |
|---|---|
| Frozen model artifact | Present in `models/artifacts/` (logistic only; LightGBM NOT_EVALUABLE) |
| Artifact SHA-256 | Verified against `record.json` |
| Prospective data snapshot | Present in `data/prospective/` |
| No retraining | None performed |
| No promotion flags | `NO_STRATEGY_ELIGIBLE`, `ai_can_promote=false` |

---

## 2. Step-by-step deployment

Apply the same procedure in each environment, starting with dev, then staging, then production only after dev and staging succeed.

### Common steps (all environments)

1. `uv sync` — installs all dependencies from committed `uv.lock` (offline).
2. `uv run sf health` — confirm all components HEALTHY or DEGRADED.
3. `uv run sf backup create` — create a pre-deployment backup.
4. `uv run sf backup verify <backup_id>` — verify backup integrity.

### Dev (`dev.toml`, loopback)

1. Apply config: `config/dev.toml`, API bound to `127.0.0.1`, `allow_remote=false`, CORS empty.
2. Run common steps above.
3. `uv run sf health` — all components HEALTHY or DEGRADED.
4. Sign-off required before promoting to staging.

### Staging (`staging.toml`, port 8099)

1. Apply config: `config/staging.toml`, API bound to `127.0.0.1:8099`, `allow_remote=false`, CORS empty, `SIGNALFORGE_API_TOKEN` set.
2. Run common steps above.
3. Auth sequence: 200/401/200 verified (valid token, no token, valid token).
4. Health and metrics endpoints verified (`GET /health/components`, `GET /metrics`).
5. Dry-run restore verified (`sf backup restore --dry-run`, 0 errors).
6. Failure simulation + restart verified.
7. State consistency verified.
8. Sign-off required before promoting to production.

### Production

1. Production deployment is NOT currently authorized. This section documents the procedure only.
2. Apply config: `config/production.toml`, API bound to `127.0.0.1`, `allow_remote=false`, CORS empty, `SIGNALFORGE_API_TOKEN` set via environment (never in source/TOML).
3. Run common steps above.
4. Auth sequence: 200/401/200 verified.
5. Health and metrics endpoints verified.
6. Dry-run restore verified (0 errors).
7. Sign-off from all roles below before activation.

**No live orders, no retraining, no trading activation, no paper trading activation.**

---

## 3. Configuration validation

```powershell
uv run sf config-show
uv run sf health
uv run sf backup list backups/
```

Confirm:
- All components HEALTHY or DEGRADED (no UNHEALTHY)
- Config dump is valid JSON/TOML
- At least one backup listed and verified
- No real secrets echoed by `config-show` (secrets redacted)

---

## 4. Startup health verification

After the application/API starts:

| Check | Expected |
|---|---|
| API bind address | `127.0.0.1` only (loopback) |
| `allow_remote` | `false` |
| CORS origins | Empty |
| `GET /health/components` | All HEALTHY or DEGRADED |
| `GET /metrics` | Prometheus-format output |
| `GET /api/v1/*` without token | 401 (when `SIGNALFORGE_API_TOKEN` set) |
| `GET /api/v1/*` with valid Bearer token | 200 |
| Live order endpoints | Do not exist (code review) |
| Training/fetch endpoints | Not exposed via HTTP |

---

## 5. Rollback trigger conditions

Roll back when any of the following occur after deployment:

- `uv run sf health` returns any component `UNHEALTHY`
- `uv run pytest` fails after deployment (regression)
- API auth fails unexpectedly (401 on all routes)
- Backup verification fails (`sf backup verify` returns FAIL)
- Model artifact checksum mismatch on load
- Config validation error on startup
- Security finding becomes active (e.g., exposed secret, unsafe deserialization)

See `docs/validation/results/q10_rollback_runbook.md` for rollback steps.

---

## 6. Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Deployment owner | | | |
| Security reviewer | | | |
| QA / Release | | | |
| Quant lead (integration) | | | |

**Deployment does NOT authorize live trading, real-money orders, model retraining, strategy promotion, or paper trading. All signal outputs are advisory-only.**
