# AGENTS.md — SignalForge Operating Rules (Phase 1)

Read this file and `memory/` before any work. It overrides any conflicting assumption.

## 1. Scope: Phase 1 Only

SignalForge Phase 1 = foundation: repo scaffolding, config, market-data interfaces/adapters,
dataset snapshots, CLI health/config commands, tests, docs, memory.
**Not implemented in Phase 1:** models, training, calibration, AI providers, signal generation,
backtests, paper trading, API/dashboard implementation. Docs for those phases must state
"planned, not implemented in Phase 1". Stop before Phase 2 unless explicitly authorized.

## 2. Independence and File Ownership

- Bounded ownership: each agent owns only its assigned paths. Never edit `src/` or `tests/`
  unless they are your assigned paths.
- Parallel subagents only for genuinely independent tasks with non-overlapping files.
- Integration lead resolves conflicts; agent suggestions are not verification.

## 3. Architecture and Module Boundaries

Canonical pipeline (see `docs/ARCHITECTURE.md`):

```text
exchange (ccxt, public) → market data → features → models → signals → risk gates → results
```

- 20 components; dashboard/API consume only canonical result schemas, never internals.
- Market-data layer: UTC-normalized, provenance-preserving, no silent fills.
- Risk gates are deterministic and independent of models/AI. `NO_TRADE` is a valid,
  expected output on invalid or insufficient evidence.

## 4. Coding Conventions

- Python 3.12, `uv`-managed isolated venv (`.venv/`, never system Python).
- Typed, modular code; Windows-safe paths (`pathlib`, no POSIX-only assumptions);
  OneDrive-safe (no file locks held, small writes, avoid churn in large dirs).
- Public interfaces typed; config validated (pydantic); errors explicit; side effects bounded.
- No secrets in code, TOML, or git. Secrets only via environment / `.env` (see `.env.example`).
- No empty placeholder modules in `src/` — every module must have a real interface or not exist.

## 5. Install / Run / Test (Windows)

```powershell
py -3.12 -m pip install uv            # once, if uv missing
uv sync                               # create .venv + install Phase 1 deps
uv run sf health                      # CLI health check
uv run sf config-show                 # validated config dump
uv run pytest                         # full test suite
uv run ruff check .                   # lint
```

## 6. Agent / Skill Responsibilities

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

## 7. Memory Procedures

1. Read `AGENTS.md` + all four `memory/` files before meaningful work.
2. Compare memory claims against current files and actual command output.
3. Update `CURRENT_STATE.md`, `NEXT_STEPS.md`, `SESSION_LOG.md`, and `PROJECT_MEMORY.md`
   only when facts changed. Record exact commands and results.
4. Preserve superseded decisions in `DECISIONS.md`; never rewrite history.
5. Never record credentials, unsupported assumptions, or unrun tests.

## 8. Phase Authorization

- Approved: Phase 1 foundation only.
- Anything beyond Phase 1 (models, backtests, AI, paper trading, live) requires explicit
  user approval of a new phase. Default answer to out-of-phase requests: stop and ask.

## 9. Risk / Security Rules

- Public read-only data in Phase 1; no live orders anywhere in the codebase.
- Validate all external inputs; treat exchange responses and AI outputs as untrusted.
- Deterministic risk gates cannot be overridden by models or AI.
- Report severity + evidence + remediation; never hide unresolved risk.

## 10. Research Integrity

- No fabrication: only information available at each decision timestamp.
- Never force a target (e.g., "90% accuracy"). Report inconclusive/negative evidence honestly.
- Distinguish **accuracy** (fraction correct) vs **win rate** (fraction of profitable trades)
  vs **profitability** (net P&L after costs). Never claim profitability without valid
  out-of-sample support net of fees, spread, slippage, and funding.

## 11. Verification Requirements

- Every handoff reports: findings, changed files, tests actually run (exact commands +
  pass/fail/skip counts), risks, next gate.
- Never count skips as passes. Never report unexecuted checks as complete.
- Reproduce defects before claiming fixes; separate new failures from pre-existing ones.
