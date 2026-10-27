# NEXT_STEPS.md — SignalForge handoff (final phase complete)

## Status: Phase 7 implemented and verified (2026-10-28). No Phase 8 exists.

The platform is complete as an advisory research system:

- Read-only API (`/api/v1`, GET-only) over canonical stores.
- Server-rendered dashboard with explicit empty/uninitialized states.
- `sf serve` loopback-only foreground server.
- Security tests (GET-only table, 405s, traversal, secrets, CORS, sanitization).
- Operational docs (`INSTALL_WINDOWS.md`, README, ARCHITECTURE, SECURITY).

## If new work is ever requested (requires explicit user approval)

- Any new phase (live trading, cloud deployment, new model families,
  write-capable API) is out of scope for this design and must be authorized
  explicitly with its own safety review. Default answer: stop and ask.

## Gates met (verified 2026-10-28)

- [x] Read-only API (32 GET endpoints, envelopes, pagination caps, 404/unavailable contracts)
- [x] Read-only adapters (no mkdir/write side effects, bounded scans, verified checksums surfaced)
- [x] Dashboard (6 pages, shared adapters, escaped output, no trading controls)
- [x] `sf serve` (loopback default, explicit remote opt-in, clear errors, graceful shutdown)
- [x] Security (GET-only build assertion, 405s, traversal blocked, secrets never leak, CORS deny-default)
- [x] Live server proof (uvicorn 127.0.0.1:8123, 4 endpoints 200, clean shutdown)
- [x] `uv sync` + `--extra api` clean, `pytest` 466 passed/1 skipped, ruff clean
- [x] Final audit (this file set) — no live orders, no real money, no public exposure anywhere

## Permanently out of scope for this design

Live orders, real-money trading, order placement/cancellation, deposits,
withdrawals, leverage, paper-state mutation via API, training/retraining or
signal/backtest triggers via API, public exposure by default, architecture
rewrites, unapproved global config changes.
