# Q2-Q4 Bug Backlog

**Generated:** 2026-11-12T14:15:00+00:00
**Phase:** Q2 (Quantitative Model Research)

## Summary

No critical defects were discovered during Q2. All existing SignalForge
infrastructure components function correctly. The Q2 result
(QUANTITATIVE EDGE NOT FOUND) reflects the genuine absence of predictive
edge in the evaluated models, not an implementation defect.

## Bugs

| ID | Phase | Component | Severity | Title | Status |
|----|-------|-----------|----------|-------|--------|
| Q2-B-001 | Q2 | Model comparison | P3 | LightGBM not installed; skipped in model comparison | Open |

### Q2-B-001: LightGBM not installed

**Component:** Model comparison (Q2.2)
**Severity:** P3 (Low)
**Reproduction:** Running `scripts/q2_research_lab.py` skips LightGBM because the `lightgbm` Python package is not installed in the environment.
**Expected:** LightGBM included as an optional model candidate when available.
**Actual:** LightGBM silently skipped; only majority, trend, and logistic are compared.
**Root-cause hypothesis:** `uv sync --extra ml` was not run; LightGBM is an optional dependency.
**Risk to platform:** None — LightGBM is optional; core comparison uses sklearn models.
**Dependencies:** `uv sync --extra ml` (adds lightgbm)
**Recommended fix:** Run `uv sync --extra ml` and re-run Q2 to include LightGBM.
**Status:** Open (documented, not blocking)

## Notes

- No P0 (critical) defects: no financial calculation errors, no leakage, no security exposures, no broken risk controls, no unintended order execution.
- No P1 (high) defects: all quantitative metrics computed correctly, calibration behaves as expected (not fitted when inappropriate), no model evaluation defects.
- Q2 result is QUANTITATIVE EDGE NOT FOUND — all models perform at or below baseline. This is a research finding, not a defect.
