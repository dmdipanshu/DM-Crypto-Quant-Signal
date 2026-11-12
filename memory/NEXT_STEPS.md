# NEXT_STEPS.md — SignalForge handoff (Phase 10 authorized 2026-10-28; post-Phase-10 build authorized by user)

## Status: Phase 10 completed and verified — frozen-model forward validation (no training, tuning, or trading). User has explicitly authorized completion of all remaining documented development phases.

`sf model train` trains a logistic baseline on a local snapshot with
chronological train/validation/holdout splits (purge + embargo), persists the
artifact via the existing registry with full provenance, and prints a JSON
summary. `sf model list` inspects registered artifacts (metadata only).

The platform is complete as an advisory research system:

- Read-only API (`/api/v1`, GET-only) over canonical stores.
- Server-rendered dashboard with explicit empty/uninitialized states.
- `sf serve` loopback-only foreground server.
- Security tests (GET-only table, 405s, traversal, secrets, CORS, sanitization).
- Operational docs (`INSTALL_WINDOWS.md`, README, ARCHITECTURE, SECURITY).

## Target architecture progress (IMPLEMENTATION_ROADMAP.md stages)

Stage 0 (Approval) — done via user authorization.
Stage 1 (Environment) — done.
Stage 2 (Domain and ports skeleton) — done:
  - `src/crypto_engine/domain/` with Timeframe, Direction, ReasonCode, Candle (stdlib only)
  - `src/crypto_engine/ports/` with MarketDataSource, SnapshotStore, SignalStore, Clock, IdGenerator
  - `tests/unit/domain/test_domain.py` and `tests/unit/domain/test_ports.py` (24 tests)
Stage 3 (Storage and market-data adapters) — done:
  - `src/crypto_engine/storage/snapshot_store.py` — FileSnapshotStore implementing ports.SnapshotStore
  - `src/crypto_engine/data/market_data_source.py` — CcxtMarketDataSource implementing ports.MarketDataSource
  - `tests/unit/domain/test_stage3_adapters.py` (6 tests)
Stage 4 (Quality engine) — done:
  - `src/crypto_engine/quality/` with audit(), validate_schema(), detect_staleness()
  - `tests/unit/quality/test_quality.py` (22 tests)
Stage 5 (Analytics extraction) — done:
  - `src/crypto_engine/analytics/` with research_proxies(), validate_holdout()
  - `tests/unit/analytics/test_analytics.py` (13 tests)
Stage 6 (Strategies and signal composition) — done:
  - `src/crypto_engine/strategies/` with StrategyMetadata, StrategyParameter, StrategyVersion, compose_signal()
  - `tests/unit/strategies/test_strategies.py` (23 tests)
Stage 7 (Risk gates independent) — done:
  - `src/crypto_engine/risk/gates.py` — existing Phase 5 Track B gates (18 deterministic gates)
  - `tests/unit/test_risk_gates.py` — existing tests
Stage 8 (Model serving / bootstrap composition root) — done:
  - `src/crypto_engine/bootstrap.py` — SignalForgeApp wiring domain→ports→quality→analytics→strategies→risk
  - `tests/unit/bootstrap/test_bootstrap.py` (9 tests)
Stage 9 (CLI command wiring) — done:
  - All CLI commands exist: health, config-show, serve, data, signal, backtest, paper, model, ai
  - `sf ai status` reports AI enablement, counts, limits (no secrets)
Stage 10 (Dashboard integration) — done:
  - AI status panel on dashboard overview page
  - GET /api/v1/ai/status endpoint, read_ai_status in repos.py
Stage 11 (Architecture tests) — done:
  - `tests/architecture/test_dependency_rules.py` — 11 tests enforcing DR-1..DR-17
Stage 12 (Documentation and verification) — done:
  - All docs consistent, all tests green, ruff clean

## Completed AI gateway remaining items

- A6 (UI failure states): AI status panel added to dashboard overview page,
  GET /api/v1/ai/status endpoint, read_ai_status in repos.py, tests added.
- A7 (Architecture tests): tests/architecture/test_dependency_rules.py with
  11 tests enforcing DR-1..DR-17 dependency rules.
- A8 (Telemetry path resolution & provider metrics): canonical get_telemetry_store(config)
  resolution across CLI, API, and dashboard; TelemetryStore.metrics() provider derivation;
	test_ai_metrics_shows_provider_id_from_telemetry and regression suite passing.

## Gates met (verified 2026-11-12)

- [x] Read-only API (33 GET endpoints, envelopes, pagination caps, 404/unavailable contracts)
- [x] Read-only adapters (no mkdir/write side effects, bounded scans, verified checksums surfaced)
- [x] Dashboard (6 pages + AI status panel, shared adapters, escaped output, no trading controls)
- [x] `sf serve` (loopback default, explicit remote opt-in, clear errors, graceful shutdown)
- [x] Security (GET-only build assertion, 405s, traversal blocked, secrets never leak, CORS deny-default)
- [x] Domain/ports skeleton (stdlib-only domain, protocol-only ports, protocol conformance tests with fakes)
- [x] Architecture dependency rules (tests/architecture/ enforcing DR-1..DR-17)
- [x] Live server proof (uvicorn 127.0.0.1:8123, 4 endpoints 200, clean shutdown)
- [x] `uv sync` + `--extra api` clean, pytest green, ruff clean
- [x] Final audit (this file set) — no live orders, no real money, no public exposure anywhere

## Permanently out of scope for this design

Live orders, real-money trading, order placement/cancellation, deposits,
withdrawals, leverage, paper-state mutation via API, training/retraining or
signal/backtest triggers via API, public exposure by default, unapproved global
config changes.
