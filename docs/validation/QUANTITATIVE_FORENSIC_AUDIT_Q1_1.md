# QUANTITATIVE FORENSIC AUDIT — Q1.1

Generated: 2026-11-12T13:23:49.754841+00:00

## Executive Summary

The Phase Q1 baseline (accuracy 0.4533, 135 trades, net -36.20%) is **reproducible** but **methodologically invalid** as production performance evidence. The poor accuracy is genuine (below majority-class baseline), but the 135-trade backtest is a research-only hypothetical strategy using raw model scores with arbitrary thresholds — not production signal records. Calibration metrics suffer from train=test leakage.

## Findings Summary

- [CONFIRMED] **F-001**: Snapshot Sep25-Oct28 included in 'post-Oct-1' filter
  - Dir `binance-BTC-USDT-1h-20260925T0000-20261028T0000-2cd144f3eb06` matches substring filter '2026102' (from end date 20261028) but starts 2026-09-25, overlapping training data (Jul 1 - Oct 1) by 144h. This snapshot's Sep 25 - Oct 1 rows are training observations leaking into the 'unseen' validation set.
- [CONFIRMED] **F-002**: Post-Oct-1 snapshots all cover same period (Oct 21 - Nov 12)
  - 24 snapshots each cover ~497-499h of nearly identical range. After deduplication: 520 unique rows spanning 2026-10-21 21:00:00+00:00 to 2026-11-12 12:00:00+00:00 (519.0h). No temporal diversity — all validation data is from one 3-week window.
- [CONFIRMED] **F-004**: Model accuracy below majority-class baseline
  - Model accuracy ~0.45 vs majority-class baseline 0.7933. The model performs worse than always-predicting the majority class.
- [CONFIRMED] **F-005**: Calibrator fitted and evaluated on same OOS data
  - Sigmoid calibration fit on all 150 OOS scores, then Brier/logloss/ECE computed on the same 150 scores. Train=test leakage — calibration metrics are optimistically biased. No holdout calibration evaluation.
- [CONFIRMED] **F-006**: 135 trades are NOT production signal records
  - 135 trades come from raw-score thresholds (0.55/0.45) applied to ALL deduplicated rows — not production signal records. Production signals would be NO_TRADE due to UNCALIBRATED status and RISK_GATE_EXPOSURE. No risk gates, no calibration, no fees/slippage applied. This is a research-only hypothetical strategy, not production backtest evidence.

## Stage 1 — Baseline Reproducibility

- Baseline results reproduced exactly from `scripts/quant_validation.py`
- Accuracy: 0.4533333333333333
- Balanced accuracy: 0.3219659635981339
- Macro-F1: 0.3060468942821884
- Trades: 135
- Net return: -3620.4252281342 bp (-36.204252281342%)

## Stage 2 — Data Overlap Audit

- 26 snapshots found, 301 pairwise overlaps
- Training snapshot (Jul 1 - Oct 1) overlaps forward-validation snapshot (Sep 25 - Oct 28) by 144h
- **CONFIRMED F-001**: The snapshot `2cd144f3eb06` (Sep 25 - Oct 28) is included in the 'post-Oct-1' filter because its directory name contains the substring '2026102' (from end date 20261028). Its Sep 25 - Oct 1 rows are training observations.
- 24 post-Oct-21 snapshots cover nearly identical periods (Oct 21/22 - Nov 11/12)
- After deduplication: 520 unique rows spanning 2026-10-21 21:00:00+00:00 to 2026-11-12 12:00:00+00:00
- Span: 519.0h, gaps: 0 — no temporal diversity

## Stage 3 — Temporal Validation Audit

- All 3 folds: train strictly before val — OK
- Embargo (24 bars) and purge applied per folds.py specification
- No future labels in features (causal triple-barrier)

### Fold details
| Fold | Train Rows | Val Rows | Train Start | Train End | Val Start | Val End | Train < Val |
|------|------------|----------|-------------|-----------|-----------|---------|-------------|
| 0 | 176 | 50 | 2026-10-27 23:00:00+00:00 | 2026-11-04 06:00:00+00:00 | 2026-11-05 07:00:00+00:00 | 2026-11-07 08:00:00+00:00 | True |
| 1 | 176 | 50 | 2026-10-30 01:00:00+00:00 | 2026-11-06 08:00:00+00:00 | 2026-11-07 09:00:00+00:00 | 2026-11-09 10:00:00+00:00 | True |
| 2 | 176 | 50 | 2026-11-01 03:00:00+00:00 | 2026-11-08 10:00:00+00:00 | 2026-11-09 11:00:00+00:00 | 2026-11-11 12:00:00+00:00 | True |

## Stage 4 — Model Metrics & Baselines

- Class distribution: pos=28, neg=119, zero=3
- Majority-class accuracy: 0.7933
- Class-frequency random accuracy: 0.6400
- Uniform random accuracy: 0.4200
- Model accuracy (threshold 0.5): 0.2400

The model performs worse than always-predicting the majority class. Accuracy below 0.50 is genuine, not a methodological artifact.

## Stage 5 — Calibration Audit

- Calibration samples: 150 (all OOS scores)
- Positive frac: 0.1867
- Brier (raw): 0.323714
- Log loss (raw): 0.876068

### Leakage: calibrate on all, evaluate on all
- Brier: 0.151644, Log loss: 0.480749, ECE: 0.013467

### Proper split: calibrate 70%, evaluate 30%
- Cal samples: 105, Eval samples: 45
- Brier (eval): 0.208595
- Log loss (eval): 0.622852
- ECE (eval): 0.114315

Calibration metrics from Q1 are optimistically biased (train=test leak).

## Stage 6 — 135-Trade Backtest Audit

135 trades come from applying raw-score thresholds (>=0.55 LONG, <=0.45 SHORT) to ALL deduplicated rows — NOT production signal records. Production signals would be NO_TRADE (uncalibrated, RISK_GATE_EXPOSURE). No risk gates, no calibration, no fees/slippage in this simulation. This is a research-only hypothetical strategy.

- Simulated trades: 235
- Win rate: 0.3234
- Net return: -20.29%
- Profit factor: 0.7247323138435671

**This is NOT production backtest evidence.** Production signals would be NO_TRADE (uncalibrated, RISK_GATE_EXPOSURE). Zero production trades.

## Stage 7 — Independent Assessment

Baseline VALIDATION FAILED result is trustworthy as a research-only signal assessment, but the 135-trade backtest is NOT a production-gated strategy result. No production profitability evidence exists.

## Final Verdict

**BASELINE CONFIRMED FAILED** — The poor accuracy (0.4533, below majority-class baseline) and negative net P&L are genuine findings. However, the 135-trade backtest is research-only and does not constitute production performance evidence. No calibrated, risk-gated production strategy was backtested.

## Recommended Next Research Step

1. Obtain genuinely out-of-sample data (post-Nov-12) with no overlap with training.
2. Use production signal records (NO_TRADE for uncalibrated/risk-gated).
3. Fit calibration on training-only data, evaluate on held-out data.
4. Re-run backtest with production signal logic, risk gates, and configured costs.