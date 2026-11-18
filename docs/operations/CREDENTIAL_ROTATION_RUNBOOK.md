# Credential Rotation Runbook — META_MUSE_API_KEY

## Credential Exposure Status

**Classification: EXPOSURE_NOT_CONFIRMED**

Git history investigation (Q12 Phase B) confirmed that the `META_MUSE_API_KEY` real value was NEVER committed to the repository:
- `.env` was never tracked in git (no commits contain `.env`)
- `config/default.toml` only ever contains the env var **name** (`api_key_env = "META_MUSE_API_KEY"`), never the actual secret value
- Only public URLs and config comments appear in history

The current `.env` contains `META_MUSE_API_KEY=` (empty placeholder). No real credential value exists in the repository.

## Rotation Procedure

Since the real key value was never committed to git, provider-side rotation may not be required. However, if a real key value ever existed locally (outside git), perform the following:

### Step 1: Rotate through the Meta provider

Log into the Meta developer console and generate a new `META_MUSE_API_KEY`. Revoke the old key through the provider's key management interface.

**Do not invent replacement credentials.** Obtain the new key only from the official Meta provider.

### Step 2: Update local environment

Replace the value in `.env`:

```powershell
# Edit .env and set META_MUSE_API_KEY=<new_value>
# .env is gitignored — never commit it
```

### Step 3: Verify application no longer depends on old credential

1. `uv run sf config-show` — confirms config loads without errors
2. `uv run sf health` — confirms all components HEALTHY or DEGRADED
3. `uv run pytest tests/unit/test_security_q11.py` — confirms no real secrets in .env

### Step 4: Confirm no secret exposure in logs or reports

1. `grep -r "META_MUSE_API_KEY=" . --include="*.md" --include="*.json"` — should show only the key name, never a value
2. Check `logs/` directory for any leaked credential values
3. Check `docs/validation/results/` for any exposed secrets

## Verification Checklist

| Check | Method | Expected Result |
|---|---|---|
| Old key revoked | Meta provider console | Key no longer valid |
| New key stored securely | `.env` (gitignored) | Value present, file not tracked |
| App doesn't use old key | `uv run sf health` | All components healthy |
| No secrets in logs | `grep -r sk- logs/` | No matches |
| No secrets in reports | `grep -r sk- docs/validation/results/` | No matches |

## If Provider Access Is Unavailable

If you cannot access the Meta provider console:

1. Leave `.env` with `META_MUSE_API_KEY=` (empty placeholder) — the provider is disabled in `config/default.toml` (`enabled = false`)
2. Document that rotation is pending provider access
3. Mark credential status as `REVOCATION_REQUIRED` until provider rotation is confirmed

## Q13 Investigation Evidence

| Check | Result |
|---|---|
| `.env` tracked in git | No — never committed |
| `config/default.toml` contains key values | No — only env var names |
| Real `META_MUSE_API_KEY` in any commit | No — confirmed via `git log --all -p -- .env` (no output) and `git log --all -p -- config/default.toml` (only env var names) |
| Other branches | Only `master` exists |
| Affected files | None |
| Affected commits | None |
| Exposure classification | EXPOSURE_NOT_CONFIRMED |

**Evidence summary:** The `META_MUSE_API_KEY` real value was never committed to the repository. `.env` was never tracked in git (no commits contain `.env`). `config/default.toml` only ever contains the env var **name** (`api_key_env = "META_MUSE_API_KEY"`), never the actual secret value. Only public URLs and config comments appear in history.

**Credential rotation status:** REVOCATION_REQUIRED — while the key was never committed to git, a real key value may have existed locally outside git. Provider-side rotation through the Meta provider console is required before the credential can be considered fully rotated.

## What NOT to Do

- Do NOT print or log the actual key value
- Do NOT commit `.env` to git
- Do NOT include the key in any report, issue, or chat message
- Do NOT rotate without verifying through the provider
- Do NOT invent replacement credentials
