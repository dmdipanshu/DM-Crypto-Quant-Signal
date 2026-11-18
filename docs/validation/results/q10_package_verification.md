# Phase 10 — Reproducible Package Verification

SignalForge Phase 10 package verification. Confirms the package can be reproducibly built, installed, and run in isolation on a clean 4 GB laptop environment. No network access required beyond committed uv.lock.

## Package Contents

| File | Purpose |
|---|---|
| pyproject.toml | Project metadata, dependencies, entry point (sf) |
| uv.lock | Pinned dependency lockfile (reproducible installs) |
| src/crypto_engine/ | Application source (21 packages) |
| config/default.toml | Validated configuration (non-secret only) |
| .env.example | Example environment file (no real secrets) |
| 	ests/ | Test suite |
| docs/ | Documentation, runbooks, validation results |

## Verification Procedure

### 1. Clean install

On a machine with Python 3.12 and uv but no prior SignalForge installation:

`powershell
py -3.12 -m pip install uv
uv sync
`

Expected: all dependencies installed from committed uv.lock, .venv/ created, no network beyond lockfile resolution.

### 2. Smoke test

`powershell
uv run sf health
uv run sf config-show
`

Expected: all components HEALTHY or DEGRADED, config dump valid.

### 3. Backup round-trip

`powershell
uv run sf backup create
uv run sf backup verify backups/<latest>
uv run sf backup list backups/
`

Expected: backup created, verification passes, at least one backup listed.

### 4. Full test suite

`powershell
uv run pytest
`

Expected: all tests pass (skips not counted as passes).

### 5. Lint

`powershell
uv run ruff check .
`

Expected: zero errors.

### 6. Security spot checks

- [ ] No pickle import in src/crypto_engine/registry/store.py
- [ ] No real API keys in .env, TOML, or source
- [ ] API routes are GET-only (read-only)
- [ ] --fail-open requires SIGNALFORGE_ALLOW_FAIL_OPEN=1
- [ ] Metric labels redact secret-like values

## Dependency Summary

Core dependencies (all optional, no auto-install):
- pydantic, pydantic-settings — config validation
- python-dotenv — .env loading
- pandas, 
umpy, scipy, pyarrow — data handling
- ccxt — public exchange market data (read-only)
- 	yper, ich — CLI
- 	enacity — retry logic
- pyyaml — YAML parsing
- scikit-learn — ML models (optional)
- lightgbm — LightGBM models (optional, NOT installed by default)
- pytest, uff — testing and linting

No prometheus_client, structlog, or AI provider SDKs are dependencies. All observability uses stdlib (logging, json, hashlib).

## Determinism

- Single-process by default (
_jobs=1 everywhere)
- Fixed andom_state=42 for all stochastic operations
- No timestamps in model predictions (only in provenance metadata)
- Checksums (SHA-256) on all artifacts, snapshots, checkpoints, backups

## Offline Capability

The package installs and runs fully offline using the committed uv.lock and local dependency cache. No package-registry network access is required after initial uv sync.

## Sign-off

| Check | Result | Date |
|---|---|---|
| Clean install | | |
| Smoke test | | |
| Backup round-trip | | |
| Full test suite | | |
| Lint | | |
| Security spot checks | | |
