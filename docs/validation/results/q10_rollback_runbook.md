# Phase 10 — Rollback Runbook

SignalForge Phase 10 rollback procedures. All operations are advisory-only, read-only, no live orders. Single-process, bounded, 4 GB laptop safe.

## When to Roll Back

Roll back when any of the following occur after deployment:

- `uv run sf health` returns any component `UNHEALTHY`
- `uv run pytest` fails after deployment (regression)
- API auth fails unexpectedly (401 on all routes)
- Backup verification fails (`sf backup verify` returns FAIL)
- Model artifact checksum mismatch on load
- Config validation error on startup
- Security finding becomes active (e.g., exposed secret, unsafe deserialization)

## Rollback Steps

### Step 1: Stop

Stop the application/API immediately. Do not process new signals or API requests.

```powershell
# If running as a service/process, stop it
# No live orders exist anywhere in the codebase; no order cancellation needed
```

### Step 2: Verify backup

Identify the most recent known-good backup:

```powershell
uv run sf backup list backups/
uv run sf backup verify backups/<backup_id>
```

If the most recent backup fails verification, try older backups until one passes.

### Step 3: Restore

Restore from the verified backup (dry-run first, then execute):

```powershell
uv run sf backup restore backups/<backup_id> --dry-run
uv run sf backup restore backups/<backup_id> --no-dry-run
```

### Step 4: Verify restore

```powershell
uv run sf health
uv run sf config-show
uv run sf model list
uv run sf data list
```

All components should be HEALTHY or DEGRADED (no UNHEALTHY).

### Step 5: Confirm no regression

```powershell
uv run pytest
uv run ruff check .
```

## Rollback Scenarios

### Scenario A: Failed deployment (new code breaks)

1. Revert to previous committed version: `git revert <deploy_commit>` or `git checkout <previous_tag>`
2. `uv sync` to reinstall dependencies
3. Verify: `uv run sf health`, `uv run pytest`
4. No data restore needed unless data was corrupted

### Scenario B: Corrupt data or artifacts

1. Do NOT continue processing
2. Restore from backup: `sf backup restore backups/<good_backup> --no-dry-run`
3. Verify all components healthy
4. Resume processing from last known-good state

### Scenario C: Exposed secret / credential leak

1. Rotate the exposed credential immediately (env var, API token, etc.)
2. Restore config from backup if config was modified
3. Verify: `uv run sf health`
4. Review audit logs for unauthorized access

### Scenario D: API auth misconfiguration

1. Check `SIGNALFORGE_API_TOKEN` is set correctly
2. Verify API routes return 401 without token, 200 with valid token
3. If auth cannot be fixed, stop the API and rollback code

## Rollback Safety Constraints

- **No live orders:** Rollback never places or cancels orders (none exist in codebase)
- **No retraining:** Rollback restores frozen artifacts only; no model retraining
- **Atomic writes:** All restore operations use tempfile + `os.replace`
- **Checksum verification:** Every restored file verified against manifest SHA-256
- **Dry-run default:** `sf backup restore` defaults to dry-run; `--no-dry-run` required to write
- **Read-only sources:** Backup reads source files only; never mutates source directories

## Post-Rollback

After successful rollback:

1. Document the incident (what failed, when, root cause)
2. Update the deployment checklist if a gap was identified
3. Create a new backup after rollback completes
4. Schedule a post-incident review
