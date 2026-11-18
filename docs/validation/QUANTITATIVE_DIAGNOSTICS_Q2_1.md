# Quantitative Diagnostics — Q2.1

Generated: 2026-11-12T15:00:16.755529+00:00

## Purpose

Diagnose label horizon and class imbalance with repeated, purged, chronological walk-forward evaluation. This phase does not tune toward a target metric, open the historical holdout, or select a production model.

## Dataset

- Rows: 17798
- Period: 2024-11-01 00:00:00+00:00 to 2026-11-12 13:00:00+00:00
- SHA-256: `675d4302072552c90af253baf43f6555674ab4343088001ba15052c5f12e2790`
- Reserved tail: not evaluated by Q2.1; previously consumed/contaminated in Q2

## Horizon 6 bars

- Research rows: 15004
- Research period: 2024-11-05 23:00:00+00:00 to 2026-07-24 02:00:00+00:00
- Class fractions: `{'-1': 0.5803785657158091, '0': 0.16948813649693414, '1': 0.25013329778725674}`

| Model | Accuracy | Balanced accuracy | Macro-F1 | Status |
|---|---:|---:|---:|---|
| majority | 0.593 | 0.3333333333333333 | 0.2481690730278301 | ok |
| trend | 0.4256666666666667 | 0.35397298972341135 | 0.3008167279048857 | ok |
| logistic | 0.593 | 0.34349266805943385 | 0.27371704913830724 | ok |

## Horizon 12 bars

- Research rows: 14998
- Research period: 2024-11-05 23:00:00+00:00 to 2026-07-23 20:00:00+00:00
- Class fractions: `{'-1': 0.6378850513401787, '0': 0.06747566342178957, '1': 0.29463928523803173}`

| Model | Accuracy | Balanced accuracy | Macro-F1 | Status |
|---|---:|---:|---:|---|
| majority | 0.6427378964941569 | 0.3333333333333333 | 0.260840108401084 | ok |
| trend | 0.48414023372287146 | 0.360252241197123 | 0.3273725420296767 | ok |
| logistic | 0.6404006677796327 | 0.33252479803660906 | 0.26169377373644015 | ok |

## Horizon 24 bars

- Research rows: 14986
- Research period: 2024-11-05 23:00:00+00:00 to 2026-07-23 08:00:00+00:00
- Class fractions: `{'-1': 0.6564793807553717, '0': 0.03162952088616042, '1': 0.3118910983584679}`

| Model | Accuracy | Balanced accuracy | Macro-F1 | Status |
|---|---:|---:|---:|---|
| majority | 0.6574290484140234 | 0.3333333333333333 | 0.26443728176201986 | ok |
| trend | 0.5008347245409015 | 0.35532764047493126 | 0.33271762585408954 | ok |
| logistic | 0.6574290484140234 | 0.3336841694314267 | 0.2657548752533138 | ok |

## Horizon 48 bars

- Research rows: 14962
- Research period: 2024-11-05 23:00:00+00:00 to 2026-07-22 08:00:00+00:00
- Class fractions: `{'-1': 0.6626787862585216, '0': 0.018714075658334446, '1': 0.31860713808314395}`

| Model | Accuracy | Balanced accuracy | Macro-F1 | Status |
|---|---:|---:|---:|---|
| majority | 0.6581939799331104 | 0.3333333333333333 | 0.2646228317870109 | ok |
| trend | 0.5030100334448161 | 0.35494545022769813 | 0.3335301904835344 | ok |
| logistic | 0.6581939799331104 | 0.33368186516916715 | 0.2659362783743223 | ok |

## Horizon 72 bars

- Research rows: 14938
- Research period: 2024-11-05 23:00:00+00:00 to 2026-07-21 08:00:00+00:00
- Class fractions: `{'-1': 0.662605435801312, '0': 0.017472218503146338, '1': 0.31992234569554157}`

| Model | Accuracy | Balanced accuracy | Macro-F1 | Status |
|---|---:|---:|---:|---|
| majority | 0.6582914572864321 | 0.3333333333333333 | 0.26464646464646463 | ok |
| trend | 0.5068676716917923 | 0.3558701423518383 | 0.3355063058423429 | ok |
| logistic | 0.6582914572864321 | 0.3336820572610992 | 0.26601334736862503 | ok |

## Decision

**No model is promoted by Q2.1.** This is diagnostic evidence only. Economic validation uses the Q3 next-open simulator with explicit cost stress; a new prospective holdout remains mandatory before any human promotion review.
