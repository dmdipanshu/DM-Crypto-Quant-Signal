# QUANTITATIVE MODEL RESEARCH — Q2

Generated: 2026-11-12T14:11:05.674385+00:00

## Executive Summary

Compared 3 models on 17798 canonical observations (Nov 2024 - Nov 2026) with chronological walk-forward (3 folds, train=200, val=50, embargo=24). Best model: majority (accuracy 0.5333, balanced accuracy 0.3333). Calibration: not fitted. Holdout accuracy: 0.6299093655589124 (historical descriptive value only; not a pristine or promotion-grade result).

## Stage 1 — Dataset

- Rows: 17798
- Range: 2024-11-01 00:00:00+00:00 to 2026-11-12 13:00:00+00:00
- Features: 28
- Checksum: 675d4302072552c9...

## Stage 4 — Model Comparison (walk-forward aggregate)

| Model | Accuracy | Bal. Acc. | Macro-F1 | Val Rows |
|-------|----------|-----------|----------|----------|
| majority | 0.5333 | 0.3333 | 0.2319 | 150 |
| trend | 0.4733 | 0.3372 | 0.3173 | 150 |
| logistic | 0.5333 | 0.3362 | 0.2573 | 150 |

### Per-fold details
#### majority
| Fold | Train | Val | Accuracy | Bal. Acc. | Macro-F1 |
|------|-------|-----|----------|-----------|----------|
| 0 | 176 | 50 | 0.38 | 0.3333333333333333 | 0.18357487922705315 |
| 1 | 176 | 50 | 0.48 | 0.5 | 0.32432432432432434 |
| 2 | 176 | 50 | 0.74 | 0.5 | 0.42528735632183906 |

#### trend
| Fold | Train | Val | Accuracy | Bal. Acc. | Macro-F1 |
|------|-------|-----|----------|-----------|----------|
| 0 | 176 | 50 | 0.36 | 0.23076923076923075 | 0.17647058823529413 |
| 1 | 176 | 50 | 0.52 | 0.5064102564102564 | 0.4485294117647059 |
| 2 | 176 | 50 | 0.54 | 0.43970893970893976 | 0.44147644487615345 |

#### logistic
| Fold | Train | Val | Accuracy | Bal. Acc. | Macro-F1 |
|------|-------|-----|----------|-----------|----------|
| 0 | 176 | 50 | 0.38 | 0.3191632928475034 | 0.2318121693121693 |
| 1 | 176 | 50 | 0.48 | 0.5 | 0.32432432432432434 |
| 2 | 176 | 50 | 0.74 | 0.5 | 0.42528735632183906 |

## Stage 5 — Calibration

- Method: sigmoid
- Calibration fitted: False
- Calibration samples: 150
- Positive frac: 0.4333
- Brier (raw): 0.249812
- Log loss (raw): 0.692911

## Stage 6 — Historical Holdout Diagnostic (Consumed / Contaminated)

- Model: majority
- Holdout rows: 2648
- Range: 2026-07-24 06:00:00+00:00 to 2026-11-11 13:00:00+00:00
- Accuracy: 0.6299
- Balanced accuracy: 0.3333
- Macro-F1: 0.2576
- ROC-AUC: 0.5
- PR-AUC: 0.32137462235649544
- Ledger pristine: False
- Holdout integrity: CONTAMINATED (the holdout overlaps production-model training through 2026-10-01 and the evaluation has already accessed it)
- Promotion evidence: NO; metrics retained solely as historical descriptive diagnostics

## Stage 7 — Strategy Research (proxy)

- trend_following: trades=50, win_rate=0.52, expectancy=0.001700, total_bp=2550.00, sharpe_proxy=18.52742321169922, max_dd_proxy=0.1145
- momentum: trades=0, win_rate=None, expectancy=0.000000, total_bp=0.00, sharpe_proxy=None, max_dd_proxy=0.0000
- mean_reversion: trades=50, win_rate=0.4222222222222222, expectancy=-0.002200, total_bp=-3300.00, sharpe_proxy=-23.362866594461973, max_dd_proxy=0.3600

## Stage 8 — Robustness

- Accuracy mean: 0.5333
- Accuracy std: 0.1858
- Block bootstrap CI: [0.4133, 0.6533] (observed=0.5333)
- Permutation MC p (sign agreement): 1.0000

## Stage 9 — Feature Ablation (drop-one, logistic base)

- Base accuracy: 0.5333
| Feature | Accuracy without | Delta vs base |
|---------|------------------|---------------|
| realized_vol_20 | 0.5200 | -0.0133 |
| volume_z_20 | 0.5267 | -0.0067 |
| vol_regime_100 | 0.5267 | -0.0067 |
| ret_4 | 0.5333 | +0.0000 |
| ret_12 | 0.5333 | +0.0000 |
| sma_20 | 0.5333 | +0.0000 |
| sma_50 | 0.5333 | +0.0000 |
| sma_ratio_20 | 0.5333 | +0.0000 |
| trend_ratio_20_50 | 0.5333 | +0.0000 |
| ema_12 | 0.5333 | +0.0000 |
| ema_26 | 0.5333 | +0.0000 |
| macd | 0.5333 | +0.0000 |
| macd_signal | 0.5333 | +0.0000 |
| macd_hist | 0.5333 | +0.0000 |
| trend_slope_5 | 0.5333 | +0.0000 |
| atr_14 | 0.5333 | +0.0000 |
| rsi_14 | 0.5333 | +0.0000 |
| bb_mid_20 | 0.5333 | +0.0000 |
| bb_pos_20 | 0.5333 | +0.0000 |
| norm_vol_20 | 0.5333 | +0.0000 |
| range | 0.5333 | +0.0000 |
| body | 0.5333 | +0.0000 |
| upper_wick | 0.5333 | +0.0000 |
| lower_wick | 0.5333 | +0.0000 |
| trend_regime | 0.5333 | +0.0000 |
| ret_1 | 0.5400 | +0.0067 |
| sma_vol_20 | 0.5400 | +0.0067 |
| bb_width_20 | 0.5467 | +0.0133 |

## Limitations
- Holdout overlaps production model training period (Jul 1 - Oct 1, 2026).
- UNCALIBRATED scores; ranking only, not probabilities.
- Proxy-level strategy diagnostics; Phase 6 owns execution/fills.
- No PBO (no CSCV partition matrix); DSR + Holm/Bonferroni used instead.

## Q2 Verdict

**QUANTITATIVE EDGE NOT FOUND**

No model demonstrated a genuine predictive edge:
- Best walk-forward accuracy: 0.5333 (majority/logistic), below majority-class baseline of ~0.5333
- Best balanced accuracy: 0.3372 (trend), well below 0.50
- Best macro-F1: 0.3173 (trend)
- Holdout ROC-AUC: 0.5000 (no ranking ability)
- Calibration: not fitted (majority is constant; logistic accuracy equals majority)
- Feature ablation: max delta +0.0133 (no feature is critical)
- Permutation MC p = 1.0000 (no detectable structure)
- Robustness: high fold variance (std 0.1858)

Proceed to Q3 only if a research hypothesis emerges; do not grant any model
production authority.

---
Machine-readable results: quantitative_model_comparison_q2.json
Experiment registry: experiments_q2.jsonl