# PROJECT_MEMORY.md — SignalForge durable memory

## Architecture

- Standalone engine `SignalForge`; pipeline: exchange → market data → features →
  models → signals → risk gates → results; dashboard/API consume canonical results.
- 20 components (see `docs/ARCHITECTURE.md`); Phases 1–2 implement data→models;
  Phase 3 adds calibration + walk-forward/holdout validation (no production signals).
- Phase 4: AI interface + analysts + ablation (quant works with AI disabled).
- Phase 5: canonical signal engine + deterministic risk gates + audit history
  (`sf signal`; no orders, no positions, no live P&L).
- Phase 6: event-safe backtester + advisory-only paper sandbox
  (`sf backtest`/`sf paper`; hypothetical fills only, never orders).
- Phase 7: read-only API + dashboard + `sf serve`
  (GET-only, loopback-only, no predictions, no mutations, no orders).
- Phase 8: local model-training workflow (`sf model train|list`) — chronological
  purge/embargo splits, logistic baseline, registry provenance, UNCALIBRATED.
- Phase 9: label→direction semantics audit, logistic diagnostics, one
  validation-only experiment (incumbent retained), dashboard overview fix.
- Phase 10 (final authorized phase): frozen-model forward validation on a new
  public BTC/USDT 1h snapshot; no retraining/tuning/calibration/trading.
- Stage 0-5 AI extension: `src/crypto_engine/ai/` (contracts, gateway, registry,
  one OpenAI-compatible adapter, signal/research advisory, composition root,
  `sf ai` CLI) + `src/crypto_engine/storage/` role boundaries.
- Progress dashboard extension: read-only `/dashboard/progress/`, append-only
  ledger in `data/project_progress/` (git-ignored), `scripts/project_progress.py`.

## Git state (verified 2026-11-07)

- Branch `master`, no configured remote, 0 staged files.
- HEAD `7c7c1bb824831368d166230630d8d42c797c52c7`.
- 2026-11-07 chain, oldest first: `a339526` (pre-existing, Phase 9 label
  semantics) → `ce94f24` forward validation → `74ee126` dashboard
  dataset-availability fix → `fbe3e35` progress runtime data local-only →
  `f5adec5` AI/storage contracts vs design intent (27 files) → `7c7c1bb`
  gitignore local runtime state.
- Earlier commits (relative order not re-verified): `6c498e9` Phase 8,
  `a1dfda0` progress dashboard, `a768063` AI stages 0-5.
- No Phase 11 path appears anywhere in Git history.
- Uncommitted: `AGENTS.md` (user-owned) and these four `memory/` files only.

## Invariants

- Phase 10 is the final authorized phase; **Phase 11 is NOT authorized**.
  Anything beyond Phase 10 needs explicit user approval; default answer stop/ask.
- UTC-normalized market data with provenance; no silent fills; `NO_TRADE` valid.
- Deterministic risk gates independent of models/AI; no live orders.
- Secrets in env/`.env` only; never TOML/code/git. Accuracy ≠ win rate ≠ profitability.

## Conventions

- Python 3.12, uv isolated `.venv/`; typed modular code; Windows-safe `pathlib`.
- `uv sync` → `uv run sf health` → `uv run sf config-show` → `uv run pytest` → `uv run ruff check .`
- Phase 2: causal features + triple-barrier labels + baselines + registry; 4 GB
  bounds (`n_jobs=1`, `chunk_rows`, `max_rows_train=2000`); scores uncalibrated;
  pickle trusted-local-only.

## Verified commands (lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync` — clean (73 packages), `uv.lock` tracked
- [x] `uv run sf health` — status ok, package 0.1.0, storage writable
- [x] `uv run sf config-show` — validated binance/BTC+ETH+SOL/1h+4h dump
- [x] `uv run sf data --help` / `uv run sf data status` — 0 snapshots, no crash
- [x] `uv run pytest` — 37 passed in ~5s, no network
- [x] `uv run ruff check .` — all checks passed
- [x] `uv run ruff format --check .` — 63 files formatted-clean

## Verified commands (Phase 2 lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync` — clean, scikit-learn 1.9.1 installed (Windows wheels)
- [x] `uv run pytest` — 98 passed, 1 skipped (lightgbm optional) in ~13–28 s
- [x] `uv run pytest -rs` — skip reason: lightgbm not installed (legitimate)
- [x] `uv run ruff check .` — clean (after 1 unused-var fix in test_models.py)
- [x] `uv run ruff format --check .` — clean (5 files reformatted by lead)
- [x] `uv run sf health` / `sf config-show` — model/registry sections present

## Verified commands (Phase 3 lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync` — clean (no dep changes; sklearn 1.9.1 retained)
- [x] `uv run pytest` — 165 passed, 1 skipped (lightgbm optional) in ~14–16 s
- [x] `uv run pytest -rs` — skip reason: lightgbm not installed (legitimate)
- [x] `uv run ruff check .` — clean
- [x] `uv run ruff format --check .` — 98 files clean
- [x] `uv run sf health` / `sf config-show` — calibration/validation sections present

## Verified commands (Phase 4 lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync --offline` — clean, 73 packages (no network for re-lock; no new deps)
- [x] `uv run pytest` — 254 passed, 1 skipped (lightgbm optional) in ~16 s
- [x] `uv run pytest -rs` — skip reason: lightgbm not installed (legitimate)
- [x] `uv run ruff check .` — clean (lead fixed 9x RUF012 ClassVar + B027 noqa + 11 auto-fixes)
- [x] `uv run ruff format --check .` — 122 files clean (lead formatted 11 files)
- [x] `uv run sf health` / `sf config-show` — `[ai]` section present, providers empty/disabled

## Verified commands (Phase 5 lead-run 2026-10-27, all EXIT 0)

- [x] `uv sync` — clean (no new deps; lockfile preserved)
- [x] `uv run pytest` — 346 passed, 1 skipped (lightgbm optional) in ~18 s
- [x] `uv run pytest -rs` — skip reason: lightgbm not installed (legitimate)
- [x] `uv run ruff check .` — clean (lead fixed 8x B904 from-exc, SIM102, F841, RUF059)
- [x] `uv run ruff format --check .` — 138 files clean (lead formatted 9 files)
- [x] `uv run sf signal --help`/`generate`/`history`/`verify` — valid JSON, NO_TRADE paths, deterministic replay
- [x] `uv run sf health` / `sf config-show` — `[signal]`/`[risk]` sections present

## Verified commands (Phase 6 lead-run 2026-10-28, all EXIT 0)

- [x] `uv sync` — clean, 73 packages (no new dependencies; lockfile preserved)
- [x] `uv run pytest` — 431 passed, 1 skipped (lightgbm optional) in ~31 s
- [x] `uv run pytest -rs` — skip reason: lightgbm not installed (legitimate)
- [x] `uv run ruff check .` — clean (incl. lead NOT_APPROVED engine change)
- [x] `uv run ruff format --check .` — 156 files clean
- [x] `uv run sf backtest --help` / `sf paper --help` — sub-apps render
- [x] `uv run sf health` / `sf config-show` — `[backtest]`/`[paper]` present

## Verified commands (Phase 7 lead-run 2026-10-28, all EXIT 0)

- [x] `uv sync --extra api` — installed fastapi 0.162.0 + uvicorn 0.54.0 (lockfile already pinned; no lock change)
- [x] `uv run --extra api pytest` — 466 passed, 1 skipped (lightgbm optional) in ~75 s (35 new API/dashboard/security/serve tests)
- [x] plain `uv run pytest` — API tests skip cleanly via importorskip; quant suite unaffected
- [x] `uv run ruff check .` + `ruff format --check .` — clean, 171 files
- [x] `uv run sf serve --help` — renders; non-loopback refused; occupied port exits 1
- [x] live server proof (subprocess uvicorn 127.0.0.1:8123): /health, /api/v1/signals, /dashboard/, /api/v1/paper/status all 200, clean shutdown
- [x] `uv run sf health` / `sf config-show` — `[api]` section present

## Verified commands (Stage 0 / A0 lead-run 2026-11-07, all EXIT 0)

- [x] `uv run --offline pytest tests/unit/test_stage0_*.py` - 71 passed (contracts/gateway/storage)
- [x] `uv run --offline pytest` - 610 passed, 4 skipped in 43.86 s (4 pre-existing skips: 1 lightgbm, 3 Phase 11 calibration-lab)
- [x] `uv run --offline ruff check` on the 12 changed files - clean
- [x] `uv run --offline ruff format --check` on the 12 changed files - clean
- [x] repo-wide `ruff check .` / `ruff format --check .` - only pre-existing untracked `scripts/export_codebase_json.py` fails (unchanged)
- [x] `uv run --offline sf health` / `sf config-show` - status ok, config validated
- [x] no dependency change (`pyproject.toml`, `uv.lock`, `config/` untouched); no `*.db`; no provider SDK; no external AI call; no commit/push at the time (this work is now committed in `a768063`)

## Verified commands (Stages 1-5 lead-run 2026-11-07, all EXIT 0)

- [x] `uv run --offline pytest tests/unit/test_stage1_ai_configuration.py` - 22 passed
- [x] `uv run --offline pytest tests/unit/test_stage2_openai_adapter.py` - 25 passed
- [x] `uv run --offline pytest tests/unit/test_stage3_ai_signal_advisory.py` - 31 passed
- [x] `uv run --offline pytest tests/unit/test_stage4_ai_research_advisory.py` - 24 passed
- [x] `uv run --offline pytest tests/unit/test_stage5_architecture.py` - 31 passed
- [x] `uv run --offline pytest tests/unit/test_stage5_provider_failure_and_resources.py` - 16 passed
- [x] `uv run --offline pytest tests/integration/test_ai_cli.py` - 12 passed
- [x] `uv run --offline pytest` - 771 passed, 4 skipped (JUnit total 775; same 4 pre-existing skips)
- [x] `uv run --offline ruff check` on all changed files - clean
- [x] `uv run --offline ruff format --check` on all changed files - clean
- [x] repo-wide `ruff check .` / `ruff format --check .` - only pre-existing untracked `scripts/export_codebase_json.py` (9 errors / 1 file)
- [x] `uv sync --offline` and `uv sync --offline --extra api` - EXIT 0, no lockfile change
- [x] `sf health`, `sf config-show`, `sf ai status|config-check|test` - EXIT 0
- [x] no `.env`, no `*.db`, no provider SDK, no external AI call, no credential, no commit/push at the time (now committed in `a768063`); HEAD was `32fe845` then, current HEAD is `7c7c1bb`

## Audit disposition (2026-11-07, lead-run, all EXIT 0)

- [x] `uv run --offline pytest` - **785 passed, 4 skipped** (JUnit tests=785,
      failures=0, errors=0, skipped=4)
- [x] `uv run --offline pytest tests/unit/test_stage2_openai_adapter.py` - 33 passed
- [x] `uv run --offline pytest tests/unit/test_stage5_provider_failure_and_resources.py` - 18 passed
- [x] Stage 0-5 focused suites - 242 passed
- [x] `ruff check` / `ruff format --check` on changed files - clean
- [x] `sf health`, `sf config-show`, `sf ai status`, `sf ai config-check`, `sf ai test` - EXIT 0
- [x] M1: egress allowlist enforced + redirects refused; M2: provider timeout/retry caps + loopback `none` auth wired, remaining inert fields documented
- [x] HEAD was `32fe845` at the time (current HEAD `7c7c1bb`); no commit/push at the time (audit fixes now committed in `a768063`); no `.env`; no `*.db`; no dependency change

## Progress dashboard (2026-11-06 UTC, lead-run, all EXIT 0 except noted)

- [x] `uv sync --offline --extra api` - EXIT 0 (fastapi 0.162.0, uvicorn 0.54.0)
- [x] `uv run sf health` - EXIT 0
- [x] `uv run sf config-show` - `api.progress_dir = "data/project_progress"`
- [x] `uv run --offline pytest` - **831 passed, 4 skipped, 0 failed** (same 4 pre-existing skips)
- [x] `uv run --offline pytest tests/unit/test_progress_schema.py tests/unit/test_progress_ledger.py tests/integration/test_progress_dashboard.py -q` - 50 passed
- [x] `uv run --offline ruff check` / `ruff format --check` on 17 changed files - clean
- [x] repo-wide `uv run --offline ruff check .` - 9 errors, all pre-existing in untracked `scripts/export_codebase_json.py` (not modified)
- [x] `node --check src/crypto_engine/dashboard/progress/progress.js` - EXIT 0
- [x] headless Chrome `/dashboard/progress/` - live indicator, error/stale banners hidden, 831-passed metric; screenshot recorded
- [x] `GET /api/v1/progress/snapshot` 200; `POST` 405; `/dashboard/` 200 with `/dashboard/progress/` link
- [x] ledger `validate` - 40 records, 0 corrupt, 0 duplicates, 0 transition warnings
- [x] Build Activity Stage (0-5) filter added after acceptance; `node --check`, focused suite and full suite re-run green
- [x] known environment trap: plain `uv sync` prunes the `api` extra (tests skip; occupied-port test fails) - always use `--extra api`
- [x] no trading, paper, risk, strategy, scheduler, live AI, credential, commit or push change

## Stage 0-5 freeze recording (2026-11-07 audit; recorded 2026-11-06 UTC, lead-run)

- [x] `uv run python scripts/project_progress.py record ...` - 9 events appended (6 stage FROZEN, overall acceptance FROZEN, M2 PASS, RESOURCE_BUDGET.md:71 PASS_WITH_FINDINGS); event ids `evt-20261106T234445Z-0c7fa94a`, `...234446Z-3c05dc7c`, `...234446Z-86b13e11`, `...234446Z-ab3b51c5`, `...234447Z-e40a23ce`, `...234447Z-6ad14211`, `...234458Z-45584633`, `...234506Z-2df84d1d`, `...234507Z-267ecb81`
- [x] `uv run python scripts/project_progress.py validate` - 49 records, 49 valid, 0 corrupt, 0 duplicates, 0 transition warnings
- [x] `uv run python scripts/project_progress.py snapshot` - manifest refreshed (event_count 49); read-only Git snapshot only
- [x] `uv sync --offline --extra api` - EXIT 0
- [x] `uv run --offline sf health` / `sf config-show` - EXIT 0; `progress_dir = "data/project_progress"`
- [x] `uv run --offline pytest` - **836 passed, 4 skipped, 0 failed, 0 errors** (JUnit tests=840)
- [x] `uv run --offline pytest tests/unit/test_progress_schema.py tests/unit/test_progress_ledger.py tests/integration/test_progress_dashboard.py -q` - 55 passed
- [x] `uv run --offline ruff check` on changed Python files - clean; `ruff format --check` - 3 files clean
- [x] repo-wide `ruff check .` - 9 errors, all pre-existing in untracked `scripts/export_codebase_json.py`
- [x] `node --check src/crypto_engine/dashboard/progress/progress.js` - EXIT 0
- [x] headless Chrome `#ai` - six FROZEN stages, `AI FOUNDATION — ACCEPTED / FROZEN`, 785 vs 831 separation, M2 accepted, finding; Overview 831 passed / 6 of 6 frozen; screenshot recorded
- [x] `GET /api/v1/progress/snapshot` 200; `POST /api/v1/progress/events` 405; `/dashboard/` 200 with progress link
- [x] no AI gateway/config change, no M2 wiring, no doc edit, no live AI call, no credential, no commit/push at the time (progress-dashboard code now committed in `a1dfda0`; ledger data is git-ignored); HEAD was `32fe845` then, current HEAD is `7c7c1bb`

## RESOURCE_BUDGET.md:71 documentation correction (2026-11-07, lead-run)

- [x] `docs/architecture/RESOURCE_BUDGET.md` - stale AI budget row replaced; `[ai.budgets]` documented as reserved/inert; no runtime budget tracker or paid/runtime fallback; legacy `BudgetTracker`/`run_with_fallback` confined to `ai_providers/`; M2 wiring needs separate authorization
- [x] read-only consistency script - 35/35 assertions passed (doc vs `AI_GATEWAY.md` section 16.9, `config/default.toml`, `ai/gateway.py`, `ai/configuration.py`, `ai/composition.py`)
- [x] `uv run --offline pytest tests/unit/test_progress_schema.py tests/unit/test_progress_ledger.py tests/integration/test_progress_dashboard.py -q` - 55 passed
- [x] `uv run python scripts/project_progress.py validate` - 52 records, 52 valid, 0 corrupt, 0 duplicates, 0 transition warnings
- [x] `uv run python scripts/project_progress.py snapshot` - EXIT 0; manifest 52 events
- [x] no code/config/gateway/ai_providers/trading/risk/strategy/execution/scheduler change; no live AI call; no commit/push at the time (documentation set now committed 2026-11-07); HEAD was `32fe845` then, current HEAD is `7c7c1bb`

## Progress dashboard sync with the correction (2026-11-07, lead-run)

- [x] `project_model.py` - `DOC-RESOURCE` status `CORRECTED` (low, non-blocking) with evidence, correction event `evt-20261107T000625Z-f31f2348`, UTC `2026-11-07T00:06:25Z`, original event `evt-20261106T234507Z-267ecb81` / `PASS_WITH_FINDINGS`; six stage and overall `remaining_finding` say `CORRECTED`
- [x] `progress.js` / `progress.css` - shared `findingCard`, green `CORRECTED` chip, Simple plain-language sentence, Technical file/event/time/original-event details, M2 "Reserved / not enforced" vs "Enforced instead"
- [x] `tests/integration/test_progress_dashboard.py` - 22 passed (corrected metadata, old-text absence, history retained, JS/CSS, M2, stage freeze, evidence separation)
- [x] `uv run --offline pytest` - 839 passed, 4 skipped, 0 failed, 0 errors (843 collected)
- [x] `uv run --offline pytest tests/integration/test_progress_dashboard.py tests/unit/test_progress_schema.py tests/unit/test_progress_ledger.py -q` - 58 passed
- [x] `ruff check` + `ruff format --check` on changed Python files - clean; `node --check progress.js` - EXIT 0
- [x] headless Chrome/CDP - 24/24 checks (Overview, Issues, Security, AI, Activity, Simple/Technical)
- [x] `project_progress.py validate` - 55 records, 55 valid, 0 corrupt, 0 duplicates, 0 transition warnings; `snapshot` EXIT 0
- [x] six AI stages + acceptance FROZEN; M2 ACCEPTED AS DOCUMENTED; no live AI call; no commit/push at the time (dashboard code now committed in `a1dfda0`, ledger data git-ignored); HEAD was `32fe845` then, current HEAD is `7c7c1bb`

## Commit + verification close-out (2026-11-07, lead-run, FRESHLY VERIFIED)

- [x] Committed this session (oldest first): `ce94f24` Phase 10 forward validation
      (3 files, +779); `74ee126` dashboard dataset-availability fix (2 files,
      +46/-2); `fbe3e35` progress runtime data local-only (3 files, +85);
      `f5adec5` AI/storage contracts vs design intent (27 files, +3617/-35);
      `7c7c1bb` gitignore local runtime state (2 files, +9/-5). HEAD
      `7c7c1bb824831368d166230630d8d42c797c52c7`; 0 staged; no remote.
- [x] `uv run --offline pytest -p no:cacheprovider --ignore=tests/unit/test_phase11_shadow.py -q tests`
      - **811 tests, 810 passed, 0 failed, 0 errors, 1 skipped**
      (`tests/leakage/test_training_leakage.py::test_lightgbm_optional_and_guarded`),
      845.174 s. The untracked Phase 11 shadow test file was excluded and never executed.
- [x] `uv run --offline ruff check src tests scripts/project_progress.py` - All checks passed
- [x] `uv run --offline ruff format --check src tests` - 178 files already formatted
- [x] `uv run --offline ruff check .` - 9 pre-existing errors, ALL in the untracked
      local `scripts/export_codebase_json.py` (incl. 2 F821 undefined `Any`); not fixed, out of scope
- [x] `uv run sf health` - status ok, exit 0; `uv run sf config-show` - exit 0
- [x] Phase 10 focused suite - 11 passed, 0 failed, 0 skipped; dashboard suite - 10 passed, 0 failed, 0 skipped
- [x] Markdown consistency over `docs/architecture/**` + `docs/ARCHITECTURE.md` - 28 files, 0 findings; `git diff --check` exit 0
- [x] Progress ledger `data/project_progress/` - 71 records, validate ok, 0 corrupt / 0 duplicates / 0 transition warnings; directory git-ignored (`!! data/project_progress/`)
- [x] Evidence hashes unchanged: `artifact.pkl` `42A8EA4BAFD248C727538E233E5335337917BCD1664D28421DAA70331ECCF4DB`; `record.json` `92D79E5923AD0C3436A3D2AEB3F6935B3A1CC03BEB1FD198C399E129FB84FBD0`; Phase 10 report `306AEA3A5375B88F13E0502818DE74C0458B00636458DCB750BA4011A0E42B6A` (still `snapshot_id` null, deliberately not rewritten)
- [x] Both snapshots load with checksum verification and matching ids: `103f110e06f0` (2209 rows), `2cd144f3eb06` (793 rows)
- [x] Phase 10 remediation closed: F10-01 provenance resolution in
      `validation/forward.py`; F10-02 append-only dated label-hash correction in
      `DECISIONS.md` (authoritative `74bd147242856a17ea17cd17916cf9c674d98e25c7fbb4c526445875e366a072`);
      F10-03 `_small_frame` now takes a pytest `tmp_path` root (no `tempfile.mkdtemp`,
      no new `fwtest*` dirs; 132 pre-existing ones left alone)
- [x] Still uncommitted: `AGENTS.md` (user-owned) + these four `memory/` files; 21 untracked paths (`skills-lock.json`, `.agents/skills/*`, `.opencode/skills/*`, `scripts/export_codebase_json.py`, `scripts/export_forward_predictions.py`, `scripts/phase11_shadow/*`, `scripts/prospective_shadow.py`, `tests/unit/test_phase11_shadow.py`)
- [x] Phase 11 remains **BLOCKED - PHASE 11 NOT AUTHORIZED**: never imported, executed, tested, staged, deleted or integrated

## Codebase hardening & full verification (2026-11-10, FRESHLY VERIFIED)

- [x] `scripts/export_codebase_json.py` - all 9 pre-existing Ruff lint errors resolved (F821, B905, SIM114, F841, RUF005); formatted clean
- [x] Repo-wide `uv run --offline ruff check .` - clean, 0 errors across entire repository
- [x] Repo-wide `uv run --offline ruff format --check .` - clean, 265 files formatted
- [x] `uv run python scripts/export_codebase_json.py` - 254 files exported, 1713 functions, 148 classes, 23 CLI commands, 40 routes, 0 errors, verification PASSED
- [x] `uv run python scripts/export_forward_predictions.py` - 624 rows, cross-check PASS vs Phase 10 report (accuracy 0.4006)
- [x] Runtime ignore: `data/signals/` and `data/backtests/` added to `.gitignore`
- [x] Full test suite (811 collected, 810 passed, 0 failed, 1 skipped): unit (697 passed), integration (82 passed), leakage (31 passed, 1 skipped for LightGBM)
- [x] `uv run sf health` exit 0, `uv run sf config-show` exit 0, `uv run sf signal verify` exit 0 (`{"ok": true, "errors": []}`)
- [x] Progress ledger: `python scripts/project_progress.py validate` - 71/71 valid events, 0 corrupt, 0 duplicates
- [x] Invariants preserved: zero live orders, loopback-only API, 4 GB single-process bounds, Phase 10 boundary respected, Phase 11 quarantined
