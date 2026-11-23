"""Q5 dashboard/API status reconciliation tests (read-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from crypto_engine.api.repos import (
    RepoRoots,
    read_q5_collection_status,
    read_q5_prospective_governance,
)


def _roots(tmp_path: Path) -> RepoRoots:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    return RepoRoots(
        signals_dir=tmp_path / "signals",
        registry_dir=tmp_path / "registry",
        data_storage=data,
        backtest_dir=tmp_path / "backtests",
        paper_path=tmp_path / "paper" / "account.json",
        experiments_path=tmp_path / "experiments.jsonl",
        holdout_ledger_path=tmp_path / "holdout.json",
    )


def _write_latest_status(tmp_path: Path, payload: dict[str, Any]) -> Path:
    p = tmp_path / "reports" / "q5-epoch3" / "latest_status.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _write_approval(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    p = tmp_path / "docs" / "validation" / "approvals" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class _FakeCollector:
    def __init__(self, *args, **kwargs):
        pass

    collection_id = "collect-epoch3-4e1b55cd-ee07-4f2a-8f4c-d0be0e848b2d"
    exchange = "binance"
    market_type = "spot"
    symbols: ClassVar[list[str]] = ["BTC/USDT"]
    timeframes: ClassVar[list[str]] = ["1h"]
    policy = type("P", (), {
        "min_observation_count": 168,
        "min_coverage_fraction": 0.95,
        "min_elapsed_observation_period_s": 7 * 86400,
    })()
    collection_manifest_path = "docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json"

    def verify_collection_integrity(self):
        return {
            "collection_id": self.collection_id,
            "status": "VALID",
            "reason": "ok",
            "associated_freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
            "associated_freeze_timestamp": "2026-11-22T08:00:00+00:00",
        }

    def validate_freeze(self):
        return {
            "status": "VALID",
            "freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
            "created_at_utc": "2026-11-22T08:00:00+00:00",
        }

    def validate_or_create_collection_manifest(self):
        return None


class _FakeStorage:
    def __init__(self, *args, **kwargs):
        pass

    def read_dataset_manifest(self):
        return {
            "observation_count": 12,
            "coverage_fraction": 0.99,
            "latest_open_time_utc": "2026-11-23T06:00:00+00:00",
        }

    def verify_ledger_chain(self):
        return {"valid": True, "event_count": 12}

    def verify_collection_start_anchor(self):
        return {
            "status": "VALID",
            "collection_started_at_utc": "2026-11-22T20:08:04.533609+00:00",
            "anchor_hash_valid": True,
            "anchor": {"anchor_hash": "abc"},
        }


def _patch_q5(monkeypatch, tmp_path: Path, *, last_status: dict[str, Any] | None = None, approval: dict[str, Any] | None = None, no_approval: bool = False):
    import crypto_engine.research.prospective.collector as collector_mod
    import crypto_engine.research.prospective.storage as storage_mod

    monkeypatch.setattr(collector_mod, "ProspectiveCollector", _FakeCollector)
    monkeypatch.setattr(storage_mod, "ProspectiveStorage", _FakeStorage)

    # Ensure manifests referenced by collector exist for legacy fallback paths.
    (tmp_path / "docs" / "validation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "validation" / "PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json").write_text(
        json.dumps({
            "collection_id": _FakeCollector.collection_id,
            "collection_started_at_utc": "2026-11-22T20:08:04.533609+00:00",
        }),
        encoding="utf-8",
    )

    if last_status is not None:
        _write_latest_status(tmp_path, last_status)
    if approval is not None:
        _write_approval(tmp_path, "collection-epoch3-authorization.json", approval)
    if no_approval:
        # no approvals written
        pass
    monkeypatch.chdir(tmp_path)


def _active_approval(*, collection_authorized=True, scheduler_authorized=False):
    return {
        "approval_id": "appr-1",
        "status": "active",
        "collection_id": _FakeCollector.collection_id,
        "freeze_id": "freeze-epoch3-ba777bcd-df63-4cfe-9aa3-18dde1fef46f",
        "collection_authorized": collection_authorized,
        "scheduler_authorized": scheduler_authorized,
        "revoked_at_utc": None,
        "valid_from_utc": "2026-11-22T00:00:00+00:00",
        "expires_at_utc": None,
    }


def test_governance_not_authorized(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path, no_approval=True)
    gov = read_q5_prospective_governance(_roots(tmp_path))
    assert gov["collection_authorized"] is False
    assert gov["scheduler_authorized"] is False
    assert gov["collection_authorization"]["authorized"] is False
    assert gov["scheduler_authorization_detail"]["authorized"] is False


def test_governance_authorized_collection_only(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path, approval=_active_approval(collection_authorized=True, scheduler_authorized=False))
    gov = read_q5_prospective_governance(_roots(tmp_path))
    assert gov["collection_authorized"] is True
    assert gov["scheduler_authorized"] is False
    assert gov["collection_authorization"]["active"] is True
    assert gov["collection_authorization"]["revoked"] is False


def test_governance_authorized_collection_and_scheduler(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path, approval=_active_approval(collection_authorized=True, scheduler_authorized=True))
    gov = read_q5_prospective_governance(_roots(tmp_path))
    assert gov["collection_authorized"] is True
    assert gov["scheduler_authorized"] is True
    assert gov["scheduler_authorization_detail"]["authorized"] is True


def test_collection_status_includes_runtime_unknown_and_schedule(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path, approval=_active_approval(collection_authorized=True, scheduler_authorized=True))
    status = read_q5_collection_status(_roots(tmp_path))
    assert status["timer_runtime_state"] == "UNKNOWN"
    assert status["next_scheduled_run_utc"] is None
    assert status["next_collection_window"] == "UNKNOWN"
    assert status["schedule"]["primary_minute_utc"] == 8
    assert status["schedule"]["retry_minute_utc"] == 12
    assert status["schedule"]["persistent"] is False
    assert status["collector_process_state"] == "UNKNOWN"
    assert status["collection_evidence_state"] == "PRESENT"


def test_latest_observation_utc_from_manifest(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path)
    status = read_q5_collection_status(_roots(tmp_path))
    assert status["latest_observation_utc"] == "2026-11-23T06:00:00+00:00"
    assert status["latest_observation"] == "2026-11-23T06:00:00+00:00"


def test_latest_status_success(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path, last_status={
        "schema": "q5-latest-status.v1",
        "completed_at_utc": "2026-11-23T06:08:10+00:00",
        "slot_utc": "2026-11-23T06:00:00+00:00",
        "scheduled_minute": 8,
        "status": "ACCEPTED",
        "exit_code": 0,
        "run_observation_count": 1,
        "cumulative_observation_count": 12,
        "accepted_observation_for_slot": True,
        "retry_skipped": False,
        "observation_count": 12,
    })
    status = read_q5_collection_status(_roots(tmp_path))
    assert status["last_execution"]["status"] == "ACCEPTED"
    assert status["last_execution"]["exit_code"] == 0


def test_latest_status_failed_data_invalid(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path, last_status={
        "schema": "q5-latest-status.v1",
        "completed_at_utc": "2026-11-23T07:08:10+00:00",
        "slot_utc": "2026-11-23T07:00:00+00:00",
        "scheduled_minute": 8,
        "status": "DATA_INVALID",
        "exit_code": 12,
        "run_observation_count": 0,
        "cumulative_observation_count": 12,
        "accepted_observation_for_slot": False,
        "retry_skipped": False,
        "observation_count": 12,
    })
    status = read_q5_collection_status(_roots(tmp_path))
    assert status["last_execution"]["status"] == "DATA_INVALID"
    assert status["last_execution"]["exit_code"] == 12
    assert status["manifest_status"] == "VALID"


def test_latest_status_missing(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path)
    status = read_q5_collection_status(_roots(tmp_path))
    assert status["last_execution"] is None
    assert status["last_execution_reason"] == "not_found"


def test_last_execution_invalid_json_does_not_crash(monkeypatch, tmp_path):
    _patch_q5(monkeypatch, tmp_path)
    p = tmp_path / "reports" / "q5-epoch3" / "latest_status.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not-json", encoding="utf-8")
    status = read_q5_collection_status(_roots(tmp_path))
    assert status["last_execution"] is None
    assert "invalid_json" in str(status["last_execution_reason"])


def test_no_collection_evidence_not_started(monkeypatch, tmp_path):
    class EmptyStorage(_FakeStorage):
        def read_dataset_manifest(self):
            return {"observation_count": 0, "coverage_fraction": 0.0, "latest_open_time_utc": None}
        def verify_ledger_chain(self):
            return {"valid": False, "event_count": 0}
        def verify_collection_start_anchor(self):
            return {"status": "MISSING", "collection_started_at_utc": None, "anchor_hash_valid": False, "anchor": None}

    import crypto_engine.research.prospective.collector as collector_mod
    import crypto_engine.research.prospective.storage as storage_mod
    monkeypatch.setattr(collector_mod, "ProspectiveCollector", _FakeCollector)
    monkeypatch.setattr(storage_mod, "ProspectiveStorage", EmptyStorage)
    monkeypatch.chdir(tmp_path)

    status = read_q5_collection_status(_roots(tmp_path))
    assert status["collection_evidence_state"] == "NOT_STARTED"
    assert status["collector_process_state"] == "UNKNOWN"
