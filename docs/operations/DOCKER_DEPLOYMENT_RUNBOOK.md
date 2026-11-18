# SignalForge Docker Deployment Runbook (Q27)

## Purpose and scope

This runbook covers **Q27 Docker containerization for local/staging engineering use only** for SignalForge in `D:\DM-Crypto-Quant-Signal`.

- Scope: reproducible container builds and local/staging runtime for the read-only API and containerized test execution.
- Out of scope / not activated: external deployment, production hosting, scheduler activation, data collection activation, strategy evaluation, paper trading, live trading, or any real-money/order execution.
- Governance preserved (Q4–Q26): `NO_STRATEGY_ELIGIBLE`, `collect-52cefa0dda64`, `freeze-aaa4dd57057a`, `scheduler_authorized=false`, `PRODUCTION_READY=false`, `LIVE_TRADING_AUTHORIZED=false`. This containerization does not change governance state or authorize out-of-scope capabilities.

## Prerequisites

- Docker Engine or Docker Desktop with **Compose v2** (`docker compose`)
- Linux container support enabled
- Run commands from the project root: `D:\DM-Crypto-Quant-Signal`

Verify environment:

```bash
docker --version
docker compose version
docker info
```

`docker info` should complete without errors and indicate a running daemon with Linux container support.

## Architecture

`compose.yaml` defines two services:

- **`api`** (target: `runtime`, image: `signalforge:local`, container: `signalforge-api`)
  - Runs the FastAPI/read-only server via: `sf serve --host 0.0.0.0 --port 8000 --allow-remote`
  - Intended for local/staging inspection of read-only surfaces only.
- **`test`** (target: `test`, image: `signalforge:test`, container: `signalforge-test`, profile: `test`)
  - Runs `uv run ruff check . && uv run pytest -q` inside the container.

No scheduler, collector, or trading services are defined or activated.

## Build

Build the API/runtime image:

```bash
docker compose build api
```

Build the test image (profile-scoped):

```bash
docker compose --profile test build test
```

Rebuild without cache when dependencies or base layers must be refreshed:

```bash
docker compose build --no-cache api
```

## Run

Start the API service:

```bash
docker compose up -d api
```

Verify container state:

```bash
docker compose ps
```

Verify health/endpoints (host loopback only):

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/metrics
```

`/api/v1/health` and `/metrics` availability depends on the application routes; check the service logs if a specific endpoint is not exposed.

Run containerized tests (one-shot):

```bash
docker compose --profile test run --rm test
```

## Environment variables

- **`SIGNALFORGE_AUTH_REQUIRED`**: defaults to `1` (fail-closed) in `compose.yaml`.
- **`SIGNALFORGE_API_TOKEN`**: must be provided at runtime when auth is enforced. Do **not** bake tokens into images, do **not** commit secrets, and do **not** store credentials in the repository.

Provide values via the shell environment or a local, uncommitted compose override (e.g. `docker-compose.override.yml` / `compose.override.yaml`, both ignored by `.dockerignore`). Example (PowerShell):

```powershell
$env:SIGNALFORGE_AUTH_REQUIRED = "1"
$env:SIGNALFORGE_API_TOKEN = "<provide-at-runtime>"
docker compose up -d api
```

If auth is enforced and no valid token is supplied, protected endpoints may return `401` (missing/invalid token) by design.

## Storage mounts and persistence

Compose mounts (relative to project root):

| Host path | Container path | Mode | Notes |
|---|---|---:|---|
| `./data` | `/app/data` | rw | Writable runtime data area (scoped). Persists on host. |
| `./data/prospective` | `/app/data/prospective` | ro | Read-only submount; protects Q5.2 prospective evidence (`collect-52cefa0dda64` / `freeze-aaa4dd57057a` context). |
| `./models/artifacts` | `/app/models/artifacts` | ro | Read-only model artifacts. |
| `./config` | `/app/config` | ro | Read-only configuration. |
| `./logs` | `/app/logs` | rw | Runtime logs (host-persisted). |
| `./reports` | `/app/reports` | rw | Runtime/generated reports (host-persisted). |

- Bind-mounted host data persists across `docker compose down` and image rebuilds.
- The root filesystem is `read_only: true` with a `/tmp` tmpfs for ephemeral writes.
- Do not mount or modify prospective evidence paths as writable; governance and evidence integrity depend on read-only handling.

## Security

- **Network exposure:** host port mapping is `127.0.0.1:8000:8000` only (loopback). The container binds `0.0.0.0` internally for Docker networking, and the CLI uses `--allow-remote` to permit that bind; exposure is controlled by compose and is not public.
- **Container hardening:** runs as non-root `10001:10001`, `no-new-privileges:true`, `cap_drop: [ALL]`, `read_only: true` with `/tmp` tmpfs.
- **Secrets:** `.dockerignore` excludes `.env`, `.env.*`, keys, and credential material. No secrets/credentials are baked into images; provide tokens at runtime only.
- **No activation:** this setup does not enable scheduler, collector, strategy evaluation, paper trading, or live trading.

## Health and logs

Follow API logs:

```bash
docker compose logs -f api
```

Inspect container health status:

```bash
docker inspect --format='{{json .State.Health}}' signalforge-api
```

Check service status:

```bash
docker compose ps
```

## Restart and shutdown

Restart the API:

```bash
docker compose restart api
```

Stop the API (container retained):

```bash
docker compose stop api
```

Tear down services (preserves bind-mounted host data):

```bash
docker compose down
```

## Rebuild

Rebuild and restart the API:

```bash
docker compose build --no-cache api
docker compose up -d api
```

If validating image contents/tests after a rebuild:

```bash
docker compose --profile test build test
docker compose --profile test run --rm test
```

## Rollback

1. Stop running services: `docker compose down`
2. Check out the previous known-good revision/tag in git (local operation; do not commit unless explicitly requested).
3. Rebuild images: `docker compose build api` (and `--profile test build test` if needed).
4. Restart: `docker compose up -d api` and re-run health checks.

Host bind-mounted data (`./data`, `./logs`, `./reports`) persists across rollback; review application/data compatibility before restart.

## Troubleshooting

- **Port conflicts (`127.0.0.1:8000` already in use):** identify the conflicting process, stop it, or adjust the host-side mapping in a local override (keep exposure loopback-only). Re-run `docker compose up -d api`.
- **Auth 401 / missing token:** ensure `SIGNALFORGE_AUTH_REQUIRED` and `SIGNALFORGE_API_TOKEN` are set at runtime and that requests include the expected auth mechanism for the API. Do not embed tokens in images or commit them.
- **Windows bind-mount permissions:** ensure Docker Desktop has access to the project drive/folder and that mounted paths exist. If writes to `./logs` or `./reports` fail, verify host directory permissions/ownership semantics for Linux containers on Windows.
- **Container startup failures:** inspect `docker compose logs api` and `docker compose ps`. Common causes: missing runtime env vars, invalid config under `./config`, or missing expected host directories for mounts.
- **Config/path issues:** confirm `./config`, `./data`, `./models/artifacts`, `./logs`, and `./reports` exist and are mounted as intended. Remember the app runs with `PYTHONPATH=/app/src` and read-only rootfs.
- **Healthcheck failing:** run `docker inspect --format='{{json .State.Health}}' signalforge-api` and check logs; verify the server is listening on container port 8000 and `/health` responds.
- **Compose validation errors:** run `docker compose config` (and `--profile test config`) to see the resolved configuration and error details.

## Notes

- This containerization is for engineering/staging validation only and does not imply production readiness or trading authorization.
- Keep all runtime secrets out of source control and images; use runtime injection only.
