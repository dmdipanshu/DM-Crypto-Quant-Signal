---
name: quant-research
description: Design causal quantitative hypotheses and evidence standards
---

# Quant Research

## Procedure

1. State the hypothesis, target, horizon, baseline, availability time, sample,
   costs, and validation procedure before tuning.
2. Audit feature availability times; check leakage, overlapping observations,
   selection bias, multiple testing, sample-size adequacy.
3. Distinguish exploration from confirmation; keep confirmatory holdouts locked.
4. Report inconclusive or negative evidence honestly; never force a target metric.

## Validation standards

- Every claim has a baseline, a sample, and an uncertainty statement.
- Accuracy vs win rate vs profitability kept distinct; profitability claimed only
  with valid out-of-sample support net of costs. `NO_TRADE` is valid.

## Failure handling

- Insufficient sample or flawed design → inconclusive verdict with reasons,
  never a tuned positive.

## Completion requirements

Handoff states: hypothesis docs, baselines, validation design, evidence grade
(exploratory vs confirmatory), limitations, unresolved questions.
