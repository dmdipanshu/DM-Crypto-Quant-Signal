# SignalForge Q2.1–Q4 Evidence Audit

**Audit date:** 2026-11-12  
**Inputs:** `validation.zip` reports and machine-readable JSON; source inspection of the corresponding Q2.1–Q4 build scripts and research helpers from `SignalForge_Q2_to_Q4_Build.zip`.

## Executive verdict

The Q2.1 → Q3 → Q4 pipeline completed against the canonical BTC/USDT 1h data and Q4 correctly returned **`NO_STRATEGY_ELIGIBLE`**. No candidate is suitable for promotion, paper trading, or live execution.

The quantitative evidence still supports **QUANTITATIVE EDGE NOT FOUND**. More importantly, the audit found methodology/integration issues that should be corrected before treating Q3 as an economic validation of the Q2.1 models.

## 1. Q2.1 — model diagnostics

- Canonical data: 17,798 rows, 2024-11-01 through 2026-11-12; SHA-256 in the report is consistent across the Q2.1/Q3 artifacts.
- Evaluated horizons: 6, 12, 24, 48 and 72 bars; five chronological validation folds per horizon.
- Only three model families ran. LightGBM was **skipped** because the optional dependency was not installed in the Windows environment.
- Class `-1` represented about 58.0%–66.3% of labels; class `0` represented only about 1.7%–16.9%.
- The majority classifier predicted `-1` for every validation observation. Logistic predicted `-1` for about 99.5%–99.9% of observations. Trend emitted only `-1` and `+1`, never neutral `0`.
- Balanced accuracy was approximately 0.333 for majority/logistic and 0.354–0.360 for trend. Trend's raw accuracy was below the majority baseline at every horizon.

**Conclusion:** horizon diagnostics ran, but the candidate models show severe class collapse and no convincing predictive edge. The run is diagnostic only; it did not open the reserved tail or promote a model.

## 2. Q3 — strategy results

All three candidates produced 2,995 out-of-fold decisions and 120 non-overlapping trades each.

| Candidate | Win rate | Expectancy / trade | Net total | Profit factor | Positive folds |
|---|---:|---:|---:|---:|---:|
| Majority | 44.17% | -29.75 bp | -3,570.26 bp | 0.6967 | 2/5 (40%) |
| Trend | 40.83% | -33.60 bp | -4,031.53 bp | 0.6671 | 1/5 (20%) |
| Logistic | 44.17% | -29.75 bp | -3,570.26 bp | 0.6967 | 2/5 (40%) |

At 2× costs, net totals deteriorated to -7,770.26 bp for majority/logistic and -8,231.53 bp for trend. Every candidate fails the predeclared requirements: profit factor >= 1.10, at least 75% positive folds, positive 2×-cost return, and a genuinely prospective holdout.

### Cost arithmetic check — passes

The configured inputs are 10 bp fee per side, 5 bp round-trip spread and 5 bp slippage per side. The implementation calculates:

`2 × fee + round-trip spread + 2 × slippage = 20 + 5 + 10 = 35 bp` per round trip.

The 1×/1.5×/2× cost schedule in the report is consistent with that formula. Funding was set to zero; these results must not be interpreted as perpetual-futures results with realistic funding unless that assumption is explicitly justified.

### Model-collapse check

Majority predicted short on all 2,995 decisions. Logistic predicted short 2,991 times and long four times. The actual 120 executed trade records for majority and logistic are identical, so the four logistic deviations never changed an executed position under the no-overlap rule. Logistic therefore provided no economic differentiation in this run.

### Important methodology gap: target vs. simulated strategy

Q2.1/Q3 create labels with `TripleBarrierConfig(pt_mult=2, sl_mult=1, side="both")`. In `src/crypto_engine/labels/triple_barrier.py`, `side="both"` follows the same barrier geometry as `side="long"`: `+1` is an upper profit-target hit and `-1` is a lower stop-loss hit on that reference long path. It is not a separate, symmetric long-and-short barrier simulation.

Q3 then interprets predictions as `-1 = SHORT`, `+1 = LONG` and evaluates them using a fixed 24-bar next-open-to-open exit. The simulator does not execute the target/stop barriers used to create the labels. This is a different economic outcome definition. The current result is useful as a fixed-horizon directional diagnostic, but it is not a direct strategy validation of the triple-barrier target.

**Required correction:** either make the Q3 simulator execute the same side-aware triple-barrier rules as the label, including entry timing and costs, or redefine the learning target as a signed fixed-horizon return that exactly matches the Q3 simulator. Add tests that prove the class-to-position mapping and the entry/exit timing.

### Return accounting caveat

`src/crypto_engine/research/execution.py` reports `total_net_return_bp` as the arithmetic sum of per-trade returns, while maximum drawdown is calculated from a compounded equity curve. Keep this labelled as a research aggregate; do not present the summed bp figure as an account-level portfolio return until the notional, sizing and compounding convention are specified consistently.

## 3. Q3/Q4 governance integration issue

In `scripts/q3_strategy_validation.py`, `_evaluate_model()` calls the promotion gate with a hard-coded `{"status": "unknown", "pristine": False}`. The same script later computes the known historical holdout state as **contaminated**. Consequently, Q3's per-model report says `unknown`, while Q4 correctly says `contaminated` after re-evaluating the gate from the Q3 artifact.

This did not make a candidate eligible—the gate remained fail-closed—but the reason is inaccurate and can confuse audits. Compute the canonical historical integrity record before evaluating candidates and pass that same record into every Q3 promotion gate. Keep the previous Q2 holdout and the newly reserved 15% tail as separately named provenance records; both are contaminated, but they are not the same timestamp/event.

## 4. Q4 — release decision

- Overall verdict: **`NO_STRATEGY_ELIGIBLE`**.
- Models evaluated: 3; eligible for human review: 0.
- Historical holdout: contaminated; prospective holdout validated: false.
- AI advisory: disabled in this run. The runner was invoked without `--ai`; this is the default behavior. It is not evidence that a configured provider credential is invalid.
- AI selection/promotion authority, human authorization, paper authorization and order-execution authorization are all false.

The Q4 artifact SHA-256 values for its Q2.1 and Q3 source artifacts match the actual files in the supplied archive.

## 5. Required next build — Q2.2 correction pass

Do **not** rerun the unchanged Q2.1 → Q3 → Q4 sequence; it will reproduce the same limitations. The next code pass should:

1. Align the label and strategy outcome definitions (triple barrier on both sides, or fixed-horizon labels plus fixed-horizon execution); explicitly document the meaning of `-1`, `0`, and `+1`.
2. Correct Q3 to pass the known contaminated holdout record to every model gate rather than hard-coding `unknown`.
3. Make performance accounting internally consistent: report compounded account return for a declared sizing convention, alongside arithmetic per-trade bp totals if retained.
4. Install and execute LightGBM in the research environment, or mark the model comparison incomplete when a required candidate is skipped. Do not silently treat three models as the full model grid.
5. Make the venue/market assumption explicit. For spot, no funding may be appropriate; for futures/perpetuals, use time- and side-aware funding assumptions and disclose liquidation/borrow/microstructure omissions.
6. Add regression tests for label semantics, side mapping, barrier/fixed-horizon parity, trade costs, fold boundaries, holdout provenance, and return accounting.
7. Freeze model, label, strategy and cost assumptions before collecting a new genuinely prospective dataset. The existing historical holdout and current reserved tail must never be relabelled pristine.

## Safety status

No model promotion, paper-state mutation, live order placement or execution authority should be enabled. Preserve `NO_STRATEGY_ELIGIBLE` until corrected methodology, realistic costs, robust walk-forward evidence and a genuinely prospective untouched holdout have all passed independent review.
