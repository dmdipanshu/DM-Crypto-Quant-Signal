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
