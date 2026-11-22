# SignalForge Backup and Recovery Runbook

## Purpose

This runbook covers backup and recovery procedures for SignalForge runtime data on a Linux VPS. It covers:

- Paper trading account state (`data/paper/account.json`)
- Logs (`data/logs/`)
- Reports (`data/reports/`)
- Configuration (`config/`)
- Compose files and Dockerfiles
- Governance and approval records (`docs/validation/approvals/`)

## What is NOT backed up by this procedure

- Q5.2 prospective collection data (`data/prospective/`) — this is read-only evidence; back up separately if required by governance.
- Docker images — rebuild from source using `docker compose build`.
- Container instances — recreate from `docker compose up -d`.

---

## Prerequisites

- The SignalForge repository is deployed at `~/signalforge/` on the VPS.
- Docker and Docker Compose are installed.
- You have write access to `~/signalforge/backups/`.

---

## 1. Backup procedure

### 1.1 Full backup (all runtime data)

Run from the VPS:

```bash
cd ~/signalforge

# Stop the API to ensure consistent state
docker compose stop api

# Create a timestamped backup directory
BACKUP_DIR="backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

# Copy runtime data (paper account, logs, reports)
cp -a data/paper "$BACKUP_DIR/"
cp -a data/logs "$BACKUP_DIR/"
cp -a data/reports "$BACKUP_DIR/"

# Copy configuration
cp -a config "$BACKUP_DIR/"

# Copy governance/approval records
cp -a docs/validation/approvals "$BACKUP_DIR/"

# Copy compose and Docker files
cp compose.yaml Dockerfile .dockerignore "$BACKUP_DIR/"

# Record the paper account checksum for integrity verification
python -c "import json,hashlib; d=json.load(open('data/paper/account.json')); print(d['checksum'])" > "$BACKUP_DIR/paper-checksum.txt"

# Record the container image digest
docker inspect signalforge:local --format='{{.RepoDigests}}' > "$BACKUP_DIR/image-digest.txt" 2>/dev/null || true

# Create a compressed archive
tar -czf "$BACKUP_DIR.tar.gz" -C backups "$(basename "$BACKUP_DIR")"
rm -rf "$BACKUP_DIR"

# Restart the API
docker compose start api

echo "Backup created: $BACKUP_DIR.tar.gz"
ls -la backups/
```

### 1.2 Paper account only backup

```bash
cd ~/signalforge
BACKUP_DIR="backups/paper-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -a data/paper "$BACKUP_DIR/"
python -c "import json,hashlib; d=json.load(open('data/paper/account.json')); print(d['checksum'])" > "$BACKUP_DIR/paper-checksum.txt"
echo "Paper account backup: $BACKUP_DIR"
```

### 1.3 Automated backup (cron example)

Add to root crontab (`sudo crontab -e`):

```bash
# Daily backup at 02:00 UTC
0 2 * * * cd /home/user/signalforge && /home/user/signalforge/scripts/backup.sh >> /var/log/signalforge-backup.log 2>&1
```

Create `scripts/backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /home/user/signalforge || exit 1

BACKUP_DIR="backups/auto-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

docker compose stop api
cp -a data/paper "$BACKUP_DIR/"
cp -a data/logs "$BACKUP_DIR/"
cp -a data/reports "$BACKUP_DIR/"
cp -a config "$BACKUP_DIR/"
cp -a docs/validation/approvals "$BACKUP_DIR/"
cp compose.yaml Dockerfile .dockerignore "$BACKUP_DIR/"
python -c "import json,hashlib; d=json.load(open('data/paper/account.json')); print(d['checksum'])" > "$BACKUP_DIR/paper-checksum.txt"
docker compose start api

tar -czf "$BACKUP_DIR.tar.gz" -C backups "$(basename "$BACKUP_DIR")"
rm -rf "$BACKUP_DIR"

# Retain only the last 7 daily backups
ls -t backups/auto-*.tar.gz | tail -n +8 | xargs -r rm -f

echo "$(date -Iseconds) Backup completed: $BACKUP_DIR.tar.gz"
```

Make it executable:

```bash
chmod +x scripts/backup.sh
```

---

## 2. Recovery procedure

### 2.1 Restore from backup

```bash
cd ~/signalforge

# Stop the API
docker compose stop api

# Select the backup to restore (replace with actual backup name)
BACKUP_FILE="backups/20260115-133000.tar.gz"

# Extract to a temporary location
RESTORE_TMP="/tmp/signalforge-restore"
rm -rf "$RESTORE_TMP"
mkdir -p "$RESTORE_TMP"
tar -xzf "$BACKUP_FILE" -C "$RESTORE_TMP"

# Find the extracted backup directory
BACKUP_DIR=$(ls -d "$RESTORE_TMP"/*/ | head -1)

# Verify paper account checksum before restoring
ORIGINAL_CHECKSUM=$(cat "$BACKUP_DIR/paper-checksum.txt")
RESTORED_CHECKSUM=$(python -c "import json,hashlib; d=json.load(open('$BACKUP_DIR/data/paper/account.json')); print(d['checksum'])")
if [ "$ORIGINAL_CHECKSUM" != "$RESTORED_CHECKSUM" ]; then
    echo "ERROR: Paper account checksum mismatch!"
    echo "  Expected: $ORIGINAL_CHECKSUM"
    echo "  Got:      $RESTORED_CHECKSUM"
    exit 1
fi

# Restore data directories
rm -rf data/paper data/logs data/reports
cp -a "$BACKUP_DIR/data/paper" data/
cp -a "$BACKUP_DIR/data/logs" data/
cp -a "$BACKUP_DIR/data/reports" data/

# Restore configuration
rm -rf config
cp -a "$BACKUP_DIR/config" config/

# Restore governance records
cp -a "$BACKUP_DIR/docs/validation/approvals" docs/validation/

# Clean up
rm -rf "$RESTORE_TMP"

# Start the API
docker compose start api

# Verify
sleep 10
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/health | python -m json.tool | head -20
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/api/v1/paper/status

echo "Recovery completed from $BACKUP_FILE"
```

### 2.2 Paper account only restore

```bash
cd ~/signalforge

docker compose stop api

BACKUP_DIR="backups/paper-20260115-133000"

# Verify checksum
ORIGINAL_CHECKSUM=$(cat "$BACKUP_DIR/paper-checksum.txt")
RESTORED_CHECKSUM=$(python -c "import json,hashlib; d=json.load(open('$BACKUP_DIR/data/paper/account.json')); print(d['checksum'])")
if [ "$ORIGINAL_CHECKSUM" != "$RESTORED_CHECKSUM" ]; then
    echo "ERROR: Paper account checksum mismatch!"
    exit 1
fi

rm -rf data/paper
cp -a "$BACKUP_DIR/data/paper" data/

docker compose start api
sleep 10
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/api/v1/paper/status

echo "Paper account recovery completed."
```

### 2.3 Full rebuild from source (disaster recovery)

If the VPS is lost entirely:

```bash
# 1. Provision a new VPS (see VPS_DEPLOYMENT_RUNBOOK.md)
# 2. Install Docker and Docker Compose
# 3. Copy the SignalForge repository to the VPS
# 4. Restore runtime data from backup (section 2.1)
# 5. Generate a new API token (do not reuse old tokens)
openssl rand -hex 32
# 6. Update .env.vps with the new token
# 7. Build and start
export SIGNALFORGE_AUTH_REQUIRED=1
export SIGNALFORGE_API_TOKEN="<new-token>"
docker compose build --no-cache api
docker compose up -d api
# 8. Verify health
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/health
```

---

## 3. Verification after recovery

```bash
cd ~/signalforge

# 1. Container is healthy
docker inspect --format='{{.State.Health.Status}}' signalforge-api

# 2. Health endpoint returns ok
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/health

# 3. Paper account checksum matches backup
python -c "import json,hashlib; d=json.load(open('data/paper/account.json')); payload={k:v for k,v in d.items() if k not in ('checksum','total_equity','realized_pnl','unrealized_pnl','total_pnl','max_drawdown_pct','win_rate')}; computed=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest(); print('checksum_ok=', d['checksum']==computed)"

# 4. Governance flags unchanged
python -c "import json; g=json.load(open('docs/validation/approvals/q5_2_collection_continuation_approval.json')); print(g['expected_final_status'])"

# 5. Read-only flags still false
python -c "import json; g=json.load(open('docs/validation/approvals/q5_2_collection_continuation_approval.json')); e=g['expected_final_status']; print('live_trading=', e['live_trading_authorized']); print('paper_trading=', e['paper_trading_authorized']); print('strategy_eval=', e['strategy_evaluation_authorized'])"
```

---

## 4. Backup retention policy

- Daily automated backups: retain 7 days.
- Manual backups: retain indefinitely (or per governance requirements).
- Test restore procedures quarterly to verify backup integrity.

---

## Notes

- Never back up `.env` files or API tokens to shared/unencrypted locations.
- Backup files contain governance evidence — protect them with appropriate access controls.
- Q5.2 prospective data is read-only evidence; consult governance before backing up or modifying.
- This runbook does not cover database backups (SignalForge uses file-based storage only).