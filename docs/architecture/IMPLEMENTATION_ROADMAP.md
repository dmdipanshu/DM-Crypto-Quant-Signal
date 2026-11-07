# Implementation Roadmap

**Proposal only. The layered rebuild has not started.** Every stage below
requires explicit phase authorization before its first line of code. Stage 0 is
the gate that authorizes everything else. The optional AI-gateway sequence in
§3 is the one exception: an authorized subset ("Stages 0–5") is already
implemented and committed; see the status note there.

## 0. Rules for every stage

1. One stage per authorization; no bundling.
2. Every stage ends with an actually-run verification record (exact commands,
   pass/fail/skip counts).
3. Every stage ends with a Git commit of the verified state. Uncommitted work
   is not a checkpoint.
4. No stage may weaken a safety boundary to make a test pass.
5. No stage may change strategy behaviour unless that change is the stage's
   explicit scope.
6. A stage that cannot meet its acceptance criteria is reported as failed, not
   redefined.
7. Documentation is updated in the same change as the code it describes.

## 1. Sequencing rationale

The order is driven by dependency, not by visible progress:

- Domain and ports must exist before anything can depend on them.
- Storage and market data must be behind ports before application code can be
  tested without files.
- Quality must exist before research can trust a dataset.
- Analytics must be extracted before strategies can consume them.
- Strategies and signals must exist before risk can be separated from them.
- Research consolidation must precede CLI thinning, otherwise the CLI keeps
  owning orchestration.
- Presentation moves last, because it should consume stable use cases.
- Optional modules come after the core is green, so their absence can be
  proven.

## 2. Stage table

| Stage | Name | Depends on | Produces |
|---|---|---|---|
| 0 | Approval and baseline freeze | — | Recorded baseline |
| 1 | Environment and verification harness | 0 | Reproducible test run |
| 2 | Domain and ports skeleton | 1 | Pure domain + protocols |
| 3 | Storage and market-data adapters | 2 | Port-backed I/O |
| 4 | Quality engine and dataset gate | 3 | Verified datasets |
| 5 | Analytics extraction | 4 | Indicators, features, regimes, MTF |
| 6 | Strategies and signal composition | 5 | Proposals, scoring, diagnostics |
| 7 | Risk separation and portfolio core | 6 | RiskDecision, ledger rules |
| 8 | Research and backtest consolidation | 7 | Walk-forward, holdout, robustness |
| 9 | Application layer and composition root | 8 | Use cases, thin CLI |
| 10 | Presentation on use cases | 9 | API and dashboard re-pointed |
| 11 | Optional modules | 10 | Scheduler, notifications, AI, ML |
| 12 | Architecture tests and reconciliation | 11 | Enforced boundaries |

---

## Stage 0 — Approval and baseline freeze

**Scope.** Obtain explicit authorization for this architecture and for the
sequence. Freeze the current state as the comparison baseline.

**Prerequisites.** This document set reviewed and accepted.

**Modules affected.** None.

**Tests.** None. Verification is documentary: record `git rev-parse HEAD`,
`git status --short`, and the current test result if the environment allows.

**Acceptance criteria.**

- A written approval exists naming the authorized stage range.
- The baseline commit hash and working-tree state are recorded.
- The current-state risks (uncommitted work, missing datasets, stale memory)
  are acknowledged in writing.

**Risks.** Starting without approval; building on an unverified tree.

**Exclusions.** No code, no dependency changes, no data fetch.

**Git checkpoint.** Record the current HEAD; do not create a commit that
implies implementation.

---

## Stage 1 — Environment and verification harness

**Scope.** Restore a working `.venv`, confirm the existing suite runs, and
establish the exact command set every later stage will use.

**Prerequisites.** Stage 0 approval; network access for `uv sync` if the
lockfile needs materializing.

**Modules affected.** None (tooling only).

**Tests.** `uv sync`, `uv run sf health`, `uv run sf config-show`,
`uv run pytest`, `uv run ruff check .`.

**Acceptance criteria.**

- All five commands run and their exact output is recorded.
- Pre-existing failures are separated from new ones.
- Skips are listed with reasons.

**Risks.** The environment may not be reconstructible offline; the lockfile
may not match the current tree.

**Exclusions.** No refactoring, no dependency additions.

**Git checkpoint.** Commit only the recorded verification notes, if any.

---

## Stage 2 — Domain and ports skeleton

**Scope.** Add `domain/` and `ports/` with pure value objects and protocol
definitions. No existing behaviour moves yet.

**Prerequisites.** Stage 1 green.

**Modules affected.** `domain/`, `ports/`, `tests/unit/domain/`,
`tests/architecture/`.

**Tests.**

- Unit tests for every value object and invariant.
- Import test proving `domain/` uses stdlib only.
- Protocol conformance tests with fakes.

**Acceptance criteria.**

- `domain/` imports no third-party package.
- Every port has a fake implementation used in a test.
- No existing module changes behaviour.
- Full suite remains green.

**Risks.** Duplicating existing types instead of migrating them; circular
imports introduced by convenience imports.

**Exclusions.** Moving logic out of current modules.

**Git checkpoint.** Required.

---

## Stage 3 — Storage and market-data adapters

**Scope.** Introduce `storage/` and `market_data/` behind ports; adapt the
existing snapshot and ccxt code to satisfy them.

**Prerequisites.** Stage 2.

**Modules affected.** `market_data/`, `storage/`, `data/`, `datasets/`,
`tests/integration/`.

**Tests.**

- Round-trip snapshot write/read with checksum verification.
- Idempotent re-write produces no new directory.
- Corrupt file is rejected.
- Adapter tests use recorded fixtures, not the network.

**Acceptance criteria.**

- Existing snapshots load through the new port unchanged.
- Checksums match current values exactly.
- No network call in the test suite.
- Application code does not import the adapter directly.

**Risks.** Silent format drift; accidental mutation of existing snapshots.

**Exclusions.** Schema 2.0 manifests; new data fetches; deletion of old code.

**Git checkpoint.** Required.

---

## Stage 4 — Quality engine and dataset gate

**Scope.** Implement `quality/` and `domain/quality.py`; make dataset
eligibility an explicit gate.

**Prerequisites.** Stage 3.

**Modules affected.** `quality/`, `domain/quality.py`, `application/quality.py`.

**Tests.**

- OHLC violations, duplicates, conflicts, gaps, staleness, off-grid bars.
- Verdict transitions.
- A `FAIL` dataset is refused by signal and research use cases.

**Acceptance criteria.**

- Every rule in `DATA_ARCHITECTURE.md` §8 has a test.
- No forward fill exists anywhere.
- Existing two snapshots re-verify as `PASS` or `WARN` with recorded reasons.

**Risks.** Reclassifying an existing dataset as failed and blocking work;
false positives on legitimate exchange gaps.

**Exclusions.** Rewriting existing snapshots.

**Git checkpoint.** Required.

---

## Stage 5 — Analytics extraction

**Scope.** Move indicator, feature, regime and multi-timeframe logic into
`analytics/` with typed inputs and outputs.

**Prerequisites.** Stage 4.

**Modules affected.** `analytics/`, `features/`, `tests/leakage/`.

**Tests.**

- Golden-value tests for each indicator on a small fixture.
- Causality test: modifying a future bar cannot change a past feature.
- MTF alignment uses only closed higher-timeframe bars.

**Acceptance criteria.**

- Numeric outputs match the current implementation within a documented
  tolerance, or differences are explained and approved.
- No look-ahead in any feature.
- Feature availability timestamps are recorded.

**Risks.** Silent numeric drift; tolerance used to hide a real change.

**Exclusions.** Changing feature definitions or adding new alpha.

**Git checkpoint.** Required.

---

## Stage 6 — Strategies and signal composition

**Scope.** Introduce the strategy protocol, registry and built-ins; move
scoring, confluence and diagnostics into `signals/`.

**Prerequisites.** Stage 5.

**Modules affected.** `strategies/`, `signals/`, `domain/signals.py`,
`domain/scoring.py`.

**Tests.**

- Registry metadata validation.
- Deterministic scoring for fixed inputs.
- Reason-code coverage for every abstention path.
- Diagnostics contain no secrets and no raw AI payloads.

**Acceptance criteria.**

- Identical inputs produce byte-identical signal records.
- Existing signal schema remains readable.
- No strategy can publish without diagnostics.

**Risks.** Behaviour change disguised as a refactor; the current policy mixes
model probability with rule thresholds.

**Exclusions.** Changing thresholds, models or strategy definitions.

**Git checkpoint.** Required.

---

## Stage 7 — Risk separation and portfolio core

**Scope.** Split risk into an explicit `RiskDecision`; add pure portfolio
ledger rules. Keep paper optional.

**Prerequisites.** Stage 6.

**Modules affected.** `risk/`, `portfolio/`, `domain/risk.py`,
`application/risk.py`.

**Tests.**

- Fixed gate order and fail-closed behaviour.
- Every limit boundary (at, just below, just above).
- Ledger arithmetic and checksum invariants.
- AI and model outputs cannot alter a decision.

**Acceptance criteria.**

- Risk decisions reference a signal id and carry a full gate trace.
- A rejected signal cannot reach a paper fill.
- Paper remains disabled by default.

**Risks.** Behaviour drift at boundaries; accidental fail-open.

**Exclusions.** Live execution, new limits without approval.

**Git checkpoint.** Required.

---

## Stage 8 — Research and backtest consolidation

**Scope.** Consolidate splits, walk-forward, holdout, robustness and the
event-safe backtester under `research/` and `backtesting/`.

**Prerequisites.** Stage 7.

**Modules affected.** `research/`, `backtesting/`,
`models/calibration/` (optional).

**Tests.**

- Purge and embargo boundary tests.
- Holdout single-access test.
- Next-bar fill and same-bar tie tests.
- Cost stress reproduces a known fixture result.

**Acceptance criteria.**

- A research run records dataset hash, parameters, code version and seed.
- Holdout access is recorded and repeat access is refused.
- Backtest results are reproducible byte-for-byte on the fixture.

**Risks.** Changing fill semantics; losing the holdout ledger guard.

**Exclusions.** New strategies; changing cost defaults.

**Git checkpoint.** Required.

---

## Stage 9 — Application layer and composition root

**Scope.** Move orchestration out of `cli.py` into use cases; add
`bootstrap.py`; make the CLI a thin presentation layer.

**Prerequisites.** Stage 8.

**Modules affected.** `application/`, `bootstrap.py`, `cli/`.

**Tests.**

- One test per use case with fakes.
- CLI smoke tests for every command.
- Import test: `cli/` does not import infrastructure adapters.

**Acceptance criteria.**

- `cli.py` (or `cli/`) contains no strategy, scoring or risk logic.
- Every command maps to exactly one use case.
- Exit codes and JSON output remain compatible.

**Risks.** Large diff; subtle CLI contract changes; import cycles.

**Exclusions.** New commands; output format changes.

**Git checkpoint.** Required.

---

## Stage 10 — Presentation on use cases

**Scope.** Re-point the API and dashboard at application query use cases.
Keep them GET-only and read-only.

**Prerequisites.** Stage 9.

**Modules affected.** `api/`, `dashboard/`, `application/queries.py`.

**Tests.**

- Route table is GET-only at build time.
- Pagination caps and 404/unavailable contracts.
- Dashboard escaping and empty states.
- No route can mutate.

**Acceptance criteria.**

- No presentation module imports `storage/` or `market_data/`.
- All existing read contracts still work.
- Remote binding still refused by default.

**Risks.** Contract drift; accidentally adding a mutation route.

**Exclusions.** New endpoints; authentication; remote access.

**Git checkpoint.** Required.

---

## Stage 11 — Optional modules

**Scope.** Add scheduler, notifications, AI context and optional ML behind
ports, disabled by default.

**Prerequisites.** Stage 10.

**Modules affected.** `scheduler/`, `notifications/`, `ai/`, `models/`,
`execution/paper/`.

**Tests.**

- Absence tests: with all optional modules disabled, the core imports and
  runs.
- Scheduler cannot bypass risk.
- AI failure downgrades safely.
- Notification failure does not affect signal generation.

**Acceptance criteria.**

- Every optional module is constructed only when enabled.
- Removing an optional package leaves the core importable.
- No optional module appears in the baseline dependency graph.

**Risks.** Optional code becoming required through convenience imports.

**Exclusions.** Enabling anything by default; live execution.

**Git checkpoint.** Required.

---

## Stage 12 — Architecture tests and reconciliation

**Scope.** Turn `DEPENDENCY_RULES.md` into enforced tests; reconcile all
documentation with the implemented reality.

**Prerequisites.** Stage 11.

**Modules affected.** `tests/architecture/`, all docs.

**Tests.** The full architecture suite: import matrix, cycles, forbidden
symbols, presentation isolation, research isolation, port completeness,
optional-module absence.

**Acceptance criteria.**

- A deliberate violation fails a named test.
- Documentation and code agree.
- Full suite, lint and CLI checks are green and recorded.
- Every "current" statement in docs is true of the tree.

**Risks.** Tests that pass vacuously; docs drifting again.

**Exclusions.** New features.

**Git checkpoint.** Required, and it is the first point at which this
architecture may be described as implemented.

## 3. AI gateway implementation sequence (proposal, not authorization)

This sequence nests inside Stage 11 (optional modules). It is **not**
authorized, not scheduled, and not started. Each step requires its own
explicit approval and ends with an actually-run verification record.

**Status update (2026-11-07, later the same day): partially implemented.**
An authorized subset of this sequence was delivered as "Stages 0–5" and
committed (`a768063`): provider-neutral contracts, registry, gateway, task
routing, one OpenAI-compatible adapter, bounded signal/research advisory,
composition root, `sf ai` CLI, and the architecture tests listed in
`tests/unit/test_stage5_architecture.py`. It is recorded in `AI_GATEWAY.md`
§24. Deviations from the table above: the port lives in `ai/contracts.py`
rather than `ports/ai.py` (A1), and only one generic OpenAI-compatible adapter
exists instead of the eight re-homed adapters (A4). A6's UI failure states are
not implemented. Everything else here remains unimplemented.

| Step | Deliverable | Acceptance criteria | Exclusions |
|---|---|---|---|
| A0 | Approve the design and the storage/resource freeze | Written approval naming this document set; no code | Any implementation |
| A1 | `ports/ai.py` + `NullAiClient` + contract tests | Port imports stdlib only; application constructible with no AI | Adapters, network |
| A2 | Registry + routing + validation | Provider/model selectable by config only; unknown/disabled entries fail explicitly | Any network call |
| A3 | Gateway: timeout, retry, budget, error normalization | Fake adapter proves every status in the closed vocabulary; no unbounded wait | Real providers |
| A4 | Re-home the 8 existing adapters behind the port | Behaviour parity on recorded fixtures; stdlib transport unchanged | New SDK dependencies |
| A5 | Task routes + capability cross-check | Per-task provider/model works; fallback stays inside the task route | Cost-based dynamic routing |
| A6 | Explanation artifact + UI failure states | Decision fields byte-identical with AI ok/failed/disabled; status visible | Any AI influence on decisions |
| A7 | Architecture tests for DR-13 … DR-17 | A deliberate violation fails a named test | New features |

Stop conditions specific to this sequence:

- any step requires a provider SDK as a **required** dependency;
- any step requires a key, account or live call to verify;
- any step would place AI output in a signal, risk or ledger record;
- any step cannot prove decision fields are unchanged when AI fails.

## 4. Stop conditions

Stop and ask the user if any of these occurs:

- a stage requires a dependency not already in the lockfile;
- a stage would change strategy behaviour;
- a stage cannot keep the suite green;
- a stage would require touching `memory/`, `AGENTS.md` or `DECISIONS.md`
  without ownership;
- a workload cannot satisfy its purpose inside the 4 GB envelope;
- any request implies live execution or remote exposure.

## 5. Verification record template

Every stage handoff must include:

```text
Stage:
Commit:
Commands run:
  uv sync                  -> exit / output summary
  uv run sf health         -> exit / output summary
  uv run sf config-show    -> exit / output summary
  uv run pytest            -> passed / failed / skipped
  uv run ruff check .      -> exit / output summary
New failures:
Pre-existing failures:
Skipped:
Risks:
Next gate:
```
