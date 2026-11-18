# Meta Credential Rotation — META_MUSE_API_KEY

**Phase:** Q17 — Credential Security & Prospective Collection Continuity
**Status:** EXPOSURE_NOT_CONFIRMED, REVOCATION_REQUIRED (precautionary; not a confirmed breach)
**Credential referenced:** META_MUSE_API_KEY (name only — no value ever printed or stored)

## Credential Exposure Status

- `META_MUSE_API_KEY` real value was NEVER committed to git.
- `.env` was never tracked in git (no commits contain `.env`).
- Config files only ever contain the env var **name**, never the actual secret value.
- No external provider rotation has been performed (no provider console access in this environment).
- Rotation status: **REVOCATION_REQUIRED** — precautionary, not a confirmed breach.

## Manual Rotation Procedure (Meta provider console)

Perform these steps in the Meta provider console. This is a manual, out-of-band procedure — no code change rotates the key.

1. **Log into the Meta provider console** using authorized operator credentials.
2. **Navigate to the API credentials / keys section** for the application owning `META_MUSE_API_KEY`.
3. **Revoke the current `META_MUSE_API_KEY`.** Confirm revocation in the provider UI so the old key is no longer valid.
4. **Generate a new key** through the provider's key management interface. Obtain the new key only from the official Meta provider — do not invent replacement credentials.
5. **Store the new key ONLY in the gitignored `.env` file** (e.g., `META_MUSE_API_KEY=<new_value>`). Never store the value in code, TOML, git, logs, or reports.
6. **Verify externally through the provider console** that the new key is active and the revoked key is rejected.
7. **Update `credential_rotation_verified` to `true` only after external verification.** Do not claim rotation without provider confirmation.

## What NOT to Do

- Do NOT print or log the actual key value.
- Do NOT commit `.env` to git.
- Do NOT include the key in any report, issue, or chat message.
- Do NOT rotate without verifying through the provider.
- Do NOT invent replacement credentials.
- Do NOT claim `META_MUSE_CREDENTIAL_ROTATED: true` until external provider confirmation is obtained.

## References

- Existing runbook: `docs/operations/CREDENTIAL_ROTATION_RUNBOOK.md`
- Q17 report: `docs/validation/results/q17_credential_and_collection_readiness.md'
