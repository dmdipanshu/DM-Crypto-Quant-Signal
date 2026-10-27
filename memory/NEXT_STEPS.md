# NEXT_STEPS.md — SignalForge handoff

## Immediate: user review (Phase 3 gate)

1. Review this Phase 3 report; explicitly approve or reject opening Phase 4.
2. Do NOT start AI (Phase 4), signals/risk (Phase 5), backtests/paper (Phase 6),
   or API/dashboard (Phase 7) until approval.

## If Phase 4 approved, proposed scope

- Multi-provider AI adapters with structured outputs, fallback, budgets.
- Optional analyst agents (technical/regime/sentiment/macro, bullish/bearish/
  neutral-judge, explainer) that contextualize evidence without inventing data.
- Ablation tests of AI-added value over the pure quantitative model.

## Gates already met (verified 2026-10-27)

- [x] Calibration (sigmoid/isotonic, chronological, 1e-12 reload fidelity)
- [x] Reliability (bins, Brier, log-loss, ECE, coverage, sparse-bin warnings)
- [x] Purged walk-forward + embargo (deterministic, disjoint exclusions)
- [x] Locked holdout protocol (tail split, access ledger, adequacy verdicts)
- [x] Experiment registry (JSONL, deterministic ids, DSR, Holm/Bonferroni, no PBO fabrication)
- [x] Cost/robustness (1x/2x fee/slippage, block bootstrap, MC, leave-one-fold-out)
- [x] `uv sync` clean, `pytest` 165 passed/1 skipped (lightgbm optional), ruff clean
- [ ] Explicit user approval to open Phase 4

## Out of scope (stop and ask)

AI providers, production signals, risk execution, full backtests, paper trading,
live orders, API/dashboard, large searches, unapproved global config changes.
