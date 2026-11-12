# CURRENT_STATE.md — SignalForge verified state (Phase 10 final, Phase 11 blocked)

## Current state (verified 2026-11-07)

- **Phase**: Phase 10 (frozen-model forward validation) authorized, executed and
  committed. Phases 1–10 are implemented. User has explicitly authorized
  completion of all remaining documented development phases (superseding prior
  Phase 11/Phase M restrictions).
- **Git**: branch `master`, no configured remote, 0 staged files. HEAD
  `7c7c1bb824831368d166230630d8d42c797c52c7`. All five 2026-11-07 workstreams are
  committed: `ce94f24` Phase 10 forward validation, `74ee126` dashboard
  dataset-availability fix, `fbe3e35` progress runtime data local-only,
  `f5adec5` AI/storage contracts vs design intent, `7c7c1bb` gitignore local
  runtime state; earlier `a768063` (AI stages 0-5), `a1dfda0` (progress
  dashboard), `6c498e9` (Phase 8), `a339526` (Phase 9). **Uncommitted:
  `AGENTS.md` (user-owned) and these four `memory/` files only.** No Phase 11
  path appears anywhere in Git history.
- **Fresh verification (2026-11-10)**: `uv run --offline pytest
  -p no:cacheprovider --ignore=tests/unit/test_phase11_shadow.py -q tests` →
  **873 tests, 872 passed, 0 failed, 0 errors, 1 skipped**
  (811 original + 62 new: 24 domain, 13 analytics, 9 bootstrap, 16 architecture);
  comprising unit (759 passed), integration (82 passed), leakage (31 passed / 1 skipped).
  Repo-wide `uv run --offline ruff check .` → All checks passed (0 errors);
  repo-wide `uv run --offline ruff format --check .` → 299 files already formatted;
  `uv run sf health` exit 0; `uv run sf config-show` exit 0;
  `uv run sf data status` exit 0 (2 snapshots verified);
  `uv run sf model list` exit 0 (logistic baseline verified);
  `uv run sf signal verify` exit 0 (0 errors, hash chain valid);
  `uv run sf ai status` exit 0 (AI disabled, advisory only);
  Phase 10 focused suite 11 passed / 0 failed / 0 skipped;
  dashboard suite 10 passed / 0 failed / 0 skipped; markdown consistency over
  `docs/architecture/**` + `docs/ARCHITECTURE.md` 28 files / 0 findings;
  `git diff --check` exit 0.
- **Evidence hashes (unchanged)**: `artifact.pkl`
  `42A8EA4BAFD248C727538E233E5335337917BCD1664D28421DAA70331ECCF4DB`;
  `record.json` `92D79E5923AD0C3436A3D2AEB3F6935B3A1CC03BEB1FD198C399E129FB84FBD0`;
  Phase 10 report `306AEA3A5375B88F13E0502818DE74C0458B00636458DCB750BA4011A0E42B6A`
  (still `snapshot_id` null, deliberately not rewritten). Both snapshots load
  with checksum verification and matching ids: `103f110e06f0` (2209 rows) and
  `2cd144f3eb06` (793 rows).
- **Progress ledger**: `data/project_progress/` 71 records, validate ok, 0
  corrupt / 0 duplicates / 0 transition warnings; directory is git-ignored.
- **Phase 10 remediation (all three findings closed)**: F10-01
  `resolve_historical_snapshot_id()` in `src/crypto_engine/validation/forward.py`
  reports a historical id only when the artifact record's `dataset_snapshot_id`
  matches the loaded manifest id and checksums agree (no recorded id → null;
  missing id / id mismatch / checksum mismatch → `ValueError`); F10-02
  append-only dated correction in `DECISIONS.md` (2026-11-07) with authoritative
  label hash `74bd147242856a17ea17cd17916cf9c674d98e25c7fbb4c526445875e366a072`
  (original entry preserved); F10-03 test helper `_small_frame` takes a pytest
  `tmp_path` root, no `tempfile.mkdtemp` remains, no new `fwtest*` dirs (132
  pre-existing ones left alone).

## Phase 7 implementation detail (historical, implemented 2026-10-28)

- **Phase**: 7 was authorized and implemented (2026-10-28) — read-only FastAPI
  (`/api/v1`, GET-only), read-only repository adapters, server-rendered
  dashboard, `sf serve` (loopback-only default), security tests, operational
  docs, final verification. (Superseded as current phase by Phases 8–10.)
- **Implemented (Phase 7, verified 2026-10-28)**:
  - `src/crypto_engine/api/` — `security.py` (ID validation, path
    confinement, secret scanning/redaction, error sanitizer), `schemas.py`
    (versioned envelope + pagination + error envelope), `repos.py`
    (bounded readers over signal history/registry/snapshots/experiments/
    holdout/backtest runs/paper state; unavailable-not-fabricated;
    read-only `SignalHistory` binding without mkdir side effects),
    `routes.py` + `progress_routes.py` (33 GET endpoints incl. health/readiness/signals/models/
    validation/experiments/research/backtests/paper/audit/data/progress),
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
- **Stage 0-5 freeze recorded in the progress dashboard (2026-11-07 audit,
  recorded 2026-11-06 UTC; ledger data is git-ignored by design, dashboard code
  committed in `a1dfda0`)**: the supplied final acceptance audit
  (`ACCEPTED — STAGE 0-5 FROZEN`) was recorded as nine appended ledger events
  (no history rewritten): six `stage` events moving `stage_0..stage_5` from
  PASS/PASS_WITH_FINDINGS to FROZEN with focused evidence 71/22/33/31/24/61
  (242 total, 0 skipped), plus `stage_0_5_final_acceptance` (FROZEN),
  `m2_classification` (PASS) and `doc_resource_budget_71`
  (PASS_WITH_FINDINGS). Audit-time full suite 785 passed / 4 skipped / 0
  failed / 0 errors; 120 architecture/security/resource/provider-failure
  tests; ruff check + format (28 files); AI offline CLI; `sf ai test` disabled
  with no network. These are labelled recorded audit evidence, not fresh runs.
  The dashboard's own 831-pass run (`evt-20261106T232449Z-3627b58f`) is kept as
  a separate record and is never combined with 785 or 242. `project_model.py`
  now carries FROZEN stages, the overall label `AI FOUNDATION — ACCEPTED /
  FROZEN`, `M2 ACCEPTED AS DOCUMENTED` (8 inert fields + fail-closed auth
  behavior) and the non-blocking `RESOURCE_BUDGET.md:71` finding (corrected in a
  later documentation-only task). `progress.js` renders acceptance/authority/M2/finding per stage,
  an "Evidence separation" panel (785 vs 831) and non-blocking tags. Display
  fix: "Active work" now uses each task's latest status, so a superseded REVIEW
  event is not shown as current work. Verification (lead-run):
  `uv run --offline pytest` **836 passed, 4 skipped, 0 failed, 0 errors**
  (JUnit tests=840); focused progress suite **55 passed**; ruff check clean and
  ruff format clean on the changed Python files; `node --check progress.js`
  EXIT 0; ledger validate 49/49 valid; headless Chrome rendered `#ai` with six
  FROZEN stages, acceptance, M2, finding and the 785/831 separation, Overview
  still shows 831 passed and 6/6 frozen; GET snapshot 200, POST 405.

- **RESOURCE_BUDGET.md:71 documentation corrected (2026-11-07; the correction is
  committed as of HEAD `7c7c1bb`)**:
  replaced the design-time AI budget row (`max_requests`, `max_chars`,
  "Budget tracker", "Stop before paid fallback") with a current-state row:
  1 request in flight plus enforced request/response/token/timeout/retry caps in
  `ai/gateway.py`; `[ai.budgets]` explicitly reserved and inert; no runtime
  budget tracker and no paid/runtime fallback wired; legacy `BudgetTracker` /
  `run_with_fallback` confined to `ai_providers/` and not imported by
  `crypto_engine.ai`; M2 wiring requires separate authorization (`AI_GATEWAY.md`
  §16.9). Historical design intent retained and labelled "Design intent (not
  implemented)". Verification: 35/35 read-only consistency assertions passed;
  focused progress suite 55 passed; ledger 52/52 valid, 0 corrupt/duplicates/
  transitions; six AI stages and the `stage_0_5_final_acceptance` event remain
  FROZEN. No code, config, gateway, `ai_providers/`, trading, risk, strategy,
  execution or scheduler change.

- **Dashboard synchronized with the RESOURCE_BUDGET correction (2026-11-07;
  committed as of HEAD `7c7c1bb`)**: the `DOC-RESOURCE` finding now displays `CORRECTED` (severity
  low, non-blocking) with seven correction-evidence items, the completion event
  `evt-20261107T000625Z-f31f2348` and UTC time `2026-11-07T00:06:25Z`. The
  original finding event `evt-20261106T234507Z-267ecb81` keeps
  `PASS_WITH_FINDINGS` in history. All six stage `remaining_finding` strings and
  `OVERALL.remaining_finding` reference the correction. `progress.js` uses a
  shared `findingCard` (Issues + Security): Simple view shows the plain-language
  correction sentence; Technical view adds file path, event id, UTC time,
  consistency evidence and original-event reference. M2 stays `ACCEPTED AS
  DOCUMENTED` with 8 inert fields plus explicit "Reserved / not enforced" vs
  "Enforced instead" text. Verification (lead-run): `uv run --offline pytest`
  **839 passed, 4 skipped, 0 failed, 0 errors** (JUnit tests=843); focused
  progress suite **58 passed**; ruff check + format clean; `node --check`
  EXIT 0; headless Chrome/CDP **24/24** checks passed; ledger 55/55 valid, 0
  corrupt/duplicates/transitions, 0 active tasks; six stages and the acceptance
  event remain FROZEN. Display-only metadata — no ledger status enum change, no
  gateway/M2 wiring, no live AI call; no commit/push at the time (now committed).

- **Limitations**: no live connectivity proof for providers; holdout never
  touched by API (read-only ledger state only); dashboard shows recorded data
  only; no profitability/accuracy/live-readiness/cloud claim. Paper fills
  remain hypothetical. `uv run` without `--offline` may hang in this sandbox
  (no package network) — verification used `--offline` where needed.
- **Phase 9 (authorized 2026-10-28, completed; committed as `a339526`, dashboard
  fix in `74ee126`)**: label→direction
  semantics verified consistent (no model-code changes); logistic diagnosed
  (majority class -1 at 61.5%, never predicts 0-class, train 0.63 → valid 0.44
  overfit/regime gap, relative features dominate); validation-only experiment
  retained incumbent (macro-F1 0.3005 vs majority 0.2539, not convincing);
  trading backtest blocked by fail-closed gates (0-signal run documented);
  dashboard contract bug fixed + browser-verified; new regression tests.
- **Phase 10 (authorized 2026-10-28, completed; committed as `ce94f24`)**: frozen-model
  forward validation on new Sep 25–Oct 28 snapshot (793 rows); 624 rows scored
  after Oct 1; accuracy 0.401 vs majority 0.644; model predicted +1 on 83% of
  a down market; report written to reports/generated/ (git-ignored); no
  training, tuning, calibration, or backtest performed. All three Phase 10
  findings (F10-01/02/03) are closed — see the current-state section above.
- **Phase 8 (authorized 2026-10-28, implemented and verified; committed as
  `6c498e9`)**:
  `src/crypto_engine/training/` + `sf model train|list`; chronological
  60/20/20 splits with purge on `observable_at` + embargo bars; logistic
  baseline on local snapshots only (single/multi-class handled, single-class
  rejected explicitly); train-only scaling; majority-baseline comparison;
  registry persistence with provenance + reload verification; UNCALIBRATED
  marking; 26 new tests; full suite 492 passed, 1 skipped; ruff clean;
  single-process, no new dependencies, no network.

- **Stage 0 / A0 (explicitly authorized 2026-11-07, implemented; committed as
  part of `a768063`)**:
  provider-neutral AI foundation (`src/crypto_engine/ai/`: contracts, closed
  failure states, registry with task routing and `api_key_env` references
  only, gateway with single-flight concurrency, bounded retries, redaction)
  and storage-role foundation (`src/crypto_engine/storage/`: Parquet /
  SQLite-WAL / JSON boundaries, WAL pragmas, deterministic atomic JSON with
  credential rejection). 71 new tests; full suite 610 passed / 4 skipped;
  ruff clean on changed files. No provider SDK, no network, no secrets, no
  `*.db`, no dependency change; no commit at the time.

- **Stages 1-5 (explicitly authorized 2026-11-07, implemented; committed as
  `a768063`)**:
  Stage 1 configuration/routing (`ai/configuration.py`, extended `AiConfig` +
  `[ai]` defaults); Stage 2 one OpenAI-compatible adapter
  (`ai/providers/openai_compatible.py`, stdlib urllib, injectable transport);
  Stage 3 signal advisory (`ai/advisory/signal.py`, `AIExplanation`,
  `apply_ai_context`, `sf signal generate --ai explain`); Stage 4 research
  advisory (`ai/advisory/research.py`, FACT/CALCULATION/INTERPRETATION/
  UNCERTAINTY/NOT_AVAILABLE, promotion denied); Stage 5 composition root
  (`ai/composition.py`), `sf ai status|config-check|providers|models|routes|test`
  (offline mock default, `--live` opt-in), architecture/failure/resource/CLI
  tests and doc updates. Stage tests: 22 + 25 + 31 + 24 + (31 + 16 + 12) = 161
  new; full suite 771 passed / 4 skipped (same 4 pre-existing skips); ruff
  clean on changed files; `uv sync --offline` and `--extra api` EXIT 0; no
  dependency/secret/network/db change; no commit at the time.

- **Stage 0-5 audit disposition (2026-11-07; committed as part of `a768063`)**:
  M1 fixed — the
  OpenAI-compatible adapter now enforces `egress_allowlist` at request time
  (empty allowlist = base-URL host only) and installs a no-redirect urllib
  handler; a 3xx response is normalized as `invalid_configuration` instead of
  forwarding `Authorization`. M2 partly wired (per-provider `timeout_s` and
  `max_retries` now cap the global limits; `auth_mechanism="none"` works for
  loopback only; `header`/`query` fail closed as `unsupported`) and the rest
  documented as not enforced in `AI_GATEWAY.md` §16.9 + `config/default.toml`
  comments. L1 stale status headers and L2 "ordered fallback" comments fixed.
  Verification: full suite **785 passed, 4 skipped** (same pre-existing skips);
  stage suites green; ruff clean on changed files; repo-wide ruff still fails
  only on pre-existing untracked `scripts/export_codebase_json.py` (9 errors).

- **Project progress dashboard (authorized extension of Phase 7, implemented and
  verified 2026-11-06 UTC; code committed as `a1dfda0`, ledger data git-ignored)**:
  read-only Project Progress &
  Development Control page at `/dashboard/progress/` backed by an append-only
  ledger. New `src/crypto_engine/progress/` (stdlib only): `schema.py` (12
  statuses, hierarchy levels, UTC/timestamp, path and secret rejection),
  `ledger.py` (append-only JSONL, atomic manifest via `os.replace`, bounded
  newest-first scans, staleness, idempotent seed, read-only snapshot that never
  creates files), `git_state.py` (read-only git rev-parse/status/diff --stat),
  `project_model.py` (static Phase A-L + Stage 0-5 + architecture/security/
  resource/glossary data), `seed_data.py` (31 recorded historical events).
  `scripts/project_progress.py` provides `record|list|validate|snapshot|seed`.
  `src/crypto_engine/api/progress_routes.py` adds GET-only
  `/api/v1/progress/{snapshot,events,history}`; `progress_dir` added to
  `ApiConfig`, `config/default.toml`, `RepoRoots` and `sf serve`. Frontend is
  HTML5 + CSS3 + vanilla JS (16 sections, Simple/Technical views, filters,
  search, 3 s visible-only polling, stale/offline banners, CSP
  `default-src 'self'`, `textContent`-only rendering). Ledger lives in
  `data/project_progress/` (untracked): 55 events (40 seeded + 9 Stage 0-5
  freeze/acceptance + 3 RESOURCE_BUDGET correction + 3 dashboard-sync events),
  0 corrupt, 0 duplicates, 0 transition warnings; latest event
  `evt-20261107T003740Z-50b7a02c` (dashboard synchronization verification, PASS).
  Docs:
  `docs/PROJECT_PROGRESS_DASHBOARD.md`,
  `docs/PROJECT_PROGRESS_TRACKING.md`.
  Verification (lead-run): `uv sync --offline --extra api` EXIT 0; `sf health`
  EXIT 0; `sf config-show` shows `progress_dir = "data/project_progress"`;
  `uv run --offline pytest` **831 passed, 4 skipped, 0 failed**; focused
  progress suite **50 passed**; ruff check/format clean on 17 changed files;
  repo-wide ruff only the 9 pre-existing `scripts/export_codebase_json.py`
  errors; `node --check progress.js` EXIT 0; headless Chrome rendered the page
  with a live indicator, hidden error/stale banners and the 831-passed metric;
  GET snapshot 200 / POST 405; `/dashboard/` 200 and links the progress page.
  Environment finding: a plain `uv sync` prunes the `api` extra, so API and
  dashboard tests skip and `test_serve_occupied_port_fails_gracefully` fails
  with the missing-uvicorn message; `--extra api` restores the green suite.
  Limits: no live AI call, no trading/paper/risk/strategy/scheduler change, no
  public exposure, no commit/push at the time; browser automation (agent-browser) is not
  installed, so interactive/mobile behaviour was not automated.
