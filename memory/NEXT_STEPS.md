# NEXT_STEPS.md — SignalForge handoff

## Immediate: user review (Phase 1 gate)

1. Review this Phase 1 report; explicitly approve or reject opening Phase 2.
2. Do NOT start features/labels/models until approval (AGENTS.md §8).

## If Phase 2 approved, proposed tasks

1. Causal features + volatility-scaled triple-barrier labels (new `features/`, `labels/`).
2. Baseline models (logreg/RF) + model registry with dataset/feature/param/checksum versioning.
3. Leakage tests + chronological splits (extends `tests/leakage/`).

## Gates already met (verified 2026-10-27)

- [x] `uv sync` clean on Windows, `uv.lock` tracked
- [x] CLI health + config-show + data status pass
- [x] pytest 37 passed, ruff check + format clean
- [ ] Explicit user approval to open Phase 2 (features/models)

## Out of scope (stop and ask)

Models, backtests, AI providers, paper trading, live orders, dashboard implementation.
