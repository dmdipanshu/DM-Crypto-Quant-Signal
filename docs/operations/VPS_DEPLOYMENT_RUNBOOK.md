# SignalForge VPS Deployment Runbook

## Purpose and scope

This runbook covers deployment of SignalForge on a Linux VPS for **read-only API and dashboard serving only**.

- Scope: containerized read-only API, dashboard, and health endpoints on a Linux VPS.
- Out of scope / not activated: live trading, real-money orders, scheduler activation, data collection activation, strategy evaluation, model retraining/tuning, paper trading, or any production trading use.
- Governance preserved (Q4–Q26): `NO_STRATEGY_ELIGIBLE`, `collect-52cefa0dda64`, `freeze-aaa4dd57057a`, `scheduler_authorized=false`, `PRODUCTION_READY=false`, `LIVE_TRADING_AUTHORIZED=false`. This deployment does not change governance state or authorize out-of-scope capabilities.
- Q5.2 prospective validation remains a separate process. It must not block VPS deployment.

**Do not purchase a VPS, create cloud resources, or deploy without explicit approval.**

---

## Prerequisites

- A Linux VPS (Ubuntu 22.04 LTS or later recommended) with root or sudo access.
- At least 2 GB RAM, 2 CPU cores, 20 GB disk.
- Docker Engine 24+ and Docker Compose v2 installed.
- UFW or firewalld for host firewall management.
- A domain name or static IP (optional; loopback-only exposure is the default).

---

## 1. Host preparation (run on the VPS)

```bash
# Update system packages
sudo apt-get update
sudo apt-get upgrade -y

# Install dependencies
sudo apt-get install -y ca-certificates curl gnupg lsb-release ufw

# Configure UFW: deny all incoming by default, allow only SSH and loopback API
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp              # SSH (adjust if using non-standard SSH port)
sudo ufw deny 8000/tcp             # Do NOT expose API port publicly
sudo ufw enable

# Verify UFW status
sudo ufw status verbose
```

---

## 2. Install Docker Engine

```bash
# Add Docker's official GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Add your user to the docker group (avoid sudo for docker commands)
sudo usermod -aG docker $USER
newgrp docker

# Verify installation
docker --version
docker compose version
docker info
```

---

## 3. Deploy SignalForge on the VPS

Copy the SignalForge repository to the VPS (via `scp`, `rsync`, or git clone — do not copy secrets):

```bash
# On your local machine, copy the repo (excluding secrets and caches)
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.env' --exclude='.git' \
  /path/to/DM-Crypto-Quant-Signal/ user@<vps-ip>:~/signalforge/

# On the VPS
cd ~/signalforge
```

Create a runtime environment file (do **not** commit this to version control):

```bash
# ~/signalforge/.env.vps  (create manually, never commit)
SIGNALFORGE_AUTH_REQUIRED=1
SIGNALFORGE_API_TOKEN=<generate-a-strong-random-token>
```

Generate a strong API token:

```bash
openssl rand -hex 32
```

Set restrictive permissions on the env file:

```bash
chmod 600 ~/signalforge/.env.vps
```

Create required host directories:

```bash
mkdir -p ~/signalforge/data ~/signalforge/logs ~/signalforge/reports ~/signalforge/backups
chmod 700 ~/signalforge/data ~/signalforge/backups
```

---

## 4. Start the API service

```bash
cd ~/signalforge

# Export environment variables for compose
export SIGNALFORGE_AUTH_REQUIRED=1
export SIGNALFORGE_API_TOKEN="<your-generated-token>"

# Build and start the API service (loopback-only port mapping)
docker compose build api
docker compose up -d api

# Verify container is running
docker compose ps

# Verify health (from the VPS, loopback only)
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/health
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/api/v1/health
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/api/v1/readiness

# Verify dashboard assets
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/dashboard/monitor/
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/dashboard/monitor/monitor.css
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/dashboard/monitor/monitor.js

# Verify paper endpoints
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/api/v1/paper/status
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/api/v1/paper/state

# Verify auth is fail-closed (should return 401)
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/health
```

Expected results:
- All authenticated requests: `200`
- Unauthenticated `/health`: `401`

---

## 5. Access pattern (loopback-only)

The compose file maps port `127.0.0.1:8000:8000`, so the API is only reachable from the VPS itself. Do **not** change this to `0.0.0.0:8000:8000` without an explicit security review.

If you need to access the API from another machine, use an SSH tunnel:

```bash
# From your local machine
ssh -L 8000:127.0.0.1:8000 user@<vps-ip>

# Then access locally
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:8000/health
```

Or use a reverse proxy (nginx/Traefik) with TLS and authentication — this requires explicit security review and is not covered by this runbook.

---

## 6. Health checks

```bash
# Container health status
docker inspect --format='{{.State.Health.Status}}' signalforge-api

# Compose service status
docker compose ps

# Application health endpoint
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/health | python -m json.tool

# Component-level health
curl -s -H "Authorization: Bearer $SIGNALFORGE_API_TOKEN" http://127.0.0.1:8000/api/v1/health/components | python -m json.tool
```

---

## 7. Logs

```bash
# Follow API logs
docker compose logs -f api

# Last 100 lines
docker compose logs --tail=100 api

# Logs since a specific time
docker compose logs --since="2026-01-01T00:00:00Z" api
```

---

## 8. Restart

```bash
# Restart the API service
docker compose restart api

# Wait for healthy
sleep 15
docker inspect --format='{{.State.Health.Status}}' signalforge-api

# Full stop and start
docker compose stop api
docker compose start api
```

---

## 9. Shutdown

```bash
# Stop the API service (container retained)
docker compose stop api

# Tear down services (preserves bind-mounted host data)
docker compose down
```

---

## 10. Rebuild

```bash
# Rebuild without cache
docker compose build --no-cache api
docker compose up -d api
```

---

## 11. Verification checklist

Before considering the deployment operational:

- [ ] `docker compose ps` shows `signalforge-api` as `healthy`
- [ ] `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/health` returns `200`
- [ ] `curl http://127.0.0.1:8000/health` (no token) returns `401`
- [ ] `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/v1/readiness` returns `200`
- [ ] `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/dashboard/monitor/` returns `200`
- [ ] `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/dashboard/monitor/monitor.css` returns `200`
- [ ] `curl -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/v1/paper/status` returns `200`
- [ ] `docker inspect --format='{{.Config.User}}' signalforge-api` shows `appuser` (non-root)
- [ ] UFW denies port 8000 from external sources
- [ ] `.env.vps` has `chmod 600` permissions and is not in git

---

## Notes

- This deployment is for engineering/staging validation only and does not imply production readiness or trading authorization.
- Keep all runtime secrets out of source control and images; use runtime injection only.
- No scheduler, collector, strategy evaluation, paper trading, or live trading services are created or activated.
- Q5.2 prospective validation remains a separate process and must not block deployment.