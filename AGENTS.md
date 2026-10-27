# AGENTS.md — SignalForge Operating Rules (Phase 4 authorized)

Read this file and `memory/` before any work. It overrides any conflicting assumption.

## 1. Scope: Phase 4 Only

SignalForge Phase 4 = provider-independent AI interface, optional provider
adapters (OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Groq, Ollama/local,
generic OpenAI-compatible), retry/fallback/budgets, structured-output validation,
specialist analyst agents, context-score contract, and ablation framework reusing
the Phase 3 experiment registry. Phases 1–3 (data, features, labels, models,
calibration, walk-forward/holdout validation) are done — extend, do not rewrite.
**Not implemented in Phase 4:** production signal generation, AI-made trading
decisions, execution/orders, full backtester, paper trading, live trading,
API/dashboard, large local model downloads, large hyperparameter searches.
Stop before Phase 5 unless explicitly authorized.

## 2. Resource Constraint: 4 GB Laptop (mandatory)

- One training job / one process by default; `n_jobs=1` everywhere.
- Bounded chunks, column/row-group Parquet reads, no full-matrix copies.
- No PyTorch/TF/Transformers/LLMs, no GPU, no pretrained downloads in Phase 4.
- AI concurrency default 1; all provider SDKs optional; ordinary tests offline.
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

- Approved: Phase 4 (provider-independent AI interface, optional adapters,
  retry/fallback/budgets, structured validation, analyst agents, context scores,
  ablation vs Phase 3 registry).
- Anything beyond Phase 4 (signals/risk Phase 5, backtests/paper Phase 6,
  API/dashboard Phase 7) requires explicit user approval. Default
  answer to out-of-phase requests: stop and ask.

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
