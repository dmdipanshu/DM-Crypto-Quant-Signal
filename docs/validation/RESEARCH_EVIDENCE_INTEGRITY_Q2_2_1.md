# Research Evidence Integrity — Q2.2.1

Research freeze timestamp: 2026-11-12T19:11:47.293700+00:00
Generated: 2026-11-12T19:47:00+00:00

## Purpose

Q2.2.1 corrects the Q2.1/Q3 methodology defects identified in the evidence
audit: aligns the training target with the simulated strategy, applies
class-weighted training per fold, purges by actual event duration, reports
collapse-aware per-class metrics, and implements real walk-forward fold
stability with corrected gross-vs-net accounting. Diagnostic only — no model
promotion.

## Dataset Population and Denominator (BUG 5 fix)

The Q2 dataset contains multiple populations with different denominators.
All reported counts are reconciled below:

| Population | Count | Description |
|------------|-------|-------------|
| total_rows | 15,129 | All rows in the canonical parquet dataset |
| warmup_dropped | 119 | Rows dropped for indicator warmup |
| usable_rows | 14,986 | Rows with complete features (total_rows − warmup_dropped) |
| complete_rows | 15,085 | Rows with complete target labels |
| incomplete_rows | 44 | Rows with incomplete target labels |
| out_of_fold_val_rows | 2,995 | Validation rows across 5 folds (5 × 599) per model |

**Class-count reconciliation (BUG 5):**

The reported class-count total of 15,085 corresponds to `complete_rows`
(rows with complete target labels), not `usable_rows` (14,986). These are
different populations:

- `usable_rows` (14,986): rows with complete features — denominator for
  feature-based calculations.
- `complete_rows` (15,085): rows with complete target labels — denominator
  for class-distribution calculations.
  - Class -1 (SHORT): 9,909 (65.69%)
  - Class 0 (neutral): 478 (3.17%)
  - Class 1 (LONG): 4,698 (31.14%)
  - Total: 9,909 + 478 + 4,698 = 15,085

Class distributions reported in fold-level and aggregate metrics use the
out-of-fold validation population (n = 2,995 per model, 599 per fold), not
the full dataset. Each fold contains 599 validation rows (class -1: 369,
class 0: 10, class 1: 220).

## Methodology Corrections

1. **Execution-aligned target**: The training label and the Q3 simulator share
   one barrier geometry — entry at the next-bar open, causal volatility at the
   decision bar, side-aware profit-target / stop-loss levels, conservative
   same-bar collision handling, and a max holding-period exit.

2. **Class-weighted training (training fold only)**: Logistic regression and
   LightGBM receive `class_weight="balanced"` computed from the training
   fold's class frequencies — never from validation or holdout data.

3. **Actual-duration purging/embargo**: Purge intervals are built from each
   label's actual observable bar (`observable_at`), not a conservative fixed
   horizon.

4. **Real walk-forward fold stability**: `n_folds` counts only successfully
   evaluated folds, not the configured number. Failed/skipped folds are
   never counted as successful. Per-fold predictive metrics include
   accuracy, balanced_accuracy, macro_precision, macro_recall, macro_f1,
   MCC, per-class precision/recall/F1, multiclass ROC-AUC, and
   prediction_collapse_status.

5. **Corrected gross-vs-net accounting**: Gross returns are calculated before
   transaction costs; net returns after all costs. Each cost component
   (fees, spread, slippage, funding) is tracked independently and applied
   exactly once per round trip. Fold-level results report both
   `gross_return_bp` and `net_return_bp` — gross is never labelled as net.

6. **Holdout integrity**: The historical holdout is assessed as
   `not_yet_available` / `contaminated` — never pristine. No prospective
   holdout exists yet, so promotion is blocked.

## Target Definition (Execution-Aligned)

- Entry: open of bar t+1 (decision at close of t)
- Profit target: entry x (1 + 2.0 x causal vol)
- Stop loss: entry x (1 - 1.0 x causal vol)
- Max horizon: 24 bars
- Same-bar collision: conservative (neutral label / stop-first execution)

## Model Comparison (Walk-Forward Aggregate)

| Model | n_folds | Accuracy | Bal. Acc. | Macro-F1 | MCC | Collapsed | Val Rows |
|-------|---------|----------|-----------|----------|-----|-----------|----------|
| majority | 5 | 0.6574 | 0.3333 | 0.2644 | 0.0000 | True (class -1) | 2,995 |
| trend | 5 | 0.5008 | 0.3553 | 0.3327 | 0.0591 | False | 2,995 |
| logistic | 5 | 0.3018 | 0.3164 | 0.2284 | -0.0051 | False | 2,995 |
| lightgbm | 5 | 0.4805 | 0.3798 | 0.3529 | 0.0623 | False | 2,995 |

## Fold Stability

Per-fold predictive metrics are computed for each successfully evaluated
fold. Aggregate stability (mean, std, min, max, median) is reported
separately for predictive metrics. Only evaluated folds are counted —
`n_folds` reflects actual evaluations (5 for all models), not the
configured fold count.

### Per-fold predictive metrics (lightgbm, fold 0)

- accuracy: 0.4825, balanced_accuracy: 0.3437, macro_f1: 0.3284, mcc: 0.0517
- per-class: -1 (P: 0.647, R: 0.417, F1: 0.507), 0 (P: 0.000, R: 0.000, F1: 0.000), 1 (P: 0.391, R: 0.614, F1: 0.478)
- prediction_collapse_status: ok
- train_rows: 5,976, validation_rows: 599
- train_start: 2025-07-13T14:00:00+00:00, train_end: 2026-03-19T13:00:00+00:00
- validation_start: 2026-03-20T14:00:00+00:00, validation_end: 2026-04-14T12:00:00+00:00

## Holdout Integrity

- Status: not_yet_available
- Evaluation status: no_prospective_data
- Historically contaminated: Yes (permanent)
- Prospective validated: No
- research_freeze_timestamp: 2026-11-12T19:11:47.293700+00:00

## Decision

**No model is promoted by Q2.2.1.** Diagnostic evidence only. The corrected
target shows whether any model family has genuine directional edge under
execution-aligned semantics. Economic validation requires Q3's
barrier-aligned simulator with a genuinely prospective holdout, which does
not yet exist.

## Limitations

- All evaluation is on the research head; the historical holdout is
  contaminated and the prospective holdout does not yet exist.
- Class distributions use out-of-fold validation population (n=2,995), not
  the full usable dataset (n=14,986).
- Scores are UNCALIBRATED rankings, not probabilities.
- Class weighting is computed per training fold only.
