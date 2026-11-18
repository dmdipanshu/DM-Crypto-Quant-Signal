# Infrastructure Security Checklist

SignalForge Q15 infrastructure security verification. All items are advisory/read-only, no live orders. This checklist does not authorize production operation or trading.

## Status

All targets below are recommendations, not established policy. Mark each item PASS/FAIL/N/A during an actual review.

---

## 1. Network security

| # | Check | Recommendation | Status |
|---|---|---|---|
| 1.1 | API bind address | `127.0.0.1` (loopback only) | |
| 1.2 | Remote access | `allow_remote = false` | |
| 1.3 | CORS | `cors_origins = []` (disabled) | |
| 1.4 | TLS | Required for any non-loopback exposure (recommendation) | |
| 1.5 | Port binding | Staging `127.0.0.1:8099`; production `127.0.0.1` (recommendation) | |

---

## 2. Authentication

| # | Check | Recommendation | Status |
|---|---|---|---|
| 2.1 | Fail-closed | Auth middleware denies by default; `NO_TRADE` on invalid/insufficient evidence | |
| 2.2 | Auth required flag | `SIGNALFORGE_AUTH_REQUIRED` honored | |
| 2.3 | API token | `SIGNALFORGE_API_TOKEN` set via environment only | |
| 2.4 | Bearer tokens | Valid Bearer token required for `/api/v1/*`; 401 without token | |
| 2.5 | Token logging | Bearer tokens never logged (redacted to `REDACTED`) | |
| 2.6 | Fail-open | `SIGNALFORGE_ALLOW_FAIL_OPEN` NOT set to `1` | |

---

## 3. Secrets

| # | Check | Recommendation | Status |
|---|---|---|---|
| 3.1 | Source code | No secrets in source code | |
| 3.2 | TOML config | No secrets in TOML files (env var names only) | |
| 3.3 | `.env` | `.env` gitignored; placeholder/empty values only | |
| 3.4 | Secret scanner | Operational (CI secret scanning + tests) | |
| 3.5 | Real credentials | No real exchange credentials anywhere in repo | |

---

## 4. Storage

| # | Check | Recommendation | Status |
|---|---|---|---|
| 4.1 | File permissions | Restrictive permissions on data dirs, backups, `.env` | |
| 4.2 | Data directories | Defined, bounded, provenance-preserving | |
| 4.3 | Backup access controls | Backups access-controlled; read-only restore source | |
| 4.4 | Model artifact integrity | SHA-256 verified against `record.json` | |
| 4.5 | Ledger integrity | SHA-256 checksum verification on backup/restore | |
| 4.6 | Atomic writes | Tempfile + `os.replace` for restore operations | |
| 4.7 | Dry-run default | `sf backup restore` defaults to dry-run | |

---

## 5. META_MUSE_API_KEY status

| Item | Value |
|---|---|
| Status | **REVOCATION_REQUIRED** |
| Exposure confirmed in repository | No (EXPOSURE_NOT_CONFIRMED) |
| Provider console access | Not available; rotation not externally verified |
| Real key in git history | Not found (`.env` never tracked) |
| Real key in config | Not found (env var name only) |
| Action required | Rotate through Meta provider console (precautionary, manual) |
| Blocks release | No; prevents unconditional credential clearance |

**Honest status:** The real `META_MUSE_API_KEY` value was never committed to the repository. Exposure was never confirmed. Provider-side rotation is a precautionary requirement, not a confirmed breach remediation. `REVOCATION_REQUIRED` remains in effect until externally verified.

---

## 6. Reviewer sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| Security reviewer | | | |
| Quant lead | | | |
