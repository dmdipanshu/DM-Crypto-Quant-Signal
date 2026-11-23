"""Mode-aware health semantics regression tests (Q9 hardening)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from crypto_engine.config.settings import DeploymentConfig, PaperConfig, Settings
from crypto_engine.observability import health as health_mod
from crypto_engine.observability.health import (
    HealthStatus,
    check_paper_account,
    check_pipeline_freshness,
    check_storage,
)


def _settings(tmp_path: Path, **paper_kwargs: Any) -> Settings:
    storage = tmp_path / "data"
    storage.mkdir(parents=True, exist_ok=True)
    return Settings(
        data={"storage_path": str(storage)},
        signal={"history_path": str(tmp_path / "signals")},
        paper=PaperConfig(account_path=str(tmp_path / "paper"), **paper_kwargs),
        deployment=DeploymentConfig(trading_authorized=False, live_orders_enabled=False),
    )


def _make_unreadable(path: Path) -> tuple[int | None, int | None]:
    if os.name == "nt":
        return None, os.stat(path).st_mode
    mode = path.stat().st_mode
    path.chmod(0o000)
    return mode, None


def _restore_path(path: Path, mode: int | None, win_mode: int | None) -> None:
    if os.name == "nt":
        if win_mode is not None:
            os.chmod(path, win_mode)
        return
    if mode is not None:
        path.chmod(mode)


def test_storage_read_only_readable_not_writable_is_healthy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = Path(settings.data.storage_path)
    mode, win_mode = (storage.stat().st_mode, None)
    storage.chmod(0o555)
    try:
        status, message, details = check_storage(settings, require_writable=False)
        assert status is HealthStatus.HEALTHY
        assert "readable" in message.lower()
        assert details["exists"] is True
        assert details["readable"] is True
        if os.name != "nt":
            assert details["writable"] is False
        assert details["require_writable"] is False
    finally:
        _restore_path(storage, mode, win_mode)


def test_storage_genuine_missing_is_unhealthy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    missing = tmp_path / "missing-storage"
    settings = Settings(
        data={"storage_path": str(missing)},
        signal={"history_path": str(tmp_path / "signals")},
        paper=PaperConfig(account_path=str(tmp_path / "paper")),
    )
    status, message, details = check_storage(settings, require_writable=False)
    assert status is HealthStatus.UNHEALTHY
    assert "missing" in message
    assert details["exists"] is False


def test_storage_writable_required_unwritable_is_unhealthy(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    storage = Path(settings.data.storage_path)
    mode, win_mode = (storage.stat().st_mode, None)
    storage.chmod(0o555)
    try:
        status, message, details = check_storage(settings, require_writable=True)
        if os.name == "nt":
            # Windows chmod does not reliably remove write permissions.
            assert status in {HealthStatus.HEALTHY, HealthStatus.UNHEALTHY}
            assert details["require_writable"] is True
        else:
            assert status is HealthStatus.UNHEALTHY
            assert "not writable" in message
            assert details["require_writable"] is True
    finally:
        _restore_path(storage, mode, win_mode)


def test_paper_disabled_missing_account_not_applicable(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=False)
    status, message, details = check_paper_account(settings)
    assert status is HealthStatus.HEALTHY
    assert "not applicable" in message
    assert details["applicable"] is False


def test_paper_authorized_enabled_missing_account_unhealthy(tmp_path: Path) -> None:
    settings = Settings(
        data={"storage_path": str(tmp_path / "data")},
        signal={"history_path": str(tmp_path / "signals")},
        paper=PaperConfig(account_path=str(tmp_path / "paper"), enabled=True),
        deployment=DeploymentConfig(trading_authorized=True, live_orders_enabled=False),
    )
    Path(settings.data.storage_path).mkdir(parents=True, exist_ok=True)
    status, message, details = check_paper_account(settings)
    assert status is HealthStatus.UNHEALTHY
    assert "missing" in message
    assert details["applicable"] is True


def test_pipeline_not_started_zero_observations_not_unhealthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        health_mod,
        "_get_prospective_lifecycle_state",
        lambda: {
            "lifecycle_state": "NOT_STARTED",
            "observation_count": 0,
            "collection_started_at_utc": None,
            "collection_start_anchor_status": "NOT_STARTED",
        },
    )
    status, message, details = check_pipeline_freshness(settings)
    assert status is HealthStatus.HEALTHY
    assert "not started" in message.lower()
    assert details["research_ready"] is False
    assert details["collection_lifecycle_state"] == "NOT_STARTED"
    assert details["observation_count"] == 0


def test_pipeline_active_missing_evidence_unhealthy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        health_mod,
        "_get_prospective_lifecycle_state",
        lambda: {
            "lifecycle_state": "ACCUMULATING",
            "observation_count": 5,
            "collection_started_at_utc": "2026-11-22T08:00:00+00:00",
            "collection_start_anchor_status": "VALID",
        },
    )
    status, _message, details = check_pipeline_freshness(settings)
    assert status in {HealthStatus.DEGRADED, HealthStatus.UNHEALTHY}
    assert details["research_ready"] is False
    assert details["collection_lifecycle_state"] == "ACCUMULATING"


def test_pipeline_accumulating_one_observation_missing_signal_history_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACCUMULATING with accepted evidence but no signal history is a readiness gap, not API failure."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        health_mod,
        "_get_prospective_lifecycle_state",
        lambda: {
            "lifecycle_state": "ACCUMULATING",
            "observation_count": 1,
            "collection_started_at_utc": "2026-11-22T20:08:04.533609+00:00",
            "collection_start_anchor_status": "VALID",
        },
    )
    (tmp_path / "data" / "prospective" / "binance" / "spot" / "BTC" / "USDT" / "1h").mkdir(
        parents=True, exist_ok=True
    )
    manifest = (
        tmp_path
        / "data"
        / "prospective"
        / "binance"
        / "spot"
        / "BTC"
        / "USDT"
        / "1h"
        / "dataset_manifest.json"
    )
    manifest.write_text(
        '{"collection_started_at_utc":"2026-11-22T20:08:04.533609+00:00","observation_count":1}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    status, message, details = check_pipeline_freshness(settings)
    assert status is HealthStatus.DEGRADED
    assert "research readiness pending" in message.lower()
    assert details["research_ready"] is False
    assert details["api_operational_ready"] is True
    assert details["collection_lifecycle_state"] == "ACCUMULATING"
    assert details["observation_count"] == 1

def test_aggregate_health_read_only_not_started_is_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        health_mod,
        "_get_prospective_lifecycle_state",
        lambda: {
            "lifecycle_state": "NOT_STARTED",
            "observation_count": 0,
            "collection_started_at_utc": None,
            "collection_start_anchor_status": "NOT_STARTED",
        },
    )
    checker = health_mod.HealthChecker()
    checker.register_check("storage", lambda: check_storage(settings, require_writable=False))
    checker.register_check("paper_account", lambda: check_paper_account(settings))
    checker.register_check("pipeline_freshness", lambda: check_pipeline_freshness(settings))
    assert checker.overall_status() is HealthStatus.HEALTHY
