# ADR-0014 — Provider-neutral AI port and universal gateway

- **Status:** Proposed (architecture freeze, design only)
- **Date:** 2026-11-07
- **Deciders:** Operator, integration lead, AI provider engineer
- **Supersedes:** none
- **Related:** ADR-0010 (optional ML and LLM), ADR-0012 (signal/risk/execution
  separation)
- **Specified in:** `docs/architecture/AI_GATEWAY.md`

## Context

The current code reaches AI providers through `ai_providers/` with a factory
and a pydantic request/response contract. That works, but:

1. there is no single application-level entry point, so a future caller could
   use a provider directly;
2. routing is a default provider plus a flat fallback list, not per-task;
3. capability differences between providers are discovered at call time;
4. adding a provider or changing a model touches code rather than validated
   configuration;
5. nothing structurally prevents a decision-path module from importing an
   adapter.

The operator also requires a specific vendor-neutral shape (port → gateway →
adapters) and the ability to select provider, model, endpoint and task-specific
models without editing domain logic.

## Decision

Adopt a **universal AI gateway**:

- **Port** (`ports/ai.py`): `typing.Protocol` + dataclass DTOs, standard
  library only, capability-based, no vendor names.
- **Gateway** (`ai/gateway.py`): the single entry point; validates requests,
  resolves routes, enforces timeouts/retries/budgets/concurrency, normalizes
  responses and errors, records usage.
- **Registry** (`ai/registry.py`): provider and model records, no secret
  values, validated at load, read-only at runtime.
- **Adapters** (`ai/providers/<provider>/`): one dialect per adapter, no
  business logic, no retry policy, no logging of payloads or credentials.
- **Task routing** (`ai/routing.py`): each task (`signal_explanation`,
  `research_summary`, `assistant`, `news_analysis`, `macro_analysis`) selects
  its own ordered route; fallback never leaves the task's approved list.
- **Custom endpoints**: an OpenAI-compatible adapter with configurable base
  URL, model id and auth mechanism, plus an egress allowlist.
- **External-API-first**: inference happens outside the laptop by default; the
  laptop orchestrates and normalizes.
- **Failure isolation**: every failure is an explicit non-`ok` status; the
  deterministic core is byte-identical whether AI ran, failed or was disabled.
- **Authority boundary**: AI is advisory. It cannot alter direction, score,
  size, risk, approval or execution, and it cannot write to any decision
  artifact.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Keep the current factory and let callers use adapters | No single seam; authority and secret boundaries become conventions, not structure |
| One adapter per call site (no gateway) | Duplicated timeout/retry/budget logic; inconsistent error handling |
| Single-vendor SDK throughout | Vendor lock-in; violates provider independence |
| Agent framework / orchestration library | Large dependency, hidden control flow, violates the 4 GB envelope and the "no framework" constraint |
| Local inference as the baseline | Cannot fit a useful model in the 4 GB budget; requires GPU/weights |
| One model for every task | Conflates cost, latency and capability requirements |
| Async fan-out to several providers | Multiplies cost and memory; makes authority and budgeting harder |
| Provider-specific DTOs in the domain | Leaks vendor formats into domain code; unverifiable |

## Trade-offs

Accepted:

- One more indirection layer between caller and HTTP.
- Registry and routing configuration must be maintained by the operator.
- Capability declarations must be kept honest; a wrong declaration is a
  configuration defect.
- Some provider-specific features are unavailable because the port is
  deliberately narrow.

Avoided:

- Vendor lock-in and SDK types in public signatures.
- Ad-hoc retries, unbounded spend and silent failures.
- AI text reaching a decision field.
- Secrets in code, config, logs, reports or responses.

## Consequences

- Adding a provider is one adapter plus registry entries, not a domain change.
- Changing model or provider is a validated configuration edit.
- The gateway is testable offline with a fake adapter; ordinary tests never
  touch the network.
- The architecture tests can enforce DR-13 … DR-17 with `ast` and `pathlib`.
- Existing adapters can be re-homed behind the port with behaviour parity;
  the current contract already has the right shape.
- No provider SDK becomes a required dependency; stdlib `urllib` remains the
  default transport.

## Revisit conditions

- A provider's API cannot be expressed by the neutral contract without leaking
  vendor semantics.
- Task routing proves too coarse (e.g. a task needs per-call model choice for a
  legitimate reason).
- Streaming becomes a real product requirement and changes the port.
- A local model demonstrably fits the envelope and improves validated results.
- The gateway becomes a bottleneck or a dumping ground for business logic.
