# SignalForge VPS Deployment Runbook (Q5.2 Epoch 3)

## Purpose and scope

This runbook deploys SignalForge on **AWS Ubuntu 26.04** for:

- read-only API + dashboard serving (containerized, loopback-only)
- explicitly gated Q5.2 Epoch 3 prospective collector (`q5-collector` Docker profile) under existing governance

**Not activated by this runbook:** live trading, real-money orders, paper trading, strategy evaluation, model retraining/tuning/calibration/promotion, scheduler auto-enable, production/public exposure. Governance remains `NO_STRATEGY_ELIGIBLE`, `PRODUCTION_READY=false`, `LIVE_TRADING_AUTHORIZED=false`, `paper_trading_authorized=false`, `strategy_evaluation_authorized=false`.

**Epoch 3 identifiers (must match exactly):**

- Freeze: `freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f` (`docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json`)
- Collection: `collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d` (`docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json`, `collection_started_at_utc: null`)
- Collection auth: `docs/validation/approvals/collection-epoch3-authorization.json` (`collection_authorized=true`, `scheduler_authorized=false`)
- Scheduler auth: `docs/validation/approvals/scheduler-epoch3-authorization.json` (`scheduler_authorized=true`, other gates false)
- Supersession: `docs/validation/EPOCH2_SUPERSESSION_RECORD.json` (append-only; Epoch 2 preserved)

**Immutable thresholds:** 168 observations, 95% coverage, 7-day minimum elapsed period from first accepted observation, 900s max accepted observation latency, 300s max clock skew. Zero accepted observations => `NOT_STARTED`; first accepted observation establishes immutable `collection_start_anchor.json`.

## Prerequisites

- AWS Ubuntu 26.04 VPS, SSH access, sudo.
- Docker Engine 24+ and Docker Compose v2.
- UFW.
- Repo path: `/home/ubuntu/DM-Crypto-Quant-Signal`.
- Service account: `ubuntu`, in `docker` group.
- No secrets in git. `.env` stays local.

## 1. Host preparation

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y ca-certificates curl gnupg lsb-release ufw git

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw deny 8000/tcp
sudo ufw enable
sudo ufw status verbose
```

Install Docker Engine per Docker's Ubuntu instructions, then:

```bash
sudo usermod -aG docker ubuntu
newgrp docker
docker --version
docker compose version
```

## 2. Clone or fast-forward update (preserve `.env`)

```bash
set -euo pipefail
REPO=/home/ubuntu/DM-Crypto-Quant-Signal
if [ ! -d "$REPO/.git" ]; then
  git clone https://github.com/<org>/DM-Crypto-Quant-Signal.git "$REPO"
fi
cd "$REPO"

# Preserve local secrets/config; never commit .env
if [ -f .env ]; then cp .env .env.backup.$(date -u +%Y%m%dT%H%M%SZ); fi

git fetch origin
git checkout main
git pull --ff-only origin main

# Restore .env if needed (do not overwrite from git)
if [ -f .env.backup.* ]; then ls -1t .env.backup.* | head -n1 | xargs -I{} cp {} .env; fi
test -f .env
```

If `git pull --ff-only` fails, stop and resolve manually. Do not force-push, reset, or rewrite history.

## 3. Runtime `.env` (preserve, never expose)

```bash
cd /home/ubuntu/DM-Crypto-Quant-Signal
# Create only if missing; keep existing .env.
test -f .env || cat > .env <<'ENV'
SIGNALFORGE_AUTH_REQUIRED=1
SIGNALFORGE_API_TOKEN=<generate-strong-random-token>
ENV
chmod 600 .env
```

Generate token locally on VPS: `openssl rand -hex 32`. Never print/store token in logs, git, or chat.

## 4. Docker build and API-only deployment

```bash
cd /home/ubuntu/DM-Crypto-Quant-Signal
mkdir -p data/prospective/binance/spot/BTC/USDT/1h logs reports/q5-epoch3 backups
sudo chown -R 10001:10001 data/prospective logs reports || true

docker compose build api
docker compose up -d api
docker compose ps
```

API must bind loopback only (`127.0.0.1:8000:8000`). Do not expose publicly without explicit security review.

Verify API (token from `.env`, not echoed):

```bash
set -a; source .env; set +a
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/health  # expect 401
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/health  # expect 200
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/api/v1/q5/prospective/governance | python3 -m json.tool
```

## 5. Explicit collector profile (no normal startup)

Normal `docker compose up -d api` must **not** start collector. Collector runs only with profile `q5-collector`:

```bash
docker compose --profile q5-collector config --services | grep -x collector
docker compose --profile q5-collector run --rm collector --help
```

Manual one-shot (only when explicitly authorized; do not start routine collection/timers casually):

```bash
docker compose --profile q5-collector run --rm collector
```

Collector hardening: read-only rootfs, `no-new-privileges`, `cap_drop: ALL`, `user: 10001:10001`, `restart: "no"`, no private exchange creds/trading capability, writable only `data/prospective`, `logs`, `reports`.

## 6. First-observation anchor verification

Before first accepted observation, status must be `NOT_STARTED` and `collection_started_at_utc` null/no valid anchor. After first accepted observation, anchor is immutable write-once:

```bash
cd /home/ubuntu/DM-Crypto-Quant-Signal
# Read-only status check
uv run python scripts/run_q5_prospective_collection.py \
  --collect-status \
  --freeze-manifest docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json \
  --collection-manifest docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json \
  --storage-root data/prospective \
  --approvals-dir docs/validation/approvals \
  --output-dir reports/q5-epoch3

# Anchor file appears only after first accepted observation
test -f data/prospective/binance/spot/BTC/USDT/1h/collection_start_anchor.json && \
  python3 -m json.tool < data/prospective/binance/spot/BTC/USDT/1h/collection_start_anchor.json || \
  echo "anchor not established (NOT_STARTED expected before first accepted observation)"
```

Do not backdate/fabricate/reset anchor. If anchor is corrupted/mismatched, stop and investigate; do not overwrite.

## 7. systemd installation and validation (do not enable timers during dev)

Files: `docker/systemd/signalforge-collector@.service`, `docker/systemd/signalforge-collector@.timer`, `docker/systemd/install-systemd.sh`, `docker/systemd/monitor-collector.sh`, `docker/systemd/uninstall-systemd.sh`.

Install without enabling timers:

```bash
cd /home/ubuntu/DM-Crypto-Quant-Signal
sudo bash docker/systemd/install-systemd.sh /home/ubuntu/DM-Crypto-Quant-Signal ubuntu
```

Validate (required on Ubuntu 26.04):

```bash
systemd-analyze verify /etc/systemd/system/signalforge-collector@.service
systemd-analyze verify /etc/systemd/system/signalforge-collector@.timer
systemd-analyze calendar --iterations=5 '*-*-* *:08:00 UTC'
systemd-analyze calendar --iterations=5 '*-*-* *:12:00 UTC'
systemctl cat signalforge-collector@08.service
systemctl cat signalforge-collector@08.timer
systemctl cat signalforge-collector@12.timer
systemctl list-timers --all | grep signalforge-collector || true
```

Expected: timer `@08` triggers service `@08`, timer `@12` triggers service `@12`; `OnCalendar=*-*-* *:%i:00 UTC`, `Persistent=false`, `AccuracySec=1s`, `Unit=signalforge-collector@%i.service`, no catch-up, `TimeoutStartSec=900`, `User=ubuntu`, `Group=docker`, `WorkingDirectory=/home/ubuntu/DM-Crypto-Quant-Signal`, `ExecStart` uses `docker compose --profile q5-collector run --rm collector ... --require-scheduler-authorization --scheduled-minute %i`; retry at :12 runs only when the :08 attempt for the same hourly slot did not accept the scheduled candle. Timers must **not** be enabled/started unless explicitly authorized with `ENABLE_TIMERS=1` during install or explicit `systemctl enable --now ...`.

## 8. Enable timers only with explicit authorization

```bash
# Only after explicit human authorization and scheduler auth record is valid.
sudo systemctl enable --now signalforge-collector@08.timer
sudo systemctl enable --now signalforge-collector@12.timer
systemctl list-timers --all | grep signalforge-collector
```

Disable immediately if needed:

```bash
sudo systemctl disable --now signalforge-collector@08.timer
sudo systemctl disable --now signalforge-collector@12.timer
```

## 9. Monitoring and troubleshooting

```bash
cd /home/ubuntu/DM-Crypto-Quant-Signal
bash docker/systemd/monitor-collector.sh /home/ubuntu/DM-Crypto-Quant-Signal
journalctl -u 'signalforge-collector@*.service' -n 100 --no-pager
docker compose logs --tail=100 api
docker compose --profile q5-collector ps -a
```

Status files: `reports/q5-epoch3/latest_status.json`, `latest_continuity.json`, `monitor_state.json`. Treat HTTP 500/invalid JSON as unavailable; do not fabricate.

## 10. Backup, recovery, disable, uninstall

See `docs/operations/BACKUP_AND_RECOVERY_RUNBOOK.md`. Quick controls:

```bash
# Disable timers
sudo systemctl disable --now signalforge-collector@08.timer signalforge-collector@12.timer
# Uninstall systemd units
sudo bash docker/systemd/uninstall-systemd.sh
```

## 11. Safe rollback

```bash
cd /home/ubuntu/DM-Crypto-Quant-Signal
git fetch origin
git log --oneline --decorate -5
# Roll back to previous known-good commit (example only; replace with approved commit)
git checkout main
git reset --hard <previous-good-commit>
docker compose build api
docker compose up -d api
# Do not re-enable timers until revalidated.
```

Use rollback only with explicit approval; never rewrite remote history. Preserve `.env` and backups before rollback.

## 12. Verification checklist

- [ ] `git pull --ff-only origin main` succeeded; `.env` preserved.
- [ ] `docker compose ps` shows `signalforge-api` healthy; API loopback-only.
- [ ] Auth fail-closed: unauthenticated 401, authenticated 200.
- [ ] Collector profile is explicit (`q5-collector`), not started by default API deploy.
- [ ] Epoch 3 freeze/collection IDs and approvals match.
- [ ] Anchor is absent/`NOT_STARTED` before first accepted observation; immutable after first acceptance.
- [ ] systemd `verify` and `calendar` checks pass on Ubuntu 26.04.
- [ ] Timers not enabled unless explicitly authorized.
- [ ] No paper trading, strategy evaluation, model promotion, or live trading enabled.
