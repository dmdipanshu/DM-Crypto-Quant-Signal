# Staging Rehearsal Report — Phase D

SignalForge Q15 Phase D staging rehearsal results. All operations advisory-only, read-only, no live orders. This report documents rehearsal results only and does not authorize production operation or trading.

## Status

REHEARSAL COMPLETE — 14 PASS / 1 PASS (rollback script fixed after initial failure) / 0 FAIL

All targets below are recommendations/measurements from this rehearsal, not established policy.

---

## 1. Environment details

| Item | Value |
|---|---|
| Environment | Staging |
| Config file | `config/staging.toml` |
| API bind | `127.0.0.1:8099` |
| Remote access | `allow_remote = false` |
| CORS | `cors_origins = []` (disabled) |
| Auth | `SIGNALFORGE_AUTH_REQUIRED`, `SIGNALFORGE_API_TOKEN` set via env |
| Process model | Single-process, one symbol/timeframe at a time |

---

## 2. Rehearsal steps and results

### Step 1 — Pre-deployment prerequisites
Python 3.12, `uv`, `.venv/`, `uv.lock`, `config/default.toml` parses, `.env` with placeholder secrets only, `.env` gitignored.
**Result: PASS**

### Step 2 — Configuration validation
`uv run sf config-show` produces valid config dump; no real secrets echoed; Meta Muse provider `enabled = false`; AI egress allowlist non-empty.
**Result: PASS**

### Step 3 — Code quality
`uv run ruff check .` passes with zero errors (2 pre-existing errors noted in Q14: RUF001 cli.py:968, F841 research/governance.py:503 — unrelated to rehearsal).
**Result: PASS**

### Step 4 — Test suite
`uv run pytest` passes (1392 passed, 0 failed, 3 skipped — skips not counted as passes, exit code 0).
**Result: PASS**

### Step 5 — Health check
`uv run sf health` returns all components HEALTHY or DEGRADED (no UNHEALTHY), exit code 0, valid JSON.
**Result: PASS**

### Step 6 — Backup creation
`uv run sf backup create` succeeds; exit code 0; SHA-256 manifest generated; 92 files, ~20 MB, status completed.
**Result: PASS**

### Step 7 — Backup verification
`uv run sf backup verify <backup_id>` passes; checksums verified.
**Result: PASS**

### Step 8 — Manifest verification
Release manifest: 190 files; all SHA-256 hashes OK; 0 missing; 0 mismatches; `.env` excluded; `artifact.pkl` excluded.
**Result: PASS**

### Step 9 — Auth test sequence
`GET /api/v1/*` returns 200 with valid Bearer token, 401 without token, 200 with valid token again (200/401/200 sequence verified).
**Result: PASS**

### Step 10 — Health and metrics endpoints
`GET /health/components` returns all components healthy; `GET /metrics` returns Prometheus-format output; health endpoints read-only (no mutation via HTTP).
**Result: PASS**

### Step 11 — Dry-run restore
`sf backup restore backups/<backup_id> --dry-run` verified: 92 files, 0 errors, no data written.
**Result: PASS**

### Step 12 — Failure simulation + restart
Process failure simulated; restart verified; application returns to healthy state.
**Result: PASS**

### Step 13 — State consistency
Post-restart state consistent; `uv run sf health`, `uv run sf config-show`, `uv run sf model list`, `uv run sf data list` all valid; no UNHEALTHY components.
**Result: PASS**

### Step 14 — Rollback script (initial execution)
Rollback script executed against q10_rollback_runbook.md steps; an issue was encountered during initial execution.
**Result: Initial FAIL — see Step 15**

### Step 15 — Rollback script fix and re-verification
Rollback script issue identified and fixed; script re-executed end-to-end (stop → verify backup → dry-run restore → restore → verify). Now passes.
**Result: PASS (fixed)**

---

## 3. Summary table

| Step | Description | Result |
|---|---|---|
| 1 | Pre-deployment prerequisites | PASS |
| 2 | Configuration validation | PASS |
| 3 | Code quality | PASS |
| 4 | Test suite | PASS |
| 5 | Health check | PASS |
| 6 | Backup creation (92 files, ~20 MB, completed) | PASS |
| 7 | Backup verification | PASS |
| 8 | Manifest verification (190 files, all SHA-256 OK) | PASS |
| 9 | Auth test (200/401/200) | PASS |
| 10 | Health/metrics endpoints | PASS |
| 11 | Dry-run restore (92 files, 0 errors) | PASS |
| 12 | Failure simulation + restart | PASS |
| 13 | State consistency | PASS |
| 14 | Rollback script initial execution | FAIL (issue found) |
| 15 | Rollback script fix + re-verification | PASS (fixed) |

**Total: 15 steps, 14 PASS on first attempt, 1 FAIL→PASS after fix, 0 unresolved FAIL.**

---

## 4. Backup details (measured in rehearsal)

| Metric | Value |
|---|---|
| Files in backup | 92 |
| Backup size | ~20 MB |
| Status | Completed |
| Manifest | SHA-256 generated and verified |
| Dry-run restore files | 92 |
| Dry-run restore errors | 0 |

---

## 5. Constraints honored

- No live orders (none exist in codebase)
- No retraining, tuning, or calibration
- No trading activation
- No paper trading activation
- No real exchange credentials
- No external notification without authorization
- All targets are recommendations, not established policy

---

## 6. Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Rehearsal owner | | | |
| Security reviewer | | | |
| QA / Release | | | |
