# SignalForge — Target Architecture

**Status: TARGET ARCHITECTURE, PARTLY IMPLEMENTED (2026-11-07).**
The provider-neutral AI gateway (`AI_GATEWAY.md`, ADR-0014) and the storage
role contracts (`STORAGE_OWNERSHIP.md`, ADR-0015) are implemented in
`src/crypto_engine/ai/` and `src/crypto_engine/storage/`. Everything else in
this directory is design intent: no code, configuration, schema, dataset or
dependency described here exists unless a section explicitly says
"implemented" or "current".

**Authorization:** `AGENTS.md` authorizes Phase 10 (frozen-model forward
validation) and forbids architecture rewrites inside it. This document set is
therefore a *proposal* produced for review. It does not authorize
implementation, and no implementation may start from it without an explicit
new phase approval recorded by the user. See §11.

---

## 1. Purpose

Design the complete target architecture for SignalForge before any rebuild:
a crypto-only quantitative market-research and signal-intelligence platform
that ingests public market data, computes causal features and regimes, runs
multiple strategies, scores and explains signals, evaluates risk, supports
reproducible research and backtesting, and optionally simulates paper
execution behind explicit safety gates.

The system is research-first. It does not assume any strategy is profitable
and it treats `NO_TRADE` as a valid, expected output.

## 2. Relationship to existing documents

| Document | Role after this set exists |
|---|---|
| `AGENTS.md` | Operating rules and phase authorization. **Unchanged, authoritative.** |
| `DECISIONS.md` | Append-only historical decision log. **Unchanged.** |
| `memory/*` | Verified project state. **Unchanged**, memory-curator owned. |
| `docs/ARCHITECTURE.md` | Record of the *current* 20-component implementation. Still accurate for current code. |
| `docs/RESEARCH_METHODOLOGY.md` | Normative methodology. This set extends it, does not replace it. |
| `docs/SECURITY.md` | Current security posture. This set extends it. |
| `docs/PAPER_TRADING.md` | Current paper-sandbox semantics. This set preserves them. |
| `docs/AI_PROVIDERS.md` | Current optional AI layer. This set preserves its constraints. |
| `docs/architecture/*` (this set) | **Target** architecture. Additive; nothing is overwritten. |

Where this set and `docs/ARCHITECTURE.md` differ, the difference is
intentional and is listed in `TARGET_REPOSITORY_STRUCTURE.md` §5
(current vs target). `docs/ARCHITECTURE.md` remains the truthful description
of the current code until a rebuild actually happens.

## 3. Architectural style and why it fits this project

**Modular monolith with Hexagonal (Ports and Adapters) boundaries.**

One deployable Python application, one process, one virtual environment, one
`sf` command. Inside it, modules are separated by explicit layers and explicit
public contracts.

Why this is the right fit for a single developer on a memory-constrained
Windows machine:

1. **One deployable artifact.** A microservice split multiplies processes,
   ports, health checks, deployment scripts and failure modes. On a machine
   that must run one training job at a time, that is pure overhead.
2. **One memory budget.** A monolith keeps one copy of the data path and one
   bounded working set. Distributed caches, queues and service hops would each
   add resident memory the hardware cannot spare.
3. **Hexagonal boundaries still buy the important benefits** — testability
   without network access, swappable storage, and a hard wall between research
   and execution — without paying for network boundaries.
4. **Deterministic, offline testability.** Domain and application code can be
   exercised against in-memory fakes; only adapters need files, SQLite or a
   network.
5. **Reversible later.** Because modules communicate through ports, a future
   split into separate deployments would be an adapter change, not a rewrite.
   That option is preserved, not exercised.
6. **Small cognitive surface.** One developer can hold the whole system in
   view; a monolith with enforced import rules is easier to keep correct than
   a service mesh nobody can debug at 2 a.m.

The style is *not* an excuse for a ball of mud: the import rules in
`DEPENDENCY_RULES.md` are normative and are meant to be enforced by
architecture tests.

## 4. Layers at a glance

| Layer | Package(s) | Owns | May import |
|---|---|---|---|
| Presentation | `cli/`, `api/`, `dashboard/` | Input parsing, output rendering, HTTP/HTML serialization | Application; the composition root from the CLI entry point only |
| Application | `application/` | Use-case orchestration, transactions, unit of work | Domain, ports, domain services |
| Domain services | `analytics/`, `strategies/`, `signals/`, `risk/`, `portfolio/`, `backtesting/`, `research/`, `quality/` | Pure computation and deterministic rules | Domain, ports (protocols only) |
| Domain | `domain/` | Entities, value objects, invariants, reason codes | Python standard library only |
| Ports | `ports/` | `typing.Protocol` interfaces for infrastructure | Domain |
| Infrastructure | `market_data/`, `storage/`, `execution/`, `scheduler/`, `notifications/`, `observability/`, `config/` | Exchange, files, SQLite, clock, logs, jobs | Domain + ports; wired by the composition root |
| Composition root | `bootstrap.py`, `cli/main.py` | Object construction, settings, adapter selection | Everything (the only place allowed to) |

See `MODULE_ARCHITECTURE.md` for the container diagram and use-case catalog,
and `DEPENDENCY_RULES.md` for the normative matrix.

## 5. Document index

| # | File | Contents |
|---|---|---|
| 1 | `README.md` | This overview, index, crosswalk, status |
| 2 | `SYSTEM_CONTEXT.md` | Actors, external systems, trust boundaries, deployment context |
| 3 | `MODULE_ARCHITECTURE.md` | Layers, modules, ports, use cases, production signal flow |
| 4 | `DEPENDENCY_RULES.md` | Normative import matrix and enforcement |
| 5 | `DATA_ARCHITECTURE.md` | Full market-data lifecycle, manifests, integrity, layout |
| 6 | `RESEARCH_GOVERNANCE.md` | Research/production separation, promotion review, historical findings |
| 7 | `SECURITY_AND_EXECUTION_BOUNDARIES.md` | Read-only default, paper safety gates, threat model |
| 8 | `RESOURCE_BUDGET.md` | Memory/CPU envelope, caps, measurement points |
| 9 | `TARGET_REPOSITORY_STRUCTURE.md` | Current tree, target tree, labels, ownership |
| 10 | `IMPLEMENTATION_ROADMAP.md` | Dependency-ordered stages with acceptance criteria |
| 11 | `AI_GATEWAY.md` | Provider-neutral AI port, gateway, registry, adapters, secret and authority boundaries |
| 12 | `STORAGE_OWNERSHIP.md` | Parquet vs SQLite WAL vs JSON ownership, integrity and retention |
| 13 | `decisions/` | Architecture Decision Records |

## 6. Diagram index

Eight baseline diagrams plus seven AI/storage diagrams, each defined once in
the document that owns it.

| # | Diagram | File |
|---|---|---|
| 1 | System context | `SYSTEM_CONTEXT.md` |
| 2 | Container / module architecture | `MODULE_ARCHITECTURE.md` |
| 3 | Dependency direction | `DEPENDENCY_RULES.md` |
| 4 | Market-data flow | `DATA_ARCHITECTURE.md` |
| 5 | Production signal flow | `MODULE_ARCHITECTURE.md` |
| 6 | Isolated research flow | `RESEARCH_GOVERNANCE.md` |
| 7 | Paper execution safety flow | `SECURITY_AND_EXECUTION_BOUNDARIES.md` |
| 8 | Failure recovery and data quality | `DATA_ARCHITECTURE.md` |
| 9 | AI gateway position | `AI_GATEWAY.md` §3 |
| 10 | AI secret boundary | `AI_GATEWAY.md` §10 |
| 11 | AI authority boundary | `AI_GATEWAY.md` §12 |
| 12 | AI data flow | `AI_GATEWAY.md` §15 |
| 13 | AI failure sequence | `AI_GATEWAY.md` §15 |
| 14 | Storage roles and ownership | `STORAGE_OWNERSHIP.md` §1 |
| 15 | Storage decision rules | `STORAGE_OWNERSHIP.md` §6 |

## 7. ADR index

| ADR | Title | Status |
|---|---|---|
| [0001](decisions/0001-modular-monolith.md) | Modular monolith over microservices | Proposed |
| [0002](decisions/0002-hexagonal-ports-and-adapters.md) | Hexagonal ports and adapters | Proposed |
| [0003](decisions/0003-domain-boundaries.md) | Domain boundaries and a stdlib-only domain | Proposed |
| [0004](decisions/0004-market-data-storage.md) | Immutable Parquet snapshot storage | Proposed |
| [0005](decisions/0005-sqlite-vs-files.md) | SQLite for metadata/ledger, files for datasets | Superseded in part by 0015 |
| [0006](decisions/0006-research-production-separation.md) | Separate research and production paths | Proposed |
| [0007](decisions/0007-strategy-promotion-governance.md) | Evidence-based strategy promotion | Proposed |
| [0008](decisions/0008-scheduler-execution-model.md) | Single-process, in-process scheduler | Proposed |
| [0009](decisions/0009-api-dashboard-security.md) | Read-only, loopback-first API and dashboard | Proposed |
| [0010](decisions/0010-optional-ml-llm.md) | Optional ML and LLM, never on the critical path | Proposed |
| [0011](decisions/0011-resource-management.md) | Design to the 4 GB envelope | Proposed |
| [0012](decisions/0012-signal-risk-execution-separation.md) | Separate signal, risk approval and execution authorization | Proposed |
| [0013](decisions/0013-package-naming-and-layout.md) | Keep `crypto_engine`, organize layer-first | Proposed |
| [0014](decisions/0014-universal-ai-gateway.md) | Provider-neutral AI port and universal gateway | Accepted — core implemented (Stages 0–5) |
| [0015](decisions/0015-storage-role-separation.md) | Parquet, SQLite WAL and JSON as separate storage roles | Accepted — role contracts implemented (Stage 0) |

## 8. The 22 subsystems and where they live

| # | Subsystem | Target home | Label |
|---|---|---|---|
| 1 | Exchange adapters | `market_data/exchange/` | CORE (REST), OPTIONAL (WebSocket) |
| 2 | Market-data ingestion | `application/ingestion.py`, `market_data/ingest.py` | CORE |
| 3 | Candle normalization | `domain/candles.py`, `market_data/normalize.py` | CORE |
| 4 | Data-quality engine | `domain/quality.py`, `quality/` | CORE |
| 5 | Historical data storage | `storage/datasets.py` | CORE |
| 6 | Indicator engine | `analytics/indicators/` | CORE |
| 7 | Market-regime classifier | `domain/regimes.py`, `analytics/regimes/` | CORE |
| 8 | Strategy registry and engine | `strategies/` | CORE |
| 9 | Multi-timeframe analysis | `analytics/mtf.py`, `application/mtf.py` | CORE |
| 10 | Signal scoring and confluence | `signals/scoring.py`, `signals/confluence.py` | CORE |
| 11 | Signal diagnostics and explanation | `signals/diagnostics.py` | CORE |
| 12 | Risk management | `risk/` | CORE |
| 13 | Portfolio management | `portfolio/`, `storage/ledger.py` | OPTIONAL (paper only) |
| 14 | Paper execution simulator | `execution/paper/` | OPTIONAL, disabled by default |
| 15 | Research/backtesting engine | `backtesting/` | CORE |
| 16 | Walk-forward and robustness | `research/` | CORE |
| 17 | Reporting | `application/reporting.py`, `storage/reports.py` | CORE |
| 18 | Scheduler and automation | `scheduler/` | OPTIONAL |
| 19 | REST API | `api/` | CORE |
| 20 | Dashboard | `dashboard/` | CORE |
| 21 | Notifications | `notifications/` | OPTIONAL |
| 22 | System health and observability | `observability/` | CORE |

"CORE" means required for the baseline research-and-signal product.
"OPTIONAL" means independently disableable and off by default.
"DEFERRED" and "OUT OF SCOPE" are defined in `TARGET_REPOSITORY_STRUCTURE.md`.

The optional AI gateway is not one of the 22 baseline subsystems. It is a
cross-cutting optional capability specified in `AI_GATEWAY.md`; it must be
absent from the baseline dependency graph.

## 9. Current vs target in one table

| Concern | Current (verified 2026-11-07) | Target |
|---|---|---|
| Package count | 21 packages directly under `crypto_engine/` | Layered modules with explicit ports |
| Layers | No `domain/`, `application/` or `ports/` separation | Four layers plus ports and a composition root |
| CLI | One ~69 KB `cli.py` containing orchestration | Thin presentation; use cases in `application/` |
| Indicators/features | `features/engineering.py` | `analytics/indicators/` + `analytics/features/` |
| Regimes | `add_regime()` inside the feature builder | `domain/regimes.py` + `analytics/regimes/` |
| Strategies | None as first-class objects | `strategies/` registry and engine |
| Risk | Gates mutate the `SignalRecord` | Separate `RiskDecision` stage |
| Portfolio | Paper account JSON only | Pure ledger + SQLite WAL adapter |
| Storage | Parquet snapshots + JSON/JSONL; SQLite WAL helper exists but no transactional state has migrated | Parquet datasets + SQLite WAL transactional state + JSON metadata |
| AI | Legacy `ai_providers/` plus the implemented provider-neutral `ai/` gateway (Stages 0–5) | `ports/ai.py` + `ai/` per `AI_GATEWAY.md` |
| Scheduler | None | Optional in-process scheduler |
| Notifications | None | Optional outbound adapter |
| Observability | Logging config only | `observability/` with health and timing |
| Tests | `unit`, `integration`, `leakage`, `fixtures` | Adds `architecture/` import-rule tests |

Detailed mapping: `TARGET_REPOSITORY_STRUCTURE.md` §5.

## 10. Non-goals and explicit exclusions

Not designed, not proposed, and not to be implied by any diagram:

- live order placement, cancellation, amendment or routing;
- real-money trading, withdrawals, deposits or transfers;
- private/authenticated exchange endpoints or stored exchange secrets;
- public internet exposure, multi-tenant hosting or user accounts;
- model retraining as part of the signal path;
- microservices, message brokers, Redis, Kafka, Docker or Kubernetes;
- GPU, distributed or deep-learning training;
- any claim of profitability, accuracy or production readiness.

## 11. Phase and authorization status

- `AGENTS.md` authorizes **Phase 10 only**, and explicitly excludes
  "architecture rewrites" from it.
- This document set was requested as an architecture-design task. It creates
  **documentation only**.
- Implementation of anything in this set requires a new, explicit phase
  authorization from the user, recorded before code is written.
- `IMPLEMENTATION_ROADMAP.md` is a proposal for sequencing, not an
  authorization, and Stage 0 in that roadmap explicitly requires approval.

Architecture freeze status (2026-11-07):

| Decision | Document | State |
|---|---|---|
| 4 GB hard budget on an ~8 GB host | `RESOURCE_BUDGET.md` §2, §11 | Frozen; open item closed |
| Parquet / SQLite WAL / JSON roles | `STORAGE_OWNERSHIP.md`, ADR-0015 | Frozen |
| Provider-neutral AI port and gateway | `AI_GATEWAY.md`, ADR-0014 | Frozen |
| AI advisory-only authority | `AI_GATEWAY.md` §12, DR-13 | Frozen |

Frozen means "agreed and not to be re-litigated"; it does not by itself mean
built. Two frozen decisions are now partly built:

- ADR-0014 / `AI_GATEWAY.md` — the provider-neutral core, registry, gateway,
  one OpenAI-compatible adapter, bounded advisory layers, composition root and
  `sf ai` CLI exist in `src/crypto_engine/ai/` (Stages 0–5). No provider SDK
  was installed, no credential was configured, no dependency was added and no
  live AI call is made by the ordinary test suite.
- ADR-0015 / `STORAGE_OWNERSHIP.md` — the role contracts, the enforced-WAL
  SQLite helper and the deterministic JSON store exist in
  `src/crypto_engine/storage/` (Stage 0). No database file, migration or
  `data/state/` directory was created.

The remaining frozen decisions (layering, target package layout, roadmap
stages) are still documentation only.

## 12. How to review this set

1. Read `SYSTEM_CONTEXT.md` and `MODULE_ARCHITECTURE.md` for shape.
2. Check `DEPENDENCY_RULES.md` against every diagram.
3. Check `DATA_ARCHITECTURE.md` against the manifest fields that actually
   exist today.
4. Check `RESEARCH_GOVERNANCE.md` for the historical-findings labelling.
5. Check `SECURITY_AND_EXECUTION_BOUNDARIES.md` against `docs/SECURITY.md`.
6. Check `TARGET_REPOSITORY_STRUCTURE.md` against the real tree.
7. Review the ADRs in `decisions/`.
8. Check `AI_GATEWAY.md` for the port, registry, secret boundary and authority
   boundary, and confirm nothing in it authorizes a call.
9. Check `STORAGE_OWNERSHIP.md` for the Parquet / SQLite WAL / JSON split and
   confirm no path is owned twice.

## 13. Maintenance rules

- This set describes a target. When implementation begins, update the
  affected document **in the same change** as the code.
- Never move a document from "proposed" to "implemented" without a recorded,
  actually-run verification command and its output.
- Superseded decisions are preserved: add a new ADR and mark the old one
  "Superseded by ADR-NNNN". Do not rewrite history.
- `memory/` and `DECISIONS.md` are owned by others; this set does not modify
  them.
