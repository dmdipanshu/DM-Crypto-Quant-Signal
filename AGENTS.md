# AGENTS.md — SignalForge Operating Rules (Phase 10 authorized)

Read this file and `memory/` before any work. It overrides any conflicting assumption.

## 1. Scope: Phase 10 Only

SignalForge Phase 10 = frozen-model forward validation on a genuinely new
BTC/USDT hourly period (public fetch, immutable new snapshot, no retraining,
no tuning, no calibration, validation-only comparison, reproducible report).
Phases 1–9 are done — extend, do not rewrite.
**Not implemented in Phase 10:** live orders, real-money trading, model
retraining/refitting/calibration/tuning, threshold selection, feature
selection, backtest execution, paper trading, portfolio accounting,
API/dashboard write endpoints, public exposure, architecture rewrites.
No Phase 11 exists; anything beyond Phase 10 needs explicit approval.

## 2. Resource Constraint: 4 GB Laptop (mandatory)

- One training job / one process by default; `n_jobs=1` everywhere.
- Bounded chunks, column/row-group Parquet reads, no full-matrix copies.
- No PyTorch/TF/Transformers/LLMs, no GPU, no pretrained downloads in Phase 8.
- Training is single-process, one snapshot at a time; fixtures ≤400 rows.
- API/dashboard: single process, loopback-only default, bounded pagination, no polling loops.
- AI concurrency default 1; all provider SDKs optional; ordinary tests offline.
- Signal generation single-process, one symbol/timeframe at a time by default.
- Backtests/paper run single-process, one symbol/timeframe, bounded bars.
- Small deterministic fixtures for unit tests; larger data explicitly configurable.
- Actionable errors on memory pressure; never silently drop rows; record sample limits.

## 3. Independence and File Ownership

- Bounded ownership: each agent owns only its assigned paths. Never edit `src/` or `tests/`
  unless they are your assigned paths.
- Sequential subagents on this 4 GB machine (no parallel agents); one job at a time.
- Integration lead resolves conflicts; agent suggestions are not verification.

## 4. Architecture and Module Boundaries

Canonical pipeline (see `docs/ARCHITECTURE.md`):

```text
exchange (ccxt, public) → market data → features → models → signals → risk gates → results
```

- 20 components; dashboard/API consume only canonical result schemas, never internals.
- Market-data layer: UTC-normalized, provenance-preserving, no silent fills.
- Risk gates are deterministic and independent of models/AI. `NO_TRADE` is a valid,
  expected output on invalid or insufficient evidence.

## 5. Coding Conventions

- Python 3.12, `uv`-managed isolated venv (`.venv/`, never system Python).
- Typed, modular code; Windows-safe paths (`pathlib`, no POSIX-only assumptions);
  OneDrive-safe (no file locks held, small writes, avoid churn in large dirs).
- Public interfaces typed; config validated (pydantic); errors explicit; side effects bounded.
- No secrets in code, TOML, or git. Secrets only via environment / `.env` (see `.env.example`).
- No empty placeholder modules in `src/` — every module must have a real interface or not exist.

## 6. Install / Run / Test (Windows)

```powershell
py -3.12 -m pip install uv            # once, if uv missing
uv sync                               # create .venv + install Phase 1 deps
uv run sf health                      # CLI health check
uv run sf config-show                 # validated config dump
uv run pytest                         # full test suite
uv run ruff check .                   # lint
```

## 7. Agent / Skill Responsibilities

| Agent | Owns |
|---|---|
| market-data-engineer | market-data adapters, snapshots |
| quant-researcher | hypotheses, labels, baselines, validation design |
| ml-model-engineer | model interfaces/artifacts (Phase 2+, stubs only in Phase 1 docs) |
| backtest-validation-engineer | causal backtests, costs, walk-forward (Phase 3+) |
| risk-security-reviewer | read-only risk/security reviews |
| ai-provider-engineer | provider adapters (Phase 4+, docs only in Phase 1) |
| qa-release-engineer | factual test runs, release-readiness reports |
| repository-auditor | read-only audits |
| memory-curator | `memory/` maintenance |
| quant-lead | integration, phase enforcement, final verification |

Skills in `.opencode/skills/*/SKILL.md` are the normative procedures for each domain.
Load the matching skill before doing domain work.

## 8. Memory Procedures

1. Read `AGENTS.md` + all four `memory/` files before meaningful work.
2. Compare memory claims against current files and actual command output.
3. Update `CURRENT_STATE.md`, `NEXT_STEPS.md`, `SESSION_LOG.md`, and `PROJECT_MEMORY.md`
   only when facts changed. Record exact commands and results.
4. Preserve superseded decisions in `DECISIONS.md`; never rewrite history.
5. Never record credentials, unsupported assumptions, or unrun tests.

## 9. Phase Authorization

- Approved: Phase 10 (frozen-model forward validation on new public data;
  no retraining, tuning, calibration, or trading). This is the final
  authorized phase; anything beyond it requires explicit user approval.
  Default answer to out-of-scope requests: stop and ask.

## 10. Risk / Security Rules

- Public read-only data in Phase 1; no live orders anywhere in the codebase.
- Validate all external inputs; treat exchange responses and AI outputs as untrusted.
- Deterministic risk gates cannot be overridden by models or AI.
- Report severity + evidence + remediation; never hide unresolved risk.

## 11. Research Integrity

- No fabrication: only information available at each decision timestamp.
- Never force a target (e.g., "90% accuracy"). Report inconclusive/negative evidence honestly.
- Distinguish **accuracy** (fraction correct) vs **win rate** (fraction of profitable trades)
  vs **profitability** (net P&L after costs). Never claim profitability without valid
  out-of-sample support net of fees, spread, slippage, and funding.

## 12. Verification Requirements

- Every handoff reports: findings, changed files, tests actually run (exact commands +
  pass/fail/skip counts), risks, next gate.
- Never count skips as passes. Never report unexecuted checks as complete.
- Reproduce defects before claiming fixes; separate new failures from pre-existing ones.


# AUTONOMOUS EXECUTION POLICY

For every coding task I give you, work autonomously from start to finish.

## Execution Rules

1. First inspect the relevant project files and understand the existing architecture.
2. Identify the root cause or exact implementation requirement.
3. Create the implementation plan internally.
4. Implement the complete solution yourself.
5. Do not stop after planning or after making only partial changes.
6. Run all relevant tests, lint, type checks, builds, and verification commands.
7. If any command or test fails, investigate the failure yourself.
8. Fix the problem and rerun the failed checks.
9. Continue iterating until the requested task is actually completed and verified.
10. Do not ask me "What should I do next?" during normal coding work.
11. Do not ask for confirmation for normal file edits, terminal commands, testing, debugging, or reversible development actions.
12. Make reasonable implementation decisions yourself based on the existing codebase and project rules.
13. Preserve existing functionality, architecture, safety constraints, and project conventions.
14. Never declare a task complete merely because code was changed. Verify the result first.

## When You MAY Ask Me

Ask me only when:

- A required piece of information cannot be determined from the repository.
- A genuine product/business decision requires my choice.
- An action is destructive or irreversible and requires explicit user authorization.
- Existing project rules explicitly require user approval.

For ordinary implementation decisions, do not interrupt the workflow.

## Automatic Error Recovery

If you encounter an error:

1. Read the complete error.
2. Identify the likely root cause.
3. Inspect the relevant code/configuration.
4. Apply the appropriate fix.
5. Rerun the failed command or test.
6. Repeat until resolved or until a genuine external blocker remains.

Do not immediately report an error and wait for my instructions.

## Required Execution Loop

INSPECT
→ PLAN
→ IMPLEMENT
→ RUN
→ TEST
→ DEBUG
→ VERIFY
→ COMPLETE

## Final Response

Only report back after completing and verifying the task.

Your final response should briefly include:

- What was changed
- Files changed
- Tests/checks executed
- Verification result
- Any genuine remaining blocker

Do not ask what I want to do next unless the completed task genuinely requires a new decision from me.

## Priority

These autonomous execution rules must NOT override existing project safety rules, security requirements, architectural constraints, or explicit instructions already defined in this AGENTS.md file.

When this policy conflicts with a more specific project safety rule, follow the more specific safety rule.