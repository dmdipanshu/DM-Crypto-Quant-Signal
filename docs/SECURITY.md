# SECURITY.md — SignalForge (Phase 7, final)

## Secrets

- Never commit secrets. `.env` is git-ignored; `.env.example` holds placeholders only.
- `config/default.toml` is non-secret settings only; TOML never holds secrets.
- AI provider keys load from environment; signal audit history rejects
  secret-like field names on append.

## Data and execution

- Public read-only market data only; no API keys required for quant path.
- No live orders anywhere in the codebase. Paper trading (Phase 6) is advisory-only.
- Phase 5 signal engine places no orders, holds no positions, tracks no live P&L:
  exposure/drawdown gates evaluate caller-supplied snapshots only, fail closed
  when required state is absent, never invent zero exposure.
- Treat exchange responses and AI outputs as untrusted: validate schemas, bound
  sizes/timeouts, handle failures explicitly. AI context is advisory-only and
  can never override a risk gate or flip a rejected signal to approved.
- Phase 7 read-only API: GET-only routes (build-time assertion + tests);
  loopback-only bind by default with explicit opt-in for remote; no wildcard
  credentialed CORS; no filesystem writes; no secret/env/keys in responses;
  path confinement against traversal; sanitized errors (no tracebacks);
  dashboard HTML-escapes all dynamic content (no raw model HTML/JS).

## Dependencies and filesystem

- Isolated `.venv/` via `uv`; `uv.lock` tracked for reproducibility.
- Writes bounded to repo (`data/`, `models/`, `reports/`); no access outside root
  without explicit approval. Windows/OneDrive-safe: short-lived locks, small writes.
- VectorBT deferred until license verified (see `DECISIONS.md`).

## Reporting

Report severity, evidence (file paths), and remediation; never hide unresolved risk.
