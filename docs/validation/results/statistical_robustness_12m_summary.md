# Statistical Robustness Analysis — 12-Month Dataset

- Dataset: HISTORICAL_ACCELERATED_VALIDATION_12M
- N candles: 8760
- Buy-and-hold return: -3213.45 bp

## Candidate: logistic (logistic)
- Temporal provenance: TEMPORAL_PROVENANCE_VERIFIED
- Status: EVALUATED
- Block-bootstrap CI: mean=-44.66 bp, 95% CI=[-47.36, -41.85], p_zero=1.0000 (n=1000, block=24)
- Permutation test: observed=-44.66 bp, p_value=0.0000, null_mean=-0.02 bp (n=1000)
- Regime robustness: 0.0000
- Benchmark comparison: candidate_net=-385874.90 bp, buy_hold_net=-3213.45 bp, outperforms_buy_hold=False, outperforms_cash=False
- Cost sensitivity:
  - mult=1.0: net=-385874.90 bp, trades=8641, profitable=False
  - mult=1.5: net=-472284.90 bp, trades=8641, profitable=False
  - mult=2.0: net=-558694.90 bp, trades=8641, profitable=False

## Candidate: trend (trend)
- Temporal provenance: TEMPORAL_PROVENANCE_UNVERIFIED
- Status: SKIPPED_UNVERIFIED
- Statistical tests: SKIPPED_UNVERIFIED (no block-bootstrap, permutation, regime, cost-sensitivity, or benchmark tests)
