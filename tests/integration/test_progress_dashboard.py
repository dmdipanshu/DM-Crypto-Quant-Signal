"""Progress dashboard + read-only progress API tests (offline, GET-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from crypto_engine.api.app import create_app, list_data_routes  # noqa: E402
from crypto_engine.api.repos import RepoRoots  # noqa: E402
from crypto_engine.config.settings import load_settings  # noqa: E402
from crypto_engine.progress.ledger import ProgressLedger  # noqa: E402
from crypto_engine.progress.seed_data import seed_events  # noqa: E402
from tests.fixtures.api_harness import api_get, api_request  # noqa: E402


def build_app(tmp_path: Path, *, seed: bool = True):
    progress_dir = tmp_path / "progress"
    if seed:
        ProgressLedger(progress_dir).seed(seed_events())
    settings = load_settings()
    roots = RepoRoots(
        signals_dir=tmp_path / "signals",
        registry_dir=tmp_path / "registry",
        data_storage=tmp_path / "data",
        backtest_dir=tmp_path / "backtests",
        paper_path=tmp_path / "paper.json",
        experiments_path=tmp_path / "experiments.jsonl",
        holdout_ledger_path=tmp_path / "holdout.json",
        progress_dir=progress_dir,
        scan_cap=5000,
    )
    return create_app(roots, settings), progress_dir


def test_snapshot_endpoint_reports_recorded_model(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    status, headers, payload = api_get(app, "/api/v1/progress/snapshot")
    assert status == 200
    assert "application/json" in headers.get("content-type", "")
    data = payload["data"]
    assert data["progress"]["available"] is True
    assert data["progress"]["manifest"]["event_count"] == len(seed_events())
    assert len(data["project"]["stages"]) == 6
    assert len(data["project"]["phases"]) == 12
    assert data["project"]["overall"]["status"] == "FROZEN"
    assert payload["meta"]["available"] is True


def test_all_six_stages_are_frozen_with_acceptance_evidence(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, payload = api_get(app, "/api/v1/progress/snapshot")
    stages = payload["data"]["project"]["stages"]
    assert [stage["status"] for stage in stages] == ["FROZEN"] * 6
    for stage in stages:
        assert stage["acceptance_date"] == "2026-11-07"
        assert "ACCEPTED" in stage["acceptance_verdict"]
        assert stage["authority"]
        assert stage["m2"].startswith("M2 ACCEPTED AS DOCUMENTED")
        assert "RESOURCE_BUDGET.md:71" in stage["remaining_finding"]
        assert "CORRECTED" in stage["remaining_finding"]
        assert "not a fresh execution" in stage["evidence_label"]


def test_overall_ai_foundation_acceptance_and_evidence_separation(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, payload = api_get(app, "/api/v1/progress/snapshot")
    overall = payload["data"]["project"]["overall"]
    assert overall["label"] == "AI FOUNDATION — ACCEPTED / FROZEN"
    assert overall["status"] == "FROZEN"
    assert overall["verdict"] == "ACCEPTED — STAGE 0-5 FROZEN"
    assert overall["combined_focused_tests"] == 242
    assert overall["focused_skipped"] == 0
    assert overall["full_suite"]["passed"] == 785
    assert overall["full_suite"]["skipped"] == 4
    assert overall["full_suite"]["failed"] == 0
    assert overall["full_suite"]["errors"] == 0
    assert "831" in overall["full_suite"]["note"]
    additional = overall["additional_verification"]
    assert additional["architecture_security_resource_provider_failure"]["passed"] == 120
    assert "28" in additional["ruff_format"]
    assert additional["sf_ai_test"].startswith("reported disabled")
    assert overall["live_provider_test"] == "NOT PERFORMED"


def test_m2_is_accepted_as_documented_and_inert(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, payload = api_get(app, "/api/v1/progress/snapshot")
    m2 = next(
        finding for finding in payload["data"]["project"]["findings"] if finding["id"] == "M2"
    )
    assert m2["status"] == "ACCEPTED AS DOCUMENTED"
    assert m2["blocking"] is False
    assert m2["fields"] == [
        "default_provider",
        "fallback_order",
        "cache_ttl_s",
        "log_prompts",
        "[ai.budgets]",
        "per-provider max_response_chars",
        "per-provider concurrency",
        "per-task bounds",
    ]
    assert any("header/query" in item for item in m2["behavior"])
    assert any("No runtime M2 wiring is authorized" in item for item in m2["behavior"])
    assert "wired" not in m2["status"].lower()
    assert "completed" not in m2["status"].lower()
    assert "not functioning features" in m2["note"]


def test_doc_resource_finding_is_corrected_and_non_blocking(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, payload = api_get(app, "/api/v1/progress/snapshot")
    finding = next(
        item for item in payload["data"]["project"]["findings"] if item["id"] == "DOC-RESOURCE"
    )
    assert finding["title"] == "RESOURCE_BUDGET.md:71 — stale AI Budget Tracker design row"
    assert finding["status"] == "CORRECTED"
    assert finding["severity"] == "low"
    assert finding["blocking"] is False
    assert finding["corrected"] is True
    assert finding["path"] == "docs/architecture/RESOURCE_BUDGET.md:71"
    assert finding["correction_event_id"] == "evt-20261107T000625Z-f31f2348"
    assert finding["correction_timestamp_utc"] == "2026-11-07T00:06:25Z"
    assert finding["original_event_id"] == "evt-20261106T234507Z-267ecb81"
    assert finding["original_status"] == "PASS_WITH_FINDINGS"
    assert finding["evidence"] == [
        "AI Budget Tracker row corrected.",
        "Runtime-enforced gateway limits documented accurately.",
        "[ai.budgets] explicitly remains reserved/inert.",
        "No runtime budget tracker or paid fallback was implemented.",
        "Legacy BudgetTracker remains isolated in ai_providers/.",
        "AI_GATEWAY.md §16.9 remains consistent.",
        "Documentation-only correction passed 35/35 consistency assertions.",
    ]
    assert "evt-20261107T000625Z-f31f2348" in finding["note"]
    assert "retained in history" in finding["note"]


def test_doc_resource_old_not_performed_text_is_absent(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, payload = api_get(app, "/api/v1/progress/snapshot")
    finding = next(
        item for item in payload["data"]["project"]["findings"] if item["id"] == "DOC-RESOURCE"
    )
    note = finding["note"]
    assert "was NOT performed" not in note
    assert "requires separate authorization" not in note
    assert "still describes" not in note
    assert "pending" not in note.lower()


def test_stage_and_overall_remaining_finding_reference_the_correction(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, payload = api_get(app, "/api/v1/progress/snapshot")
    project = payload["data"]["project"]
    for stage in project["stages"]:
        assert "RESOURCE_BUDGET.md:71" in stage["remaining_finding"]
        assert "CORRECTED" in stage["remaining_finding"]
        assert "not performed" not in stage["remaining_finding"]
    overall = project["overall"]
    assert "CORRECTED" in overall["remaining_finding"]
    assert "evt-20261107T000625Z-f31f2348" in overall["remaining_finding"]
    assert "pending" not in overall["remaining_finding"].lower()


def test_seed_keeps_audit_785_and_never_claims_dashboard_831() -> None:
    """The Stage audit and the dashboard run stay separate evidence records."""
    events = seed_events()
    full_suite_events = [event for event in events if event.get("task_id") == "full_suite"]
    assert len(full_suite_events) == 1
    assert full_suite_events[0]["tests"]["passed"] == 785
    assert full_suite_events[0]["tests"]["skipped"] == 4
    assert all(event.get("tests", {}).get("passed") != 831 for event in events)

    focused_events = [
        event for event in events if event.get("task_id") == "stage_0_5_focused_suite"
    ]
    assert len(focused_events) == 1
    assert focused_events[0]["tests"]["passed"] == 242
    # The recorded counts are never merged into a single combined total.
    assert 785 + 242 not in [event.get("tests", {}).get("passed") for event in events]


def test_events_endpoint_filters(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    status, _, payload = api_get(app, "/api/v1/progress/events", "status=FROZEN&limit=10")
    assert status == 200
    assert payload["data"]["total"] == 1
    assert payload["data"]["events"][0]["status"] == "FROZEN"

    status, _, payload = api_get(app, "/api/v1/progress/events", "level=test&limit=10")
    assert status == 200
    assert payload["data"]["total"] == 2

    status, _, payload = api_get(app, "/api/v1/progress/events", "q=785&limit=10")
    assert status == 200
    assert payload["data"]["total"] == 1


def test_history_endpoint_is_bounded(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    status, _, payload = api_get(app, "/api/v1/progress/history", "limit=5")
    assert status == 200
    assert len(payload["data"]["events"]) == 5
    assert payload["meta"]["limit"] == 5


def test_invalid_status_and_timestamp_are_rejected(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    status, _, payload = api_get(app, "/api/v1/progress/events", "status=DONE")
    assert status == 400
    assert payload["error"]["code"] == "invalid_status"

    status, _, payload = api_get(app, "/api/v1/progress/events", "since=not-a-time")
    assert status == 400
    assert payload["error"]["code"] == "invalid_timestamp"


def test_progress_endpoints_are_get_only(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    for path in (
        "/api/v1/progress/snapshot",
        "/api/v1/progress/events",
        "/api/v1/progress/history",
    ):
        status, _, _ = api_request(app, "POST", path, body=b"{}")
        assert status == 405
        status, _, _ = api_request(app, "DELETE", path)
        assert status == 405


def test_all_progress_routes_are_get(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    progress_routes = [
        (path, methods) for path, methods in list_data_routes(app) if "progress" in path
    ]
    assert progress_routes
    assert all(methods == {"GET"} for _, methods in progress_routes)


def test_dashboard_page_renders_with_csp_and_nav(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    status, headers, body = api_get(app, "/dashboard/progress/")
    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    csp = headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp
    for label in ("Overview", "Project Roadmap", "Development Timeline", "AI Stages", "Glossary"):
        assert label in body
    assert "/dashboard/progress/progress.js" in body
    assert "/dashboard/progress/progress.css" in body
    lowered = body.lower()
    assert "<form" not in lowered
    assert "place order" not in lowered


def test_progress_assets_are_served_and_safe(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    status, headers, css = api_get(app, "/dashboard/progress/progress.css")
    assert status == 200
    assert "text/css" in headers.get("content-type", "")
    assert "--bg:" in css

    status, headers, js = api_get(app, "/dashboard/progress/progress.js")
    assert status == 200
    assert "javascript" in headers.get("content-type", "")
    assert "textContent" in js
    assert "innerHTML" not in js
    assert "document.write" not in js
    assert "http://" not in js and "https://" not in js
    assert "AI FOUNDATION" in js
    assert "ACCEPTED AS DOCUMENTED" in js


def test_progress_assets_render_corrected_finding_in_both_views(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, css = api_get(app, "/dashboard/progress/progress.css")
    assert '.chip[data-status="CORRECTED"]' in css

    _, _, js = api_get(app, "/dashboard/progress/progress.js")
    assert "function findingCard(" in js
    assert "SIMPLE_FINDING_TEXT" in js
    assert "The outdated budget-tracker documentation has been corrected." in js
    assert "still does not enforce runtime AI budgets or paid fallback" in js
    assert "AI Stage 0\u20135 remains accepted and frozen." in js
    assert "Correction evidence" in js
    assert "Correction event" in js
    assert "Correction time (UTC)" in js
    assert "Original event" in js
    assert "Original status" in js
    assert "Reserved / not enforced" in js
    assert "Enforced instead" in js
    # Event/timestamp details are Technical-view only; evidence is always shown.
    assert (
        "if (technical && (finding.path || finding.correction_event_id || finding.original_event_id))"
        in js
    )


def test_dashboard_page_has_no_inline_handlers_or_scripts(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, body = api_get(app, "/dashboard/progress/")
    assert "onclick=" not in body.lower()
    assert "onerror=" not in body.lower()
    assert "<script>" not in body.lower()
    assert 'src="/dashboard/progress/progress.js"' in body


def test_existing_dashboard_still_renders_and_links_progress(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    status, _, body = api_get(app, "/dashboard/")
    assert status == 200
    assert "SignalForge" in body
    assert "/dashboard/progress/" in body


def test_progress_responses_do_not_expose_secrets(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    _, _, payload = api_get(app, "/api/v1/progress/snapshot")
    serialized = json.dumps(payload)
    for needle in ("api_key", "apikey", "secret=", "Bearer ", "sk-proj-", "AKIA"):
        assert needle not in serialized
    assert "C:\\\\" not in serialized


def test_unknown_progress_path_is_not_served(tmp_path: Path) -> None:
    app, _ = build_app(tmp_path)
    status, _, _ = api_get(app, "/dashboard/progress/../progress.js")
    assert status in (307, 404)
    status, _, _ = api_get(app, "/dashboard/progress/unknown.txt")
    assert status == 404


def test_stale_snapshot_is_flagged(tmp_path: Path) -> None:
    progress_dir = tmp_path / "progress"
    ledger = ProgressLedger(progress_dir, stale_after_s=60)
    ledger.append(
        {
            "schema_version": 1,
            "event_id": "old-event",
            "timestamp_utc": "2020-01-01T00:00:00Z",
            "level": "task",
            "workstream": "test_ws",
            "task_id": "old",
            "status": "IN_PROGRESS",
            "title": "Old event",
            "summary": "An old event.",
        }
    )
    settings = load_settings()
    roots = RepoRoots(progress_dir=progress_dir)
    app = create_app(roots, settings)
    _, _, payload = api_get(app, "/api/v1/progress/snapshot")
    assert payload["data"]["progress"]["stale"] is True
    assert payload["meta"]["stale"] is True


def test_event_text_is_returned_as_data_not_html(tmp_path: Path) -> None:
    app, progress_dir = build_app(tmp_path)
    ledger = ProgressLedger(progress_dir)
    ledger.append(
        {
            "schema_version": 1,
            "event_id": "xss-event",
            "timestamp_utc": "2026-11-06T20:00:00Z",
            "level": "task",
            "workstream": "test_ws",
            "task_id": "xss",
            "status": "IN_PROGRESS",
            "title": "<img src=x onerror=alert(1)>",
            "summary": "<script>alert(1)</script>",
        }
    )
    status, _, payload = api_get(app, "/api/v1/progress/events", "q=alert&limit=5")
    assert status == 200
    assert payload["data"]["events"][0]["title"] == "<img src=x onerror=alert(1)>"
    # The browser never receives this as HTML: the page uses textContent.
    _, _, page = api_get(app, "/dashboard/progress/")
    assert "<img src=x" not in page
