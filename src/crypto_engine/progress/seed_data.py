"""Deterministic seed events for the project progress ledger.

Every event below is derived from a *recorded* project report or from the
current authorized build. Nothing here is fabricated measurement:

* Phases A-L are ``HISTORICAL`` project context from ``memory/``; they are not
  current filesystem evidence.
* Stage 0-5 results are the last recorded focused reports, not a fresh run.
* Live provider connectivity is ``NOT_VERIFIED``.
* The progress dashboard starts as ``PLANNED`` -> ``IN_PROGRESS``; a final
  ``PASS`` is only appended after the integrated checks actually run.

``ProgressLedger.seed`` appends these events idempotently (duplicate event IDs
are skipped), so running the seeder twice is safe.
"""

from __future__ import annotations

from typing import Any

SEED_SCHEMA_VERSION = 1


def _phase_event(
    phase: str,
    title: str,
    summary: str,
    details: str,
    timestamp: str,
    tests: str,
    limitations: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": SEED_SCHEMA_VERSION,
        "event_id": f"seed-phase-{phase.lower()}-historical",
        "timestamp_utc": timestamp,
        "level": "phase",
        "workstream": "historical_phases",
        "phase": phase,
        "task_id": f"phase_{phase.lower()}",
        "status": "HISTORICAL",
        "title": title,
        "summary": summary,
        "details": details,
        "tests": {"note": tests},
        "evidence": ["memory/SESSION_LOG.md", "memory/PROJECT_MEMORY.md"],
        "blockers": [],
        "next_action": None,
        "agent": "recorded project history",
        "simple": {
            "what": summary,
            "why": "This phase established a layer the current engine still relies on.",
            "working": "Completed in a previous development session.",
            "not_verified": "Historical context only; deleted artifacts are not current evidence.",
            "next": "No action; historical record.",
        },
        "technical": {
            "module_paths": [],
            "commands": [],
        },
        "blockers_note": None,
        "limitations": limitations,
    }


def seed_events() -> list[dict[str, Any]]:
    """Return the ordered, idempotent seed event list."""
    events: list[dict[str, Any]] = []

    # -- historical Phase A-L ------------------------------------------------
    phases = [
        (
            "A",
            "Phase A — Repository restructuring and reference review",
            "Repository, configuration, documentation and agent/skill scaffolding were established.",
            "Created pyproject.toml, config/default.toml, docs/, memory/ and .opencode agents/skills. "
            "Defined the canonical pipeline and the 4 GB operating constraint.",
            "2026-10-27T09:00:00Z",
            "37 passed (Phase 1 baseline)",
            ["No engine code at the start of the phase"],
        ),
        (
            "B",
            "Phase B — Domain contracts",
            "Typed, validated domain contracts were defined before engine code.",
            "Settings, types and candle validation with UTC normalization plus monotonic/duplicate checks.",
            "2026-10-27T12:00:00Z",
            "37 passed (Phase 1)",
            ["Contracts only; no exchange adapter yet"],
        ),
        (
            "C",
            "Phase C — Exchange adapters and market data",
            "Public OHLCV fetching and immutable snapshot storage were built.",
            "ccxt public adapter with pagination, retries and stale warnings; Parquet snapshots with "
            "provenance and checksum. No live order path by design.",
            "2026-10-27T18:00:00Z",
            "37 passed (Phase 1)",
            ["No live order path by design"],
        ),
        (
            "D",
            "Phase D — Indicators and market regimes",
            "Causal features and triple-barrier labels were added.",
            "Causal feature builder with chunking; labels carry observable_at and incomplete labels are excluded.",
            "2026-10-28T10:00:00Z",
            "98 passed, 1 skipped (Phase 2)",
            ["Features are research-grade, not a profitability claim"],
        ),
        (
            "E",
            "Phase E — Strategy engine and signal intelligence",
            "Canonical signal records were produced from fitted models.",
            "Model interface and baselines; signal composer with deterministic IDs and NO_TRADE as a first-class output.",
            "2026-10-29T11:00:00Z",
            "346 passed, 1 skipped (Phase 5)",
            ["Scores are uncalibrated unless a calibrator is applied"],
        ),
        (
            "F",
            "Phase F — Risk management",
            "Deterministic, fail-closed risk gates were implemented.",
            "Fixed-order gates A→B→C→D; AI cannot override them; NO_TRADE remains valid and expected.",
            "2026-10-29T15:00:00Z",
            "346 passed, 1 skipped (Phase 5)",
            ["No live execution; gates are advisory to the paper sandbox"],
        ),
        (
            "G",
            "Phase G — Portfolio and paper execution",
            "An advisory-only paper sandbox with hypothetical fills was added.",
            "Persistent paper state with duplicate protection; fills are hypothetical and no order adapter exists.",
            "2026-10-30T10:00:00Z",
            "431 passed, 1 skipped (Phase 6)",
            ["No real-money or live order path"],
        ),
        (
            "H",
            "Phase H — Advanced backtesting",
            "A causal, costed backtester with conservative fills was built.",
            "Next-bar fills, transaction costs and conservative tie-breaking; reproducible run ledgers.",
            "2026-10-30T14:00:00Z",
            "431 passed, 1 skipped (Phase 6)",
            ["Historical simulation only; no profitability guarantee"],
        ),
        (
            "I",
            "Phase I — Scheduler",
            "Scheduling work was recorded as a historical roadmap phase.",
            "No src/ scheduler module is present now. scripts/prospective_shadow.py uses the Windows Task "
            "Scheduler externally for a shadow run; that is not a live trading scheduler.",
            "2026-10-31T09:00:00Z",
            "No current scheduler test suite",
            ["Not present as a src/ module in the current filesystem"],
        ),
        (
            "J",
            "Phase J — Dashboard and API",
            "A read-only API and server-rendered dashboard were delivered.",
            "GET-only /api/v1 with versioned envelopes; server-rendered dashboard and sf serve, loopback-only by default.",
            "2026-10-31T13:00:00Z",
            "466 passed, 1 skipped (Phase 7)",
            ["No public exposure by default", "Dashboard shows recorded data only"],
        ),
        (
            "K",
            "Phase K — Security and readiness",
            "The read-only surface was hardened and documented.",
            "GET-only build assertion, path confinement, secret scanning and CORS deny-default plus security tests.",
            "2026-10-31T17:00:00Z",
            "466 passed, 1 skipped (Phase 7)",
            ["Local tests do not approve public deployment"],
        ),
        (
            "L",
            "Phase L — Historical market expansion and validation",
            "Historical data expansion and validation experiments were recorded.",
            "Historical outcomes remain historical. Deleted Phase L artifacts are intentionally not shown as "
            "present; Phase 10 forward validation is the current evidence.",
            "2026-11-05T10:00:00Z",
            "Historical records; not a current suite",
            ["Deleted files are not current evidence"],
        ),
    ]
    for phase, title, summary, details, timestamp, tests, limitations in phases:
        event = _phase_event(phase, title, summary, details, timestamp, tests, limitations)
        # ``blockers_note`` and ``limitations`` are not schema fields; fold them
        # into details so the historical caveats stay visible.
        event.pop("blockers_note", None)
        extra = event.pop("limitations")
        event["details"] = details + "\n\nLimitations: " + "; ".join(extra)
        events.append(event)

    # -- AI Stage 0-5 --------------------------------------------------------
    stages = [
        (
            0,
            "Foundation",
            "2026-11-06T08:00:00Z",
            "2026-11-06T09:30:00Z",
            "PASS",
            71,
            "25 contracts + 27 gateway + 19 storage",
            [
                "src/crypto_engine/ai/contracts.py",
                "src/crypto_engine/ai/gateway.py",
                "src/crypto_engine/storage",
            ],
            [],
        ),
        (
            1,
            "Configuration and routing",
            "2026-11-06T10:00:00Z",
            "2026-11-06T11:15:00Z",
            "PASS",
            22,
            "configuration and routing",
            ["src/crypto_engine/ai/configuration.py", "src/crypto_engine/config/settings.py"],
            ["Some parsed fields remain inert (see M2)"],
        ),
        (
            2,
            "External provider adapter",
            "2026-11-06T12:00:00Z",
            "2026-11-06T14:00:00Z",
            "PASS_WITH_FINDINGS",
            33,
            "offline/mock adapter tests",
            ["src/crypto_engine/ai/providers/openai_compatible.py"],
            [
                "Offline/mock tests passed; a real external API call is not verified.",
                "M2 residual configuration limitations remain documented.",
            ],
        ),
        (
            3,
            "Signal advisory",
            "2026-11-06T15:00:00Z",
            "2026-11-06T16:00:00Z",
            "PASS",
            31,
            "signal advisory",
            ["src/crypto_engine/ai/advisory/signal.py"],
            [],
        ),
        (
            4,
            "Research advisory",
            "2026-11-06T16:30:00Z",
            "2026-11-06T17:30:00Z",
            "PASS",
            24,
            "research advisory",
            ["src/crypto_engine/ai/advisory/research.py"],
            [],
        ),
        (
            5,
            "Hardening and CLI",
            "2026-11-06T04:00:00Z",
            "2026-11-06T05:30:00Z",
            "PASS",
            61,
            "31 architecture + 18 failure/resource + 12 CLI",
            ["src/crypto_engine/ai/composition.py", "src/crypto_engine/cli.py"],
            ["Live provider test NOT PERFORMED", "Dashboard integration was not part of Stage 5"],
        ),
    ]
    for stage, title, started, finished, final_status, passed, groups, files, limitations in stages:
        base = {
            "schema_version": SEED_SCHEMA_VERSION,
            "level": "stage",
            "workstream": "ai_gateway",
            "stage": stage,
            "task_id": f"stage_{stage}",
            "agent": "ml-model-engineer / ai-provider-engineer",
        }
        events.append(
            {
                **base,
                "event_id": f"seed-stage-{stage}-started",
                "timestamp_utc": started,
                "status": "IN_PROGRESS",
                "title": f"Stage {stage} — {title} started",
                "summary": f"Implementation of Stage {stage} ({title}) began under recorded authorization.",
                "details": "Stage scope followed the Stage 0-5 implementation plan; no live provider call was made.",
                "files": files,
                "blockers": [],
                "next_action": f"Complete Stage {stage} focused tests.",
                "simple": {
                    "what": f"Work started on Stage {stage}: {title}.",
                    "why": "The AI layer is built in small, separately verifiable stages.",
                    "working": "Implementation started; not yet verified.",
                    "not_verified": "Everything until the focused tests complete.",
                    "next": "Run the Stage focused tests.",
                },
            }
        )
        events.append(
            {
                **base,
                "event_id": f"seed-stage-{stage}-finished",
                "timestamp_utc": finished,
                "status": final_status,
                "title": f"Stage {stage} — {title} recorded result",
                "summary": f"Recorded Stage {stage} focused evidence: {passed} passed ({groups}).",
                "details": (
                    "This is the last recorded Stage report, not a fresh execution. "
                    "A live external provider call was not performed."
                ),
                "files": files,
                "tests": {
                    "passed": passed,
                    "failed": 0,
                    "skipped": 0,
                    "total": passed,
                    "command": f"uv run --offline pytest tests/unit/test_stage{stage}_*.py -q",
                    "report": f"recorded Stage {stage} report",
                },
                "evidence": [f"Recorded Stage {stage} report (2026-11-07)"],
                "blockers": [],
                "next_action": limitations[0]
                if limitations
                else f"Proceed after Stage {stage} review.",
                "simple": {
                    "what": f"Stage {stage} ({title}) finished its focused checks.",
                    "why": "Focused tests prove the stage works in isolation before integration.",
                    "working": f"Offline focused tests passed: {passed}.",
                    "not_verified": "A real external provider request has not been tested.",
                    "next": "Continue to the next stage or the acceptance audit.",
                },
                "technical": {
                    "commands": [f"uv run --offline pytest tests/unit/test_stage{stage}_*.py -q"],
                },
            }
        )

    # -- live provider NOT_VERIFIED -----------------------------------------
    events.append(
        {
            "schema_version": SEED_SCHEMA_VERSION,
            "event_id": "seed-live-provider-not-verified",
            "timestamp_utc": "2026-11-06T05:45:00Z",
            "level": "verification",
            "workstream": "ai_gateway",
            "stage": 2,
            "task_id": "live_provider_check",
            "status": "NOT_VERIFIED",
            "title": "Live external provider connectivity is not verified",
            "summary": "All Stage 0-5 provider tests are offline/mock. No live AI API call or real credential was used.",
            "details": (
                "Stage 2 passed offline/mock adapter tests (33 passed). A real external API request was "
                "not performed and remains NOT_VERIFIED. No API key was read or used."
            ),
            "evidence": ["Stage 2 recorded report", "docs/architecture/AI_GATEWAY.md §16.9"],
            "blockers": ["No live-provider authorization and no credential were provided."],
            "next_action": "Live connectivity review requires separate explicit approval.",
            "agent": "risk-security-reviewer",
            "simple": {
                "what": "The project has not talked to a real external AI provider yet.",
                "why": "Safety rules forbid live provider calls and API keys without approval.",
                "working": "Offline mock tests pass.",
                "not_verified": "Real provider connectivity and credentials.",
                "next": "A separate live-connectivity approval would be required.",
            },
        }
    )

    # -- recorded test suites ------------------------------------------------
    events.append(
        {
            "schema_version": SEED_SCHEMA_VERSION,
            "event_id": "seed-tests-stage-0-5-focused",
            "timestamp_utc": "2026-11-06T06:00:00Z",
            "level": "test",
            "workstream": "ai_gateway",
            "task_id": "stage_0_5_focused_suite",
            "status": "PASS",
            "title": "Stage 0-5 combined focused suite: 242 passed",
            "summary": "The combined Stage 0-5 focused tests recorded 242 passed, 0 failed, 0 skipped.",
            "details": "71 + 22 + 33 + 31 + 24 + 61 = 242 focused tests across Stages 0-5.",
            "tests": {
                "passed": 242,
                "failed": 0,
                "skipped": 0,
                "total": 242,
                "command": "uv run --offline pytest tests/unit/test_stage0_*.py tests/unit/test_stage1_*.py "
                "tests/unit/test_stage2_*.py tests/unit/test_stage3_*.py tests/unit/test_stage4_*.py "
                "tests/unit/test_stage5_*.py -q",
                "report": "final acceptance audit",
            },
            "evidence": ["Stage 0-5 final acceptance audit (2026-11-07)"],
            "blockers": [],
            "next_action": "Run the full project suite.",
            "agent": "qa-release-engineer",
        }
    )
    events.append(
        {
            "schema_version": SEED_SCHEMA_VERSION,
            "event_id": "seed-tests-full-suite",
            "timestamp_utc": "2026-11-06T06:10:00Z",
            "level": "test",
            "workstream": "ai_gateway",
            "task_id": "full_suite",
            "status": "PASS",
            "title": "Full project suite: 785 passed, 4 skipped, 0 failed",
            "summary": "The last recorded full-suite run reported 785 passed and 4 skipped.",
            "details": (
                "Recorded by the final acceptance audit. Skips are 1 lightgbm test and 3 calibration-lab "
                "tests. Skips are not counted as passes. This is a recorded result, not a fresh run."
            ),
            "tests": {
                "passed": 785,
                "failed": 0,
                "skipped": 4,
                "total": 789,
                "command": "uv run --offline pytest -q",
                "report": "final acceptance audit",
            },
            "evidence": ["Stage 0-5 final acceptance audit (2026-11-07)"],
            "blockers": [],
            "next_action": "Keep the recorded result labelled with its date and source.",
            "agent": "qa-release-engineer",
        }
    )

    # -- acceptance + freeze -------------------------------------------------
    events.append(
        {
            "schema_version": SEED_SCHEMA_VERSION,
            "event_id": "seed-stage-0-5-acceptance",
            "timestamp_utc": "2026-11-06T06:20:00Z",
            "level": "verification",
            "workstream": "ai_gateway",
            "task_id": "stage_0_5_acceptance",
            "status": "PASS",
            "title": "Stage 0-5 final acceptance audit returned ACCEPTED",
            "summary": "The read-only final acceptance audit accepted Stage 0-5 for the authorized scope.",
            "details": (
                "The audit made no implementation changes. M2 was accepted as documented. "
                "Live provider testing was not performed and is not implied by acceptance."
            ),
            "evidence": ["Read-only final acceptance audit (2026-11-07)"],
            "blockers": [],
            "next_action": "Freeze Stage 0-5 and prepare the dashboard handoff.",
            "agent": "quant-lead",
        }
    )
    events.append(
        {
            "schema_version": SEED_SCHEMA_VERSION,
            "event_id": "seed-stage-0-5-frozen",
            "timestamp_utc": "2026-11-06T06:25:00Z",
            "level": "verification",
            "workstream": "ai_gateway",
            "task_id": "stage_0_5_acceptance",
            "status": "FROZEN",
            "title": "Stage 0-5 frozen",
            "summary": "Verdict: ACCEPTED — STAGE 0-5 FROZEN for the authorized implementation scope.",
            "details": (
                "Reopening Stage 0-5 requires explicit user approval. The freeze does not approve live "
                "provider connectivity, public deployment, trading, or consolidation of ai_providers/."
            ),
            "evidence": ["Read-only final acceptance audit (2026-11-07)"],
            "blockers": [],
            "next_action": "Authorized follow-on work only (this progress dashboard).",
            "agent": "quant-lead",
        }
    )

    # -- progress dashboard (current authorized work) -----------------------
    dashboard_base = {
        "schema_version": SEED_SCHEMA_VERSION,
        "level": "task",
        "workstream": "progress_dashboard",
        "phase": "J",
        "task_id": "progress_dashboard",
        "files": [
            "src/crypto_engine/progress",
            "src/crypto_engine/api/progress_routes.py",
            "src/crypto_engine/dashboard/progress",
            "scripts/project_progress.py",
            "tests/unit/test_progress_schema.py",
            "tests/unit/test_progress_ledger.py",
            "tests/integration/test_progress_dashboard.py",
            "docs/PROJECT_PROGRESS_DASHBOARD.md",
            "docs/PROJECT_PROGRESS_TRACKING.md",
        ],
        "blockers": [],
        "agent": "quant-lead",
    }
    events.append(
        {
            **dashboard_base,
            "event_id": "seed-dashboard-planned",
            "timestamp_utc": "2026-11-06T07:00:00Z",
            "status": "PLANNED",
            "title": "Project progress dashboard authorized",
            "summary": (
                "A read-only project progress and development control dashboard was authorized at "
                "/dashboard/progress/ with an append-only progress ledger."
            ),
            "details": (
                "Scope: HTML/CSS/vanilla JavaScript, GET-only endpoints, append-only JSONL ledger, "
                "stdlib recorder. Not authorized: trading, paper changes, risk/strategy/scheduler changes, "
                "live AI calls, provider consolidation, commit or push."
            ),
            "next_action": "Implement the progress schema, ledger, API, frontend, tests and docs.",
            "simple": {
                "what": "Work was approved to build a progress dashboard for the whole project.",
                "why": "The project needs one trustworthy place that shows what is built and what is not.",
                "working": "Approved; implementation not started yet.",
                "not_verified": "Everything in the new dashboard.",
                "next": "Build the progress ledger and dashboard.",
            },
        }
    )
    events.append(
        {
            **dashboard_base,
            "event_id": "seed-dashboard-in-progress",
            "timestamp_utc": "2026-11-06T07:05:00Z",
            "status": "IN_PROGRESS",
            "title": "Progress dashboard implementation started",
            "summary": "The append-only progress ledger, read-only API and dashboard frontend are being implemented.",
            "details": (
                "The ledger lives under data/project_progress/ (events.jsonl + derived manifest.json). "
                "The dashboard reads recorded events only; it never runs tests, git writes, or AI calls."
            ),
            "next_action": "Complete implementation, run integrated checks, then record the verified result.",
            "simple": {
                "what": "The progress dashboard is being built.",
                "why": "So the team and the user can see real, recorded progress instead of guesses.",
                "working": "Under construction; not verified yet.",
                "not_verified": "The dashboard and its checks.",
                "next": "Run the integrated test suite and record the result.",
            },
        }
    )

    return events


__all__ = ["SEED_SCHEMA_VERSION", "seed_events"]
