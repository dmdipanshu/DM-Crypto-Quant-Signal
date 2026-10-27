# SESSION_LOG.md — SignalForge session history

## 2026-10-27 — Foundation build started (foundation track)

- Workspace audit: `.opencode/` stubs only (10 agents, 12 skills, 4 commands);
  no `src/`, no `tests/`, no config, no commits on `master`.
- Built Phase 1 foundation: `pyproject.toml`, `.gitignore`, `.env.example`,
  `config/default.toml`, root docs, `docs/*`, `memory/*`, expanded all agents/skills.
- Verification deferred per instructions: no `uv sync` / `pytest` run; lead integrates.
- Next: engine track builds `src/` + `tests/`; lead runs checks and updates state.

## 2026-10-27 — Engine build + lead integration (both tracks verified)

- Engine track built `src/crypto_engine/` (settings, types, validation, CcxtAdapter
  with pagination/retries/stale warnings, snapshots with atomic writes + sha256,
  Typer CLI `health`/`config-show`/`data status`/`data fetch`) + `tests/` (37 tests).
- Lead fix: moved `pytest` + `ruff` from `[dev]` extra into main dependencies so
  documented `uv sync` → `uv run pytest` works without `--extra dev`.
- Lead verification (Windows, repo root, EXIT 0 all): `uv sync`; `sf health`;
  `sf config-show`; `sf data --help`/`data status`; `pytest` 37 passed;
  `ruff check` clean; `ruff format --check` 63 files clean.
- State updated: Phase 1 complete pending user review; Phase 2 NOT started.

## 2026-10-27 - Phase 2 authorized and implemented (lead integration)

- Auth: Phase 2 only (features, triple-barrier labels, baselines, registry, anti-leakage). AGENTS.md scope updated with 4GB bounds (n_jobs=1, chunked, max_rows_train=2000).
- Track A (features/labels) verified: 34 new tests, full suite 71 passed at handoff.
- Track B (models/registry) partially survived abort; lead verified and completed: sklearn 1.9.1 to main deps (lightgbm stays optional), ModelConfig/RegistryConfig in settings + default.toml.
- Lead fixes: 1 ruff unused-var (test_models.py), ruff format 5 files. Final: pytest 98 passed/1 skipped (lightgbm optional), ruff check + format clean, sf health/config-show EXIT 0.
- Resource probe (300 rows, tracemalloc, cold import): ~12.2s, ~55 MiB Python-alloc peak; not a process-RSS guarantee.
- No 90% or profitability claim. Phase 3 NOT started; awaiting user review.

## 2026-10-27 - Phase 3 authorized and implemented (lead integration)

- Auth: Phase 3 only (calibration, reliability, purged walk-forward + embargo, locked holdout, experiment registry, DSR, cost/robustness). AGENTS.md scope updated to Phase 3; fixed duplicated section numbers (two ##3); sequential-subagent rule for 4GB.
- Hygiene: normalized trailing blank lines in DECISIONS.md + SESSION_LOG.md; rewrote NEXT_STEPS.md to Phase 3 state (old Phase 2 gate retired, history preserved in git).
- Track A (calibration/reliability/config): CalibratedClassifier (event P(y==positive_class), sigmoid/isotonic, t_bounds, min 20, 1e-12 reload), reliability module, CalibrationConfig + ValidationConfig in settings + default.toml; 26 tests.
- Track B (validation): folds (tail-anchored, purge+embargo, disjoint counts), holdout ledger v1, metrics (tagged raw|calibrated, proxy Sharpe/drawdown warned), robustness (fee/slip grid, block bootstrap, MC, LOOFO), experiments (JSONL registry, DSR, Holm/Bonferroni, pbo_note refuses fabrication); 41 tests.
- Lead verification: uv sync EXIT 0; pytest 165 passed/1 skipped (lightgbm optional); ruff check + format (98 files) clean; sf health/config-show EXIT 0 with new sections.
- Resource probe (300-row OHLCV -> 181 feats, 2 folds, 50 bootstraps): ~0.19s, ~475 KiB tracemalloc peak (not RSS).
- No 90% or profitability claim. Phase 4 NOT started; awaiting user review.

## 2026-10-27 - Phase 4 authorized and implemented (lead integration)

- Auth: Phase 4 only (AI interface, 8 adapters, retry/fallback/budgets, validation, 8 analysts, context scores, ablation). AGENTS.md scope updated to Phase 4.
- Track A (providers) survived abort with 1 failing test (ollama actionable message); lead fixed base.py to include redacted exception detail. Env has NO package network: reverted ai-* extras from pyproject (BYO SDKs via pip, adapters stdlib-urllib) so uv.lock stays valid; documented in default.toml + AI_PROVIDERS.md.
- Track B (analysts/ablation/docs): 8 analysts, sequential orchestrator, 4-arm AblationStudy (temporal cutoff, registry-recorded, folds-only + ledger guard), AI_PROVIDERS.md rewritten; 31 tests.
- Lead fixes: 9x RUF012 ClassVar + B027 noqa + 11 ruff auto-fixes in provider files; formatted 11 files; fixed stale ai-extras doc refs. Final: pytest 254 passed/1 skipped (lightgbm optional), ruff check + format (122 files) clean, sf health/config-show EXIT 0 with [ai] disabled-default.
- Resource probe (6 analysts, mocked provider): ~0.03s, ~44 KiB tracemalloc peak (not RSS).
- Risk review (lead): no secrets in repo; no live-order paths; AI cannot mutate quant artifacts/registry/holdout; ablation refuses non-pristine holdout; injection treated as data.
- Ablation verdict: inconclusive (mocked zero-value context, delta 0.0; no timestamped news dataset). No AI predictive-value, profitability, or 90% claim. Phase 5 NOT started; awaiting user review.

## 2026-10-27 - Phase 5 authorized and implemented (lead integration)

- Auth: Phase 5 only (canonical signal schema/policy, edge/costs, deterministic gates, advisory AI, levels, audit history, `sf signal`). AGENTS.md scope updated to Phase 5.
- Track A (signals schema/policy/costs/levels + SignalConfig/RiskConfig + 46 tests) clean at handoff: pytest 300 passed/1 skipped, ruff clean.
- Track B subagent backend unreachable (2x DNS failure); files had landed across attempts: risk/gates.py, signals/history.py, cli.py signal sub-app, 4 test files. Lead adopted as implementer: reviewed, fixed, verified.
- Lead fixes: 8x B904 `from exc` in cli.py, SIM102 dedent (repaired veto indentation), F841 + RUF059 in tests; formatted 9 files. Final: pytest 346 passed/1 skipped (lightgbm optional), ruff check + format (138 files) clean.
- Subagent backend outage noted as infra failure, not a code defect; no parallel agents used (4GB rule + outage).
- Resource probe (decide()+run_gates, 40-row fixture; policy LONG/approved, gates fail-closed on missing edge): ~0.04s, ~26 KiB tracemalloc peak (not RSS).
- Risk review (lead): no secrets; no order paths; gates fixed-order with trace; exposure never invented; AI advisory-only (verified LONG stays LONG); history hash-chained + secret-scan; replay deterministic.
- No profitability, 90%, or live-readiness claim. Phase 6 NOT started; awaiting user review.

## 2026-10-28 - Phase 6 authorized and implemented (lead integration)

- Auth: Phase 6 only (event-safe backtester, realistic costs, conservative fills, reproducible ledger, advisory-only paper sandbox, `sf backtest`/`sf paper`). AGENTS.md scope updated to Phase 6.
- Track A (backtesting engine/costs/metrics + BacktestConfig/PaperConfig + 29 tests + timing-leakage tests) clean at handoff: pytest 376 passed/1 skipped, ruff clean.
- Track B (paper state/loop/compare + both CLI sub-apps + PAPER_TRADING.md + 54 tests) clean at handoff: pytest 430 passed/1 skipped, ruff clean, no ccxt/order imports (grep-verified).
- Lead integration fix (correctness): engine acted on direction regardless of `decision.status` — now only `approved` records trade (`NOT_APPROVED` skip); fixed the broken NEUTRAL-close test to use an approved NEUTRAL and added a rejected-LONG never-trades test. Docstring updated.
- Probe honesty note: first probe used stale fixture levels (entry_ref far below fill) and exited at the touched absolute TP for a net loss — correct engine behavior, wrong probe setup; re-ran with aligned levels (TP exit, +46.38 on 10000).
- Resource probe (200-bar backtest, 1 TP trade): ~0.05s, ~168 KiB tracemalloc peak (not RSS).
- Risk review (lead): no secrets; no live-order paths (no order/trade API imports in new paths); gates double-enforced (CLI filters + engine NOT_APPROVED); exposure never invented; AI untouched by Phase 6; history/audit logs intact.
- Final: pytest 431 passed/1 skipped (lightgbm optional), ruff check + format (156 files) clean, all CLI helps + health + config-show EXIT 0.
- No profitability, accuracy, or live-readiness claim. Paper fills are hypothetical. Phase 7 NOT started; awaiting user review.

## 2026-10-28 - Phase 7 authorized and implemented (lead implementation, final phase)

- Auth: Phase 7 only (read-only API + dashboard + `sf serve` + security tests + docs + final verification). AGENTS.md scope updated; no Phase 8 exists.
- Implemented directly by lead (integration surface too coupled to split safely): `src/crypto_engine/api/` (security/schemas/repos/routes/app), `src/crypto_engine/dashboard/` (pages/routes sharing repo readers), `sf serve` (loopback enforced, extra-aware), `[api]` config, 35 new tests + 2 fixture helpers (stdlib ASGI harness, populated tmp stores).
- API design: 32 GET routes, versioned envelopes (`api_version`/`request_id`/`data_ts`), pagination caps (50/500), `unavailable` payloads (never fabricated rows), `SignalHistory` read path without mkdir side effects (lead fix).
- FastAPI 0.162 note: `include_router` defers merging (`_IncludedRouter`); `assert_get_only`/`list_data_routes` traverse lazy routers — build assertion still holds, tests prove it.
- Lead verification: full suite 466 passed/1 skipped (extra on) and green-with-skips (extra off); ruff clean (171 files); live uvicorn proof 4x200 + clean shutdown; resource probe (overview+signals pages: ~0.09s, ~58 KiB tracemalloc peak, not RSS).
- Risk review (lead): route table GET-only (asserted at build + tested incl. 405s); path confinement tested; secrets scanned (env-secret test); CORS deny-default; errors sanitized (no tracebacks); dashboard escapes all dynamic content; no trading buttons/forms; no order/trade API imports added.
- Final audit: git state clean, history linear, no uncommitted user changes found; docs distinguish implemented/verified vs unavailable integrations; no profitability/accuracy/live/cloud claim.
- No Phase 8. Standing by for user review, not new work.

## 2026-10-28 - Final documentation cleanup (docs-only, no code changes)

- Auth: documentation-only maintenance; `src/`, `tests/`, `pyproject.toml`,
  `uv.lock`, `config/default.toml`, `.env.example`, `.opencode/` untouched.
- Fixed: README stale Phase 6 blockquote removed (contradicted the Phase 7
  blockquote above it); README status "in progress" -> "complete (final)";
  duplicated `## Configuration` section merged into one; SESSION_LOG Phase 7
  probe count corrected (206 -> 171 files, matching `ruff format --check`).
- Verified consistent, left intact: ARCHITECTURE.md component table (all 20
  implemented as designed), RESEARCH_METHODOLOGY.md phase summary,
  AI_PROVIDERS.md (Phase 4 origin label = implementation phase, correct),
  PAPER_TRADING.md (Phase 6 origin label, correct), phase-labeled doc titles
  (denote origin phase, not current status — consistent with DECISIONS log).
- Checks (lead-run, this session): `ruff check .` clean;
  `ruff format --check .` 171 files clean; `uv run pytest` 466 passed,
  1 skipped (lightgbm optional); `uv run --extra api --offline pytest`
  466 passed, 1 skipped (same lightgbm skip); skip reason confirmed via `-rs`.
  Markdown fence audit: no unclosed fences in root/docs/memory/opencode docs.
  Referenced local paths (config, docs, memory, src modules) verified present.
- No Phase 8. No new work started.
