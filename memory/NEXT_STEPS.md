# NEXT_STEPS.md — SignalForge handoff

## Immediate: user review (Phase 2 gate)

1. Review this Phase 2 report; explicitly approve or reject opening Phase 3.
2. Do NOT start calibration/ensembles/walk-forward/holdout (Phase 3), AI (Phase 4),
   or signals/risk/backtests/paper/API (Phases 5–7) until approval.

## If Phase 3 approved, proposed tasks

1. Probability calibration + reliability diagrams (no rewrite of Phase 2 interfaces).
2. Purged walk-forward validation + embargo + locked final holdout.
3. Cost stress tests, bootstrap/MC, multiple-testing corrections, statistical reports.

## Gates already met (verified 2026-10-27)

- [x] Causal features + registry (31 specs, chunked, deterministic)
- [x] Triple-barrier labels (configurable, ambiguity policy, incomplete handling)
- [x] Lightweight baselines (trend/majority/logistic) + local registry (checksums)
- [x] Anti-leakage tests (causality + training discipline + negative control)
- [x] `uv sync` clean, `pytest` 98 passed/1 skipped (lightgbm optional), ruff clean
- [ ] Explicit user approval to open Phase 3

## Out of scope (stop and ask)

Calibration/ensembles, AI providers, production signals, risk execution, full
backtests, paper trading, API/dashboard, large searches, holdout evaluation.
