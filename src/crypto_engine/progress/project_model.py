"""Static project model for the progress dashboard.

This module is the single source of truth for *project knowledge* that is not
derived from the event ledger: the historical phase roadmap, the Stage 0-5
record, architecture layers, security checks, resource limits, glossary and
documentation links. The ledger adds live task-level updates on top.

Honesty rules baked into this data:

* Historical phases are labelled ``HISTORICAL`` and are not current filesystem
  evidence.
* Stage results are labelled as the last *recorded* report, not a fresh run.
* Live provider connectivity is ``NOT_VERIFIED``.
* M2 residual configuration fields are ``INERT`` and are not shown as working.
* Public deployment is not approved merely because local tests pass.
"""

from __future__ import annotations

import copy
from typing import Any

from crypto_engine.progress.schema import LEVELS, STATUS_DESCRIPTIONS, STATUSES

PHASES: list[dict[str, Any]] = [
    {
        "id": "A",
        "title": "Repository restructuring and reference review",
        "status": "HISTORICAL",
        "objective": "Establish a clean, reproducible repository and the agent/skill scaffolding used by later phases.",
        "work": [
            "Created pyproject.toml, config/default.toml, docs/, memory/ and .opencode agents/skills.",
            "Reviewed reference material and defined the canonical pipeline.",
            "Set the 4 GB operating constraint and the no-secrets-in-TOML rule.",
        ],
        "components": ["pyproject.toml", "config/default.toml", "docs/", "memory/", ".opencode/"],
        "decisions": [
            "uv-managed isolated .venv",
            "Public read-only data only",
            "No secrets in code or TOML",
        ],
        "evidence": ["Foundation build recorded in memory/SESSION_LOG.md"],
        "tests": "37 passed (Phase 1 baseline)",
        "limitations": ["No engine code at the start of the phase"],
        "outcome": "Repository and process foundation established.",
        "relationship": "Current src/crypto_engine/, docs/ and memory/ inherit this structure.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "B",
        "title": "Domain contracts",
        "status": "HISTORICAL",
        "objective": "Define typed, validated domain contracts before engine code.",
        "work": [
            "Settings, types and candle validation.",
            "UTC normalization plus monotonic/duplicate checks.",
        ],
        "components": [
            "src/crypto_engine/config/settings.py",
            "src/crypto_engine/types.py",
            "src/crypto_engine/data/validation.py",
        ],
        "decisions": ["pydantic-validated configuration", "Explicit errors over silent coercion"],
        "evidence": ["Phase 1 tests"],
        "tests": "37 passed (Phase 1)",
        "limitations": ["Contracts only; no exchange adapter yet"],
        "outcome": "Validated domain contracts.",
        "relationship": "Still the contract layer used by later phases.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "C",
        "title": "Exchange adapters and market data",
        "status": "HISTORICAL",
        "objective": "Fetch public OHLCV safely and persist immutable snapshots.",
        "work": [
            "ccxt public adapter with pagination, retries and stale warnings.",
            "Parquet snapshots with provenance and checksum.",
        ],
        "components": [
            "src/crypto_engine/data/ccxt_adapter.py",
            "src/crypto_engine/datasets/snapshots.py",
        ],
        "decisions": [
            "Public read-only exchange data",
            "Immutable snapshots with checksums",
            "No silent fills",
        ],
        "evidence": ["Phase 1 tests", "Snapshot checksum verification"],
        "tests": "37 passed (Phase 1)",
        "limitations": ["No live order path by design"],
        "outcome": "Market-data pipeline.",
        "relationship": "Used by features, models, backtests and forward validation.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "D",
        "title": "Indicators and market regimes",
        "status": "HISTORICAL",
        "objective": "Build causal features and labels.",
        "work": [
            "Causal feature builder with chunking.",
            "Triple-barrier labels with observable_at.",
        ],
        "components": ["src/crypto_engine/features/", "src/crypto_engine/labels/"],
        "decisions": ["Causality first", "Incomplete labels excluded", "n_jobs=1 / chunked"],
        "evidence": ["Phase 2 tests"],
        "tests": "98 passed, 1 skipped (Phase 2)",
        "limitations": ["Features are research-grade, not a profitability claim"],
        "outcome": "Causal feature and label layers.",
        "relationship": "Used by training and validation.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "E",
        "title": "Strategy engine and signal intelligence",
        "status": "HISTORICAL",
        "objective": "Produce canonical signal records from fitted models.",
        "work": [
            "Model interface and baselines.",
            "Signal composer with deterministic IDs and NO_TRADE.",
        ],
        "components": ["src/crypto_engine/models/", "src/crypto_engine/signals/"],
        "decisions": ["Fitted artifacts only", "Deterministic IDs", "NO_TRADE is first-class"],
        "evidence": ["Phase 5 tests"],
        "tests": "346 passed, 1 skipped (Phase 5)",
        "limitations": ["Scores are uncalibrated unless a calibrator is applied"],
        "outcome": "Canonical signal engine.",
        "relationship": "Core of the current deterministic pipeline.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "F",
        "title": "Risk management",
        "status": "HISTORICAL",
        "objective": "Enforce deterministic, fail-closed risk gates.",
        "work": ["Fixed-order gates A→B→C→D.", "AI cannot override; NO_TRADE is valid."],
        "components": ["src/crypto_engine/risk/gates.py"],
        "decisions": ["Deterministic gates independent of AI", "Fail closed"],
        "evidence": ["Phase 5 gate tests"],
        "tests": "346 passed, 1 skipped (Phase 5)",
        "limitations": ["No live execution; gates are advisory to the paper sandbox"],
        "outcome": "Deterministic risk gate layer.",
        "relationship": "Still authoritative over AI advice.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "G",
        "title": "Portfolio and paper execution",
        "status": "HISTORICAL",
        "objective": "Provide an advisory-only paper sandbox.",
        "work": ["Persistent paper state with duplicate protection.", "Hypothetical fills only."],
        "components": ["src/crypto_engine/paper_trading/"],
        "decisions": ["Paper fills are hypothetical", "No order adapter"],
        "evidence": ["Phase 6 tests"],
        "tests": "431 passed, 1 skipped (Phase 6)",
        "limitations": ["No real-money or live order path"],
        "outcome": "Advisory-only paper sandbox.",
        "relationship": "Still the only execution-like layer; no live orders.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "H",
        "title": "Advanced backtesting",
        "status": "HISTORICAL",
        "objective": "Run causal, costed backtests with conservative fills.",
        "work": ["Next-bar fills, costs and conservative ties.", "Reproducible run ledgers."],
        "components": ["src/crypto_engine/backtesting/"],
        "decisions": ["Causal timing", "Costs included", "Conservative tie-breaking"],
        "evidence": ["Phase 6 tests", "Timing-leakage tests"],
        "tests": "431 passed, 1 skipped (Phase 6)",
        "limitations": ["Historical simulation only; no profitability guarantee"],
        "outcome": "Causal backtester.",
        "relationship": "Used for research; not a live trading system.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "I",
        "title": "Scheduler",
        "status": "HISTORICAL",
        "objective": "Schedule recurring research runs.",
        "work": ["Historical scheduler work recorded in the roadmap."],
        "components": ["Historical only; no src/ scheduler module is present now."],
        "decisions": ["Scheduling must never imply live trading"],
        "evidence": ["Historical roadmap record"],
        "tests": "No current scheduler test suite",
        "limitations": [
            "Not present as a src/ module in the current filesystem",
            "scripts/prospective_shadow.py uses the Windows Task Scheduler externally for a shadow run",
        ],
        "outcome": "Historical phase; not current filesystem evidence.",
        "relationship": "No live scheduler is part of the current dashboard/API.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "J",
        "title": "Dashboard and API",
        "status": "HISTORICAL",
        "objective": "Expose read-only research data locally.",
        "work": [
            "GET-only /api/v1 with versioned envelopes.",
            "Server-rendered dashboard and sf serve.",
        ],
        "components": ["src/crypto_engine/api/", "src/crypto_engine/dashboard/", "sf serve"],
        "decisions": ["GET-only", "Loopback-only by default", "No mutations"],
        "evidence": ["Phase 7 tests", "Live uvicorn proof"],
        "tests": "466 passed, 1 skipped (Phase 7)",
        "limitations": ["No public exposure by default", "Dashboard shows recorded data only"],
        "outcome": "Read-only API and dashboard.",
        "relationship": "Extended by the new progress dashboard.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "K",
        "title": "Security and readiness",
        "status": "HISTORICAL",
        "objective": "Harden the read-only surface and document readiness.",
        "work": [
            "GET-only build assertion, path confinement, secret scanning and CORS deny-default.",
            "Security tests and operational docs.",
        ],
        "components": [
            "src/crypto_engine/api/security.py",
            "tests/integration/test_api_security.py",
            "docs/SECURITY.md",
        ],
        "decisions": ["Fail closed", "No secrets in responses", "No public exposure by default"],
        "evidence": ["Phase 7 security tests"],
        "tests": "466 passed, 1 skipped (Phase 7)",
        "limitations": ["Local tests do not approve public deployment"],
        "outcome": "Security baseline.",
        "relationship": "Still enforced; the progress dashboard reuses the same GET-only rules.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
    {
        "id": "L",
        "title": "Historical market expansion and validation",
        "status": "HISTORICAL",
        "objective": "Expand historical data and validate the research process.",
        "work": ["Historical data expansion and validation experiments."],
        "components": ["Historical only; deleted artifacts are not shown as present."],
        "decisions": ["Validation-only comparisons", "No forced accuracy target"],
        "evidence": ["Historical records in memory/"],
        "tests": "Historical records; not a current suite",
        "limitations": [
            "Historical outcomes remain historical",
            "Deleted files are not current evidence",
        ],
        "outcome": "Historical phase; superseded by Phase 10 forward validation.",
        "relationship": "Phase 10 forward validation is the current evidence, not Phase L artifacts.",
        "historical_note": "Historical project context; not current filesystem evidence.",
    },
]

STAGES: list[dict[str, Any]] = [
    {
        "id": 0,
        "title": "Foundation",
        "status": "FROZEN",
        "objective": "Provider-neutral contracts, gateway, registry, errors, storage contracts, secret boundary, failure isolation and advisory-only authority.",
        "components": [
            "ai/contracts.py",
            "ai/errors.py",
            "ai/registry.py",
            "ai/gateway.py",
            "storage/contracts.py",
            "storage/sqlite.py",
            "storage/json_store.py",
        ],
        "evidence": [
            {
                "label": "Focused tests",
                "value": "71 passed",
                "detail": "25 contracts + 27 gateway + 19 storage",
                "source": "Stage 0 report",
                "date": "2026-11-07",
            }
        ],
        "tests": {
            "passed": 71,
            "failed": 0,
            "skipped": 0,
            "total": 71,
            "groups": [
                {"name": "contracts", "passed": 25},
                {"name": "gateway", "passed": 27},
                {"name": "storage", "passed": 19},
            ],
            "command": "uv run --offline pytest tests/unit/test_stage0_*.py -q",
            "report": "recorded Stage 0 report",
        },
        "findings": [],
        "limitations": ["No concrete provider adapter yet"],
        "next": "Stage 1 configuration and routing",
        "acceptance_date": "2026-11-07",
        "acceptance_verdict": "ACCEPTED - STAGE 0-5 FROZEN",
        "evidence_label": "Recorded from the supplied Stage 0-5 final acceptance audit (2026-11-07); not a fresh execution.",
        "authority": "Advisory only: AI cannot change direction, score, risk, position, approval, execution, breakers or ledger.",
        "m2": "M2 ACCEPTED AS DOCUMENTED - residual fields inert; no runtime wiring authorized.",
        "remaining_finding": "RESOURCE_BUDGET.md:71 - CORRECTED (documentation-only, non-blocking; finding DOC-RESOURCE).",
        "recorded_report": True,
    },
    {
        "id": 1,
        "title": "Configuration and routing",
        "status": "FROZEN",
        "objective": "Map validated TOML configuration onto provider/model/task routing with environment-based credential references.",
        "components": ["ai/configuration.py", "config/settings.py", "config/default.toml"],
        "evidence": [
            {
                "label": "Focused tests",
                "value": "22 passed",
                "source": "Stage 1 report",
                "date": "2026-11-07",
            }
        ],
        "tests": {
            "passed": 22,
            "failed": 0,
            "skipped": 0,
            "total": 22,
            "groups": [{"name": "configuration", "passed": 22}],
            "command": "uv run --offline pytest tests/unit/test_stage1_ai_configuration.py -q",
            "report": "recorded Stage 1 report",
        },
        "findings": [],
        "limitations": ["Some parsed fields remain inert (see M2)"],
        "next": "Stage 2 provider adapter",
        "acceptance_date": "2026-11-07",
        "acceptance_verdict": "ACCEPTED - STAGE 0-5 FROZEN",
        "evidence_label": "Recorded from the supplied Stage 0-5 final acceptance audit (2026-11-07); not a fresh execution.",
        "authority": "Advisory only: AI cannot change direction, score, risk, position, approval, execution, breakers or ledger.",
        "m2": "M2 ACCEPTED AS DOCUMENTED - residual fields inert; no runtime wiring authorized.",
        "remaining_finding": "RESOURCE_BUDGET.md:71 - CORRECTED (documentation-only, non-blocking; finding DOC-RESOURCE).",
        "recorded_report": True,
    },
    {
        "id": 2,
        "title": "External provider adapter",
        "status": "FROZEN",
        "objective": "One OpenAI-compatible adapter behind AIProviderPort with egress allowlist, HTTPS policy, redirect protection and bounded request/response sizes.",
        "components": ["ai/providers/openai_compatible.py"],
        "evidence": [
            {
                "label": "Focused tests",
                "value": "33 passed",
                "source": "Stage 2 report",
                "date": "2026-11-07",
            }
        ],
        "tests": {
            "passed": 33,
            "failed": 0,
            "skipped": 0,
            "total": 33,
            "groups": [{"name": "adapter", "passed": 33}],
            "command": "uv run --offline pytest tests/unit/test_stage2_openai_adapter.py -q",
            "report": "recorded Stage 2 report",
        },
        "findings": [
            "Offline/mock tests passed; a real external API call is not verified.",
            "M2 residual configuration limitations remain documented.",
        ],
        "limitations": ["Real provider connectivity NOT_VERIFIED", "No live credential was used"],
        "next": "Stage 3 signal advisory",
        "acceptance_date": "2026-11-07",
        "acceptance_verdict": "ACCEPTED - STAGE 0-5 FROZEN",
        "evidence_label": "Recorded from the supplied Stage 0-5 final acceptance audit (2026-11-07); not a fresh execution.",
        "authority": "Advisory only: AI cannot change direction, score, risk, position, approval, execution, breakers or ledger.",
        "m2": "M2 ACCEPTED AS DOCUMENTED - residual fields inert; no runtime wiring authorized.",
        "remaining_finding": "RESOURCE_BUDGET.md:71 - CORRECTED (documentation-only, non-blocking; finding DOC-RESOURCE).",
        "recorded_report": True,
    },
    {
        "id": 3,
        "title": "Signal advisory",
        "status": "FROZEN",
        "objective": "AI explanations are advisory-only; the deterministic signal remains authoritative and AI failure never fails the signal.",
        "components": ["ai/advisory/signal.py"],
        "evidence": [
            {
                "label": "Focused tests",
                "value": "31 passed",
                "source": "Stage 3 report",
                "date": "2026-11-07",
            }
        ],
        "tests": {
            "passed": 31,
            "failed": 0,
            "skipped": 0,
            "total": 31,
            "groups": [{"name": "signal advisory", "passed": 31}],
            "command": "uv run --offline pytest tests/unit/test_stage3_ai_signal_advisory.py -q",
            "report": "recorded Stage 3 report",
        },
        "findings": [],
        "limitations": ["AI advice cannot change direction, score, risk or execution"],
        "next": "Stage 4 research advisory",
        "acceptance_date": "2026-11-07",
        "acceptance_verdict": "ACCEPTED - STAGE 0-5 FROZEN",
        "evidence_label": "Recorded from the supplied Stage 0-5 final acceptance audit (2026-11-07); not a fresh execution.",
        "authority": "Advisory only: AI cannot change direction, score, risk, position, approval, execution, breakers or ledger.",
        "m2": "M2 ACCEPTED AS DOCUMENTED - residual fields inert; no runtime wiring authorized.",
        "remaining_finding": "RESOURCE_BUDGET.md:71 - CORRECTED (documentation-only, non-blocking; finding DOC-RESOURCE).",
        "recorded_report": True,
    },
    {
        "id": 4,
        "title": "Research advisory",
        "status": "FROZEN",
        "objective": "Structured research summaries with FACT / CALCULATION / INTERPRETATION / UNCERTAINTY / NOT_AVAILABLE and promotion restrictions.",
        "components": ["ai/advisory/research.py"],
        "evidence": [
            {
                "label": "Focused tests",
                "value": "24 passed",
                "source": "Stage 4 report",
                "date": "2026-11-07",
            }
        ],
        "tests": {
            "passed": 24,
            "failed": 0,
            "skipped": 0,
            "total": 24,
            "groups": [{"name": "research advisory", "passed": 24}],
            "command": "uv run --offline pytest tests/unit/test_stage4_ai_research_advisory.py -q",
            "report": "recorded Stage 4 report",
        },
        "findings": [],
        "limitations": ["Promotion to paper testing is denied by construction"],
        "next": "Stage 5 hardening and CLI",
        "acceptance_date": "2026-11-07",
        "acceptance_verdict": "ACCEPTED - STAGE 0-5 FROZEN",
        "evidence_label": "Recorded from the supplied Stage 0-5 final acceptance audit (2026-11-07); not a fresh execution.",
        "authority": "Advisory only: AI cannot change direction, score, risk, position, approval, execution, breakers or ledger.",
        "m2": "M2 ACCEPTED AS DOCUMENTED - residual fields inert; no runtime wiring authorized.",
        "remaining_finding": "RESOURCE_BUDGET.md:71 - CORRECTED (documentation-only, non-blocking; finding DOC-RESOURCE).",
        "recorded_report": True,
    },
    {
        "id": 5,
        "title": "Hardening and CLI",
        "status": "FROZEN",
        "objective": "Composition root, offline CLI, architecture tests, failure isolation and resource enforcement.",
        "components": [
            "ai/composition.py",
            "sf ai status",
            "sf ai config-check",
            "sf ai providers",
            "sf ai models",
            "sf ai routes",
            "sf ai test",
        ],
        "evidence": [
            {
                "label": "Architecture tests",
                "value": "31 passed",
                "source": "Stage 5 report",
                "date": "2026-11-07",
            },
            {
                "label": "Failure/resource tests",
                "value": "18 passed",
                "source": "Stage 5 report",
                "date": "2026-11-07",
            },
            {
                "label": "CLI tests",
                "value": "12 passed",
                "source": "Stage 5 report",
                "date": "2026-11-07",
            },
        ],
        "tests": {
            "passed": 61,
            "failed": 0,
            "skipped": 0,
            "total": 61,
            "groups": [
                {"name": "architecture", "passed": 31},
                {"name": "failure/resource", "passed": 18},
                {"name": "cli", "passed": 12},
            ],
            "command": "uv run --offline pytest tests/unit/test_stage5_architecture.py tests/unit/test_stage5_provider_failure_and_resources.py tests/integration/test_ai_cli.py -q",
            "report": "recorded Stage 5 report",
        },
        "findings": [
            "M2 residual fields are documented as inert; no runtime failover, budget tracker, cache or prompt logging exists."
        ],
        "limitations": [
            "Live provider test NOT PERFORMED",
            "Dashboard integration was not part of Stage 5",
        ],
        "next": "Final acceptance and freeze (completed by the read-only audit)",
        "acceptance_date": "2026-11-07",
        "acceptance_verdict": "ACCEPTED - STAGE 0-5 FROZEN",
        "evidence_label": "Recorded from the supplied Stage 0-5 final acceptance audit (2026-11-07); not a fresh execution.",
        "authority": "Advisory only: AI cannot change direction, score, risk, position, approval, execution, breakers or ledger.",
        "m2": "M2 ACCEPTED AS DOCUMENTED - residual fields inert; no runtime wiring authorized.",
        "remaining_finding": "RESOURCE_BUDGET.md:71 - CORRECTED (documentation-only, non-blocking; finding DOC-RESOURCE).",
        "recorded_report": True,
    },
]

OVERALL: dict[str, Any] = {
    "label": "AI FOUNDATION — ACCEPTED / FROZEN",
    "status": "FROZEN",
    "verdict": "ACCEPTED — STAGE 0-5 FROZEN",
    "acceptance_date": "2026-11-07",
    "acceptance_source": "Supplied Stage 0-5 final acceptance audit. Recorded evidence, not a fresh execution.",
    "combined_focused_tests": 242,
    "focused_skipped": 0,
    "full_suite": {
        "passed": 785,
        "skipped": 4,
        "failed": 0,
        "errors": 0,
        "command": "uv run --offline pytest -q",
        "source": "Stage 0-5 final acceptance audit",
        "date": "2026-11-07",
        "note": "Audit-time evidence. The dashboard's own 831-pass run is recorded separately in the ledger and is not combined with this number.",
    },
    "additional_verification": {
        "architecture_security_resource_provider_failure": {"passed": 120, "skipped": 0},
        "ruff_check": "passed on changed Stage 0-5 files",
        "ruff_format": "passed for all 28 reviewed files",
        "ai_offline_cli": "passed",
        "sf_ai_test": "reported disabled; no network request was made",
    },
    "m2": "M2 ACCEPTED AS DOCUMENTED",
    "remaining_finding": "RESOURCE_BUDGET.md:71 - CORRECTED (documentation-only, non-blocking; event evt-20261107T000625Z-f31f2348)",
    "live_provider_test": "NOT PERFORMED",
    "dashboard_integration": "NEW WORK AUTHORIZED BY THIS REQUEST",
    "commit": "NOT CREATED",
    "push": "NOT PERFORMED",
    "freeze_basis": "Read-only final acceptance audit returned ACCEPTED — STAGE 0-5 FROZEN; no implementation changes were made by the audit. The audit did not perform a live provider call.",
    "recorded_report": "2026-11-07 final acceptance audit",
}

ARCHITECTURE: dict[str, Any] = {
    "layers": [
        {
            "name": "PRESENTATION",
            "components": [
                {
                    "name": "CLI (sf)",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/cli.py",
                },
                {
                    "name": "API (/api/v1)",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/api/",
                },
                {
                    "name": "Dashboard (/dashboard/, /dashboard/progress/)",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/dashboard/",
                },
            ],
        },
        {
            "name": "APPLICATION",
            "components": [
                {
                    "name": "Use cases / CLI commands",
                    "status": "IMPLEMENTED",
                    "evidence": "sf signal, sf backtest, sf paper, sf model, sf ai, sf serve",
                }
            ],
        },
        {
            "name": "DOMAIN SERVICES",
            "components": [
                {
                    "name": "Analytics (features, labels)",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/features/, labels/",
                },
                {
                    "name": "Strategies / signals",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/signals/",
                },
                {"name": "Risk", "status": "IMPLEMENTED", "evidence": "src/crypto_engine/risk/"},
                {
                    "name": "Portfolio / paper",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/paper_trading/",
                },
                {
                    "name": "Backtesting",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/backtesting/",
                },
                {
                    "name": "Research / validation",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/validation/, registry/",
                },
                {
                    "name": "Quality",
                    "status": "IMPLEMENTED",
                    "evidence": "tests/ plus validation metrics",
                },
            ],
        },
        {
            "name": "DOMAIN",
            "components": [
                {
                    "name": "Pure business contracts",
                    "status": "IMPLEMENTED",
                    "evidence": "signals/schema.py, models/base.py, ai/contracts.py, storage/contracts.py",
                }
            ],
        },
        {
            "name": "PORTS",
            "components": [
                {
                    "name": "Interfaces",
                    "status": "IMPLEMENTED",
                    "evidence": "AIProviderPort, storage protocols",
                }
            ],
        },
        {
            "name": "INFRASTRUCTURE",
            "components": [
                {
                    "name": "Market data",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/data/, datasets/",
                },
                {
                    "name": "Storage",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/storage/",
                },
                {
                    "name": "Execution",
                    "status": "PARTIAL",
                    "evidence": "Paper sandbox only; no live order adapter",
                },
                {
                    "name": "Scheduler",
                    "status": "HISTORICAL",
                    "evidence": "Phase I historical; no src/ scheduler module present",
                },
                {
                    "name": "Notifications",
                    "status": "DESIGN_ONLY",
                    "evidence": "No notification module",
                },
                {
                    "name": "Observability",
                    "status": "PARTIAL",
                    "evidence": "Logging configured; no metrics/tracing stack",
                },
                {
                    "name": "Configuration",
                    "status": "IMPLEMENTED",
                    "evidence": "src/crypto_engine/config/",
                },
            ],
        },
        {
            "name": "COMPOSITION ROOT",
            "components": [
                {
                    "name": "Bootstrap",
                    "status": "IMPLEMENTED",
                    "evidence": "ai/composition.py, api/app.py, cli.py",
                }
            ],
        },
    ],
    "ai_flow": [
        {"step": "AI task", "status": "IMPLEMENTED"},
        {"step": "AI gateway", "status": "IMPLEMENTED"},
        {"step": "Provider registry", "status": "IMPLEMENTED"},
        {"step": "Provider adapter", "status": "IMPLEMENTED"},
        {
            "step": "External API",
            "status": "NOT_VERIFIED",
            "note": "No live call was made; offline/mock only.",
        },
        {"step": "AI response validation", "status": "IMPLEMENTED"},
        {"step": "Advisory output", "status": "IMPLEMENTED"},
        {"step": "Deterministic core remains authoritative", "status": "IMPLEMENTED"},
    ],
    "note": "Based on docs/ARCHITECTURE.md, docs/architecture/AI_GATEWAY.md §16 and current source. The 'design-only target architecture' paragraph in docs/ARCHITECTURE.md predates Stage 0-5 and is stale for the AI gateway.",
}

SUBSYSTEMS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Exchange adapter (ccxt)",
        "phase": "1",
        "status": "IMPLEMENTED",
        "note": "Public read-only, rate-limited, bounded retries.",
    },
    {
        "id": 2,
        "name": "OHLCV schema + validation",
        "phase": "1",
        "status": "IMPLEMENTED",
        "note": "UTC, monotonic, no duplicates.",
    },
    {
        "id": 3,
        "name": "Provenance tracker",
        "phase": "1",
        "status": "IMPLEMENTED",
        "note": "Source, fetch time, params, checksum.",
    },
    {
        "id": 4,
        "name": "Snapshot store (Parquet)",
        "phase": "1",
        "status": "IMPLEMENTED",
        "note": "Immutable, reproducible datasets.",
    },
    {
        "id": 5,
        "name": "Config loader (TOML+env)",
        "phase": "1",
        "status": "IMPLEMENTED",
        "note": "pydantic-validated; no secrets in TOML.",
    },
    {
        "id": 6,
        "name": "CLI (sf health, sf config-show)",
        "phase": "1",
        "status": "IMPLEMENTED",
        "note": "typer + rich.",
    },
    {
        "id": 7,
        "name": "Logging",
        "phase": "1",
        "status": "IMPLEMENTED",
        "note": "Level from config, override via env.",
    },
    {
        "id": 8,
        "name": "Feature builder",
        "phase": "2",
        "status": "IMPLEMENTED",
        "note": "Causal, chunked, registry-documented.",
    },
    {
        "id": 9,
        "name": "Label builder",
        "phase": "2",
        "status": "IMPLEMENTED",
        "note": "Triple-barrier, observable-at, incomplete excluded.",
    },
    {
        "id": 10,
        "name": "Model interface",
        "phase": "2",
        "status": "IMPLEMENTED",
        "note": "Fitted-only predict/score, schema-checked, checksummed.",
    },
    {
        "id": 11,
        "name": "Training pipeline",
        "phase": "2",
        "status": "IMPLEMENTED",
        "note": "Baselines (trend/majority/logistic), n_jobs=1.",
    },
    {
        "id": 12,
        "name": "Calibration",
        "phase": "3",
        "status": "IMPLEMENTED",
        "note": "P(y==positive_class), sigmoid/isotonic, never on holdout.",
    },
    {
        "id": 13,
        "name": "Inference service",
        "phase": "5",
        "status": "IMPLEMENTED",
        "note": "Single-row decide(), fitted artifacts only, no retraining.",
    },
    {
        "id": 14,
        "name": "Signal composer",
        "phase": "5",
        "status": "IMPLEMENTED",
        "note": "Canonical SignalRecord v1.0, deterministic ids, NO_TRADE first-class.",
    },
    {
        "id": 15,
        "name": "Backtest engine (causal)",
        "phase": "6",
        "status": "IMPLEMENTED",
        "note": "Next-bar fills, costed, conservative ties, reproducible ledger.",
    },
    {
        "id": 16,
        "name": "Risk gates (deterministic)",
        "phase": "5",
        "status": "IMPLEMENTED",
        "note": "Fixed order A→B→C→D, fail-closed, AI never overrides; NO_TRADE valid.",
    },
    {
        "id": 17,
        "name": "AI provider adapters",
        "phase": "4",
        "status": "IMPLEMENTED",
        "note": "Legacy ai_providers/ coexists with the newer ai/ gateway; consolidation is not authorized.",
    },
    {
        "id": 18,
        "name": "API layer",
        "phase": "7",
        "status": "IMPLEMENTED",
        "note": "GET-only /api/v1, versioned envelopes, no mutations.",
    },
    {
        "id": 19,
        "name": "Dashboard",
        "phase": "7",
        "status": "IMPLEMENTED",
        "note": "Server-rendered pages plus the new progress control center; escaped, no trading controls.",
    },
    {
        "id": 20,
        "name": "Paper-trading loop",
        "phase": "6",
        "status": "IMPLEMENTED",
        "note": "Advisory-only sandbox, persistent state, duplicate protection, no orders.",
    },
]

SECURITY_CHECKS: list[dict[str, Any]] = [
    {
        "id": "hardcoded-credentials",
        "label": "API credentials not hardcoded",
        "status": "PASS",
        "evidence": "AST/scan tests in tests/unit/test_stage5_architecture.py",
    },
    {
        "id": "secretref",
        "label": "SecretRef boundary",
        "status": "PASS",
        "evidence": "SecretRef is the only environment reader in ai/contracts.py",
    },
    {
        "id": "https",
        "label": "HTTPS enforcement",
        "status": "PASS",
        "evidence": "Remote endpoints must use https; loopback is the only exception",
    },
    {
        "id": "egress",
        "label": "Egress allowlist",
        "status": "PASS",
        "evidence": "Provider host must match egress_allowlist or the base URL host",
    },
    {
        "id": "redirects",
        "label": "Redirect refusal",
        "status": "PASS",
        "evidence": "_NoRedirectHandler; 3xx is normalized, not followed",
    },
    {
        "id": "authorization",
        "label": "Authorization header protection",
        "status": "PASS",
        "evidence": "Authorization is only added for bearer auth and never forwarded across a redirect",
    },
    {
        "id": "auth-fail-closed",
        "label": "Unsupported authentication mechanisms fail closed",
        "status": "PASS",
        "evidence": "header/query return unsupported; loopback none is explicit",
    },
    {
        "id": "request-size",
        "label": "Request-size limits",
        "status": "PASS",
        "evidence": "max_request_chars = 8000",
    },
    {
        "id": "response-size",
        "label": "Response-size limits",
        "status": "PASS",
        "evidence": "max_response_chars = 4000; adapter read cap 200,000 bytes",
    },
    {
        "id": "timeouts",
        "label": "Timeout bounds",
        "status": "PASS",
        "evidence": "min(global, provider) timeout ceiling",
    },
    {
        "id": "retries",
        "label": "Retry bounds",
        "status": "PASS",
        "evidence": "0-3 retries, exponential backoff capped at 2 s",
    },
    {
        "id": "authority",
        "label": "AI authority isolation",
        "status": "PASS",
        "evidence": "AI cannot change direction, score, risk, position, approval, execution, breakers or ledger",
    },
    {
        "id": "no-trade",
        "label": "NO_TRADE protection",
        "status": "PASS",
        "evidence": "Deterministic gates are independent of models/AI; NO_TRADE is first-class",
    },
    {
        "id": "no-live-orders",
        "label": "No live order adapter",
        "status": "PASS",
        "evidence": "No order placement/cancellation code; paper fills are hypothetical",
    },
    {
        "id": "no-unapproved-execution",
        "label": "No unapproved execution",
        "status": "PASS",
        "evidence": "GET-only API; no write endpoints; loopback-only default",
    },
    {
        "id": "log-hygiene",
        "label": "No credential exposure in logs",
        "status": "PASS",
        "evidence": "Redaction and secret-scan tests; no resolved secret is printed",
    },
]

FINDINGS: list[dict[str, Any]] = [
    {
        "id": "M2",
        "title": "M2 — Partially addressed / residual configuration limitations",
        "severity": "medium",
        "status": "ACCEPTED AS DOCUMENTED",
        "blocking": False,
        "fields": [
            "default_provider",
            "fallback_order",
            "cache_ttl_s",
            "log_prompts",
            "[ai.budgets]",
            "per-provider max_response_chars",
            "per-provider concurrency",
            "per-task bounds",
        ],
        "behavior": [
            "Unsupported header/query auth mechanisms fail closed.",
            "Only bearer and loopback-none authentication are implemented.",
            "No runtime M2 wiring is authorized.",
        ],
        "note": "Parsed and validated but inert. They are not functioning features and must not be displayed as such. AI_GATEWAY.md §16.9 and config/default.toml mark them reserved/not enforced.",
    },
    {
        "id": "LEGACY",
        "title": "Legacy ai_providers/ coexists with the newer ai/ gateway",
        "severity": "low",
        "status": "NOT AUTHORIZED TO CONSOLIDATE",
        "note": "src/crypto_engine/ai_providers/ is unchanged. Consolidation has not been authorized.",
    },
    {
        "id": "LIVE",
        "title": "Real provider connectivity has not been verified",
        "severity": "medium",
        "status": "NOT_VERIFIED",
        "note": "All Stage 0-5 provider tests are offline/mock. No live AI API call or real credential was used.",
    },
    {
        "id": "PUBLIC",
        "title": "Public network deployment is not approved",
        "severity": "high",
        "status": "NOT_APPROVED",
        "note": "Local tests do not imply deployment, trading or public-exposure readiness.",
    },
    {
        "id": "DOC-RESOURCE",
        "title": "RESOURCE_BUDGET.md:71 — stale AI Budget Tracker design row",
        "severity": "low",
        "status": "CORRECTED",
        "blocking": False,
        "path": "docs/architecture/RESOURCE_BUDGET.md:71",
        "corrected": True,
        "correction_event_id": "evt-20261107T000625Z-f31f2348",
        "correction_timestamp_utc": "2026-11-07T00:06:25Z",
        "original_event_id": "evt-20261106T234507Z-267ecb81",
        "original_status": "PASS_WITH_FINDINGS",
        "evidence": [
            "AI Budget Tracker row corrected.",
            "Runtime-enforced gateway limits documented accurately.",
            "[ai.budgets] explicitly remains reserved/inert.",
            "No runtime budget tracker or paid fallback was implemented.",
            "Legacy BudgetTracker remains isolated in ai_providers/.",
            "AI_GATEWAY.md §16.9 remains consistent.",
            "Documentation-only correction passed 35/35 consistency assertions.",
        ],
        "note": (
            "Corrected by the documentation-only task recorded at 2026-11-07T00:06:25Z "
            "(event evt-20261107T000625Z-f31f2348). The design-time row (max_requests, max_chars, "
            "'Stop before paid fallback') was replaced with the enforced gateway limits; [ai.budgets] "
            "remains reserved and inert; no runtime budget tracker or paid fallback was implemented; "
            "legacy BudgetTracker remains isolated in ai_providers/; AI_GATEWAY.md §16.9 remains "
            "consistent. The original finding event evt-20261106T234507Z-267ecb81 "
            "(PASS_WITH_FINDINGS) is retained in history."
        ),
    },
    {
        "id": "DOC-ARCH",
        "title": "docs/ARCHITECTURE.md design-only paragraph predates Stage 0-5",
        "severity": "low",
        "status": "FINDING",
        "note": "The paragraph says docs/architecture/ is design only; the AI gateway is now implemented and documented in AI_GATEWAY.md §16.",
    },
]

RESOURCE_LIMITS: dict[str, Any] = {
    "documented": [
        {"label": "Laptop RAM", "value": "8 GB", "source": "RESOURCE_BUDGET.md"},
        {"label": "Hard operating memory budget", "value": "4 GB", "source": "AGENTS.md §2"},
        {
            "label": "GPU",
            "value": "GTX 1650, 4 GB VRAM",
            "source": "documented operating constraint",
        },
        {"label": "Execution approach", "value": "CPU-first", "source": "AGENTS.md"},
        {"label": "AI inference", "value": "External API-first", "source": "AI_GATEWAY.md §13"},
        {"label": "Local LLM", "value": "Not part of baseline", "source": "AI_GATEWAY.md"},
        {"label": "AI concurrent requests", "value": "1", "source": "gateway enforcement"},
        {"label": "Process count (baseline)", "value": "1", "source": "RESOURCE_BUDGET.md"},
        {"label": "Request size", "value": "8,000 chars", "source": "max_request_chars"},
        {"label": "Response size", "value": "4,000 chars", "source": "max_response_chars"},
        {"label": "Output tokens", "value": "1,024", "source": "max_output_tokens"},
        {
            "label": "Evidence",
            "value": "64 keys, 8,000 chars per value",
            "source": "advisory evidence bounds",
        },
    ],
    "recorded_probes": [
        {
            "label": "Dashboard render (Phase 7)",
            "value": "~0.09 s wall, ~58 KiB tracemalloc peak",
            "source": "memory/CURRENT_STATE.md",
            "note": "Recorded probe, not a live process-RSS measurement.",
        }
    ],
    "unavailable": ["Live RAM utilization", "Live GPU utilization", "Live process RSS"],
    "note": "Documented limits are not live measurements. No fabricated utilization is shown.",
}

GLOSSARY: list[dict[str, str]] = [
    {
        "term": "SignalForge",
        "definition": "The standalone crypto quantitative research engine in this repository.",
    },
    {
        "term": "Workstream",
        "definition": "A bounded stream of authorized work, such as ai_gateway or progress_dashboard.",
    },
    {
        "term": "Phase",
        "definition": "A historical development phase (A-L) recorded in project memory.",
    },
    {"term": "Stage", "definition": "A Stage 0-5 AI implementation increment."},
    {"term": "Task", "definition": "A bounded unit of work inside a stage or workstream."},
    {"term": "Verification", "definition": "A recorded test, audit or review that checks a claim."},
    {
        "term": "Evidence",
        "definition": "A command result, file path, report or test count supporting a claim.",
    },
    {"term": "Ledger", "definition": "The append-only events.jsonl progress record."},
    {
        "term": "Snapshot",
        "definition": "The derived manifest plus the latest events served to the dashboard.",
    },
    {
        "term": "Deterministic core",
        "definition": "The signal/risk/execution logic that remains authoritative over AI advice.",
    },
    {
        "term": "Advisory-only",
        "definition": "AI output may explain or summarize but cannot change deterministic decisions.",
    },
    {
        "term": "NO_TRADE",
        "definition": "A valid, expected deterministic output when evidence is invalid or insufficient.",
    },
    {
        "term": "M2",
        "definition": "The residual configuration limitations accepted as documented, not wired at runtime.",
    },
    {
        "term": "PASS_WITH_FINDINGS",
        "definition": "A recorded result that passed with documented, non-blocking findings.",
    },
    {"term": "NOT_VERIFIED", "definition": "A claim for which no recorded evidence exists yet."},
    {
        "term": "HISTORICAL",
        "definition": "Recorded historical context, not current filesystem evidence.",
    },
    {
        "term": "Simple View",
        "definition": "Plain-language explanations of what happened, why, whether it works and what is next.",
    },
    {
        "term": "Technical View",
        "definition": "Module paths, commands, counts, error codes, evidence and Git details.",
    },
]

DOCS: list[dict[str, str]] = [
    {
        "title": "Architecture",
        "path": "docs/ARCHITECTURE.md",
        "description": "Canonical pipeline and the 20 implemented components.",
    },
    {
        "title": "AI Gateway",
        "path": "docs/architecture/AI_GATEWAY.md",
        "description": "Provider-neutral AI design contract plus §16 implementation truth.",
    },
    {
        "title": "Storage Ownership",
        "path": "docs/architecture/STORAGE_OWNERSHIP.md",
        "description": "Parquet / SQLite WAL / JSON role boundaries.",
    },
    {
        "title": "Dependency Rules",
        "path": "docs/architecture/DEPENDENCY_RULES.md",
        "description": "Layering and import-direction enforcement.",
    },
    {
        "title": "Resource Budget",
        "path": "docs/architecture/RESOURCE_BUDGET.md",
        "description": "4 GB operating envelope and per-workload bounds.",
    },
    {
        "title": "AI Providers",
        "path": "docs/AI_PROVIDERS.md",
        "description": "Provider adapter target and legacy package description.",
    },
    {
        "title": "Security",
        "path": "docs/SECURITY.md",
        "description": "Read-only API, loopback, secret and execution boundaries.",
    },
    {
        "title": "Install (Windows)",
        "path": "docs/INSTALL_WINDOWS.md",
        "description": "Windows install and run instructions.",
    },
    {
        "title": "Paper Trading",
        "path": "docs/PAPER_TRADING.md",
        "description": "Advisory-only paper sandbox; hypothetical fills.",
    },
    {
        "title": "Research Methodology",
        "path": "docs/RESEARCH_METHODOLOGY.md",
        "description": "Causal research and validation methodology.",
    },
    {
        "title": "Progress Dashboard",
        "path": "docs/PROJECT_PROGRESS_DASHBOARD.md",
        "description": "How to launch and use this control center.",
    },
    {
        "title": "Progress Tracking",
        "path": "docs/PROJECT_PROGRESS_TRACKING.md",
        "description": "Event schema, status definitions and the agent recording workflow.",
    },
]

HIERARCHY: dict[str, Any] = {
    "levels": [
        "PROJECT",
        "WORKSTREAM",
        "PHASE",
        "STAGE",
        "SUBSYSTEM",
        "TASK",
        "STEP",
        "VERIFICATION",
        "EVIDENCE",
    ],
    "description": "Each level can carry a status, timestamps, files, tests, evidence, blockers and a next action. The ledger records task-level updates and the dashboard expands them in place.",
}

PROJECT_MODEL: dict[str, Any] = {
    "phases": PHASES,
    "stages": STAGES,
    "overall": OVERALL,
    "architecture": ARCHITECTURE,
    "subsystems": SUBSYSTEMS,
    "security_checks": SECURITY_CHECKS,
    "findings": FINDINGS,
    "resource_limits": RESOURCE_LIMITS,
    "glossary": GLOSSARY,
    "docs": DOCS,
    "hierarchy": HIERARCHY,
    "levels": sorted(LEVELS),
    "statuses": sorted(STATUSES),
    "status_descriptions": STATUS_DESCRIPTIONS,
    "milestones": {
        "phases": len(PHASES),
        "stages": len(STAGES),
        "subsystems": len(SUBSYSTEMS),
        "security_checks": len(SECURITY_CHECKS),
        "findings": len(FINDINGS),
    },
}


def project_snapshot() -> dict[str, Any]:
    """Return a deep copy so callers cannot mutate the static model."""
    return copy.deepcopy(PROJECT_MODEL)


__all__ = [
    "ARCHITECTURE",
    "DOCS",
    "FINDINGS",
    "GLOSSARY",
    "HIERARCHY",
    "OVERALL",
    "PHASES",
    "PROJECT_MODEL",
    "RESOURCE_LIMITS",
    "SECURITY_CHECKS",
    "STAGES",
    "SUBSYSTEMS",
    "project_snapshot",
]
