# Target Repository Structure

**This document distinguishes what exists from what is proposed.** The target
tree below does **not** exist. It is a design proposal for a future approved
implementation. The current tree is reported exactly as verified on
2026-11-07.

## 1. Current repository (verified)

```text
D:\DM-Crypto-Quant-Signal\
  .agents/                     agent workspace data (untracked)
  .opencode/                   agent, skill and command definitions
  config/default.toml          validated settings, no secrets
  data/processed/binance/BTC-USDT/1h/
    binance-BTC-USDT-1h-20260701T0000-20261001T0000-103f110e06f0/
    binance-BTC-USDT-1h-20260925T0000-20261028T0000-2cd144f3eb06/
  docs/                        8 top-level documents plus docs/architecture/ (this design set)
  memory/                      4 project-memory files
  models/artifacts/logistic-ee482c-3e3324f2-20261028T132236/
  reports/generated/            forward-validation files and phase11_shadow/
  scripts/                     phase11_shadow/, prospective_shadow.py, exporters
  src/crypto_engine/           21 packages, 102 Python files
  tests/                       unit, integration, leakage, fixtures
  AGENTS.md  DECISIONS.md  README.md  CONTRIBUTING.md
  pyproject.toml  uv.lock  .gitignore  .env.example
```

Current `src/crypto_engine/` packages (all 21 exist today):

```text
agents/  ai/  ai_providers/  api/  backtesting/  calibration/  config/
dashboard/  data/  datasets/  features/  labels/  models/  paper_trading/
progress/  registry/  risk/  signals/  storage/  training/  validation/
cli.py  __init__.py
```

There is no `domain/`, `application/`, `ports/`, `market_data/`, `analytics/`,
`strategies/`, `portfolio/`, `execution/`, `research/`, `scheduler/`,
`notifications/` or `observability/` package, and no `bootstrap.py`. The
`ai/` and `storage/` packages exist but are not the full target layouts
described in `AI_GATEWAY.md` §6.1 and `STORAGE_OWNERSHIP.md` §9.

## 2. Target tree

Legend: **CORE** required for the baseline product; **OPTIONAL** independently
disableable and off by default; **DEFERRED** designed but not built in the
first implementation; **OUT** explicitly out of scope.

```text
src/crypto_engine/
  __init__.py                              CORE
  bootstrap.py                             CORE   composition root

  domain/                                  CORE   stdlib only
    __init__.py
    instruments.py                         CORE   Instrument, TradingPair
    candles.py                             CORE   Timeframe, Candle, CandleSeries
    quality.py                             CORE   QualityReport, Gap, verdicts
    indicators.py                          CORE   IndicatorSpec (definitions)
    regimes.py                             CORE   Regime, RegimeState
    strategies.py                          CORE   StrategyMetadata, StrategyVersion
    signals.py                             CORE   SignalProposal, SignalRecord, Direction
    scoring.py                             CORE   ScoreBreakdown, ConfluenceResult
    risk.py                                CORE   RiskLimits, RiskDecision, verdicts
    sizing.py                              CORE   PositionSize rules
    portfolio.py                           CORE   Position, LedgerEntry, EquityPoint
    research.py                            CORE   ResearchRun, ValidationResult
    reason_codes.py                        CORE   closed reason-code vocabulary
    errors.py                              CORE   domain error types

  ports/                                   CORE   protocols only
    market_data.py                         CORE
    snapshots.py                           CORE
    catalog.py                             CORE
    metadata.py                            CORE      SQLite WAL-backed target
    signals.py                             CORE
    models.py                              OPTIONAL
    reports.py                             CORE
    ledger.py                              OPTIONAL
    holdout.py                             CORE
    clock.py                               CORE
    ids.py                                 CORE
    ai.py                                  OPTIONAL
    notifications.py                       OPTIONAL
    execution.py                           OPTIONAL  paper only
    jobs.py                                OPTIONAL

  application/                             CORE   orchestration
    ingestion.py                           CORE
    quality.py                             CORE
    features.py                            CORE
    regimes.py                             CORE
    mtf.py                                 CORE
    signals.py                             CORE
    risk.py                                CORE
    backtesting.py                         CORE
    research.py                            CORE
    portfolio.py                           OPTIONAL
    paper.py                               OPTIONAL
    reporting.py                           CORE
    health.py                              CORE
    queries.py                             CORE   read-side use cases
    dto.py                                 CORE
    errors.py                              CORE

  quality/                                 CORE
    checks.py                              CORE
    gaps.py                                CORE
    staleness.py                           CORE

  analytics/                               CORE
    indicators/
      returns.py                           CORE
      trend.py                             CORE
      volatility.py                        CORE
      momentum.py                          CORE
      volume.py                            CORE
      funding.py                           OPTIONAL  requires funding data
    features/
      builder.py                           CORE
      registry.py                          CORE
      causality.py                         CORE
    regimes/
      classifier.py                        CORE
      rules.py                             CORE
    mtf/
      align.py                             CORE

  strategies/                              CORE
    base.py                                CORE
    registry.py                            CORE
    metadata.py                            CORE
    builtin/
      baseline.py                          CORE
      trend_following.py                   CORE
      mean_reversion.py                    CORE
      momentum.py                          CORE

  signals/                                 CORE
    scoring.py                             CORE
    confluence.py                          CORE
    diagnostics.py                         CORE
    compose.py                             CORE
    history.py                             CORE

  risk/                                    CORE
    gates.py                               CORE
    limits.py                              CORE
    sizing.py                              CORE
    breakers.py                            CORE

  portfolio/                               OPTIONAL  paper accounting
    ledger.py                              OPTIONAL
    accounting.py                          OPTIONAL
    equity.py                              OPTIONAL

  backtesting/                             CORE
    engine.py                              CORE
    costs.py                               CORE
    metrics.py                             CORE
    types.py                               CORE

  research/                                CORE
    splits.py                              CORE
    walk_forward.py                        CORE
    holdout.py                             CORE
    robustness.py                          CORE
    experiments.py                         CORE
    metrics.py                             CORE

  market_data/                             CORE
    exchange/ccxt_rest.py                  CORE
    exchange/websocket.py                  OPTIONAL
    ingest.py                              CORE
    normalize.py                           CORE

  storage/                                 CORE
    datasets.py                            CORE   parquet snapshots
    manifests.py                           CORE
    signal_store.py                        CORE
    report_store.py                        CORE
    holdout_ledger.py                      CORE
    ledger_json.py                         OPTIONAL
    model_store.py                         OPTIONAL
    sqlite/metadata.py                     CORE      transactional state (ADR-0015)
    sqlite/ledger.py                       OPTIONAL  paper ledger

  execution/                               OPTIONAL
    paper/venue.py                         OPTIONAL
    paper/step.py                          OPTIONAL
    live/                                  OUT   must not exist

  scheduler/                               OPTIONAL
    runner.py                              OPTIONAL
    jobs.py                                OPTIONAL

  notifications/                           OPTIONAL
    base.py                                OPTIONAL
    webhook.py                             OPTIONAL

  observability/                           CORE
    logging.py                             CORE
    health.py                              CORE
    timing.py                              CORE

  config/                                  CORE
    settings.py                            CORE
    env.py                                 CORE

  ai/                                      OPTIONAL
    gateway.py                             OPTIONAL   implements the AiClient port
    registry.py                            OPTIONAL   provider + model metadata, no secrets
    routing.py                             OPTIONAL   task -> ordered route
    schemas/                               OPTIONAL   closed output schemas
    providers/openai/                      OPTIONAL
    providers/anthropic/                   OPTIONAL
    providers/google/                      OPTIONAL
    providers/meta/                        OPTIONAL
    providers/nvidia/                      OPTIONAL
    providers/custom/                      OPTIONAL   OpenAI-compatible endpoint
    analysts/                              OPTIONAL
    context.py                             OPTIONAL

  models/                                  OPTIONAL  ML layer
    base.py                                OPTIONAL
    baselines.py                           OPTIONAL
    sklearn_wrappers.py                    OPTIONAL
    labels.py                              OPTIONAL
    training/                              OPTIONAL
    calibration/                           OPTIONAL

  cli/                                     CORE
    main.py                                CORE
    commands/data.py                       CORE
    commands/signal.py                     CORE
    commands/model.py                      OPTIONAL
    commands/backtest.py                   CORE
    commands/research.py                   CORE
    commands/paper.py                      OPTIONAL
    commands/serve.py                      CORE
    commands/health.py                     CORE

  api/                                     CORE   requires the api extra
    app.py  routes.py  schemas.py  security.py

  dashboard/                               CORE
    routes.py  pages.py
```

## 3. Package labels in one table

| Package | Label | Rationale |
|---|---|---|
| `domain/` | CORE | Every rule depends on it |
| `ports/` | CORE | Dependency-inversion seam |
| `application/` | CORE | All operator intentions |
| `quality/` | CORE | No research without it |
| `analytics/` | CORE | Indicators and features are the product |
| `strategies/` | CORE | Multi-strategy is a stated objective |
| `signals/` | CORE | Scoring and explanation |
| `risk/` | CORE | Deterministic gates |
| `backtesting/` | CORE | Reproducible research |
| `research/` | CORE | Walk-forward and holdout |
| `market_data/` REST | CORE | Data source |
| `market_data/` WebSocket | OPTIONAL | Not needed for hourly research |
| `storage/` files | CORE | Datasets and reports |
| `storage/` SQLite WAL | CORE | Canonical transactional state (ADR-0015); JSON fallback for export |
| `observability/` | CORE | Health and logs |
| `config/` | CORE | Validated settings |
| `cli/` | CORE | Primary interface |
| `api/` | CORE | Read-only interface (extra) |
| `dashboard/` | CORE | Read-only interface |
| `portfolio/` | OPTIONAL | Only paper accounting |
| `execution/paper` | OPTIONAL | Disabled by default |
| `scheduler/` | OPTIONAL | No daemon by default |
| `notifications/` | OPTIONAL | Outbound convenience |
| `ai/` | OPTIONAL | Advisory only |
| `models/` | OPTIONAL | Baseline signals must work without ML |
| `execution/live` | OUT | Never part of this architecture |
| public auth / multi-tenant | OUT | No remote exposure |
| distributed compute / brokers | OUT | Violates the resource envelope |

### Deferred

Designed, deliberately not built in the first implementation:

| Item | Home (when built) | Reason for deferral |
|---|---|---|
| Derived feature cache / curated datasets | `analytics/features/cache.py`, `data/curated/` | Adds invalidation complexity; recomputation is affordable at current sizes |
| Portfolio optimisation | `portfolio/optimisation.py` | Requires validated multi-asset evidence that does not exist |
| Multi-exchange ingestion | `market_data/exchange/multi.py` | One public venue is sufficient for research; adds rate-limit and schema surface |
| Additional notification channels | `notifications/smtp.py`, `notifications/webhook.py` | One generic webhook covers the need until proven otherwise |
| Scheduler persistence and catch-up policy | `scheduler/persistence.py` | Manual runs suffice; catch-up semantics need a real operational requirement |
| Streaming/WebSocket feature pipeline | `analytics/streaming.py` | Hourly research does not need it; memory cost is unbounded |

## 4. Supporting directories

| Path | Purpose | Owner | Git |
|---|---|---|---|
| `tests/unit/` | Per-module tests, deterministic, offline | Module owner | Tracked |
| `tests/integration/` | Cross-module and adapter tests | Integration lead | Tracked |
| `tests/architecture/` | Import-rule, cycle and boundary tests | Integration lead | Tracked |
| `tests/leakage/` | Causality and future-information tests | Quant researcher | Tracked |
| `tests/fixtures/` | Small deterministic fixtures (≤400 rows) | Quant researcher + QA | Tracked |
| `config/` | Validated non-secret settings | Integration lead | Tracked |
| `data/` | Datasets and runtime state | Market-data engineer | Ignored |
| `models/` | Model artifacts | ML engineer | Ignored |
| `reports/` | Generated research output | Producing agent | Generated ignored |
| `docs/` | Architecture and methodology | Quant lead | Tracked |
| `scripts/` | One-off research and migration scripts | Quant researcher | Tracked, never imported by `src/` |

`tests/architecture/` is new. The other four test directories exist today.

## 5. Current to target mapping

| Current module | Target home | Notes |
|---|---|---|
| `cli.py` (~69 KB) | `cli/` + `application/` | The largest structural change: orchestration moves out of the CLI |
| `data/adapter.py` | `market_data/exchange/ccxt_rest.py` | Adapter behind a port |
| `data/validation.py` | `quality/` + `domain/quality.py` | Split rules from pandas checks |
| `data/types.py` | `domain/candles.py` | Typed domain values |
| `datasets/snapshots.py` | `storage/datasets.py` + `storage/manifests.py` | Storage adapter |
| `features/engineering.py` | `analytics/indicators/` + `analytics/features/` | Split by concern |
| `features/registry.py` | `analytics/features/registry.py` | Feature metadata |
| `features/` regime helpers | `analytics/regimes/` | Regime is first-class |
| `labels/triple_barrier.py` | `models/labels.py` | Optional ML |
| `models/` | `models/` | Optional, unchanged role |
| `training/` | `models/training/` | Optional |
| `calibration/` | `models/calibration/` | Optional |
| `registry/store.py` | `storage/model_store.py` | Port implementation |
| `validation/folds.py` | `research/splits.py` | Research core |
| `validation/holdout.py` | `research/holdout.py` | Holdout ledger preserved |
| `validation/robustness.py` | `research/robustness.py` | Cost stress |
| `validation/experiments.py` | `research/experiments.py` | Registry |
| `validation/metrics.py` | `research/metrics.py` | Metrics discipline |
| `validation/forward.py` | `research/forward.py` | Frozen-model validation |
| `validation/ablation.py` | `ai/validation/` | Optional AI |
| `signals/schema.py` | `domain/signals.py` + `signals/compose.py` | Values vs composition |
| `signals/policy.py` | `signals/scoring.py` + `signals/confluence.py` | Split policy from maths |
| `signals/costs.py` | `backtesting/costs.py` | One cost model |
| `signals/levels.py` | `signals/levels.py` | Keep as a signal utility |
| `signals/history.py` | `storage/signal_store.py` + `signals/history.py` | Split I/O from chain rules |
| `risk/gates.py` | `risk/` + `domain/risk.py` | Rules and thresholds separated |
| `backtesting/` | `backtesting/` | Role unchanged |
| `paper_trading/state.py` | `portfolio/ledger.py` + `storage/ledger_json.py` | Split rules from I/O |
| `paper_trading/loop.py` | `execution/paper/step.py` + `application/paper.py` | Isolate the venue |
| `paper_trading/compare.py` | `application/paper.py` | Comparison is a use case |
| `api/` | `api/` | Role unchanged, must not grow logic |
| `dashboard/` | `dashboard/` | Role unchanged |
| `agents/` | `ai/analysts/` | Optional |
| `ai_providers/` | `ai/providers/` | Optional; `ai/providers/openai_compatible.py` exists (Stages 0–5) |
| `config/settings.py` | `config/settings.py` | Keep |

## 6. Differences that require explicit approval

Each of these changes behaviour or structure beyond documentation:

1. Introducing `domain/`, `ports/`, `application/` and `bootstrap.py`.
2. Moving orchestration out of `cli.py`.
3. Splitting risk into a separate `RiskDecision` artifact.
4. Making ML and AI optional packages rather than present-by-default.
5. Adding SQLite WAL as the canonical transactional store (ADR-0015).
6. Adding a scheduler and notifications.
7. Renaming or relocating any existing module.

None of these is authorized by this document.

## 7. Current-state inconsistencies to resolve outside this task

Observed on 2026-11-07, recorded here for the integration lead, **not fixed**:

1. **Phase 11 artifacts are present** (`scripts/phase11_shadow/`,
   `scripts/prospective_shadow.py`, `tests/unit/test_phase11_shadow.py`,
   `reports/generated/phase11_shadow/`) although Phase 11 is permanently
   retired. They must not be treated as authorized work.
2. **`.gitignore` gaps:** `reports/historical_backtests/`, `data/paper/` and
   `data/scheduler/` are not ignored. Recreating those directories would show
   them as untracked.
3. **Documentation drift:** `memory/CURRENT_STATE.md` and `NEXT_STEPS.md`
   mention 32 GET endpoints for Phase 7; the static route inventory of the
   current source yields 30 in `api/routes.py` plus 7 dashboard routes.
   Memory files are not edited here.
4. **Test status was unverified at the time of writing:** no `.venv` and no
   project dependencies were installed, so no test or lint command could run.

Status update (2026-11-07, later the same day): item 4 is resolved. The
repository now has a working uv-managed `.venv`; targeted `pytest` and `ruff`
runs execute offline. Item 2 is partly resolved: `data/project_progress/` is
now ignored by `.gitignore:38`. The three paths listed in item 2
(`reports/historical_backtests/`, `data/paper/`, `data/scheduler/`) are still
not ignored. Items 1 and 3 remain open and are still outside this document's
scope.

## 8. What this document is not

It is not a migration plan, a commit plan, a file-move instruction, or an
authorization to delete anything. `IMPLEMENTATION_ROADMAP.md` sequences the
work; Stage 0 requires approval before any of it starts.
