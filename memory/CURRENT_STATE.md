# CURRENT_STATE.md — SignalForge verified state

- **Phase**: 3 in progress (authorized 2026-10-27) — calibration, reliability,
  purged walk-forward + embargo, locked holdout, experiment registry,
  multiple-testing, cost/robustness. Stop before Phase 4.
- **Implemented (Phase 3, verified 2026-10-27)**:
  - `src/crypto_engine/calibration/` — `CalibratedClassifier` (event
    `P(y == positive_class)`, binary-only; sigmoid/Platt + isotonic; chronological
    `t_bounds` enforcement; `min_cal_samples=20`; save/load within 1e-12) +
    `reliability.py` (quantile/uniform tables, sparse-bin warnings, Brier,
    clipped log-loss, ECE with binning caveat, coverage at thresholds, small
    classification report).
  - `src/crypto_engine/validation/` — `folds.py` (tail-anchored, rolling/
    expanding train, purge of `[entry, observable]` overlaps + embargo, disjoint
    exclusion counts, `InsufficientDataError`, n_folds <= 10), `holdout.py`
    (chronological tail, `HoldoutLedger` v1 audit trail, adequacy verdicts),
    `metrics.py` (accuracy, P/R per class, CM, class counts, Brier/log-loss/ECE
    tagged raw|calibrated, coverage, proxy expectancy/win-rate, proxy Sharpe/
    drawdown with proxy warning, same-fold baseline comparison),
    `robustness.py` (1x/2x fee/slippage grid, circular block bootstrap CI,
    permutation MC, leave-one-fold-out), `experiments.py` (JSONL registry,
    deterministic ids, holdout-use marking, failures kept, DSR with fallback
    disclosure, Holm/Bonferroni, `pbo_note` refusal to fabricate).
  - Config: `[calibration]` (sigmoid, 20, null) + `[validation]` (3 folds,
    200/50 windows, embargo 24, purge on, holdout 0.15, min 30, boot <= 200,
    block 20, fee 10 bp, slip 5 bp, seed 42) in `Settings` + `default.toml`.
  - Tests: +26 calibration/reliability, +41 validation (walk-forward, holdout,
    metrics/robustness, experiments, validation-leakage).
- **Verification (lead-run 2026-10-27)**: `uv sync` EXIT 0,
  `uv run pytest` **165 passed, 1 skipped** (skip = lightgbm optional, legitimate),
  `uv run pytest -rs` confirms skip reason, `uv run ruff check .` clean,
  `uv run ruff format --check .` 98 files clean, `sf health` + `sf config-show`
  EXIT 0 (calibration/validation sections present).
- **Resource probe (representative, tracemalloc, no sklearn import)**: 300-row
  OHLCV -> 181 feature rows, 2 folds, 50 block bootstraps (block 20): ~0.19 s wall,
  ~475 KiB Python-alloc peak. Method: in-process `tracemalloc`; NOT process-RSS;
  4 GB guidance (n_jobs=1, folds <= 10, boot <= 200, fixtures <= 300) retained.
- **Limitations**: scores calibrated only w.r.t. calibration sample; isotonic
  needs more data than the 20-sample floor; ECE binning-dependent; cost proxy is
  a research approximation (no fills/sizing/funding/portfolio); DSR fallback
  `1/(T-1)` assumes near-independent trials; no PBO number (structure unjustified);
  holdout lock is audit-based; LightGBM untested; no profitability claim.
