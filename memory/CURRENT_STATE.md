# CURRENT_STATE.md — SignalForge verified state

- **Phase**: 6 in progress (authorized 2026-10-27/28) — event-safe backtesting,
  advisory-only paper trading, `sf backtest` / `sf paper` CLI.
  Stop before Phase 7.
- **Implemented (Phase 6, verified 2026-10-28)**:
  - `src/crypto_engine/backtesting/` — `types.py` (Fill/Position/Trade,
    `validate_bars`, skip-reason vocabulary incl. `NOT_APPROVED`),
    `costs.py` (`ExecutionCostModel` from Phase 5 `CostEstimate`, bp units,
    decimal-mixup guard), `engine.py` (`run_backtest`: entry = first bar open
    strictly after `generated_at`; BUY pays ref*(1+(spread/2+slip)/1e4);
    same-bar SL+TP → STOP_LOSS; timeout exits at bar open; END_OF_DATA = last
    close mark; funding per-bar side-agnostic; single position; only
    `approved` records trade), `metrics.py` (drawdown/Sharpe/profit-factor/
    win-rate/expectancy, `(value, warning)`, never NaN).
  - `src/crypto_engine/paper_trading/` — `state.py` (checksummed atomic JSON
    `PaperAccount`, `CorruptStateError`, duplicates consumed on accept+reject,
    halt≠liquidation, explicit flatten), `loop.py` (one bar + one pending
    signal per step, same next-bar/SL+TP/timeout/funding semantics as engine,
    `STALE_FUTURE`/`DUPLICATE`/`HALTED` rejections, gate-checked, pure-ish),
    `compare.py` (trade-count/entry/side/qty/pnl/equity-end match with
    tolerances, `match`/`diverged` verdict).
  - CLI: `sf backtest run` (offline, bounded `max_bars`, exit 0 with valid
    zero-trade ledger, exit 1 on missing model/snapshot/errors, never
    downsamples) + `sf paper init/status/step/flatten/verify/compare`
    (offline; step exit 0 for accepts and clean rejections; verify exit 1 on
    tamper; compare exit 0/2/1 match/diverged/error). End-to-end proof:
    backtest → identical paper stepping → flatten → `compare` = match.
  - Config: `[backtest]` (equity 10000, position 10%, holding 24, levels
    optional, cap 5000 bars) + `[paper]` (account path, poll bars, pending
    cap 100, fail-closed) in `Settings` + `default.toml`.
  - Tests: +30 backtest engine/costs/timing (Track A), +54 paper/CLI
    (Track B), +1 lead NOT_APPROVED test = 85 new; pre-existing intent intact.
- **Verification (lead-run 2026-10-28)**: `uv sync` EXIT 0 (73 packages, no
  new deps), `uv run pytest` **431 passed, 1 skipped** (lightgbm optional,
  legitimate), `uv run pytest -rs` confirms skip, `uv run ruff check .`
  clean, `uv run ruff format --check .` 156 files clean,
  `sf backtest --help` / `sf paper --help` / `generate`-path CLIs + `sf
  health` + `sf config-show` ([backtest]/[paper] present) all EXIT 0.
- **Resource probe (representative, tracemalloc)**: 200-bar backtest, 1
  approved LONG (aligned levels) → TP exit, net +46.38 on 10000 (~+0.46%
  after 20 bp round-trip costs): ~0.05 s wall, ~168 KiB Python-alloc peak.
  Stale-level probe (entry_ref far from fill) correctly exited at the touched
  absolute TP level for a net loss — engine follows levels as specified, a
  reminder that levels must be fresh. Method: in-process `tracemalloc`;
  NOT process-RSS; 4 GB guidance (single-process, one symbol/timeframe,
  ≤5000-bar cap, ≤60-row fixtures) retained.
- **Limitations**: single position (no pyramiding/partials); funding
  side-agnostic per-bar assumption; END_OF_DATA is a marking convention;
  SL/TP are absolute signal levels (stale levels bite); paper steps must
  arrive in order; halt≠liquidation; LightGBM untested; no profitability,
  accuracy, or live-readiness claim. Paper fills are hypothetical.
