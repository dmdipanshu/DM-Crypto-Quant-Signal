# Production Recovery Plan

SignalForge Q15 disaster recovery procedures. All operations advisory-only, read-only, no live orders. This plan documents recovery procedures only and does not authorize production operation, live trading, real-money orders, model retraining, or paper trading.

## Status

Operational targets below are **recommendations, not established policy**. Tested measurements are distinguished from recommendations throughout.

## 1. Operational targets (all recommendations)

| Target | Value | Type |
|---|---|---|
| RTO (Recovery Time Objective) | 30 minutes | Recommendation |
| RPO (Recovery Point Objective) | 24 hours | Recommendation |
| Backup frequency | Daily | Recommendation |
| Backup retention | 30 days | Recommendation |
| Health-check interval | 60 seconds | Recommendation |
| Log retention | 30 days / 5 rotated 10 MB files | Recommendation |
| Alert severity levels | info / warning / critical | Recommendation |
| Escalation — critical | Immediate | Recommendation |
| Escalation — warning | 4 hours | Recommendation |
| Escalation — info | Daily review | Recommendation |

**Tested measurements (from Q14/Q15 rehearsal):** clean install verified, backup create/verify/list/restore dry-run passed, 190/190 manifest files SHA-256 OK, 1392 tests passed 0 failed. These are test-environment measurements, not production guarantees.

## 2. External notifications

No external notification (email, paging, webhook, provider console, exchange) without explicit authorization. All recovery actions are local and read-only unless authorized.

## 3. Failure scenarios and recovery procedures

### 3.1 Startup failure

**Symptoms:** `uv run sf health` returns UNHEALTHY or non-zero exit; `uv run sf config-show` fails.

**Recovery:**
1. Stop the application/API immediately.
2. Inspect startup logs for config validation errors.
3. Validate config: `uv run sf config-show`.
4. If config invalid, restore config from the most recent verified backup (`sf backup restore --dry-run`, then `--no-dry-run`).
5. Re-run `uv run sf health`; require HEALTHY or DEGRADED (no UNHEALTHY).
6. If unresolved, rollback code to previous committed version (`git revert`/`git checkout`), `uv sync`, re-verify.

### 3.2 Process crash

**Symptoms:** API/process not responding; health endpoint unavailable.

**Recovery:**
1. Confirm process state (no live orders exist to cancel).
2. Restart the process single-process, one symbol/timeframe at a time.
3. `uv run sf health` — all components HEALTHY or DEGRADED.
4. `uv run pytest` — confirm no regression.
5. Create a new backup after recovery (`sf backup create`, then `sf backup verify`).
6. If crashes repeat, see repeated restarts (3.10).

### 3.3 Disk exhaustion

**Symptoms:** Write failures, backup creation fails, logs cannot rotate.

**Recovery:**
1. Stop processing; do not force writes.
2. Identify large files (backups, logs, rotated 10 MB log files).
3. Rotate/retain logs per policy (5 rotated 10 MB files, 30 days retention — recommendation).
4. Free disk space; verify write capability with a dry-run restore (`sf backup restore --dry-run`).
5. Create a fresh backup once space is available; verify it.
6. Resume processing from last known-good state.

### 3.4 Memory exhaustion

**Symptoms:** Process OOM, degraded health, slow response on 4 GB laptop.

**Recovery:**
1. Stop the process; bounded single-process operation only.
2. Reduce working set; avoid full-matrix copies, use column/row-group Parquet reads.
3. Restart and monitor `uv run sf health`.
4. Confirm no UNHEALTHY components.
5. Record sample limits; never silently drop rows.

### 3.5 Stale market data

**Symptoms:** Market data timestamp older than acceptable; provenance gap.

**Recovery:**
1. Do not silently fill or infer missing data.
2. Fetch public market data (ccxt, public read-only) to refresh the snapshot.
3. Verify UTC normalization and provenance preservation.
4. `uv run sf health` — data component HEALTHY or DEGRADED.
5. Resume signal generation from refreshed data; no retraining.

### 3.6 Persistence errors

**Symptoms:** Write failures to data directories, ledger, or model artifacts.

**Recovery:**
1. Stop writes immediately.
2. Check file permissions and data directory access controls.
3. Verify disk space (3.3).
4. Restore affected files from the most recent verified backup (atomic writes: tempfile + `os.replace`).
5. Verify restored files against manifest SHA-256.
6. `uv run sf health` — all components HEALTHY or DEGRADED.

### 3.7 Backup failure

**Symptoms:** `sf backup create` or `sf backup verify` returns FAIL.

**Recovery:**
1. Stop scheduled backups; do not overwrite existing good backups.
2. Inspect backup logs and manifest.
3. Free disk space / fix permissions (3.3, 3.6).
4. Retry `sf backup create`; then `sf backup verify <backup_id>`.
5. If backup cannot be created, use the most recent known-good verified backup for any restore.
6. Alert critical; do not proceed with deployment until backup operational.

### 3.8 Ledger integrity failures

**Symptoms:** SHA-256 checksum mismatch on ledger/artifact; restore verification fails.

**Recovery:**
1. Stop processing; do not trust corrupted ledger/artifact.
2. Identify affected files via manifest.
3. Restore from the most recent backup where checksums match (`sf backup restore --dry-run` first).
4. Verify every restored file against manifest SHA-256 (checksum verification).
5. `uv run sf health` — all components HEALTHY or DEGRADED.
6. Document incident; create new backup after recovery.

### 3.9 API auth failure

**Symptoms:** 401 on all routes with valid token; auth fail-closed behavior broken.

**Recovery:**
1. Check `SIGNALFORGE_API_TOKEN` is set correctly via environment (not in source/TOML).
2. Verify routes return 401 without token and 200 with valid Bearer token.
3. Confirm `SIGNALFORGE_AUTH_REQUIRED` honored; `SIGNALFORGE_ALLOW_FAIL_OPEN` NOT set to `1`.
4. Confirm Bearer tokens are not logged (redacted to `REDACTED`).
5. If auth cannot be fixed, stop the API and rollback code.
6. Alert critical; no external notification without authorization.

### 3.10 Repeated restarts

**Symptoms:** Process crashes/restart loop exceeding bounded retries.

**Recovery:**
1. Stop automatic restarts; break the loop.
2. Investigate root cause from logs (startup failure, crash, disk, memory).
3. Apply the matching recovery procedure (3.1–3.4).
4. Do not resume processing until `uv run sf health` is clean.
5. Alert critical; document incident; post-incident review.

## 4. Recovery verification checklist

After any recovery:

| Check | Command | Expected |
|---|---|---|
| Health | `uv run sf health` | All HEALTHY or DEGRADED |
| Config | `uv run sf config-show` | Valid dump, no secrets echoed |
| Tests | `uv run pytest` | All pass, no skips as passes |
| Lint | `uv run ruff check .` | Zero errors |
| Backup | `sf backup create` + `sf backup verify` | Creates and verifies |
| Restore dry-run | `sf backup restore --dry-run` | 0 errors, no data written |

## 5. Post-recovery actions

1. Document the incident (what failed, when, root cause).
2. Update this plan or the deployment checklist if a gap was identified.
3. Create a new backup after recovery completes.
4. Schedule a post-incident review.
5. No live orders, no retraining, no trading activation, no paper trading activation.

## 6. Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Recovery owner | | | |
| Security reviewer | | | |
| Quant lead (integration) | | | |
