# SignalForge Backup and Recovery Runbook (Q5.2 Epoch 3, Ubuntu 26.04)

## Purpose

Covers backup/recovery for SignalForge VPS deployment at `/home/ubuntu/DM-Crypto-Quant-Signal` on AWS Ubuntu 26.04. Includes API runtime data, Epoch 3 prospective evidence, governance artifacts, compose/systemd assets, and safe rollback. Never back up secrets to git or unencrypted shared locations.

## In scope

- `.env` (local only, secret) — backup encrypted/off-host; never commit.
- `data/prospective/binance/spot/BTC/USDT/1h` (Epoch 3 prospective evidence, anchor, ledger, manifests)
- `reports/q5-epoch3`
- `logs`
- `docs/validation` Epoch 3 manifests/approvals/supersession and immutable Epoch 2 records
- `compose.yaml`, `Dockerfile`, `docker/systemd/*`, `docs/operations/*`

## Out of scope / cautions

- Do not fabricate or rewrite prospective observations, anchors, manifests, or governance records.
- Do not restore old data over a newer immutable anchor without investigation.
- Docker images are rebuildable; backups focus on data/config/evidence.

## 1. Pre-backup safety

```bash
set -euo pipefail
cd /home/ubuntu/DM-Crypto-Quant-Signal
# Stop timers before maintenance backup if enabled
sudo systemctl disable --now signalforge-collector@08.timer signalforge-collector@12.timer || true
docker compose ps
```

## 2. Backup procedure

```bash
set -euo pipefail
cd /home/ubuntu/DM-Crypto-Quant-Signal
TS=$(date -u +%Y%m%dT%H%M%SZ)
BK=backups/signalforge-epoch3-$TS
mkdir -p "$BK"

# Preserve env separately and securely (do not include in git)
if [ -f .env ]; then install -m 600 .env "$BK/.env.local"; fi

# Runtime/evidence data
mkdir -p "$BK/data" "$BK/reports" "$BK/logs" "$BK/docs" "$BK/docker"
cp -a data/prospective "$BK/data/prospective"
cp -a reports/q5-epoch3 "$BK/reports/q5-epoch3" 2>/dev/null || true
cp -a logs "$BK/logs" 2>/dev/null || true

# Governance and deployment assets
cp -a docs/validation "$BK/docs/validation"
cp -a docs/operations "$BK/docs/operations"
cp -a docker/systemd "$BK/docker/systemd"
cp -a compose.yaml Dockerfile .dockerignore "$BK/"

# Checksums
( cd "$BK" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )

tar -C backups -czf "backups/signalforge-epoch3-$TS.tar.gz" "signalforge-epoch3-$TS"
sha256sum "backups/signalforge-epoch3-$TS.tar.gz" > "backups/signalforge-epoch3-$TS.tar.gz.sha256"
rm -rf "$BK"
ls -lh "backups/signalforge-epoch3-$TS.tar.gz"*
```

Store backup off-host with restricted access. Never upload `.env` or token-bearing files to public storage.

## 3. Recovery procedure

```bash
set -euo pipefail
cd /home/ubuntu/DM-Crypto-Quant-Signal
sudo systemctl disable --now signalforge-collector@08.timer signalforge-collector@12.timer || true
docker compose stop api || true

ARCHIVE=backups/signalforge-epoch3-<timestamp>.tar.gz
sha256sum -c "$ARCHIVE.sha256"
TMP=$(mktemp -d)
tar -xzf "$ARCHIVE" -C "$TMP"
RESTORE=$(find "$TMP" -maxdepth 1 -mindepth 1 -type d | head -n1)

# Restore evidence/runtime data cautiously
mkdir -p data reports logs docs docker
cp -a "$RESTORE/data/prospective" data/
cp -a "$RESTORE/reports/q5-epoch3" reports/ 2>/dev/null || true
cp -a "$RESTORE/logs" logs/ 2>/dev/null || true
cp -a "$RESTORE/docs/validation" docs/
cp -a "$RESTORE/docs/operations" docs/
cp -a "$RESTORE/docker/systemd" docker/
cp -a "$RESTORE/compose.yaml" "$RESTORE/Dockerfile" "$RESTORE/.dockerignore" ./

# Restore .env only if missing; prefer current secret.
if [ ! -f .env ] && [ -f "$RESTORE/.env.local" ]; then install -m 600 "$RESTORE/.env.local" .env; fi

sudo chown -R 10001:10001 data/prospective logs reports || true
docker compose build api
docker compose up -d api
rm -rf "$TMP"
```

## 4. Post-recovery verification

```bash
cd /home/ubuntu/DM-Crypto-Quant-Signal
set -a; source .env; set +a
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/health
uv run python scripts/run_q5_prospective_collection.py --collect-status \
  --freeze-manifest docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json \
  --collection-manifest docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json \
  --storage-root data/prospective --approvals-dir docs/validation/approvals --output-dir reports/q5-epoch3
```

Confirm Epoch 3 IDs unchanged, anchor hash/immutability intact, no fabricated observations, and no timers enabled unless explicitly authorized.

## 5. Disable and uninstall collector systemd

```bash
sudo systemctl disable --now signalforge-collector@08.timer signalforge-collector@12.timer || true
sudo bash /home/ubuntu/DM-Crypto-Quant-Signal/docker/systemd/uninstall-systemd.sh
systemctl list-timers --all | grep signalforge-collector || true
```

## 6. Safe rollback to previous commit

```bash
cd /home/ubuntu/DM-Crypto-Quant-Signal
cp .env .env.backup.rollback.$(date -u +%Y%m%dT%H%M%SZ)
git fetch origin
git checkout main
git log --oneline --decorate -5
# Explicitly approved previous good commit only:
git reset --hard <previous-good-commit>
docker compose build api
docker compose up -d api
# Revalidate before any timer enablement.
```

Never force-push/reset remote history. Do not enable timers after rollback until systemd validation and governance checks pass.

## 7. Recovery do-not-do

- Do not restore by overwriting immutable anchor with fabricated/backdated value.
- Do not enable timers during recovery validation.
- Do not change thresholds, IDs, or governance flags.
- Do not start paper trading, strategy evaluation, model promotion, or live trading.
