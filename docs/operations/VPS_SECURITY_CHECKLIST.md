# SignalForge VPS Security Checklist (Q5.2 Epoch 3)

## Purpose

This checklist verifies Ubuntu 26.04 VPS deployment security before API is considered operational and before any collector timer is enabled. Every item must be checked. Do not expose API publicly or enable timers if any required item fails.

## A. Host/network security

| # | Check | Command | Expected | Initials |
|---|-------|---------|----------|----------|
| A1 | UFW default deny incoming | `sudo ufw status verbose` | `Default: deny (incoming)` | |
| A2 | SSH allowed | `sudo ufw status verbose` | `22/tcp ALLOW IN` (or approved port) | |
| A3 | API 8000 not public | `sudo ufw status verbose` | no `8000/tcp ALLOW IN` | |
| A4 | API loopback bound | `docker compose ps` | `127.0.0.1:8000->8000/tcp` | |
| A5 | No public compose port | `docker compose config` | `host_ip: 127.0.0.1` | |

## B. Container security

| # | Check | Command | Expected | Initials |
|---|-------|---------|----------|----------|
| B1 | Non-root user | `docker inspect --format='{{.Config.User}}' signalforge-api` | `10001:10001`/appuser | |
| B2 | Read-only rootfs | `docker compose config` | `read_only: true` | |
| B3 | No new privileges | `docker compose config` | `no-new-privileges:true` | |
| B4 | Capabilities dropped | `docker compose config` | `cap_drop: [ALL]` | |
| B5 | tmpfs /tmp | `docker compose config` | `tmpfs: [/tmp]` | |
| B6 | Restart policy none | `docker compose config` | `restart: 'no'` | |
| B7 | Collector profile-gated | `docker compose config --services` / `--profile q5-collector config --services` | collector absent by default, present only with profile | |
| B8 | Prospective mount read-only in API | `docker compose config` | `.../data/prospective/...:ro` on api | |

## C. Secrets/auth

| # | Check | Command | Expected | Initials |
|---|-------|---------|----------|----------|
| C1 | `.env` not in git | `git status --short -- .env` | no tracked/staged `.env` | |
| C2 | `.env` permissions | `ls -l .env` | `-rw-------` | |
| C3 | Auth required | `docker compose config` | `SIGNALFORGE_AUTH_REQUIRED=1` | |
| C4 | Unauth rejected | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health` | `401` | |
| C5 | Auth accepted | `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/health` | `200` | |
| C6 | No token baked into image | `docker history signalforge:local` | no token value | |

## D. Epoch 3 governance and data integrity

| # | Check | Command | Expected | Initials |
|---|-------|---------|----------|----------|
| D1 | Freeze ID | `jq -r .freeze_id docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json` | `freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f` | |
| D2 | Collection ID | `jq -r .collection_id docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json` | `collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d` | |
| D3 | Collection start null before anchor | `jq -r .collection_started_at_utc docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json` | `null` until first accepted observation establishes anchor | |
| D4 | Collection auth | `jq '.collection_authorized,.scheduler_authorized' docs/validation/approvals/collection-epoch3-authorization.json` | `true`, `false` | |
| D5 | Scheduler auth independent | `jq '.collection_authorized,.scheduler_authorized' docs/validation/approvals/scheduler-epoch3-authorization.json` | `true`, `true` and other gates false | |
| D6 | Thresholds unchanged | review manifests/code | 168 obs, 95% coverage, 7 days, 900s latency, 300s skew | |
| D7 | No historical backfill/fabrication | review collector/storage behavior | no backfill, anchor from actual ingestion event only | |
| D8 | Epoch 2 preserved | `git status -- docs/validation/*EPOCH2* docs/validation/approvals/*epoch2*` | unchanged/append-only supersession only | |

## E. systemd and timer safety

| # | Check | Command | Expected | Initials |
|---|-------|---------|----------|----------|
| E1 | Service verify | `systemd-analyze verify /etc/systemd/system/signalforge-collector.service` | pass | |
| E2 | Timer verify | `systemd-analyze verify /etc/systemd/system/signalforge-collector@.timer` | pass | |
| E3 | Primary schedule | `systemd-analyze calendar --iterations=3 '*-*-* *:08:00 UTC'` | next runs at :08 UTC | |
| E4 | Retry schedule | `systemd-analyze calendar --iterations=3 '*-*-* *:12:00 UTC'` | next runs at :12 UTC | |
| E5 | No catch-up | `systemctl cat signalforge-collector@08.timer` | `Persistent=false`, `AccuracySec=1s` | |
| E6 | No overlap/repeated retry | service+status logic | oneshot; :12 skips if `latest_status.json` shows slot accepted by :08 | |
| E7 | Timers not enabled by default | `systemctl list-timers --all \| grep signalforge-collector \|\| true` | empty unless explicitly authorized | |
| E8 | Correct path/user | `systemctl cat signalforge-collector.service` | `/home/ubuntu/DM-Crypto-Quant-Signal`, `User=ubuntu`, `Group=docker` | |

## F. Trading/safety disabled

| # | Check | Command | Expected | Initials |
|---|-------|---------|----------|----------|
| F1 | Paper trading disabled | review config/governance | `paper_trading_authorized=false` | |
| F2 | Strategy evaluation disabled | review config/governance | `strategy_evaluation_authorized=false` | |
| F3 | Model promotion disabled | review config/governance | `ai_can_promote=false` | |
| F4 | Live trading disabled | review config/governance | `LIVE_TRADING_AUTHORIZED=false` | |
| F5 | No trading endpoints/orders | code/API review | no live order placement | |

## G. Sign-off

All required items verified: ____________________  Date (UTC): __________

Deployer: ____________________  Reviewer: ____________________
