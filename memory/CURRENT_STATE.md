# CURRENT_STATE.md — SignalForge verified state

- **Phase**: 5 in progress (authorized 2026-10-27) — canonical signal schema,
  model-to-decision policy, edge/cost gating, deterministic risk gates,
  advisory-only AI, level validation, audit history, `sf signal` CLI.
  Stop before Phase 6.
- **Implemented (Phase 5, verified 2026-10-27)**:
  - `src/crypto_engine/signals/` — `schema.py` (closed `SignalRecord` v1.0,
    16-hex deterministic ids, LONG/SHORT/NEUTRAL/NO_TRADE first-class,
    NO_TRADE+approved rejected, reason vocabulary), `policy.py` (pure
    `decide()` fixed veto order, calibrator MATCH check, rule-based→
    RULE_NO_PROBABILITY, multiclass argmax, SHORT needs −1 class,
    `apply_gate_veto`), `costs.py` (bp units, decimal-mixup guard),
    `levels.py` (conservative tick rounding, estimate-only suggest),
    `history.py` (JSONL, per-record sha256 chain, idempotent append,
    tamper-detecting verify, no secrets).
  - `src/crypto_engine/risk/gates.py` — fixed order A→B→C→D; freshness/
    completeness/consistency; artifact/schema/output/calibration;
    directions/disagreement/edge/age/horizon/regime/AI-veto; exposure only
    from caller-supplied snapshots, EXPOSURE_UNKNOWN + fail-closed default,
    never invented zero; AI never overrides.
  - `sf signal` CLI (`generate`/`history`/`verify`): offline default (no live
    fetch unless `--fetch`), missing/corrupt model → error exit, no fallback;
    v1 has no calibrator (unavailable → usually NO_TRADE with reasons, exit 0);
    replay deterministic (same inputs → same signal_id).
  - Config: `[signal]` (horizon 24, t_long 0.6/t_short 0.4, v1, calibration
    required, edge required, all directions, 60/90 min limits,
    `data/signals`, cap 500) + `[risk]` (1.0/10.0/5.0 %, fail_closed).
  - Tests: +46 policy/schema/costs (Track A), +46 risk/history/CLI/replay
    (Track B) = 92 new; pre-existing intent unchanged.
- **Verification (lead-run 2026-10-27)**: `uv sync` EXIT 0,
  `uv run pytest` **346 passed, 1 skipped** (skip = lightgbm optional, legitimate),
  `uv run ruff check .` clean (lead fixed 8x B904 from-exc, SIM102, F841,
  RUF059 in new files), `uv run ruff format --check .` 138 files clean
  (lead formatted 9 files), `sf signal --help`/`generate`/`history`/`verify`,
  `sf health`, `sf config-show` ([signal]/[risk] present) all EXIT 0.
- **Resource probe (representative, tracemalloc)**: decide()+run_gates on
  40-row fixture (policy LONG/approved → gates fail-closed on missing edge
  evidence): ~0.04 s wall, ~26 KiB Python-alloc peak. Method: in-process
  `tracemalloc`; NOT process-RSS; 4 GB guidance (single-process, one
  symbol/timeframe, ≤50-row fixtures, history cap 500) retained.
- **Limitations**: CLI v1 uncalibrated by default (usually abstains); no
  calibrator wiring in CLI yet; exposure/drawdown evaluated only from
  caller-supplied state (no live portfolio, no persistent P&L); edge must come
  from validated evidence (never confidence); levels are references, not fills;
  history is an audit log, not a trading ledger; LightGBM untested;
  no profitability/90%/live-readiness claim.
