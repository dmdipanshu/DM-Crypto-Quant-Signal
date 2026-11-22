# SignalForge VPS Security Checklist

## Purpose

This checklist verifies that a SignalForge VPS deployment is secured before the API service is considered operational. Every item must be checked and initialed. Do not proceed to production use if any item is unchecked.

---

## A. Network security

| # | Check | Command | Expected result | Initials |
|---|-------|---------|-----------------|----------|
| A1 | UFW default deny incoming | `sudo ufw status verbose` | `Default: deny (incoming)` | |
| A2 | SSH port allowed | `sudo ufw status verbose` | `22/tcp ALLOW IN` (or custom SSH port) | |
| A3 | API port 8000 NOT publicly exposed | `sudo ufw status verbose` | No `8000/tcp ALLOW IN` rule | |
| A4 | API accessible only on loopback | `docker compose ps` | Port `127.0.0.1:8000->8000/tcp` | |
| A5 | No public port mapping | `docker compose config` | `host_ip: 127.0.0.1` in ports section | |

---

## B. Container security

| # | Check | Command | Expected result | Initials |
|---|-------|---------|-----------------|----------|
| B1 | Container runs as non-root | `docker inspect --format='{{.Config.User}}' signalforge-api` | `appuser` (or uid 10001) | |
| B2 | Read-only rootfs | `docker compose config` | `read_only: true` | |
| B3 | No new privileges | `docker compose config` | `no-new-privileges:true` | |
| B4 | All capabilities dropped | `docker compose config` | `cap_drop: [ALL]` | |
| B5 | /tmp is tmpfs (writable scratch) | `docker compose config` | `tmpfs: [/tmp]` | |
| B6 | Restart policy is "no" (no auto-restart) | `docker compose config` | `restart: 'no'` | |
| B7 | Healthcheck configured | `docker compose config` | `healthcheck` section present | |
| B8 | Container health is healthy | `docker inspect --format='{{.State.Health.Status}}' signalforge-api` | `healthy` | |

---

## C. Authentication and authorization

| # | Check | Command | Expected result | Initials |
|---|-------|---------|-----------------|----------|
| C1 | Auth required by default | `docker compose config` | `SIGNALFORGE_AUTH_REQUIRED: "1"` | |
| C2 | Valid token returns 200 | `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/health` | `200` | |
| C3 | Missing token returns 401 | `curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health` | `401` | |
| C4 | Invalid token returns 401 | `curl -o /dev/null -w "%{http_code}" -H "Authorization: Bearer wrong" http://127.0.0.1:8000/health` | `401` | |
| C5 | Token not baked into image | `docker history signalforge:local` | No `SIGNALFORGE_API_TOKEN` in build steps | |
| C6 | Token file permissions | `ls -l .env.vps` | `-rw-------` (600) | |

---

## D. Secrets and credentials

| # | Check | Command | Expected result | Initials |
|---|-------|---------|-----------------|----------|
| D1 | No .env in git | `git status --short .env` | No output (untracked/ignored) | |
| D2 | .dockerignore excludes secrets | `grep -E '^\.env' .dockerignore` | `.env` and `.env.*` listed | |
| D3 | No API keys in source | `grep -r "api_key" src/ --include="*.py" -l` | No output (or only env var names) | |
| D4 | Token generated with sufficient entropy | `openssl rand -hex 32` | 64 hex chars (256 bits) | |

---

## E. Governance flags (must remain unchanged)

| # | Flag | Expected | Source |
|---|------|----------|--------|
| E1 | `PRODUCTION_READY` | `false` | `docs/validation/approvals/q5_2_collection_continuation_approval.json` |
| E2 | `LIVE_TRADING_AUTHORIZED` | `false` | Same approval record |
| E3 | `paper_trading_authorized` | `false` | Same approval record |
| E4 | `strategy_evaluation_authorized` | `false` | Same approval record |
| E5 | `ai_can_promote` | `false` | Same approval record |
| E6 | `Q5_2_PROSPECTIVE_VALIDATED` | `false` | Same approval record |
| E7 | `scheduler_authorized` | `false` | Same approval record |

Verify with:

```bash
python -c "import json; g=json.load(open('docs/validation/approvals/q5_2_collection_continuation_approval.json')); print(json.dumps(g['expected_final_status'], indent=2)); print(json.dumps(g['q4_governance_preserved'], indent=2))"
```

---

## F. Data integrity

| # | Check | Command | Expected result | Initials |
|---|-------|---------|-----------------|----------|
| F1 | Paper account checksum valid | `uv run sf paper verify` | `ok: true` | |
| F2 | Paper account data unchanged | Compare checksum before/after | Identical | |
| F3 | Prospective data read-only | `docker compose config` | `./data/prospective:/app/data/prospective:ro` | |
| F4 | Model artifacts read-only | `docker compose config` | `./models/artifacts:/app/models/artifacts:ro` | |

---

## G. No trading activation

| # | Check | Command | Expected result | Initials |
|---|-------|---------|-----------------|----------|
| G1 | No scheduler service | `docker compose config` | No scheduler service defined | |
| G2 | No collector service | `docker compose config` | No collector service defined | |
| G3 | No live trading endpoint | `curl http://127.0.0.1:8000/` | No trading routes exposed | |
| G4 | `trading_enabled: false` | `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/health` | `"trading_enabled":false` | |
| G5 | No real exchange orders | `grep -r "place_order\|submit_order\|create_order" src/ --include="*.py"` | No output | |

---

## Sign-off

All checklist items verified: ____________________  Date: __________

Deployer: ____________________  Reviewer: ____________________

---

## Remediation

If any item fails:

1. Do **not** start or expose the API service.
2. Fix the failing item.
3. Re-verify the failed item.
4. Re-run the full checklist.