# DECISIONS.md — SignalForge Decision Log (Phase 1)

Append-only. Never rewrite history; supersede with new dated entries.

## 2026-10-27 — Phase 1 stack

- **Python 3.12 + uv, isolated `.venv/`**: reproducible, Windows-friendly, avoids
  system-Python pollution and OneDrive permission issues. `uv.lock` is tracked.
- **pydantic v2 + pydantic-settings**: validated config with typed errors.
- **pandas + numpy + scipy + pyarrow**: market-data frames, stats, parquet snapshots.
- **ccxt v4**: public read-only exchange data (Binance default), rate-limited, bounded retries.
- **typer + rich**: `sf` CLI with readable output.
- **tenacity**: bounded retry/backoff for network calls.
- **pyyaml**: future config/docs tooling; minimal use in Phase 1.
- **tomli**: conditional dep for Python < 3.11 only; 3.12 uses stdlib `tomllib`.
- **pytest + ruff (dev)**: tests and lint, run via `uv run`.
- **2026-10-27 (integration amendment)**: `pytest>=8` + `ruff>=0.4` promoted to
  main `dependencies` (kept also in `[dev]` extra) so plain `uv sync` installs
  them and documented `uv run pytest` / `uv run ruff check .` work — engine
  track found plain sync missed them; lead fixed and re-verified (`uv sync`
  EXIT 0, `pytest` 37 passed, `ruff check` clean).
- **ML/API deps (scikit-learn, lightgbm, optuna, fastapi, uvicorn) as optional extras only**:
  not installed or imported in Phase 1; prevents scope creep and heavy installs.

## 2026-10-27 — No VectorBT until license verified

- VectorBT (all variants) deferred: license terms must be verified compatible
  (including redistribution/commercial implications) before adoption.
- Phase 1 backtest needs are met by deterministic in-house accounting fixtures;
  revisit in Phase 3 with a license review recorded here.

## 2026-10-27 — Isolated venv, tracked lockfile

- `.venv/` git-ignored; `uv.lock` tracked for reproducible installs.
- `data/raw/`, `data/processed/`, `models/artifacts/`, `reports/generated/`,
  `*.parquet` (except test fixtures) ignored — large regenerable outputs stay out of git.

## 2026-10-27 — Secrets in env, never TOML/git

- `config/default.toml` holds non-secret defaults only; `.env.example` documents
  placeholder keys; `.env` is ignored and never committed.

## 2026-10-27 - Phase 2 stack and resource bounds

- scikit-learn>=1.3 promoted to main dependencies (Windows wheels for 3.12/3.13, lightweight, no GPU); lightgbm/optuna stay in [ml] optional, lazy-guarded, never required for core tests.
- ModelConfig enforces n_jobs=1 (le=1), max_rows_train=2000 default with explicit logging when capped, chunk_rows=5000; RegistryConfig path models/artifacts (git-ignored), allow_overwrite=false.
- Model scores documented UNCALIBRATED; no calibration claim until Phase 3 evidence.
- Pickle artifacts trusted-local-only with sha256 verification; JSON for rule-based models; corruption/tampering rejected.
- Representative probe: 300-row OHLCV -> 181 feature rows x4 cols + logistic fit ~12.2s cold / ~55 MiB Python-alloc peak (tracemalloc, not RSS guarantee).
- No 90% or profitability claim; randomized-label control within chance bands only.

## 2026-10-27 - Phase 3 validation and calibration design

- Calibration event fixed as P(y == positive_class), binary-only; sigmoid default, isotonic gated by sample; never fit on holdout; reload fidelity 1e-12.
- Folds tail-anchored with purge of label-information overlap + embargo in bars (documented vs horizons); exclusions disjoint and recorded; holdout = chronological tail with JSON ledger v1 (audit-based lock, adequacy verdicts).
- Experiment registry JSONL with deterministic ids; failures kept, never counted; DSR per Bailey-Lopez de Prado with 1/(T-1) fallback disclosed; no PBO number fabricated (pbo_note).
- Cost proxy is a research approximation (fixed fractional outcomes, per-row costs, 2x stress); NOT a fill-level backtest; proxy Sharpe/drawdown carry warnings. Full execution stays Phase 6.
- Metrics separated by type (directional vs proxy-trade vs portfolio-proxy); ECE binning-dependent; block bootstrap for serial dependence; counts configurable for 4GB (folds<=10, boot<=200).

## 2026-10-27 - Phase 4 AI integration design

- Provider interface: AiRequest/AiResponse with 9 statuses; NON_RETRYABLE (auth/invalid/unsupported/disabled/budget) never retried; retries bounded (<=3); fallback sequential, stops before paid escalation on budget exhaustion.
- Adapters (docs verified 2026-10-27, drift possible): openai Responses API, anthropic messages, gemini generateContent (key-in-query limitation), deepseek/openrouter/groq OpenAI-compatible, ollama local no-auth, compatible generic with capability flags. Transport = stdlib urllib + injection for fakes; SDKs lazy/BYO.
- No ai-* registry extras: sandbox lacks package network, so new requirements would invalidate uv.lock. SDKs are user-installed (pip install openai ...); base stays SDK-free; ordinary tests offline with mocked transports.
- Analysts advisory-only with closed schemas; judge lean-only, never trade direction; context scores bounded/expiring, never returns/probabilities/edges; orchestrator template-merge, sequential default.
- Ablation: 4 arms on identical folds, train-end evidence cutoff, registry-recorded (n_trials=4), margin verdict (+0.02 acc default); result inconclusive on mocked data — AI stays explanation/context-only until real timestamped evidence + validated path exist.

## 2026-10-27 - Phase 5 signal engine and risk design

- Label semantics fixed: triple-barrier `+1` = favorable-up (pt-hit long geometry), `-1` = favorable-down, `0` = neutral/timeout/ambiguous; policy direction mapping assumes `long_up` convention; SHORT requires `-1` in the label set.
- Calibration honesty: calibrator applies only on family+version+feature-hash+positive-class MATCH; P(y==positive_class) is a ranking score for its event, not a trade-win probability; uncalibrated/rule-based/multiclass-without-calibrator paths abstain or go NEUTRAL with reasons (CLI v1 has no calibrator wiring).
- Edge discipline: expected_edge_bp comes only from caller-supplied validated evidence (walk-forward proxy or configured estimate); never inferred from classification confidence; missing cost → COST_INPUTS_MISSING, missing edge → EDGE_UNAVAILABLE, below cost+margin → INSUFFICIENT_EDGE; bp units with decimal-mixup guard (>=10000bp rejected).
- Gates: fixed order A→B→C→D with per-gate trace (observed/limit, no secrets); exposure/drawdown evaluated ONLY from caller-supplied snapshots; absent required state → EXPOSURE_UNKNOWN + fail-closed default; never invent zero exposure; no persistent P&L (no fake daily tracker); AI presence-checked only, never overrides.
- Levels are references, not fills: tick rounding conservative (stop down for LONG/up for SHORT); history is a hash-chained audit log (idempotent, tamper-detecting), not a trading ledger.
- Replay: single-row decision input (CLI slices snapshot tail); same snapshot+model+config → same signal_id; perturbation changes it.
- Subagent backend outage (DNS) during Track B: lead implemented directly; files from partial attempts reviewed line-by-line before adoption.

## 2026-10-28 - Phase 6 backtest and paper design

- Timing contract: signal generated_at in bar [t] fills no earlier than bar t+1 open (entry = first open strictly greater than generated_at); paper steps enforce generated_at < bar.ts (STALE_FUTURE reject). No wall-clock use; no invented bars.
- Costs: BUY pays ref*(1+(spread/2+slip)/1e4), SELL receives mirror; fee on notional; funding per-bar side-agnostic (conservative assumption, documented); bp units with >=10000 decimal-mixup guard; legs reused from Phase 5 CostEstimate, not duplicated.
- Ambiguity: same-bar SL+TP touch exits STOP_LOSS (matches triple-barrier conservative default); timeout exits at bar open before SL/TP; END_OF_DATA = last-close mark (convention, not a fill); levels are absolute signal levels — stale levels bite (probe-verified), so signals must be fresh.
- Approval discipline: ONLY decision.status == approved records may trade (NOT_APPROVED skip); CLI filters + engine double-enforces. NEUTRAL closes only when approved.
- Accounting: equity = cash ± qty*close marked each bar; fee math reconciles (cash + position value − costs); deterministic ids; run-twice identical ledger (tested).
- Paper sandbox: checksummed atomic JSON state; duplicates consumed on accept AND reject (idempotent replay); halt blocks new positions (halt != liquidation); explicit costed flatten (MANUAL_FLATTEN via paper_reason — ExitReason left unchanged to avoid churning Track A types); steps must arrive in order; FIFO caps documented.
- Divergence: paper stepping identical bar+signal sequences must reproduce backtest fills (proven: backtest → paper → flatten → compare = match); tolerances explicit; exit_reason intentionally not compared.
- No orders: no ccxt/order/trade API imports in backtesting or paper_trading (grep-verified); no live credentials, no real money, no portfolio accounting beyond the sandbox.

## 2026-10-28 - Phase 9 semantic audit + logistic diagnostics (no model-code changes)

- Directional semantics VERIFIED consistent end-to-end, no mismatch: with
  config side="both" the labeler uses long geometry (+1 = upper barrier first,
  -1 = lower barrier first, 0 = timeout/ambiguous); policy maps predicted
  -1/0/+1 to SHORT/NEUTRAL/LONG (multiclass argmax; binary t_long/t_short);
  backtest opens same-direction positions (SHORT via SELL). Pinned by new
  regression tests (tests/unit/test_phase9_semantics.py).
- Documented nuance (not a code defect): with asymmetric multiples (pt 2.0 /
  sl 1.0), label -1 records a long stop-out (down 1σ before up 2σ), which does
  NOT prove a mirrored short would profit. The SHORT direction is a
  directional hypothesis whose edge must be measured — never assumed. No
  silent reinterpretation; existing artifact untouched.
- Real-snapshot diagnosis (2209 bars → 2066 usable; read-only, artifact
  unmodified): labels -1/+1/0 = 1270/697/99 (majority -1 at 61.5%); model
  predicts only ±1, never 0 (4.8% minority class suppressed by argmax — no
  mapping bug, no class_weight knob exists in the wrapper by design).
  Valid acc 43.6% vs majority 61.5%; balanced accuracy 0.336 vs 0.333
  (chance); train acc 63.0% → valid 43.6% gap = overfitting and/or regime
  shift. Top-|coefficients| are relative/oscillator features (macd_hist,
  bb_pos_20, trend_slope_5); raw price-level features do NOT dominate.
  Metrics code hand-verified against manual confusion counts.
- Controlled experiment (validation-only selection, predeclared rule: highest
  validation macro-F1): majority 0.254, logistic 0.300, trend 0.285,
  relative-only logistic 0.261. Selected: incumbent logistic config — but the
  margin over degenerate alternatives is not convincing, so the artifact is
  RETAINED unchanged and no new model was registered. Holdout figures
  (0.47/0.61) are previously-inspected comparisons only, not pristine;
  forward validation on genuinely new data recommended before any claim.
- Backtest verdict: BLOCKED by design. Default gates veto every signal
  (proven: full 2209-bar `sf backtest run` → 0 signals/0 trades; live veto
  trace ends at policy:calibration UNCALIBRATED). Removing calibration/edge
  gates to force trades is forbidden, so no trading backtest was run.
- Incidental fix from browser smoke test: dashboard overview read the wrong
  keys from the data-status payload (showed "none recorded" with 1 snapshot
  present); fixed to the shared contract + regression test; verified live via
  agent-browser (health JSON + overview render). `agent-browser open` hangs in
  this sandbox (Chrome launch); `navigate`/`snapshot` work.
- Grill-me skill is a stub pointing at a nonexistent "grilling" skill (load of
  "grilling" fails); its stated intent was applied as an explicit adversarial
  review instead (recorded in the Phase 9 handoff).

## 2026-10-28 - Phase 7 read-only API and dashboard design (final phase)

- Read-only contract: 32 GET routes under `/api/v1` (+ root/health/dashboard pages); versioned envelopes (`api_version 1.0`, `request_id`, `data_ts`); pagination caps (default 50, max 500 enforced); `unavailable` payloads with reasons (not_initialized/empty/missing/corrupt) — never fabricated rows; expired/stale signal records labeled, never presented as actionable.
- Adapters: one `RepoRoots` per process, dependency-injected; bounded JSONL scans (`ledger_scan_cap` 5000); `SignalHistory` reads never create directories (lead fix — constructor mkdir is a write side effect); checksummed paper state verified before display, corrupt → unavailable; model artifacts never loaded/deserialized by the API (metadata only).
- Server: `sf serve` foreground, loopback-only default with explicit `--allow-remote` gate (non-loopback without it is a hard error); CORS disabled unless `cors_origins` set (no wildcard credentials); no training/fetch/backtest/mutation at startup or per request; port-occupied → clear exit 1; Ctrl+C shutdown.
- fastapi 0.162.0 + uvicorn 0.54.0 via existing `api` extra (lockfile already pinned; no lock change, no pyproject change); core suite runs without the extra (importorskip skips), quant CLI unaffected; jinja2/httpx deliberately NOT added — string-built escaped HTML + stdlib ASGI test harness instead (4GB, no new heavy deps).
- Dashboard: 6 server-rendered pages sharing repo readers (no second source of truth); `html.escape` on all dynamic values; explicit empty/uninitialized states; paper always labeled hypothetical; no trading buttons/forms/order paths; no fake live-updating values; no calibrated-probability display of raw scores.
- Security posture: GET-only asserted at build (`assert_get_only` traverses lazy routers) and tested (405s on POST/PUT/DELETE/PATCH for data + root paths); path traversal and malformed ids rejected (400/404, sanitized); env secrets scanned absent from all responses; tracebacks never leak (generic 500 + request id; server logs only).
- Verified, not merely implemented: 35 new tests green; live uvicorn proof (127.0.0.1:8123, 4 endpoints 200, clean shutdown); final audit clean. No live/paper account exists; no cloud deployment claimed.

## 2026-10-28 - Phase 10 frozen forward validation (no training, no tuning, no commit)

- Model logistic-ee482c-3e3324f2 (artifact sha256 42a8ea4b…, label_hash 74cd…,
  side=both, pt 2.0/sl 1.0, horizon 24) evaluated on genuinely new snapshot
  binance-BTC-USDT-1h-20260925T0000-20261028T0000-2cd144f3eb06 (checksum
  90e5339e…, 793 rows, Sep 25–Oct 28). Eval rows strictly after 2026-10-01
  (first scored bar Oct 1 01:00, last Oct 27; tail incomplete excluded).
- Exclusions: 26 pre-cutoff rows (Sep overlap, warm-up context only), 119
  warm-up NaN rows, 44 incomplete-horizon rows; 624 rows scored.
  Purge kept all training rows honest (observable_at verified ≤ cutoff).
- Result: accuracy 0.401, balanced accuracy 0.367, macro-F1 0.264 vs
  historical-majority (-1) baseline 0.644. Predicted +1 on 83% of rows while
  reality was 64% -1 (October downtrend the model persistently read as
  bullish). Per-class: -1 P/R 0.73/0.19, +1 0.33/0.91, 0: 0/0.
  Confusion [-1,0,+1]x[-1,0,+1]: [[77,0,325],[11,0,21],[17,0,173]].
- Conclusion: the frozen model does not beat the trivial baseline on unseen
  data; no retraining/tuning (holdout already viewed; tuning would be
  invalid). Retain-as-is is moot — no deployment path exists (fail-closed
  gates veto uncalibrated, edgeless signals; no backtest run, by design).
- Backtest remains BLOCKED: a cost-aware backtest would require bypassing
  calibration/edge gates, which is forbidden. No trades simulated.
- Grill-me skill is a stub pointing at nonexistent "grilling"; applied its
  stated adversarial intent manually (recorded in the Phase 9 handoff and
  session log). Dashboard smoke test ran under Phase 9 (browser-verified
  render + contract-bug fix); not re-run here — no dashboard code changed.

## 2026-11-07 - Phase 10 label-hash transcription correction (append-only)

- The Phase 10 entry dated 2026-10-28 above records the frozen model's label
  hash with the prefix `74cd…`. The authoritative artifact record
  (`models/artifacts/logistic-ee482c-3e3324f2-20261028T132236/record.json`)
  and the historical forward report
  (`reports/generated/forward_validation_binance-BTC-USDT-1h-20260925T0000-20261028T0000-2cd144f3eb06_20261028T145235.json`,
  where `labels.recorded_hash == labels.hash`) both carry the complete value
  `74bd147242856a17ea17cd17916cf9c674d98e25c7fbb4c526445875e366a072`.
- This entry supersedes only that transcription. The original Phase 10 text is
  preserved unchanged for audit history. No model, artifact, validation run,
  metric, prediction or report was changed; this is a documentation
  correction, not a model or validation change.

## 2026-10-28 - Phase 8 local model-training workflow

- New `src/crypto_engine/training/` package (splits.py + pipeline.py) plus `sf model train|list`; reuses load_snapshot, compute_features, triple-barrier helpers, valid_mask, assert_no_leakage, label_config_hash, SklearnLogistic, MajorityBaseline, save/load/list_models, classification_report_small — no existing module modified.
- Chronological 60/20/20 splits (configurable fractions); purge drops training rows whose observable_at exceeds the cutoff; embargo drops trailing train rows (validation.embargo_bars); single/multi-class handled, single-class explicitly rejected; max_rows_train cap applied to most-recent rows and recorded.
- No calibration fitting in Phase 8 (models marked UNCALIBRATED); no thresholds fitted (holdout used for reporting only, never tuning); edge vs confidence never conflated (edge gating stays in the Phase 5 policy).
- Registry discipline preserved: save_model no-overwrite honored, FileExistsError surfaced with guidance; reload verified (fitted, schema, checksum, prediction compatibility).
- CLI is offline-only (no fetch flag exists); discovery via list_snapshots latest match; invalid inputs exit 1/2 with guidance.
- 26 new tests; full suite 492 passed, 1 skipped; ruff clean; fixtures <=400 rows; no new dependencies.

## 2026-11-12 — AI telemetry path consistency and store resolution

- **Canonical helper `get_telemetry_store(config)`**: Resolves custom or default telemetry path across CLI (`sf ai metrics`), API (`read_ai_metrics`), and dashboard repositories.
- **Record-derived provider ID**: `TelemetryStore.metrics()` computes `provider_id` from observed telemetry events and passes it into `TelemetryMetrics` (single provider name, `mixed` for multiple, or empty if none).
- **Flexible record ingestion**: `TelemetryStore.record()` accepts `AIResponse`, `TelemetryRecord`, or dictionaries without silent drops.
- **Isolated resolution**: No global cached store singleton is retained across invocations, guaranteeing test isolation and avoiding state leakage across tests or environments.
- **RepoRoots inclusion**: `RepoRoots` includes `ai_telemetry_path` defaulting to `data/ai_telemetry/events.jsonl` and forwarded consistently through API and dashboard router factories.
