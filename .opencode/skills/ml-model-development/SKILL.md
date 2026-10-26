---
name: ml-model-development
description: Develop leakage-safe models, calibration, inference, and artifacts
---

# ML Model Development

## Procedure

1. Separate fit, transform, calibration, inference, evaluation — no shared state.
2. Fit every learned preprocessing step only on permitted (training) data.
3. Version feature schemas, training windows, hyperparameters, code identity, and
   artifact checksums together.
4. Calibrate separately from fitting; validate on held-out data.
5. Reject untrained or incompatible artifacts at inference with explicit errors.

## Validation standards

- Leakage audit: availability times checked for every feature; no training-serving skew.
- Calibration quality reported; inference compatibility enforced by schema + checksum.
- Never advertise an untrained model; never claim performance without out-of-sample support.

## Failure handling

- Schema/checksum mismatch or untrained artifact → hard rejection, never silent default.
- Leakage suspicion → halt training, report, do not tune around it.

## Completion requirements

Handoff states: interfaces, artifact versions + checksums, calibration results,
tests run, skew/leakage risks and unresolved issues. (Planned Phase 2; not
implemented in Phase 1.)
