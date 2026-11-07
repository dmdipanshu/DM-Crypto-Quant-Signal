"""Append-only progress ledger tests (offline, tmp directories only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crypto_engine.progress.ledger import (
    EVENTS_FILENAME,
    MANIFEST_FILENAME,
    ProgressLedger,
    parse_utc,
    resolve_progress_root,
    utc_now,
)
from crypto_engine.progress.schema import ProgressValidationError
from crypto_engine.progress.seed_data import seed_events


def make_event(
    event_id: str,
    status: str = "IN_PROGRESS",
    *,
    task_id: str = "task_a",
    workstream: str = "test_ws",
    timestamp: str = "2026-11-06T10:00:00Z",
    **overrides,
):
    event = {
        "schema_version": 1,
        "event_id": event_id,
        "timestamp_utc": timestamp,
        "level": "task",
        "workstream": workstream,
        "task_id": task_id,
        "status": status,
        "title": f"Event {event_id}",
        "summary": "Deterministic ledger test event.",
    }
    event.update(overrides)
    return event


def test_append_and_read_newest_first(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED", timestamp="2026-11-06T10:00:00Z"))
    ledger.append(make_event("e2", "IN_PROGRESS", timestamp="2026-11-06T10:01:00Z"))
    payload = ledger.read_events(limit=10)
    assert payload["available"] is True
    assert payload["total"] == 2
    assert [event["event_id"] for event in payload["events"]] == ["e2", "e1"]


def test_append_only_preserves_existing_bytes(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED"))
    first = ledger.events_path.read_text(encoding="utf-8")
    ledger.append(make_event("e2", "IN_PROGRESS"))
    second = ledger.events_path.read_text(encoding="utf-8")
    assert second.startswith(first)
    assert len(second.splitlines()) == 2


def test_duplicate_event_id_is_rejected(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED"))
    with pytest.raises(ProgressValidationError, match="duplicate event_id"):
        ledger.append(make_event("e1", "IN_PROGRESS"))


def test_skip_existing_is_idempotent(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED"))
    result = ledger.append(make_event("e1", "PLANNED"), skip_existing=True)
    assert result["appended"] is False
    assert result["reason"] == "duplicate_event_id"


def test_invalid_transition_is_rejected(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED"))
    with pytest.raises(ProgressValidationError, match="invalid transition"):
        ledger.append(make_event("e2", "PASS"))


def test_force_allows_transition(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED"))
    result = ledger.append(make_event("e2", "PASS"), enforce_transitions=False)
    assert result["appended"] is True


def test_corrupt_line_is_reported_not_dropped(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED"))
    with ledger.events_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("{not valid json}\n")
    report = ledger.validate()
    assert report["ok"] is False
    assert report["total_records"] == 2
    assert report["valid_events"] == 1
    assert report["corrupt"][0]["line"] == 2
    payload = ledger.read_events(limit=10)
    assert payload["corrupt_count"] == 1


def test_duplicate_lines_are_reported_by_validate(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED"))
    with ledger.events_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(ledger.events_path.read_text(encoding="utf-8").splitlines()[0] + "\n")
    report = ledger.validate()
    assert report["ok"] is False
    assert report["duplicates"]


def test_snapshot_staleness(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress", stale_after_s=60)
    ledger.append(make_event("e1", "IN_PROGRESS", timestamp="2020-01-01T00:00:00Z"))
    assert ledger.snapshot()["stale"] is True
    ledger.append(make_event("e2", "IN_PROGRESS", task_id="task_b", timestamp=utc_now()))
    assert ledger.snapshot()["stale"] is False


def test_snapshot_uninitialized(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    snapshot = ledger.snapshot()
    assert snapshot["available"] is False
    assert snapshot["reason"] == "not_initialized"


def test_seed_is_idempotent(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    first = ledger.seed(seed_events())
    second = ledger.seed(seed_events())
    assert first["appended"] == len(seed_events())
    assert first["errors"] == []
    assert second["appended"] == 0
    assert second["skipped"] == len(seed_events())
    assert ledger.snapshot()["manifest"]["event_count"] == len(seed_events())


def test_manifest_is_written_and_counts_are_derived(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "PLANNED"))
    ledger.append(make_event("e2", "IN_PROGRESS"))
    assert (tmp_path / "progress" / MANIFEST_FILENAME).is_file()
    manifest = ledger.snapshot()["manifest"]
    assert manifest["event_count"] == 2
    assert manifest["metrics"]["active_tasks"] == 1
    assert manifest["metrics"]["pending_tasks"] == 0
    assert manifest["last_event_id"] == "e2"


def test_filters_status_level_stage_and_query(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.seed(seed_events())
    frozen = ledger.read_events(limit=50, status="FROZEN")
    assert frozen["total"] == 1
    tests = ledger.read_events(limit=50, level="test")
    assert tests["total"] == 2
    stage_two = ledger.read_events(limit=50, stage=2)
    assert stage_two["total"] >= 1
    assert all(event.get("stage") == 2 for event in stage_two["events"])
    search = ledger.read_events(limit=50, q="785")
    assert search["total"] == 1


def test_scan_cap_is_bounded(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress", scan_cap=2)
    for index in range(3):
        ledger.append(make_event(f"e{index}", "IN_PROGRESS", task_id=f"task_{index}"))
    payload = ledger.read_events(limit=10)
    assert payload["scanned"] == 2
    assert payload["truncated"] is True


def test_update_git_state_is_persisted(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "IN_PROGRESS"))
    git_state = {
        "available": True,
        "branch": "master",
        "head": "abc123",
        "collected_utc": utc_now(),
    }
    manifest = ledger.update_git_state(git_state)
    assert manifest["git"]["branch"] == "master"
    assert ledger.snapshot()["manifest"]["git"]["head"] == "abc123"


def test_snapshot_does_not_write_when_manifest_is_missing(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    ledger.append(make_event("e1", "IN_PROGRESS"))
    ledger.manifest_path.unlink()
    snapshot = ledger.snapshot()
    assert snapshot["available"] is True
    assert snapshot["manifest_rebuilt"] is True
    assert snapshot["manifest"]["event_count"] == 1
    # A read-only snapshot must not recreate the derived manifest.
    assert not ledger.manifest_path.exists()


def test_resolve_progress_root_confines_to_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    resolved = resolve_progress_root(repo, "data/project_progress")
    assert resolved == (repo / "data" / "project_progress").resolve()
    with pytest.raises(ValueError, match="inside the repository"):
        resolve_progress_root(repo, "../outside")


def test_parse_utc_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="timezone"):
        parse_utc("2026-11-06T10:00:00")
    assert parse_utc("2026-11-06T10:00:00Z").tzinfo is not None


def test_events_filename_constant(tmp_path: Path) -> None:
    ledger = ProgressLedger(tmp_path / "progress")
    assert ledger.events_path.name == EVENTS_FILENAME
