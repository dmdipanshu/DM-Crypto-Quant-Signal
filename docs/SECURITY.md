# SECURITY.md — SignalForge (Phase 1)

## Secrets

- Never commit secrets. `.env` is git-ignored; `.env.example` holds placeholders only.
- `config/default.toml` is non-secret settings only; TOML never holds secrets.
- AI provider keys (Phase 4+) load from environment.

## Data and execution

- Phase 1 uses public read-only market data only; no API keys required.
- No live orders anywhere in the codebase. Paper trading (Phase 6) is advisory-only.
- Treat exchange responses and AI outputs as untrusted: validate schemas, bound
  sizes/timeouts, handle failures explicitly.

## Dependencies and filesystem

- Isolated `.venv/` via `uv`; `uv.lock` tracked for reproducibility.
- Writes bounded to repo (`data/`, `models/`, `reports/`); no access outside root
  without explicit approval. Windows/OneDrive-safe: short-lived locks, small writes.
- VectorBT deferred until license verified (see `DECISIONS.md`).

## Reporting

Report severity, evidence (file paths), and remediation; never hide unresolved risk.
