# SignalForge Quantitative Build Roadmap — Q2 to Q4

**Scope:** research and advisory engineering only. This roadmap does not enable paper-state mutation, live trading, order placement, or model promotion.

## Baseline decision

Q2 concluded **QUANTITATIVE EDGE NOT FOUND**. Its reported classifier metrics were close to class-prior baselines, ROC-AUC was 0.50, and the permutation test did not reject the null. The old report's final-holdout designation was not valid for promotion: the historical period beginning 2026-07-24 overlaps production-model training through 2026-10-01 and had already been accessed. The repository now records that result as contaminated/descriptive only.

Q2→Q4 therefore builds a better research process, not a claim that a profitable strategy has been discovered.

## Q2.1 — targeted diagnostic research

**Implementation:** `scripts/q2_1_diagnostic_lab.py`

- Split the raw chronological OHLCV series before feature or label generation. The reserved tail receives no features, labels, predictions, or metrics in this phase.
- Compare predeclared triple-barrier horizons of 6, 12, 24, 48 and 72 bars.
- Report label/class distribution, majority-class fraction, accuracy, balanced accuracy and macro-F1.
- Use five purged/embargoed chronological walk-forward folds per horizon.
- Compare majority, trend and logistic baselines; include LightGBM only when the optional dependency is installed.
- Do not tune hyperparameters, select a winning production model, calibrate on the reserved tail, or claim economic edge.

**Artifacts:**

- `docs/validation/QUANTITATIVE_DIAGNOSTICS_Q2_1.md`
- `docs/validation/results/quantitative_diagnostics_q2_1.json`

**Status:** code implemented; research run remains pending in the user's fully provisioned environment.

## Q3 — cost-aware strategy validation

**Implementation:** `scripts/q3_strategy_validation.py` and `src/crypto_engine/research/execution.py`

- Apply the raw-bar holdout split before generating features and triple-barrier labels.
- Produce out-of-fold predictions from five purged/embargoed folds.
- Treat each prediction as a close-of-bar decision; enter at the next bar open and exit at the open after a fixed 24-bar holding period.
- Disallow overlapping positions and report skipped overlaps/incomplete exits.
- Include explicit fee, spread, slippage and funding assumptions, then stress total costs at 1.0x, 1.5x and 2.0x.
- Report each model separately. Do not combine the best return, best profit factor or largest trade count from different models into a synthetic strategy.
- Use a deterministic fail-closed gate: at least 100 trades, profit factor at least 1.10, at least 75% positive walk-forward folds, and positive net return at 2.0x costs. Even a passing result can only become **eligible for human review**.

The helper is a transparent fixed-horizon OHLC research diagnostic, not a replacement for SignalForge's production event-driven backtester. It does not model stop/target execution, liquidation, borrow, actual exchange funding settlements or market impact. Cost defaults are explicit assumptions and must be replaced with realistic venue/fee-tier assumptions before drawing conclusions.

**Artifacts:**

- `docs/validation/STRATEGY_VALIDATION_Q3.md`
- `docs/validation/results/strategy_validation_q3.json`

**Status:** code implemented; research run remains pending in the user's fully provisioned environment.

## Q4 — deterministic research release and advisory integration

**Implementation:** `scripts/q4_research_intelligence.py`, read-only API adapter/route and Research dashboard panel.

- Combine Q2.1 diagnostics and Q3 model-specific metrics into a checksummed release record.
- Preserve candidate results separately; no candidate is silently selected.
- Make AI summarization opt-in (`--ai`). The AI receives candidate-scoped, allowlisted deterministic evidence for each model separately; there is no cross-model aggregation. It cannot select, score, promote or authorize a strategy.
- Expose the release through `GET /api/v1/research/release` and `/dashboard/research` using read-only repository adapters.
- Reject malformed/oversized release files and any authority declaration that enables AI promotion, paper trading or order execution.

**Artifacts:**

- `docs/validation/QUANTITATIVE_RELEASE_Q4.md`
- `docs/validation/results/quantitative_release_q4.json`

**Status:** code implemented; generated release pending completion of Q2.1 and Q3.

## Promotion and prospective-data rule

The historical Q2 holdout is consumed and contaminated. The tail reserved by these new scripts is deliberately not evaluated, but it overlaps the previously accessed historical period and must not be re-labelled pristine. A genuinely prospective holdout must begin strictly after the model and strategy parameters are frozen, have zero prior access, and remain untouched until the predeclared evaluation. New data must be collected after the freeze point; a retrospective split of the existing data does not satisfy this rule.

Even after a clean prospective result, the maximum automated status is `ELIGIBLE_FOR_HUMAN_REVIEW`. Human authorization, production promotion, paper-trading authorization and order-execution authorization remain false. Risk controls remain deterministic and independent from AI.

## Run the complete sequence

From the repository root in the project's provisioned Python environment:

```powershell
uv run python scripts/run_q2_to_q4.py
```

To request an optional AI advisory only after the deterministic release has been built:

```powershell
uv run python scripts/run_q2_to_q4.py --ai
```

Optional Q3 assumptions can be overridden explicitly:

```powershell
uv run python scripts/run_q2_to_q4.py --fee-bp 10 --spread-bp 5 --slippage-bp 5 --funding-bp-per-bar 0
```

The orchestrator stops at the first failed phase and preserves any earlier artifacts. It does not download data, update the canonical dataset, write signals, modify paper state, promote models, or execute trades.

## Verification status for this build

- The Q2.1 → Q3 → Q4 pipeline completed on a deterministic synthetic OHLCV dataset. All five Q2.1 horizons and all five Q3 folds completed for majority, trend, logistic and LightGBM. This validates orchestration and code paths only; synthetic results are not market evidence.
- Focused Q2–Q4 governance, simulator, release and dashboard tests: 17 passed.
- Model-wrapper tests, including negative and three-class LightGBM label encoding: 12 passed.
- Read-only API and dashboard smoke test passed; GET-only route invariant held.
- The real canonical BTC/USDT Parquet run and the complete repository suite were not completed in the build sandbox because `pyarrow` was not installed there and package-network access was unavailable. Run the documented command in the project's provisioned `uv` environment before treating Q2.1/Q3 results as real-data evidence.
- Ruff was unavailable in the build sandbox; formatting/lint verification remains to be run in the local project environment.
