# AI_PROVIDERS.md — SignalForge (Phase 4, IMPLEMENTED 2026-10-27)

> Implemented state. Track A (provider interface) + Track B (analyst agents,
> ablation) are wired and tested offline. All providers disabled by default;
> secrets from environment only.

## What exists

- **8 adapters** (`src/crypto_engine/ai_providers/`): `openai`, `anthropic`,
  `gemini`, `deepseek`, `openrouter`, `groq`, `ollama`, `compatible`
  (generic OpenAI-compatible). Factory: `create_provider(provider_id,
  model_id, ...)`; `enabled_providers(settings)` (default: none).
- **Transport**: stdlib `urllib` by default. Provider SDKs are
  bring-your-own (sandbox has no package-registry network): `pip install
  openai anthropic google-generativeai` only if you want SDK transports.
  `ollama` needs no SDK and no key (local `http://localhost:11434`).
- **Contract**: `AiRequest` (bounded evidence: 64 keys / 8000 chars per
  string) → `AiResponse` (`ok` only on parsed structured success; typed
  failures otherwise). `BudgetTracker` + `run_with_fallback` sequential
  (concurrency 1), correlation preserved, budget exhaustion stops before
  paid fallbacks.
- **Analysts** (`src/crypto_engine/agents/`): 8 narrow agents —
  `technical`, `regime`, `news`, `macro`, `bull`, `bear`, `judge`,
  `explainer` — over closed Pydantic schemas (`extra="forbid"`).
  Orchestrator `run_analysis` is sequential; merge is a template, never an
  LLM call/vote. Provider `None`/disabled → `disabled` outputs + quant-only
  note (exit 0).
- **Context-score contract**: `ContextScore.value ∈ [-1, 1]` is a bounded
  interpretive lean — NOT a return, NOT a probability, NOT an edge. Feeds
  only the ablation `contextual_veto` abstention policy on folds, never a
  trade rule.
- **Ablation** (`src/crypto_engine/validation/ablation.py`): 4 arms
  (`quant_only` / `quant_plus_context` / `explanation_only` /
  `contextual_veto`) on identical purged folds, each recorded in the Phase 3
  `ExperimentRegistry` (`n_trials=4`). Verdict `inconclusive` unless the
  prespecified margin holds (default +0.02 accuracy with coverage
  retained). Folds-only; holdout never touched (non-pristine ledger →
  refusal). On mocked/no-real-news data the verdict is `inconclusive`
  ("no timestamped contextual dataset").

## Failure matrix (honest statuses, never silent success)

| Condition | Status | Behavior |
|---|---|---|
| Missing env key | `auth_failure` | no transport call |
| HTTP 401/403 | `auth_failure` | no retry |
| HTTP 429 | `rate_limited` | 1 bounded retry; `Retry-After` captured |
| HTTP 5xx / conn failure | `provider_outage` | bounded retries (`max_retries ≤ 3`) |
| HTTP 404 | `unsupported` | no retry |
| Bad JSON / non-object | `invalid_output` | never `ok` |
| Budget spent | `budget_exhausted` | fallback chain stops |
| Provider off / `None` | `disabled` | analysts return `disabled` |
| Schema-invalid analyst output | `invalid` | reasons listed, no raw passthrough |

## Hard limits

AI may never: fabricate market facts, set risk limits, override
deterministic gates, or create live orders. Failures downgrade safely
(reduced functionality or abstention). Analysts have no tools/shell/
filesystem/network; model text is untrusted; evidence is wrapped as data
under a constant task string (prompt-injection: instructions in evidence
cannot alter the task). Secrets: environment only (see `.env.example`);
keys never appear in responses, logs, or errors (redacted, tests assert).

## Residual risks

- **API drift**: adapter endpoints verified against docs 2026-10-27; vendor
  changes can break mappings (guarded by offline contract tests + typed
  failures, not by live checks).
- **Gemini key-in-query**: key travels as a URL parameter per vendor API —
  prefer header-based providers where URL logging is a concern.
- **Ollama on 4 GB**: local inference competes with the training budget;
  keep models small or keep AI disabled during training.
- **Prompt-injection defense-in-depth**: constant task + data-wrapping +
  output validation mitigate, but do not formally eliminate, instruction
  smuggling in external text — treat news/macro payloads as untrusted.
- **No-real-news verdict**: with mocked or unscored context the ablation
  correctly reports `inconclusive`; do not promote context arms without a
  timestamped contextual dataset clearing the margin on unseen folds.
