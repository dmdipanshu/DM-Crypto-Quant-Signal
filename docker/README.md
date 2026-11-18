# SignalForge Docker

Engineering/staging containerization for SignalForge (no external deployment, no trading activation).

## Prerequisites
- Docker Engine/Desktop with Compose v2 (`docker compose`)
- Linux container support

## Quick start
```bash
# Build API image
docker compose build api

# Start API (published only on host loopback)
docker compose up -d api

# Check health
curl http://127.0.0.1:8000/health
docker compose ps
```

## Run containerized tests
```bash
docker compose --profile test build test
docker compose --profile test run --rm test
```

## Security and governance notes
- API host exposure is restricted to `127.0.0.1:8000` via compose port mapping.
- Container runs as non-root (`10001:10001`) with `no-new-privileges`, all capabilities dropped, read-only rootfs + tmpfs.
- Prospective evidence and model artifacts are mounted read-only for the API container.
- No `.env` or credentials are baked into images; set `SIGNALFORGE_AUTH_REQUIRED`/`SIGNALFORGE_API_TOKEN` at runtime.
- No scheduler, collector, strategy evaluation, paper trading, or live trading services are created/activated.

See `docs/operations/DOCKER_DEPLOYMENT_RUNBOOK.md` for full operational details.
