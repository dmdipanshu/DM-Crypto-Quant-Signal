"""Progress event schema tests (offline, deterministic)."""

from __future__ import annotations

import pytest

from crypto_engine.progress.schema import (
    LEVELS,
    STATUSES,
    ProgressValidationError,
    check_transition,
    normalize_event,
    schema_document,
)

EXPECTED_STATUSES = {
    "NOT_STARTED",
    "PLANNED",
    "IN_PROGRESS",
    "REVIEW",
    "BLOCKED",
    "PASS",
    "PASS_WITH_FINDINGS",
    "FAILED",
    "FROZEN",
    "HISTORICAL",
    "DEFERRED",
    "NOT_VERIFIED",
}


def base_event(**overrides):
    event = {
        "schema_version": 1,
        "event_id": "evt-test-0001",
        "timestamp_utc": "2026-11-06T10:00:00Z",
        "level": "task",
        "workstream": "progress_dashboard",
        "status": "IN_PROGRESS",
        "title": "Test event",
        "summary": "A deterministic test event.",
    }
    event.update(overrides)
    return event


def test_status_values_are_exactly_the_approved_set() -> None:
    assert STATUSES == EXPECTED_STATUSES


def test_levels_cover_the_development_hierarchy() -> None:
    for level in (
        "project",
        "workstream",
        "phase",
        "stage",
        "subsystem",
        "task",
        "step",
        "verification",
        "evidence",
    ):
        assert level in LEVELS


def test_valid_event_is_normalized() -> None:
    normalized = normalize_event(base_event(timestamp_utc="2026-11-06T10:00:00+00:00"))
    assert normalized["timestamp_utc"] == "2026-11-06T10:00:00Z"
    assert normalized["status"] == "IN_PROGRESS"
    assert normalized["schema_version"] == 1


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="timezone"):
        normalize_event(base_event(timestamp_utc="2026-11-06T10:00:00"))


def test_non_utc_offset_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="UTC"):
        normalize_event(base_event(timestamp_utc="2026-11-06T10:00:00+02:00"))


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="unknown field"):
        normalize_event(base_event(unexpected="value"))


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="status must be one of"):
        normalize_event(base_event(status="DONE"))


def test_unknown_level_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="level must be one of"):
        normalize_event(base_event(level="sprint"))


def test_absolute_file_path_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="repository-relative"):
        normalize_event(base_event(files=["C:/Users/secret.txt"]))


def test_parent_traversal_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="repository-relative"):
        normalize_event(base_event(files=["../outside.txt"]))


def test_secret_like_field_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="secret-like"):
        normalize_event(base_event(api_key="value"))


def test_credential_shaped_value_is_rejected() -> None:
    with pytest.raises(ProgressValidationError, match="credential"):
        normalize_event(base_event(details="sk-" + "a" * 24))


def test_tests_counts_must_be_non_negative_integers() -> None:
    with pytest.raises(ProgressValidationError, match=r"tests\.passed"):
        normalize_event(base_event(tests={"passed": -1}))
    with pytest.raises(ProgressValidationError, match=r"tests\.failed"):
        normalize_event(base_event(tests={"failed": True}))


def test_tests_total_cannot_understate_counts() -> None:
    with pytest.raises(ProgressValidationError, match=r"tests\.total"):
        normalize_event(base_event(tests={"passed": 5, "failed": 1, "total": 3}))


def test_simple_and_technical_fields_are_bounded() -> None:
    normalized = normalize_event(
        base_event(
            simple={"what": "Something happened.", "why": "Because it matters."},
            technical={"commands": ["uv run pytest"], "module_paths": ["src/x.py"]},
        )
    )
    assert normalized["simple"]["what"] == "Something happened."
    assert normalized["technical"]["commands"] == ["uv run pytest"]
    with pytest.raises(ProgressValidationError, match=r"simple\.unknown"):
        normalize_event(base_event(simple={"unknown": "x"}))


def test_status_transitions() -> None:
    assert check_transition("PLANNED", "IN_PROGRESS")
    assert check_transition("IN_PROGRESS", "PASS")
    assert check_transition("PASS", "FROZEN")
    assert check_transition("PASS_WITH_FINDINGS", "FROZEN")
    assert check_transition("REVIEW", "PASS_WITH_FINDINGS")
    assert not check_transition("FROZEN", "IN_PROGRESS")
    assert not check_transition("HISTORICAL", "PASS")
    assert not check_transition("PLANNED", "PASS")


def test_schema_document_is_serializable_and_complete() -> None:
    document = schema_document()
    assert document["schema_version"] == 1
    assert set(document["statuses"]) == EXPECTED_STATUSES
    assert "allowed_transitions" in document
    assert "required_fields" in document
