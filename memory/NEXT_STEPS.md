# NEXT_STEPS.md — SignalForge handoff

## Immediate: user review (Phase 4 gate)

1. Review this Phase 4 report; explicitly approve or reject opening Phase 5.
2. Do NOT start production signals/risk (Phase 5), backtests/paper (Phase 6),
   or API/dashboard (Phase 7) until approval.

## If Phase 5 approved, proposed scope

- Canonical signal decision engine (LONG/SHORT/NEUTRAL/NO_TRADE JSON with IDs,
  horizons, calibrated probabilities where available, evidence, abstention
  reasons) consuming Phase 2–4 outputs.
- Deterministic risk gates (hard-coded policy, configurable values) independent
  of models/AI; AI context advisory-only, never overriding gates.

## Gates already met (verified 2026-10-27)

- [x] Provider-independent interface (typed requests/responses, 9 statuses)
- [x] 8 adapters (urllib default, SDKs BYO, all disabled default, env-only secrets)
- [x] Retry/fallback/budgets (bounded, sequential, stops before paid escalation)
- [x] Structured validation (closed schemas, injection-as-data, redaction)
- [x] 8 analysts + sequential orchestrator (AI-disabled path verified)
- [x] Context-score contract (bounded, expiring, NOT a return/probability/edge)
- [x] Ablation (4 arms, temporal cutoff, registry-recorded, verdict: inconclusive)
- [x] `uv sync --offline` clean, `pytest` 254 passed/1 skipped, ruff clean
- [ ] Explicit user approval to open Phase 5

## Out of scope (stop and ask)

Production signals, AI trading decisions, execution/orders, full backtests, paper
trading, live trading, API/dashboard, large local downloads, large searches,
unapproved global config changes.
