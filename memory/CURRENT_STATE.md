# CURRENT_STATE.md — SignalForge verified state

- **Phase**: 2 in progress (authorized 2026-10-27) — causal features, triple-barrier
  labels, lightweight baselines, local registry. Stop before Phase 3.
- **Implemented (Phase 2, verified 2026-10-27)**:
  - `src/crypto_engine/features/` (registry 31 specs, causal engineering: lagged
    returns, SMA/EMA/MACD, ATR-14, realized vol, RSI-14, Bollinger, volume,
    candle anatomy, vol/trend regime, BTC/ETH merge_asof, funding hook, chunked
    `compute_features`).
  - `src/crypto_engine/labels/triple_barrier.py` (pt/sl volatility-scaled,
    max-horizon, long/short/both, cost-aware, ambiguity policies, incomplete
    handling, `valid_mask`, `assert_no_leakage`, save/load with config hash).
  - `src/crypto_engine/models/` (TrendBaseline, MajorityBaseline, SklearnLogistic
    n_jobs=1/scaler-fit-on-train-only, optional LightGBM lazy-guarded) +
    `src/crypto_engine/registry/store.py` (ModelRecord, atomic saves, sha256,
    no-overwrite, corruption rejection, pickle warning).
  - `config/default.toml` [model]+[registry], `Settings` extended (ModelConfig
    n_jobs=1/max_rows_train=2000, RegistryConfig).
  - Tests: `test_features` 13, `test_labels` 12, `test_causality` 9,
    `test_models`, `test_registry`, `test_training_leakage` (scaler discipline,
    randomized-label control, save/load fidelity, corruption, reproducibility).
- **Verification (lead-run 2026-10-27)**: `uv sync` EXIT 0 (sklearn 1.9.1),
  `uv run pytest` **98 passed, 1 skipped** (skip = lightgbm optional, legitimate),
  `uv run ruff check .` clean, `ruff format` clean after 5-file reformat,
  `sf health` + `sf config-show` EXIT 0 (model/registry sections present).
- **Resource probe (representative, tracemalloc, cold import)**: 300-row OHLCV →
  181 feature rows × 4 cols + logistic fit: ~12.2 s wall (incl. sklearn import),
  ~55 MiB Python-alloc peak. Method: `tracemalloc` in-process; NOT a process-RSS
  guarantee; 4 GB guidance (chunk_rows, max_rows_train=2000, n_jobs=1) retained.
- **Limitations**: no calibration claim (scores uncalibrated); no walk-forward/
  holdout evaluation (Phase 3); LightGBM untested (not installed); pickle loads
  trusted-local-only; no profitability claim.
