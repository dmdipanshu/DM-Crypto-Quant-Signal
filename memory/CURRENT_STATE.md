# CURRENT_STATE.md — SignalForge verified state

- **Phase**: 4 in progress (authorized 2026-10-27) — provider-independent AI
  interface, 8 optional adapters, retry/fallback/budgets, structured validation,
  8 analysts, context-score contract, 4-arm ablation. Stop before Phase 5.
- **Implemented (Phase 4, verified 2026-10-27)**:
  - `src/crypto_engine/ai_providers/` — `types.py` (AiRequest/AiResponse, 9
    statuses, evidence bounds 64 keys/8000 chars, secrets never in responses),
    `base.py` (urllib transport injection, secret-at-call-time, redaction,
    bounded retries with NON_RETRYABLE set, truncation flags, actionable
    Ollama hint), 8 adapters (openai Responses API, anthropic messages,
    gemini generateContent, deepseek/openrouter/groq OpenAI-compatible,
    ollama local no-auth, compatible generic + capability flags),
    `factory.py` (8 ids, unknown-id error, `enabled_providers` default none),
    `budget.py` (in-memory caps, sequential fallback, stops before paid
    escalation, correlation preserved).
  - `src/crypto_engine/agents/` — closed schemas (`extra="forbid"`;
    ContextScore in [-1,1] documented NOT a return/probability/edge),
    8 analysts (technical/regime/news/macro/bull/bear/judge/explainer;
    evidence-as-data, no tools, disabled→disabled, invalid→invalid with
    reasons), `orchestrator.py` (sequential default, template merge, quant-only
    note when AI disabled).
  - `src/crypto_engine/validation/ablation.py` — 4 arms
    (quant_only/quant_plus_context/explanation_only/contextual_veto) on
    identical folds, train-end evidence cutoff, registry-recorded, margin
    verdict (+0.02 acc default), folds-only with pristine-ledger guard.
  - Config: `[ai]` + `[ai.budgets]` + commented `[[ai.providers]]` examples
    (ALL disabled), `AiConfig`/`AiProviderConfig`/`AiBudgetConfig`;
    `.env.example` extended (all keys commented); NO ai-* extras — SDKs are
    bring-your-own (no registry network in sandbox; lockfile preserved).
  - Tests: +58 provider/security, +31 analysts/ablation/temporal = 89 new.
- **Verification (lead-run 2026-10-27)**: `uv sync --offline` EXIT 0
  (73 packages; network unavailable for re-lock),
  `uv run pytest` **254 passed, 1 skipped** (skip = lightgbm optional, legitimate),
  `uv run ruff check .` clean (lead fixed 9x RUF012 ClassVar + B027 noqa + 11
  auto-fixes in provider files), `uv run ruff format --check .` 122 files clean
  (lead formatted 11 files), `sf health` + `sf config-show` EXIT 0 (`[ai]`
  section present, providers empty/disabled).
- **Resource probe (representative, tracemalloc)**: 6-analyst orchestration with
  mocked provider: ~0.03 s wall, ~44 KiB Python-alloc peak. Method: in-process
  `tracemalloc`; NOT process-RSS; 4 GB guidance (concurrency 1 sequential,
  response ≤4000 chars, retries ≤2, fixtures small) retained.
- **Ablation verdict**: `inconclusive` — zero-value mocked context gives accuracy
  delta exactly 0.0; no timestamped contextual dataset exists. No AI
  predictive-value claim; AI stays explanation/context-only.
- **Limitations**: adapter endpoints verified 2026-10-27 (drift possible);
  Gemini key-in-query; Ollama CPU competes for 4 GB; injection defense is
  depth not proof; holdout never touched by ablation; LightGBM untested;
  no profitability/90% claim.
