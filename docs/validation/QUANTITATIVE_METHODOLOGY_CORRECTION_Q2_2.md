# Quantitative Methodology Correction — Q2.2

Generated: 2026-11-12T20:21:59.538221+00:00

## Purpose

Correct the Q2.1/Q3 methodology defects identified in the evidence audit: align the training target with the simulated strategy, apply class-weighted training per fold, purge by actual event duration, and report collapse-aware, per-class metrics. Diagnostic only — no model promotion.

## Target definition (execution-aligned)

- Entry: open of bar ``t+1`` (decision at close of ``t``)
- Profit target: entry x (1 + 2.0 x causal vol)
- Stop loss: entry x (1 - 1.0 x causal vol)
- Max horizon: 24 bars
- Same-bar collision: conservative (neutral label / stop-first execution)
- Definition hash: a4fce32d2c1fdaf7

## Dataset

- Usable research rows: 14986
- Features: 28
- Class counts: {'-1': 9909, '0': 478, '1': 4698}
- Neutral (0) fraction: 0.03168710639708319

## Model comparison (walk-forward aggregate)

| Model | Accuracy | Bal. Acc. | Macro-F1 | MCC | Collapsed | Val Rows |
|-------|----------|-----------|----------|-----|-----------|----------|
| majority | 0.6574290484140234 | 0.3333333333333333 | 0.26443728176201986 | 0.0 | True | 2995 |
| trend | 0.5008347245409015 | 0.35532764047493126 | 0.33271762585408954 | 0.05909054061891688 | False | 2995 |
| logistic | 0.3018363939899833 | 0.31642680834420317 | 0.22837865326529636 | -0.005110453935846215 | False | 2995 |
| lightgbm | 0.48046744574290484 | 0.3798130690695645 | 0.3528807984250258 | 0.06227480741351092 | False | 2995 |

## Holdout integrity

- Status: not_yet_available
- Evaluation status: no_prospective_data
- Prospective validated: False
- Reasons: No dataset observations exist after the research freeze timestamp; prospective holdout validation is not yet possible.

## Decision

**No model is promoted by Q2.2.** Diagnostic evidence only. The corrected target shows whether any model family has genuine directional edge under execution-aligned semantics. Economic validation requires Q3's barrier-aligned simulator with a genuinely prospective holdout, which does not yet exist.
