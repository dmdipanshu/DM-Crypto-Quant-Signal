# Phase 10 — Resilience & Disaster Recovery Runbook

SignalForge Phase 10 (Q10) disaster recovery procedures. All operations are advisory-only, read-only by default, and require no network, no live orders, and no model retraining. Single-process, bounded, 4 GB laptop safe.

## 1. Backup

### `sf backup` — create a full backup

```powershell
uv run sf backup --config config/default.toml
```

Backs up:
- Model artifacts: `models/artifacts/` (registry path)
- Data snapshots: `data/` (storage path)
- Paper trading state: `data/paper/` (account path)
- Logs: parent dir of `settings.logging.file_path`

Output is written to `settings.backup.path` (default `backups/`). Each backup gets a timestamped directory containing copied files plus a `manifest.json` with SHA-256 checksums for every file.

Files >50 MB are skipped with a warning (bounded for 4 GB laptop).

### Example output

```
backup_id: 20261115T103000Z
created_utc: 2026-11-15T10:30:00+00:00
files: 47
total_bytes: 1234567
status: completed
```

## 2. Verify

### `sf backup verify <backup_dir>` — check backup integrity

Recomputes SHA-256 for every file listed in `manifest.json` and compares.

```powershell
uv run sf backup verify backups/20261115T103000Z
```

Exit codes: `0` = all checksums match, `1` = mismatch or missing manifest.

## 3. List

### `sf backup list <backup_dir>` — list available backups

```powershell
uv run sf backup list backups/
```

Prints backup_id, created_utc, file count, and status for each backup, newest first.

## 4. Restore

### `sf backup restore <backup_dir> --dry-run` (default) — preview restore

Shows what would be restored without writing anything:

```powershell
uv run sf backup restore backups/20261115T103000Z --dry-run
```

### `sf backup restore <backup_dir> --no-dry-run` — perform restore

Restores files atomically to their original locations after verifying the manifest. Existing files are overwritten only after checksum verification passes.

```powershell
uv run sf backup restore backups/20261115T103000Z --no-dry-run
```

**Safety:** Restore verifies the manifest before writing any file. If any checksum mismatches, the restore aborts with an error and writes nothing.

## 5. Retention

### `sf backup retention <backup_dir> --keep 5` — prune old backups

Keeps only the N most recent backups (default 5), deletes older ones (manifest + all files).

```powershell
uv run sf backup retention backups/ --keep 5
```

## 6. Disaster Recovery Scenarios

### Scenario A: Corrupt model artifact

Symptom: `sf signal generate` fails with `CorruptArtifactError` or checksum mismatch on load.

Recovery:
1. List backups: `sf backup list backups/`
2. Restore the model artifact from the most recent good backup:
   `sf backup restore backups/<good_backup> --no-dry-run`
3. Verify: `uv run sf signal generate --model models/artifacts/<model_id> --snapshot data/...`

### Scenario B: Corrupt paper trading state

Symptom: `sf paper run` reports inconsistent account hash or ledger chain break.

Recovery:
1. Stop: do not continue paper trading.
2. Restore paper state from backup:
   `sf backup restore backups/<good_backup> --no-dry-run`
3. Verify health: `uv run sf health` (checks `paper_account` component)

### Scenario C: Missing/corrupt config

Symptom: `uv run sf` fails with `FileNotFoundError` or pydantic `ValidationError`.

Recovery:
1. Restore `config/default.toml` from backup.
2. Verify: `uv run sf config-show`

### Scenario D: Full data loss (registry + snapshots + paper state)

Recovery:
1. Restore from the most recent complete backup:
   `sf backup restore backups/<latest> --no-dry-run`
2. Verify all components: `uv run sf health`
3. Verify model registry: `uv run sf model list`
4. Verify data snapshots: `uv run sf data list`

## 7. Backup Schedule Recommendation

| What | Frequency | Retention |
|---|---|---|
| Model artifacts | After every training run | 5 backups |
| Data snapshots | After every fetch | 5 backups |
| Paper trading state | After every `sf paper run` cycle | 10 backups |
| Full system | Daily (manual) | 5 backups |

No automated scheduler exists in Phase 10; backups are triggered manually via `sf backup`.

## 8. Safety Constraints

- **No live orders:** Backup/restore never places orders or sends network requests.
- **No retraining:** Restore does not retrain models; it restores frozen artifacts.
- **Atomic writes:** All file writes use tempfile + `os.replace`; `.tmp` leftovers are cleaned on failure.
- **Checksum verification:** Every restored file is verified against its manifest SHA-256 before being considered restored.
- **Dry-run default:** `sf backup restore` defaults to `--dry-run`; `--no-dry-run` is required to write.
- **Read-only sources:** Backup reads source files only; it never mutates the source directories.