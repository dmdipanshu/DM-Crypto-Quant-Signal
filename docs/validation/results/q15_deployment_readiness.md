# Q15 Deployment Infrastructure Readiness & Deployment Rehearsal

**Phase:** Q15 — Phase F: Testing and Final Report
**Status:** Q15_DEPLOYMENT_READY_WITH_WARNINGS
**Final Decision:** Q15_DEPLOYMENT_READY_WITH_WARNINGS (rehearsal passed, infrastructure ready, but META_MUSE_API_KEY rotation outstanding and Q5.2 prospective incomplete)
**Timestamp (UTC):** 2026-11-15T14:30:31Z
**Execution time:** 5.12 minutes
**Scope:** Q15 Phase F — Testing and final report. Deployment infrastructure readiness rehearsal on staging config. No trading, no live orders, no real exchange credentials, no model retraining/calibration. Source files and Q4-Q14 governance evidence not modified.

## Executive summary

All 12 Q15 test steps executed and passed. The full regression suite is green: 1392 passed, 0 failed, 3 skipped, exit code 0. Clean installation verified (`uv sync --extra api` exit 0; `sf health` exit 0). Backup create + dry-run restore verified (93 files, 0 errors). All Q4–Q14 governance invariants preserved: `PRODUCTION_READY=false`, `LIVE_TRADING_AUTHORIZED=false`, `Q5_2_PROSPECTIVE_VALIDATED=false`, `ai_can_promote=false`, Q4 `NO_STRATEGY_ELIGIBLE`. No source files or Q4–Q14 governance evidence were modified. No trading, live orders, paper trading, or real exchange credentials were activated.

The deployment infrastructure is ready for staging rehearsal with two outstanding warnings: (1) `META_MUSE_API_KEY` rotation is `REVOCATION_REQUIRED` (precautionary, not a confirmed breach), and (2) Q5.2 prospective validation remains incomplete. Neither blocks deployment-readiness assessment, but both must close before any production consideration.

## Deployment architecture

SignalForge is a single-process, loopback-only advisory research platform.

Canonical pipeline:

```text
exchange (ccxt, public read-only) → market data → features → models → signals → risk gates → results
```

Configuration layer:
- `config/*.toml` (default, staging, development, production) validated by pydantic `Settings`.
- Secrets only in gitignored `.env` via `META_MUSE_API_KEY` env var — never in code, TOML, or git.
- `config/staging.toml` `[deployment]`: `environment = "staging"`, `trading_authorized = false`, `live_orders_enabled = false`, `retraining_enabled = false`, `fail_closed_defaults = true`.
- `[backup].path = "backups"` (disaster-recovery destination).

API / dashboard layer:
- `[api]` binds `127.0.0.1:8000`, `allow_remote = false`, `cors_origins = []`; GET-only routes with 405s on mutating methods; traversal/ID validation/secret redaction/sanitized errors.
- Server-rendered HTML dashboard with no trading controls or forms.

Backup / restore (Phase D):
- Atomic writes (temp file + `os.replace`), source directories read-only, per-file 50 MB cap, SHA-256 manifest for tamper detection.
- `restore` is dry-run by default; `fail_closed_defaults = true`.

Risk gates:
- Deterministic, `fail_closed`, independent of models/AI; `NO_TRADE` is a valid expected output. No live orders, paper trading, retraining, or public exposure anywhere in the codebase.

## Infrastructure security findings

| Finding | Severity | Status | Detail |
|---|---|---|---|
| `META_MUSE_API_KEY` rotation outstanding | HIGH | REVOCATION_REQUIRED | Real key value never committed to git; `.env` never tracked; no external provider rotation performed (no provider console access). Classification EXPOSURE_NOT_CONFIRMED. Rotation is precautionary, not a confirmed breach. Runbook: `docs/operations/CREDENTIAL_ROTATION_RUNBOOK.md`. External rotation verified: false. |
| API binding loopback-only by default | INFO | PASS | `[api] host = 127.0.0.1`, port 8000, `allow_remote = false`; CORS empty; GET-only build assertion; 405s on mutating methods; traversal/ID validation/secret redaction/sanitized errors tested. |
| No hardcoded API keys; no pickle imports in `src/`; `.env` gitignored | INFO | PASS | Secret scan clean; unsafe deserialization absent; CI secret scanning configured. |
| Backup tamper detection via SHA-256 manifest | INFO | PASS | Backup create + dry-run restore verified (93 files, 0 errors); manifest checksums verified on create. |

## Configuration and automation changes

No source files were modified. The rehearsal used the existing `config/staging.toml` (pre-existing, untracked in this worktree) with the following relevant sections active:
- `[backup].path = "backups"`
- `[deployment] fail_closed_defaults = true`, `trading_authorized = false`, `live_orders_enabled = false`, `retraining_enabled = false`
- `[api] host = 127.0.0.1`, `allow_remote = false`

No new configuration or automation was introduced; no existing source or governance files were touched.

## Rehearsal results (Phase D)

| Step | Command | Exit code | Result |
|---|---|---|---|
| Backup create | `uv run sf backup create --config config/staging.toml` | 0 | backup id=`286fc87b195644eca421ccc818c7668c`, created `2026-11-15T14:26:18.930892+00:00`, 93 files, 20027892 bytes, status completed |
| Backup list | `uv run sf backup list backups` | 0 | 3 backups listed newest first; latest = `286fc87b195644eca421ccc818c7668c` |
| Backup restore dry-run | `uv run sf backup restore backups\\286fc87b195644eca421ccc818c7668c --dry-run --config config/staging.toml` | 0 | Restore preview: Restored=93, Skipped=0, Errors=0, Dry run=True |
| Clean install | `uv sync --extra api` then `uv run sf health` | 0 / 0 | `uv sync` exit 0; `sf health` exit 0 (status ok, config valid, storage writable) |

Rollback readiness: dry-run restore succeeded; no user data overwritten; `fail_closed_defaults=true` in `[deployment]`.

## Rollback and recovery results

Backup create exit code 0; dry-run restore exit code 0 (93 files restored in preview, 0 errors). Reliability tests (backup/restoration and failure recovery) passed: 8 passed, 0 failed, 0 skipped, exit 0 (run twice: steps 6 and 8). No user data overwritten. Fail-closed defaults enforced.

## Tests executed

| # | Step | Command | Passed | Failed | Skipped | Exit code |
|---|---|---|---:|---:|---:|---:|
| 1 | Deployment configuration | `uv run pytest tests/unit/test_config.py -v` | 4 | 0 | 0 | 0 |
| 2 | Clean installation | `uv sync --extra api`; `uv run sf health` | — | — | — | 0 / 0 |
| 3 | Startup and shutdown | `uv run pytest tests/integration/test_serve_cli.py -v` | 3 | 0 | 0 | 0 |
| 4 | Authentication | `uv run pytest tests/unit/test_api_security.py -v` | 9 | 0 | 0 | 0 |
| 5 | Health and metrics | `uv run pytest tests/unit/test_observability_q9.py -v` | 30 | 0 | 0 | 0 |
| 6 | Backup and restoration | `uv run pytest tests/unit/test_reliability.py -v` | 8 | 0 | 0 | 0 |
| 7 | Rollback | `uv run sf backup create --config config/staging.toml`; `uv run sf backup restore <latest> --dry-run --config config/staging.toml` | — | — | — | 0 / 0 |
| 8 | Failure recovery | `uv run pytest tests/unit/test_reliability.py -v` | 8 | 0 | 0 | 0 |
| 9 | Q7/Q8 paper-trading regression | `uv run pytest tests/unit/test_paper_*.py tests/integration/test_paper_cli.py -v` | 110 | 0 | 0 | 0 |
| 10 | Q9 observability regression | `uv run pytest tests/unit/test_observability_q9.py tests/unit/test_metrics_robustness.py -v` | 40 | 0 | 0 | 0 |
| 11 | Q10–Q14 security/release regression | `uv run pytest tests/unit/test_security_q10.py tests/unit/test_security_q11.py tests/unit/test_security_scan.py -v` | 50 | 0 | 0 | 0 |
| 12 | Full repository suite | `uv run pytest` | 1392 | 0 | 3 | 0 |

**Totals:** 12 steps executed, 12 passed, 0 failed. Test totals: 1646 passed, 0 failed, 3 skipped. Full suite exit code 0.

> Note: `tests/unit/test_paper_*.py` was expanded explicitly (PowerShell does not expand globs): `test_paper_compare.py`, `test_paper_loop.py`, `test_paper_q7_orders.py`, `test_paper_q7_pnl_risk.py`, `test_paper_q7_simulator.py`, `test_paper_q8_integration.py`, `test_paper_state.py` plus `tests/integration/test_paper_cli.py`.

## Governance invariants preserved (Q4–Q14)

- `q4_decision`: NO_STRATEGY_ELIGIBLE
- `ai_can_promote`: false
- `PRODUCTION_READY`: false (must remain false)
- `LIVE_TRADING_AUTHORIZED`: false (must remain false)
- `Q5_2_PROSPECTIVE_VALIDATED`: false (must remain false; prospective validation incomplete: collect-52cefa0dda64 / freeze-aaa4dd57057a)
- `META_MUSE_API_KEY`: REVOCATION_REQUIRED (EXPOSURE_NOT_CONFIRMED; not externally verified)

## Readiness flags (12)

| Flag | Value |
|---|---|
| `credential_rotation_verified` | false |
| `q4_q13_evidence_verified` | true |
| `release_manifest_verified` | true |
| `release_secret_scan_passed` | true |
| `unsafe_deserialization_absent` | true |
| `clean_install_verified` | true |
| `backup_restore_verified` | true |
| `full_regression_passed` | true |
| `governance_preserved` | true |
| `q5_2_prospective_validated` | false |
| `production_ready` | false |
| `live_trading_authorized` | false |

## Remaining risks

1. `META_MUSE_API_KEY` may exist locally outside git (not externally rotated) — HIGH — REVOCATION_REQUIRED. Rotate through Meta provider console; store new key in gitignored `.env`; verify externally. Runbook: `docs/operations/CREDENTIAL_ROTATION_RUNBOOK.md`.
2. Q5.2 prospective validation incomplete (collect-52cefa0dda64 / freeze-aaa4dd57057a) — MEDIUM — INCOMPLETE. Complete prospective validation before any promotion decision; `Q5_2_PROSPECTIVE_VALIDATED` must remain false until done.
3. `artifact.pkl` files remain on disk (2 copies) — MEDIUM — ACCEPTED. Not loaded by any active code path; JSON replacements exist.
4. No live connectivity proof for providers; sandbox has no package network — LOW — ACCEPTED. Provider SDKs are bring-your-own; adapters use stdlib urllib; no runtime fallback wired.

## Actual time consumed

Start: 2026-11-15T14:25:23Z | End: 2026-11-15T14:30:31Z | Duration: 5.12 minutes.

## Recommended Q16 milestone

**Q16 — Credential rotation completion and Q5.2 prospective validation completion.** Externally rotate `META_MUSE_API_KEY` via the Meta provider console and store the new key in the gitignored `.env`; complete Q5.2 prospective validation (collect-52cefa0dda64 / freeze-aaa4dd57057a); re-run the full regression suite and release acceptance after both close. Production deployment remains unauthorized; `PRODUCTION_READY` and `LIVE_TRADING_AUTHORIZED` stay false.

## Final decision line

**Q15_DEPLOYMENT_READY_WITH_WARNINGS** — deployment infrastructure ready and rehearsal passed; META_MUSE_API_KEY rotation outstanding and Q5.2 prospective validation incomplete; production deployment and live trading remain unauthorized.
