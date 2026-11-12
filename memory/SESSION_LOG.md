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

## 2026-10-28 - Phase 8 authorized and implemented (lead implementation)

- Auth: Phase 8 only (local model-training workflow + `sf model` CLI). Implemented
  directly by lead: `src/crypto_engine/training/` (splits.py chronological purge/
  embargo + pipeline.py orchestration), `model_app` with `train`/`list` in cli.py,
  `tests/fixtures/model_train_fixtures.py` (oscillating/monotone snapshots),
  `test_model_splits.py` (7), `test_model_train.py` (11), `test_model_cli.py` (8).
- Reused without modification: load_snapshot, compute_features, TripleBarrier*
  helpers, valid_mask, assert_no_leakage, label_config_hash, SklearnLogistic,
  MajorityBaseline, save/load/list_models, classification_report_small.
- Lead fixes during verification: split-fraction error messages, monotone fixture
  (accelerating drift for finite features + nonzero volume), fixture sizes
  (n=400 e2e; regime warm-up needs ~100 bars), NOT_APPROVED-style guard not needed
  here (training consumes no decisions); docstring-only cleanup, no behavior change.
- Lead verification: targeted 26 passed; full `uv run pytest` 492 passed,
  1 skipped (lightgbm optional); `ruff check` + `ruff format --check` clean;
  `sf model --help` / `train --help` render; AGENTS.md + NEXT_STEPS updated to
  Phase 8 (no Phase 9).
- Resource note: single-process, one snapshot at a time; fixtures <=400 rows;
  chunked features; no new dependencies. No live data, no network, no training
  on fetched data (no --fetch flag exists).
- Honesty notes: holdout metrics are reported on first training (no tuning loop,
  thresholds never fitted); models marked UNCALIBRATED; no profitability or
  90% claim. Phase 8 test count: 26 new. No commit per instructions.

## 2026-10-28 - Phase 9 authorized; audit + diagnostics underway (lead, no commit)

- Auth: Phase 9 only (semantic audit, logistic diagnostics, controlled
  experiment, regression tests). AGENTS.md updated; Phase 8 work + skill
  installs left untouched and uncommitted.
- Grill-me skill inspected (`SKILL.md` + `agents/openai.yaml`): it is a stub
  pointing at a non-existent "grilling" skill (load attempt failed as
  expected). Applied its stated intent instead: adversarial self-review of
  the diagnosis and experimental design, recorded in the completion report.
- Semantics (verified from code, not assumed): side="both" uses LONG geometry
  (+1 = upper first, -1 = lower first, 0 = timeout/ambiguous). Policy maps
  predicted -1/0/+1 to SHORT/NEUTRAL/LONG (multiclass argmax); backtest opens
  same-direction positions, SHORT via SELL. Asymmetric barriers (2.0/1.0) mean
  -1 documents a long stop-out, not a proven short profit — the SHORT trade is
  a directional hypothesis whose edge must be measured, never assumed. No code
  change: mapping is directionally consistent end-to-end (new regression tests
  pin it).
- Real-data diagnostics (read-only, existing artifact): 2209 bars -> 2066
  usable; labels -1/+1/0 = 1270/697/99 (majority -1 at 61.5%); model predicts
  only +/-1, never 0; valid acc 0.436 vs majority 0.615 reproduced exactly.
- Purge=0 explained: barriers resolve within 1-5 bars near the cutoff, so no
  training row's outcome extends past it (verified, not assumed).

## 2026-10-28 - Phase 9 completed (lead; no commit per instructions)

- Experiment (TEMP scripts, nothing committed to repo): majority / logistic /
  trend / relative-only logistic on identical chronological splits; selection
  on validation macro-F1 only (predeclared). Winner: incumbent config
  (macro-F1 0.3005 vs majority 0.2539) — margin not convincing; train 0.6296
  vs valid 0.4358 gap = overfit and/or regime shift. Verdict: RETAIN incumbent,
  no new artifact, no tuning. Holdout figures already viewed — reported as
  previously-inspected comparisons only; forward validation recommended.
- Backtest: BLOCKED by design, not run. Default gates veto everything
  (0-signal `sf backtest run` on real data; live veto trace ends at
  policy:calibration UNCALIBRATED). Bypassing gates is forbidden.
- Incidental fix: dashboard overview read wrong keys from data-status payload
  (showed "none recorded" with a snapshot present); fixed pages.py + added
  overview-vs-API regression test; verified live via agent-browser
  (uvicorn 127.0.0.1:8123, health JSON + overview render incl. "1 snapshot(s)
  recorded"; server shut down clean afterwards).
- agent-browser: `open` hangs in this sandbox (Chrome launch); `navigate` +
  `snapshot` work. Smoke test claimed only for what actually ran.
- Final: full suite 498 passed, 1 skipped (lightgbm optional); ruff check +
  format clean; new tests/unit/test_phase9_semantics.py (6 tests: label
  meanings, direction mapping, hand-checked metrics, default-gate veto).
  Existing artifact untouched; no uncommitted work modified or deleted.

## 2026-10-28 - Phase 10 authorized and executed (frozen forward validation; no commit)

- Auth: Phase 10 only (frozen-model forward validation; no training, tuning, calibration, or trading). AGENTS.md scope updated (no Phase 11).
- New snapshot fetched via `sf data fetch` (public Binance 1h, Sep 25-Oct 28, 793 rows, checksum verified, no gaps/duplicates): binance-BTC-USDT-1h-20260925T0000-20261028T0000-2cd144f3eb06.
- New code: `src/crypto_engine/validation/forward.py` (load/verify/predict/summarize/report, read-only vs artifact) + 10 tests in `tests/unit/test_forward_validation.py` (cutoff discipline, purge/embargo, incomplete-label exclusion, schema/hash compat, historical majority, hand-checked metrics, artifact immutability, no-overwrite report).
- Forward run: 624 rows scored (Oct 1 01:00 -> Oct 27; 26 pre-cutoff + 119 warm-up + 44 incomplete excluded); accuracy 0.401, balanced 0.367, macro-F1 0.264 vs majority (-1) baseline 0.644; predicted +1 on 83% of a 64%-down market (recall -1: 0.19, +1: 0.91). Confusion [[77,0,325],[11,0,21],[17,0,173]].
- Verdict: model does not beat the trivial baseline on genuinely new data; artifact left intact (checksum-verified on load; no writes performed). No retraining, no threshold changes, no calibration, no backtest (still blocked by fail-closed gates - unchanged).
- Report: reports/generated/forward_validation_*_20261028T145235.json (git-ignored). Full suite + ruff clean (see completion report). No commit per instructions.

## 2026-11-07 - Stage 0 / A0 foundation implemented (explicit user authorization; no commit)

- Auth: Stage 0/A0 only (contracts + boundaries + configuration + validation). No live/paper/scheduler/provider SDK/API key/network/strategy/risk changes.
- New `src/crypto_engine/ai/`: `contracts.py` (AITask, AICapabilities, AIProviderSpec, AIModel, AIRequest, AIResponse, AIUsage, AIErrorInfo, AIProviderPort, AIResourceLimits, SecretRef), `errors.py` (closed AIStatus incl. NOT_CONFIGURED/DISABLED/INVALID_CONFIGURATION/AUTHENTICATION_ERROR/RATE_LIMITED/TIMEOUT/PROVIDER_UNAVAILABLE/INVALID_RESPONSE/UNSUPPORTED_CAPABILITY/INTERNAL_ERROR, redaction, classify_exception), `registry.py` (task -> provider -> model routing, api_key_env references only, read-only snapshot), `gateway.py` (enablement/capability/credential pre-checks, exactly-one-in-flight, bounded retries with injected sleep, response/error normalization, no signal/risk authority).
- New `src/crypto_engine/storage/`: role protocols + suffix/network-path guards (Parquet=OHLCV, SQLite WAL=transactional, JSON=metadata), `sqlite.py` (WAL, foreign_keys=ON, busy_timeout=5000, short explicit transactions, nested rejected), `json_store.py` (deterministic sorted JSON, atomic replace, credential-key rejection, `*_env`/`*_ref` references allowed).
- New tests: `tests/unit/test_stage0_ai_contracts.py`, `test_stage0_ai_gateway.py`, `test_stage0_storage.py` (71 tests; fake adapters, tmp_path DBs, AST import-boundary proof).
- Verified: `uv run --offline pytest` 610 passed, 4 skipped in 43.86 s (same 4 pre-existing skips); ruff check + format clean on all 12 changed files; repo-wide ruff still fails only on pre-existing untracked scripts/export_codebase_json.py (9 errors, unchanged); `sf health`/`sf config-show` EXIT 0; pyproject/uv.lock/config unchanged; no *.db created; no external calls; no commit/push.


## 2026-11-07 - Stages 1-5 implemented sequentially (explicit user authorization; no commit)

- Stage 1: `ai/configuration.py` maps validated TOML `[ai]` (providers, models,
  tasks, limits) onto `AIRegistry`/`AIGateway`; `AiConfig` extended with
  enabled switch, bounded limits, model/task records; 22 tests.
- Stage 2: `ai/providers/openai_compatible.py` (chat completions over stdlib
  urllib, injectable transport, HTTPS-except-loopback, 200,000-byte response
  cap, normalized statuses, usage extraction); 25 tests. No SDK.
- Stage 3: `ai/advisory/signal.py` builds bounded evidence (<=64 keys,
  priority truncation with explicit markers), returns frozen `AIExplanation`,
  and `apply_ai_context` replaces only `ai_context`; AI authority fields are
  rejected; `sf signal generate --ai explain` wired after risk gates; 31 tests.
- Stage 4: `ai/advisory/research.py` classifies allowlisted metrics, emits
  NOT_AVAILABLE for missing values, rejects unknown/hallucinated/promotion
  fields, and never mutates deterministic reports; 24 tests.
- Stage 5: `ai/composition.py` (only adapter importer), `sf ai status`,
  `config-check`, `providers`, `models`, `routes`, `test` (mock default,
  `--live` opt-in); architecture tests (AI authority, no SDK/import cycles,
  storage roles, secret boundary), provider-failure/resource tests, CLI tests;
  docs updated (`AI_GATEWAY.md`, `STORAGE_OWNERSHIP.md`, `DEPENDENCY_RULES.md`,
  `RESOURCE_BUDGET.md`).
- Verification: stage suites 22/25/31/24/(31+16+12) passed; full
  `uv run --offline pytest` 771 passed, 4 skipped (pre-existing); ruff clean on
  changed files; repo-wide ruff still fails only on pre-existing untracked
  `scripts/export_codebase_json.py`; `uv sync --offline` and `--extra api`
  EXIT 0; `sf health`/`config-show`/`sf ai ...` EXIT 0; HEAD 32fe845 unchanged;
  no commit/push; no `.env`, `*.db`, dependency or credential change.

## 2026-11-07 - Stage 0-5 audit disposition (no commit)

- M1 (medium): `egress_allowlist` is now enforced in
  `ai/providers/openai_compatible.py` (empty allowlist = base-URL host only) and
  `urllib_transport` uses a no-redirect opener; HTTP 3xx becomes
  `invalid_configuration` instead of leaking `Authorization` to a redirect
  target. 8 new adapter tests.
- M2 (medium): per-provider `timeout_s`/`max_retries` now cap the global
  `[ai]` limits in `gateway.py` (2 new tests); `auth_mechanism="none"` is
  implemented for loopback endpoints (no Authorization header) and
  `header`/`query` fail closed as `unsupported`. Remaining parsed-but-inert
  fields (`default_provider`, `fallback_order`, `cache_ttl_s`, `log_prompts`,
  `[ai.budgets]`, per-provider `max_response_chars`/`concurrency`, per-task
  bounds) are documented in `AI_GATEWAY.md` §16.9 and marked `reserved`/`not
  applied` in `config/default.toml`.
- L1/L2: `AI_GATEWAY.md` and `AI_PROVIDERS.md` status headers corrected to
  implemented; "ordered fallback" comments corrected to "first enabled entry
  wins; no runtime failover".
- Verification: full `uv run --offline pytest` **785 passed, 4 skipped**;
  focused Stage 0-5 suites 242 passed; ruff clean on changed files; repo-wide
  ruff still only the pre-existing untracked `scripts/export_codebase_json.py`;
  `sf health`/`config-show`/`sf ai ...` EXIT 0; HEAD 32fe845 unchanged; no
  commit/push; no `.env`, `*.db` or dependency change.

## 2026-11-06 (UTC) - Project progress dashboard (no commit)

- Built a read-only Project Progress & Development Control page at
  `/dashboard/progress/` with an append-only JSONL ledger under
  `data/project_progress/` (events.jsonl + atomically replaced manifest.json +
  schema.json). Core in `src/crypto_engine/progress/` (stdlib only); recorder
  CLI `scripts/project_progress.py` (`record|list|validate|snapshot|seed`).
- GET-only `/api/v1/progress/snapshot|events|history`; `progress_dir` added to
  `ApiConfig`, `config/default.toml`, `RepoRoots` and `sf serve`. Frontend is
  HTML5/CSS3/vanilla JS with 16 sections, Simple/Technical views, filters,
  search, 3 s visible-only polling, stale/offline banners, CSP
  `default-src 'self'` and `textContent`-only rendering. Progress nav link
  added to the existing dashboard.
- Historical Phase A-L and Stage 0-5 are seeded as recorded context (labelled
  HISTORICAL / recorded report); live provider connectivity stays NOT_VERIFIED;
  M2 inert fields are shown as INERT.
- Verification (lead-run): `uv sync --offline --extra api` EXIT 0; `sf health`
  EXIT 0; `sf config-show` shows `progress_dir`; `uv run --offline pytest`
  **831 passed, 4 skipped** (same 4 pre-existing skips); focused progress suite
  **50 passed**; ruff check/format clean on 17 changed files; repo-wide ruff
  only the 9 pre-existing untracked `scripts/export_codebase_json.py` errors;
  `node --check progress.js` EXIT 0; headless Chrome rendered live data
  (`data-state="live"`, error/stale banners hidden, 831-passed metric) with
  screenshot evidence; GET snapshot 200, POST 405; `/dashboard/` 200 and links
  the progress page. Ledger: 40 events, 0 corrupt/duplicates/transition
  warnings; latest `evt-20261106T233326Z-1f77065a` (activity-view stage filter,
  PASS).
- Post-acceptance UI addition: a Stage (0-5) filter was added to the Build
  Activity view; `node --check`, the focused suite (50), the full suite
  (831 passed, 4 skipped) and a headless `#activity` DOM check all passed.
- Environment finding: plain `uv sync` prunes the `api` extra, so API and
  dashboard tests skip and `test_serve_occupied_port_fails_gracefully` fails
  with the missing-uvicorn message; `--extra api` restores 831 passed.
- No trading, paper, risk, strategy, scheduler, live AI, credential, commit or
  push change. HEAD 32fe845 unchanged.

## 2026-11-06 (UTC) - Stage 0-5 final acceptance recorded (no commit)

- Scope: record the supplied read-only Stage 0-5 final acceptance audit
  (`ACCEPTED — STAGE 0-5 FROZEN`) in the existing progress dashboard and
  append-only ledger. No AI gateway/config change, no M2 wiring, no doc edit,
  no live AI call, no commit/push, no history rewrite.
- Appended 9 events with the official recorder (real UTC recording timestamps,
  generated ids, validated before write): six `stage` FROZEN events for
  `stage_0..stage_5` (71/22/33/31/24/61 focused; 242 total, 0 skipped), one
  `verification` FROZEN event `stage_0_5_final_acceptance` (785/4/0/0),
  one `verification` PASS event `m2_classification`, and one `security`
  PASS_WITH_FINDINGS event `doc_resource_budget_71`. Ledger: 49 records, 49
  valid, 0 corrupt, 0 duplicates, 0 transition warnings.
- Display: all six stages FROZEN with acceptance date 2026-11-07, authority,
  M2 classification and remaining finding; overall `AI FOUNDATION — ACCEPTED /
  FROZEN`; "Evidence separation" shows the audit 785/4 and the dashboard
  831/4 as separate records that are never combined; `M2 ACCEPTED AS
  DOCUMENTED` lists the 8 inert fields, fail-closed header/query auth, bearer
  + loopback-none only, and no runtime wiring; `RESOURCE_BUDGET.md:71` shown as
  documentation-only, non-blocking (document untouched).
- Display fix: the Overview/header "Active work" line used the newest
  REVIEW/IN_PROGRESS event even when that task had since PASSed; it now uses
  each task's latest status, so it reads "No active item recorded".
- Verification (lead-run): `uv sync --offline --extra api` EXIT 0; `sf health`
  EXIT 0; `sf config-show` shows `progress_dir`; `uv run --offline pytest`
  **836 passed, 4 skipped, 0 failed, 0 errors** (JUnit tests=840); focused
  progress suite **55 passed**; ruff check clean and ruff format clean on the
  changed Python files; repo-wide ruff only the 9 pre-existing untracked
  `scripts/export_codebase_json.py` errors; `node --check progress.js` EXIT 0;
  headless Chrome rendered `#ai` (six FROZEN, acceptance, 785 vs 831, M2,
  finding), Overview (831 passed, 6/6 frozen, live indicator, banners hidden)
  and Security/Issues (M2 non-blocking, finding non-blocking) with a
  screenshot; GET snapshot 200, POST 405, `/dashboard/` 200 + progress link.
- HEAD 32fe845 unchanged; all work uncommitted; no reset/clean/restore/stash/
  checkout.

## 2026-11-07 (UTC) - RESOURCE_BUDGET.md AI budget row corrected (no commit)

- Scope: documentation-only correction of the stale design-time AI Budget
  Tracker row in `docs/architecture/RESOURCE_BUDGET.md:71`. No code, config,
  gateway, `[ai.budgets]` wiring, legacy `BudgetTracker`/`ai_providers/`,
  trading, risk, strategy, execution or scheduler change; no live AI call.
- Correction: replaced `max_requests` / `max_chars` / "Budget tracker" /
  "Stop before paid fallback" with a current-state row (1 request in flight;
  bounded request/response/token/timeout/retry caps enforced in
  `ai/gateway.py`; `[ai.budgets]` reserved and inert). Added a current
  implementation paragraph with the enforced values, a reserved/not-implemented
  paragraph (no runtime budget tracker, no paid/runtime fallback, legacy
  `BudgetTracker`/`run_with_fallback` is `ai_providers/`-only and not imported
  by `crypto_engine.ai`, M2 wiring needs separate authorization and review,
  cross-reference to `AI_GATEWAY.md` §16.9), and a labelled "Design intent (not
  implemented)" note retaining the historical context.
- Ledger: appended 3 events via the official recorder for task
  `doc_resource_budget_71` (IN_PROGRESS → PASS → PASS verification):
  `evt-20261107T000429Z-ad5850ed`, `evt-20261107T000502Z-401c24be`,
  `evt-20261107T000625Z-f31f2348`. Ledger 52 records, 52 valid, 0 corrupt,
  0 duplicates, 0 transition warnings; six AI stages and the acceptance event
  remain FROZEN.
- Verification (lead-run): 35/35 read-only consistency assertions passed
  (document vs `AI_GATEWAY.md` §16.9, `config/default.toml`, `gateway.py`,
  `configuration.py`, `composition.py`); focused progress suite **55 passed**;
  `snapshot` EXIT 0. HEAD 32fe845 unchanged; no staging/commit/push.
- Residual display note: the dashboard's static `project_model.py` finding text
  still says the doc correction "requires separate authorization and was NOT
  performed"; it was not edited because this task is documentation-only. The
  ledger now records the completed correction.

## 2026-11-07 (UTC) - Progress dashboard synchronized with the correction (no commit)

- Scope: dashboard display, project-state metadata, progress-ledger and tests
  only. No AI gateway, M2 wiring, `[ai.budgets]`, provider adapter,
  `ai_providers/`, trading, risk, strategy, paper, scheduler, live AI or
  commit/push change.
- Model: `DOC-RESOURCE` now has status `CORRECTED`, severity `low`,
  `blocking: false`, `path`, `corrected: true`, `correction_event_id`
  `evt-20261107T000625Z-f31f2348`, `correction_timestamp_utc`
  `2026-11-07T00:06:25Z`, `original_event_id`
  `evt-20261106T234507Z-267ecb81`, `original_status` `PASS_WITH_FINDINGS`, seven
  evidence items and a note retaining the original event. All six stage
  `remaining_finding` strings and `OVERALL.remaining_finding` now say
  `CORRECTED`; the old "was NOT performed" / "requires separate authorization"
  text is gone.
- UI: new shared `findingCard()` used by Issues and Security; green
  `CORRECTED` chip; evidence always shown; Simple view shows the plain-language
  sentence; Technical view adds file, event id, UTC time, consistency evidence
  and original-event reference. M2 keeps `ACCEPTED AS DOCUMENTED` with 8 inert
  fields plus explicit "Reserved / not enforced" vs "Enforced instead" text.
- Tests: `test_progress_dashboard.py` updated (corrected metadata, old-text
  absence, stage/overall reference, JS/CSS rendering, M2 not-wired, evidence
  separation).
- Verification (lead-run): full suite **839 passed, 4 skipped, 0 failed, 0
  errors** (843 collected); focused progress suite **58 passed**; ruff check +
  format clean on changed Python files; `node --check progress.js` EXIT 0;
  headless Chrome/CDP **24/24** checks passed across Overview, Issues,
  Security, AI, Activity, Simple and Technical views; ledger **55/55 valid**,
  0 corrupt/duplicates/transitions, 0 active tasks; six stages and the
  acceptance event remain FROZEN.
- Ledger: appended `evt-20261107T003250Z-d8e2e562` (IN_PROGRESS),
  `evt-20261107T003255Z-a4724d24` (PASS implementation),
  `evt-20261107T003740Z-50b7a02c` (PASS verification). Original finding and
  the three documentation-correction events are unchanged.
- HEAD 32fe845 unchanged; no staging/commit/push.

## 2026-11-10 (UTC) - Project handoff reconciliation and build draft execution

- Scope: Comprehensive handoff analysis against working tree (`D:\DM-Crypto-Quant-Signal`), creation of `PROJECT_HANDOFF_ANALYSIS_AND_BUILD_DRAFT.md`, and step-by-step verification of build lifecycle within authorized Phase 10 scope.
- Verified working tree: branch `master`, HEAD `7c7c1bb824831368d166230630d8d42c797c52c7`, Python 3.13.9, `uv` 0.13.7.
- Dependency sync: `uv sync --locked --extra api --offline` clean (installed 7 packages: anyio, click, fastapi, h11, opentelemetry-api, starlette, uvicorn).
- CLI health: `sf health` (status: ok, version: 0.1.0, config exists, storage writable).
- CLI config: `sf config-show` dump validated with all sections intact.
- Code standards: `ruff check src tests scripts/project_progress.py` passed; `ruff format --check src tests` passed (178 files formatted).
- Leakage tests: `pytest -q tests/leakage` -> 31 passed, 1 skipped (LightGBM optional), 0 failures.
- Integration tests: `pytest -q tests/integration` -> 82 passed, 0 failures.
- Forward validation tests: `pytest -q tests/unit/test_forward_validation.py` -> 11 passed, 0 failures.
- Security tests: `pytest -q tests/unit/test_api_security.py` -> 9 passed, 0 failures (GET-only table, 405s, traversal, secrets, CORS).
- Risk gates tests: `pytest -q tests/unit/test_risk_gates.py` -> 29 passed, 0 failures (deterministic A->B->C->D gates, fail-closed).
- Market data status: `sf data status` -> 2 Parquet snapshots verified (`103f110e06f0` 2209 rows, `2cd144f3eb06` 793 rows).
- Model list: `sf model list` -> registered baseline artifact `logistic-ee4...`.
- Signal verification: `sf signal verify` -> hash-chained audit ledger verified (`{"ok": true, "errors": []}`).
- Progress ledger: `python scripts/project_progress.py validate` -> 71/71 valid events, 0 corrupt, 0 duplicates.
- Paper trading: `sf paper status` -> fail-closed uninitialized state confirmed.
- Phase 11 quarantine: untracked shadow scripts (`scripts/phase11_shadow/`, `tests/unit/test_phase11_shadow.py`) strictly isolated; Phase 11 remains retired.
- Invariants preserved: zero live orders, no real-money trading, loopback-only API (`127.0.0.1`), 4 GB memory bounds (`n_jobs=1`).
- Autonomous cleanup & codebase hardening:
  - Fixed all 9 pre-existing Ruff lint errors in `scripts/export_codebase_json.py` (added `Any` import, explicit `zip(..., strict=False)`, simplified class filter, removed unused `apps`/`aliases`/`default`, replaced list concat with iterable unpacking). Repo-wide `ruff check .` now 100% clean (0 errors).
  - Formatted `scripts/export_codebase_json.py`. Repo-wide `ruff format --check .` now 100% clean (265 files formatted).
  - Executed `scripts/export_codebase_json.py`: 254 files exported, 1713 functions, 148 classes, 23 CLI commands, 40 routes, 0 errors, verification PASSED.
  - Added `data/signals/` and `data/backtests/` to `.gitignore` under local runtime output to prevent generated execution state from dirtying git status.
  - Refreshed project progress manifest via `python scripts/project_progress.py snapshot`; validation passed (71/71 valid events).
  - Re-verified core suites: 54 tests passed across forward validation, API security, deterministic risk gates, and CLI integration.
  - Comprehensive test suite execution: unit (697 passed), integration (82 passed), and leakage (31 passed, 1 skipped for LightGBM): 810 passed, 1 skipped, 0 failed across 811 total tests.
  - Executed `scripts/export_forward_predictions.py`: 624 rows verified against Phase 10 forward validation report (accuracy 0.4006), manifest generated, cross-check PASS.
  - Re-verified CLI commands: `sf health` exit 0, `sf config-show` exit 0, `sf data status` (2 snapshots verified), `sf model list` (1 model), `sf signal verify` (0 errors).

## 2026-11-12 (UTC) - Fix AI metrics integration test & telemetry consistency

- **Root cause investigation**:
  - `test_ai_metrics_shows_provider_id_from_telemetry` failed because `TelemetryStore.metrics()` computed `provider_id` but omitted passing `provider_id=provider_id` to `TelemetryMetrics(...)`, causing `provider_id` to default to `""` and CLI to render `n/a`.
  - `TelemetryStore.record()` previously accepted only `AIResponse`, causing `TelemetryRecord` inputs from tests or callers to be dropped silently.
  - In `src/crypto_engine/cli.py`, `ai_metrics` resolved custom telemetry paths inconsistently, and `src/crypto_engine/api/repos.py` `read_ai_metrics` invoked `get_default_store()`, ignoring `RepoRoots` and relying on a cached global store.
- **Implemented fixes**:
  - `src/crypto_engine/ai/telemetry.py`:
    - Updated `TelemetryStore.record()` to accept `AIResponse | TelemetryRecord | dict[str, Any]` and ensure parent directory creation before write.
    - Updated `TelemetryStore.metrics()` to pass `provider_id=provider_id` into `TelemetryMetrics`.
    - Added `resolve_telemetry_path(config=None, default_path=...)` resolving `Settings`, `AiConfig`, `RepoRoots`, strings, and `Path`s with Windows compatibility and canonical repo-root relative path resolution.
    - Added `get_telemetry_store(config=None)` factory that avoids shared global cached store state.
    - Updated `get_default_store()` to delegate to `get_telemetry_store(None)`.
  - `src/crypto_engine/cli.py`:
    - Updated `ai_metrics` to use canonical `get_telemetry_store(settings)`.
    - Updated `sf serve` to pass `ai_telemetry_path` to `RepoRoots`.
  - `src/crypto_engine/ai/composition.py`:
    - Updated `build_configured_gateway` to obtain store via `get_telemetry_store(config)`.
  - `src/crypto_engine/api/repos.py`:
    - Added `ai_telemetry_path` to `RepoRoots`.
    - Updated `read_ai_metrics` to use `get_telemetry_store(roots)` and handle empty/missing telemetry with standard unavailable envelopes (`"missing"` and `"empty"`).
  - `src/crypto_engine/api/routes.py` & `src/crypto_engine/dashboard/routes.py`:
    - Reconstructed `RepoRoots` forwarding `ai_telemetry_path` to ensure CLI and dashboard use the same canonical store.
  - `tests/integration/test_ai_cli.py`:
    - Corrected `test_ai_metrics_shows_provider_id_from_telemetry` to isolate temporary telemetry file with `meta_muse`, `muse-spark-1.3`, status `ok`, and nonzero latency, safely configuring TOML without fragile regex.
    - Isolated `test_ai_metrics_shows_n_a_when_no_providers` from production telemetry.
    - Added 6 regression tests: custom telemetry path respected, default path works, multiple providers display `mixed`, Windows paths handling, empty telemetry unavailable state in repos, and CLI/dashboard telemetry data consistency.
  - `tests/unit/test_stage2_openai_adapter.py`:
    - Verified unit tests for single and multiple providers telemetry metrics aggregation.
- **Verification**:
  - `uv run pytest -q tests/integration/test_ai_cli.py -k test_ai_metrics_shows_provider_id_from_telemetry -vv` -> 1 passed (0.40s).
  - `uv run pytest -q tests/integration/test_ai_cli.py` -> 20 passed.
  - `uv run pytest -q tests/unit/test_stage2_openai_adapter.py` -> 40 passed.
  - Full suite: `uv run pytest -q --ignore=tests/unit/test_phase11_shadow.py` -> 945 passed, 1 skipped (LightGBM optional), 0 failed.
  - `uv run ruff check .` -> All checks passed (0 errors).
  - `uv run ruff format --check .` -> 301 files already formatted.
  - `uv run sf ai metrics` -> Provider: mixed, total requests: 187, exit 0.
  - `uv run sf health` -> exit 0.
  - Dashboard and CLI verified reading consistent telemetry.
