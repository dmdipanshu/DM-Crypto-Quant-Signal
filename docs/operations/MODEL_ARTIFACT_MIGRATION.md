# Model Artifact Migration — Pickle to JSON

## Migration Overview

Q11 identified unsafe `pickle.load()` deserialization in `src/crypto_engine/research/q6/frozen_inference.py`. Q12 completed the migration of all legacy `artifact.pkl` files to `artifact.json`.

Migration principle: One-time format migration from pickle to JSON. No retraining, no recalibration, no model parameter changes. Original `.pkl` files are preserved on disk (not deleted).

## Migrated Artifacts

| # | Path | artifact.pkl SHA-256 | artifact.json SHA-256 | Status |
|---|------|---------------------|----------------------|--------|
| 1 | `models/artifacts/logistic-ee482c-3e3324f2-20261028T132236/` | `42a8ea4bafd248c727538e233e5335337917bcd1664d28421daa70331eccf4db` | `81230d7a59faabf52866188297ca3995e37df61e2a3522ebb2d7bfdacadc4997` | Migrated |
| 2 | `SignalForge_Q2_to_Q4_Build/models/artifacts/logistic-ee482c-3e3324f2-20261028T132236/` | `42a8ea4bafd248c727538e233e5335337917bcd1664d28421daa70331eccf4db` | `81230d7a59faabf52866188297ca3995e37df61e2a3522ebb2d7bfdacadc4997` | Migrated |

Both artifacts are identical copies (same content, same SHA-256 for both `.pkl` and `.json`).

## Source Code Changes

| File | Change |
|---|---|
| `src/crypto_engine/research/q6/frozen_inference.py` | Removed `import pickle`; `reconstruct_logistic_model()` loads from `artifact.json` only |
| `src/crypto_engine/registry/store.py` | Already JSON-only (Q10); `PICKLE_ARTIFACT` constant kept as deprecated |

## Unsafe Code Remaining

| File | Line | Issue | Scope |
|---|------|-------|-------|
| `SignalForge_Q2_to_Q4_Build/src/crypto_engine/registry/store.py:140` | `pickle.loads(blob)` | Active unsafe deserialization | Build directory, NOT part of release package |

The `SignalForge_Q2_to_Q4_Build` directory is a separate build artifact directory, not part of the main release package (`pyproject.toml` packages only `src/crypto_engine`).

## Validation

- `SklearnLogistic.from_dict(artifact.json)` produces `_coef` not None, `_intercept` not None
- `reconstruct_logistic_model()` loads successfully from `artifact.json`
- `artifact.pkl` preserved on disk untouched
- `record.json` preserved on disk untouched
- All Q6 frozen inference tests pass (39/39)

## Safety Rules Observed

- No `pickle.load()` called on untrusted artifacts at runtime
- No unknown pickle files deserialized for inspection
- No pickle loading restored as fallback
- No model parameters changed
- No candidate identities modified
- No Q6 historical validation evidence modified
- No existing artifacts deleted
