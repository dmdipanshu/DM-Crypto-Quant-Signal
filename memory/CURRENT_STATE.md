# CURRENT_STATE.md — SignalForge verified state (final phase)

- **Phase**: 7 authorized and implemented (2026-10-28) — read-only FastAPI
  (`/api/v1`, GET-only), read-only repository adapters, server-rendered
  dashboard, `sf serve` (loopback-only default), security tests, operational
  docs, final verification. No Phase 8 exists.
- **Implemented (Phase 7, verified 2026-10-28)**:
  - `src/crypto_engine/api/` — `security.py` (ID validation, path
    confinement, secret scanning/redaction, error sanitizer), `schemas.py`
    (versioned envelope + pagination + error envelope), `repos.py`
    (bounded readers over signal history/registry/snapshots/experiments/
    holdout/backtest runs/paper state; unavailable-not-fabricated;
    read-only `SignalHistory` binding without mkdir side effects),
    `routes.py` (32 GET endpoints incl. health/readiness/signals/models/
    validation/experiments/research/backtests/paper/audit/data),
    `app.py` (factory with GET-only build assertion incl. lazy 0.162
    `_IncludedRouter` traversal, CORS default-deny + explicit allowlist,
    sanitized error handlers, `list_data_routes`).
  - `src/crypto_engine/dashboard/` — server-rendered HTML (inline CSS, no
    JS framework), shared repo readers, escaped output, explicit empty/
    uninitialized/stale states, no trading buttons/forms, paper labeled
    hypothetical, `models` page without credentials.
  - `sf serve` (foreground, loopback enforced, `--allow-remote` explicit,
    clear missing-extra/port-occupied errors, no training/fetch/mutation).
  - Config: `[api]` (host 127.0.0.1, port 8000, remote false, CORS [],
    page caps 50/500, ledger cap 5000, backtest/experiments/holdout paths).
    No `pyproject.toml` change; `api` extra + lockfile already covered it.
  - Tests: `tests/fixtures/api_harness.py` (stdlib ASGI caller, no httpx),
    `test_api.py` (envelopes, filters, pagination, 404s, unavailable states,
    audit-read-only proof), `test_api_security.py` (GET-only table, 405s,
    traversal, ID validation, secret hygiene incl. env-secret scan, CORS,
    injection escaping), `test_dashboard.py` (8 rendering/honesty tests),
    `test_serve_cli.py` (help, non-loopback refusal, occupied-port failure).
- **Verification (lead-run 2026-10-28)**: `uv sync --extra api` EXIT 0 (fastapi
  0.162.0 + uvicorn 0.54.0; lockfile already pinned, no lock change);
  `uv run --extra api pytest` **466 passed, 1 skipped** (lightgbm optional)
  in ~50–75 s (35 new); plain `uv sync` + `uv run pytest` → core suite green
  with the 4 API/dashboard modules skipping cleanly via `importorskip`
  (quant CLI unaffected without the extra);
  `ruff check` + `ruff format --check` clean (171 files);
  `sf serve --help` EXIT 0 (loopback refusal + occupied-port paths tested);
  live server proof: real uvicorn on 127.0.0.1:8123 served `/health`,
  `/api/v1/signals`, `/dashboard/`, `/api/v1/paper/status` all 200, then
  graceful shutdown; `sf health` + `sf config-show` ([api] present) all EXIT 0.
- **Resource probe (representative, tracemalloc)**: overview + signals page
  render on populated fixtures (5 signals, 1 model, 1 snapshot, 1 backtest):
  ~0.09 s wall, ~58 KiB Python-alloc peak, ~2.4 KB + ~3.9 KB HTML.
  Method: in-process `tracemalloc`; NOT process-RSS; 4 GB guidance
  (single-process, bounded pages ≤500, ledger scan cap 5000, fixtures small).
- **Limitations**: no live connectivity proof for providers; holdout never
  touched by API (read-only ledger state only); dashboard shows recorded data
  only; no profitability/accuracy/live-readiness/cloud claim. Paper fills
  remain hypothetical. `uv run` without `--offline` may hang in this sandbox
  (no package network) — verification used `--offline` where needed.
