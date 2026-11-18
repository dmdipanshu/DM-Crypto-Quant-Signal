# Q21 Evaluation Readiness Checklist

**Collection ID:** collect-52cefa0dda64
**Freeze ID:** freeze-aaa4dd57057a
**Current State:** ACCUMULATING
**Q6 Preflight:** BLOCKED

## State Machine Distinctions

| State | eligible_for_evaluation | evaluated | Meaning |
|---|---|---|---|
| ACCUMULATING | False | False | Post-freeze data exists but quality/coverage/count requirements not yet met (current state) |
| ELIGIBLE_FOR_EVALUATION | True | False | All provenance + quality requirements pass; awaiting explicit evaluation action |
| EVALUATED | True | True | Explicit evaluation performed after the freeze with all requirements satisfied |

Do not automatically transition between states.

## Deterministic Evaluation Checklist

| # | Step | Exact CLI Command | Status |
|---|---|---|---|
| 1 | Verify official collection eligibility | uv run python scripts/run_q5_prospective_collection.py --validate-prospective | Not yet (currently ACCUMULATING) |
| 2 | Verify observation count >= 168 | uv run python scripts/run_q5_prospective_collection.py --collect-status | Not yet (16 of 168) |
| 3 | Verify coverage >= 0.95 | uv run python scripts/run_q5_prospective_collection.py --collect-status | Not yet (22.54% of 95%) |
| 4 | Verify minimum duration >= 7 days | uv run python scripts/run_q5_prospective_collection.py --collect-status | Not yet (~2.99 days elapsed) |
| 5 | Verify OHLCV quality | Covered by Q5 --validate-prospective and Q6 --preflight | Not yet |
| 6 | Verify first-seen provenance | uv run python scripts/run_q6_prospective_evaluation.py --preflight | Not yet |
| 7 | Verify duplicate count = 0 | --collect-status or --preflight | Not yet (2 duplicates exist) |
| 8 | Verify conflict count = 0 | --collect-status or --preflight | Pass (0 conflicts) |
| 9 | Verify ledger integrity (hash chain valid) | --collect-status or --preflight | Pass (valid) |
| 10 | Verify collection and freeze identity match | --collect-status | Pass (both verified) |
| 11 | Run Q6 preflight | uv run python scripts/run_q6_prospective_evaluation.py --preflight | Not yet (BLOCKED) |
| 12 | Obtain explicit evaluation authorization | Human grants --authorize | PENDING |
| 13 | Evaluate only eligible frozen candidates | uv run python scripts/run_q6_prospective_evaluation.py --evaluate --candidate <id> --authorize | NOT TO BE EXECUTED NOW |
| 14 | Persist evaluation evidence | Done by Q6 evaluation script | Not yet |
| 15 | Produce evidence-based strategy assessment | Review q6_evaluation.json + governance report | Not yet |

## Candidate Integrity Findings

| Candidate | Artifact | Q4 Eligible | Details |
|---|---|---|---|
| majority | No artifact | No | No artifact directory |
| trend | No artifact | No | No artifact directory |
| logistic | Yes (ee482c-3e3324f2) | No | artifact.json + record.json present |
| lightgbm | No artifact | No | NOT_EVALUABLE (package not installed) |

**Q4:** eligible_model_ids: [], models_eligible_for_human_review: 0, promotion_eligible: false, overall_verdict: NO_STRATEGY_ELIGIBLE

**Integrity:** CandidateLock + compute_lock_hash (SHA-256 over lock fields), binding to freeze ID and collection ID

## Authorization Controls

- **Q5:** explicit action required (--evaluate with --validate-prospective); no automatic promotion
- **Q6:** --authorize mandatory for --evaluate; q6_cannot_auto_promote() → automatic_promotion=False, execution_authorized=False; human_approval_required=True; max outcome VALIDATION_PASSED_PENDING_HUMAN_REVIEW
- **Q4:** NO_STRATEGY_ELIGIBLE preserved; q6_cannot_override=True
- **Prospective data:** read-only; no retraining/recalibration/tuning/feature-selection/backfill
- **Parent governance:** max ELIGIBLE_FOR_HUMAN_REVIEW; human_authorized=False, execution_authorized=False, ai_promotion_authority=False

## Q6 Preflight Current Status

BLOCKED — 3 conditions:

1. Observation count 0 is below minimum 168
2. No observations collected; cannot evaluate prospective holdout
3. Prospective holdout is ACCUMULATING, not ELIGIBLE_FOR_EVALUATION

All 15 preflight conditions must pass before evaluation can proceed.

## Reminders

- Do NOT execute --authorize, promotion, or candidate evaluation during Q21
- Do not print credentials
- Q5_2_PROSPECTIVE_VALIDATED must remain false until all official gates pass
- PRODUCTION_READY must remain false
- LIVE_TRADING_AUTHORIZED must remain false
