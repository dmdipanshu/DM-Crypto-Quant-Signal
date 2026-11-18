# Q27 Docker Readiness Report

**Phase:** Q27  
**Topic:** Docker containerization readiness for local/staging API and containerized test execution  
**Timestamp (UTC):** 2026-11-18T12:43:14Z  
**Completed at (UTC):** 2026-11-18T12:43:14Z  
**Status:** PASSED_WITH_WARNINGS  
**Final Decision:** `Q27_DOCKER_READINESS_PASSED_WITH_WARNINGS`

## Decision

Docker containerization is implemented and runtime-verified for **local/staging read-only API use** with hardening controls. Host regression is green on failures/lint (`1336 passed, 0 failed, 8 skipped`, `ruff` clean, `sf health` ok) but test collection/count differs from the Q26 baseline in the current working tree. Containerized full-suite execution is substantially passing with **one known OS-specific failure** (`tests/integration/test_ai_cli.py::test_ai_metrics_windows_path_handling`, Windows-path semantics mismatch under Linux), not an application regression.

**Caveats:** not production/live deployment; governance constraints unchanged; Q5.2 thresholds/state unchanged and no `data/prospective/` evidence modified; release manifest is stale relative to Q27 additions; compose healthcheck/auth probe mismatch may misreport Docker health; extensive uncommitted working tree (including unrelated modifications); `META_MUSE_API_KEY` rotation/revocation pending.

## Scope and governance preservation

- Scope: reproducible container builds, local/staging read-only API runtime, and containerized test execution only.
- Not activated/authorized: production hosting/external deployment, scheduler activation/registration, data collection activation, strategy evaluation, paper trading, live trading, or real-money/order execution.
- Preserved governance: `NO_STRATEGY_ELIGIBLE`, `collect-52cefa0dda64`, `freeze-aaa4dd57057a`, `Q5.2` thresholds unchanged/not eligible/not validated, `collection_authorized` via verified approval record only, `scheduler_authorized=true` / registered, `PRODUCTION_READY=false`, `LIVE_TRADING_AUTHORIZED=false`.
- `data/prospective/` evidence: **not modified**.

## Environment (verified)

| Item | Evidence |
|---|---|
| Docker Engine | 29.9.3 |
| Docker Compose | v5.7.0 |
| `docker info` | OK (desktop-linux/WSL2) |
| Smoke test | `hello-world` OK (Phase A) |
| Dependency declaration | `pyarrow>=14.0` in `pyproject.toml` |
| Installed dependency | `pyarrow 26.0.1` (Phase B) |

## Artifacts created (Phase C)

| Path | Notes |
|---|---|
| `.dockerignore` | Build context exclusions |
| `Dockerfile` | Multi-stage `builder`/`runtime`/`test`; pinned Python 3.12.12 and uv 0.5.24; non-root user `10001`; healthcheck |
| `compose.yaml` | `api` service + `test` profile; loopback-only publish `127.0.0.1:8000:8000`; read-only rootfs + tmpfs; `no-new-privileges`; `cap_drop: [ALL]`; auth fail-closed env; RO mounts for `data/prospective`, `models/artifacts`, `config`; scoped RW mounts for `data/logs/reports` with prospective submount RO; no scheduler/collector/trading services |
| `docker/README.md` | Docker usage notes |
| `docs/operations/DOCKER_DEPLOYMENT_RUNBOOK.md` | Local/staging runbook and governance notes |

**Config validation:** `docker compose config` exit 0; `docker compose --profile test config` exit 0.

## Build and image results (Phase D)

| Image | Size | Result |
|---|---|---|
| `signalforge:local` | 1.17 GB | Built successfully |
| `signalforge:test` | 1.24 GB | Built successfully |

**Runtime image inspection:** `uid=10001(appuser)` (non-root); no `.env*` files in image; no `*.pkl` in `/app` outside vendored `.venv` test fixtures.

**Secret-pattern scan:** no embedded secret values found. Scan showed only config/key-name references (e.g. `META_MUSE` names) and third-party vendor artifacts under `.venv`.

## Runtime verification (Phase E)

API started via Compose on loopback (`127.0.0.1`).

| Check | Result |
|---|---|
| Unauthenticated `GET /health` | `401` (fail-closed) |
| Authenticated `GET /health` | `200` — `status=ok`, `read_only=true`, `trading_enabled=false`, `checks.status=unhealthy` due to `paper_account` missing; `prospective_collection` healthy |
| Authenticated `GET /api/v1/health` | `200` |
| Authenticated `GET /metrics` | `200` (empty body observed) |
| `docker exec id` | non-root `10001` |
| `docker port` | only `127.0.0.1:8000->8000/tcp` |
| Compose healthcheck (observed earlier) | starting/failing due to unauthenticated probe (`401`) while API requires auth |
| `data/prospective` write attempt | failed read-only (expected) |
| Persistence (bind mounts) | isolated temp marker in `data/` persisted across restart and `down`/`up`; no official Q5.2 data modified |

## Host fix (minimal, verified)

- `src/crypto_engine/cli.py`: updated `serve` to catch `ImportError` from `create_app` and emit actionable `uv sync --extra api` guidance (addresses failing serve occupied-port/missing-extra test behavior).
- Note: file had pre-existing unrelated working-tree modifications.

## Host regression (latest verified)

| Check | Command | Result |
|---|---|---|
| Tests | `uv run pytest -q` | **1336 passed, 0 failed, 8 skipped** (exit 0) |
| Lint | `uv run ruff check .` | **All checks passed!** (exit 0) |
| Health | `uv run sf health` | **ok JSON** (exit 0) |

**Q26 baseline:** 1396 passed, 0 failed, 3 skipped, ruff clean.

**Comparison/discrepancy:** collection/count differs in the current working tree (**1336/0/8** vs **1396/0/3**) with **no failures**. Earlier intermediate runs showed `1398/0/3` and `1398/1/3` (before fix) due to in-progress changes; final verified state is `1336/0/8`.

## Containerized tests

**Initial issue:** test profile blocked by uv cache on read-only filesystem; fixed by cache envs/tmp paths and direct `ruff`/`pytest` command.

**Adjusted test harness:** read-only repo assets/data/models mounts including `.env.example` as `/app/.env`, `SIGNALFORGE_AUTH_REQUIRED=0` for in-process API tests, prospective submount kept RO, `api` service unchanged.

| Run | ruff | pytest | Notes |
|---|---|---|---|
| Full run (adjusted harness) | PASS | **1395 passed, 1 failed, 3 skipped** (exit 1) | Single failure: `tests/integration/test_ai_cli.py::test_ai_metrics_windows_path_handling` — Windows-path semantics mismatch under Linux container (not app regression) |
| Prior unadjusted run | — | **1322 passed, 73 failed, 4 skipped** | Missing mounts/auth configuration for test harness (not application defects) |

**Limitations:** one known OS-specific container failure (Linux vs Windows path semantics); adjusted harness differs from production-like `api` runtime; unadjusted failures attributable to harness setup.

## Security checks summary

- Non-root runtime (`10001:10001`), loopback-only publish (`127.0.0.1:8000->8000/tcp`)
- Auth fail-closed behavior verified (`401` unauthenticated)
- Read-only root filesystem, tmpfs, `no-new-privileges`, `cap_drop ALL`
- `data/prospective` mounted read-only and write attempt rejected
- No `.env*` / no unexpected `*.pkl` artifacts in image; no embedded secret values found in scan

## Release manifest action

| Item | Detail |
|---|---|
| Current manifest | `docs/validation/results/release_candidate_manifest.json` — **Q16** (620 files, `2026-11-18T08:18:10Z`), does not include new Q27 Docker artifacts |
| Generator | `scripts/generate_release_manifest.py` (Q16-specific: hardcoded root, phase/title/version/release_type fixed to Q16 release-candidate values; full-tree walk of current working tree with fixed excludes) |
| Action taken | **Not regenerated** |
| Rationale | Regeneration is risky/misleading for project conventions and current state: would overwrite Q16 identity/metadata and include extensive uncommitted/unrelated working-tree changes while implying a release-candidate manifest update. |
| Recommended process | From a clean/reviewed release scope, create or adapt a phase-appropriate deterministic generator (do not overwrite Q16 metadata unless intentionally superseding), confirm include/exclude rules and uncommitted-file policy, then run `uv run python scripts/generate_release_manifest.py` (or the new generator) and verify the manifest reflects intended Q27 artifacts and release conventions before committing. |

## Known issues / warnings

- `META_MUSE_API_KEY` provider rotation `REVOCATION_REQUIRED` (external credential hygiene; no secrets printed).
- Stale doc ref: `SignalForge_Q2_Q4_Evidence_Audit.md:4` references `SignalForge_Q2_to_Q4_Build.zip`.
- `__pycache__` remnants of deleted files present.
- Compose healthcheck probe is unauthenticated (`401`) against auth-required API and may show unhealthy in Docker health status despite endpoint working with auth.
- Extensive uncommitted/untracked working tree, including pre-existing unrelated modifications.
- Host test count differs from Q26 baseline in current tree (no failures).
- Container full-suite has one known OS-specific Linux/Windows-path failure (not app regression).
- Release candidate manifest stale relative to Q27 additions.

## Final decision

**`Q27_DOCKER_READINESS_PASSED_WITH_WARNINGS`** — Docker containerization is ready for **local/staging engineering use** of the read-only API with the documented hardening and verification, subject to the warnings above. This does **not** authorize production/live deployment, does **not** change governance/authorization state, and does **not** modify `data/prospective/` evidence or activate scheduler/collector/evaluation/paper/live trading capabilities.
