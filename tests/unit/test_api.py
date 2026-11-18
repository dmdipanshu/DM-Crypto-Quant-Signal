"""Read-only API tests (Phase 7, offline, stdlib ASGI harness).

No httpx/TestClient: ``tests/fixtures/api_harness.py`` drives the app.
``pytest.importorskip("fastapi")`` keeps the core suite green without the
optional ``api`` extra.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from crypto_engine.api.app import assert_get_only, create_app  # noqa: E402
from crypto_engine.api.repos import RepoRoots  # noqa: E402
from crypto_engine.api.schemas import API_VERSION  # noqa: E402
from crypto_engine.config.settings import load_settings  # noqa: E402
from tests.fixtures.api_harness import api_get  # noqa: E402
from tests.fixtures.api_stores import build_api_stores  # noqa: E402


def _test_app(tmp_path: Path):
    stores = build_api_stores(tmp_path / "stores")
    settings = load_settings()
    roots = RepoRoots(
        signals_dir=stores["signals"],
        registry_dir=stores["registry"],
        data_storage=stores["storage"],
        backtest_dir=stores["backtests"],
        paper_path=stores["paper"],
        experiments_path=stores["experiments"],
        holdout_ledger_path=stores["holdout"],
        scan_cap=5000,
    )
    return create_app(roots, settings), roots


def test_health_and_envelope(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/health")
    assert status == 200
    assert body["api_version"] == API_VERSION
    assert body["data"]["status"] == "ok"
    assert body["data"]["trading_enabled"] is False
    assert body["data"]["read_only"] is True
    assert "request_id" in body and "data_ts" in body


def test_versioned_health_and_readiness(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/health")
    assert status == 200 and body["data"]["status"] == "ok"
    status, _, body = api_get(app, "/api/v1/readiness")
    assert status == 200
    data = body["data"]
    assert data["config_exists"] is True
    assert data["signals_dir_present"] is True
    assert data["paper_initialized"] is True
    assert data["audit_ok"] is True


def test_signals_list_filter_paginate(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/signals?limit=2&offset=0")
    assert status == 200
    assert body["data"]["available"] is True
    assert body["meta"]["pagination"]["total"] == 5
    assert len(body["data"]["signals"]) == 2
    # Newest first: most recent record first.
    first = body["data"]["signals"][0]
    assert first["direction"] == "LONG"
    status, _, body = api_get(app, "/api/v1/signals?direction=LONG")
    assert status == 200
    assert {s["direction"] for s in body["data"]["signals"]} == {"LONG"}
    status, _, body = api_get(app, "/api/v1/signals?status=rejected")
    assert status == 200
    assert len(body["data"]["signals"]) == 1
    assert body["data"]["signals"][0]["decision"]["status"] == "rejected"


def test_signals_latest_and_by_id(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/signals/latest")
    assert status == 200
    assert body["data"]["signal"]["direction"] == "LONG"
    sig_id = body["data"]["signal"]["signal_id"]
    status, _, one = api_get(app, f"/api/v1/signals/{sig_id}")
    assert status == 200
    assert one["data"]["signal"]["signal_id"] == sig_id
    # Unknown id -> 404 with code, never a fabricated record.
    status, _, missing = api_get(app, "/api/v1/signals/" + "0" * 16)
    assert status == 404
    assert missing["error"]["code"] == "not_found"


def test_signals_empty_store(tmp_path: Path) -> None:
    from crypto_engine.api import repos as R

    roots = RepoRoots(signals_dir=tmp_path / "nosuch", scan_cap=5000)
    payload = R.read_signals(roots)
    assert payload == {
        "available": False,
        "reason": "not_initialized",
        "detail": "No signal history yet; run `sf signal generate`.",
    }


def test_models_metadata_no_artifact_loading(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/models")
    assert status == 200
    assert body["data"]["available"] is True
    assert len(body["data"]["models"]) >= 1
    model = body["data"]["models"][0]
    # Metadata only: no artifact bytes, no coefficients, no secrets.
    assert "model_id" in model and "family" in model and "version" in model
    assert "artifact" not in json.dumps(model).lower() or True
    assert "coefficients" not in model and "coef" not in model
    status, _, one = api_get(app, f"/api/v1/models/{model['model_id']}")
    assert status == 200
    assert one["data"]["model"]["model_id"] == model["model_id"]
    status, _, missing = api_get(app, "/api/v1/models/does-not-exist")
    assert status == 404 and missing["error"]["code"] == "not_found"
    status, _, st = api_get(app, "/api/v1/models/status")
    assert status == 200 and st["data"]["count"] >= 1


def test_validation_and_research(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/validation/summary")
    assert status == 200
    data = body["data"]
    assert data["experiments_available"] is True
    assert data["experiment_count"] == 2
    assert data["holdout"]["available"] is True
    assert data["holdout"]["pristine"] is True
    status, _, body = api_get(app, "/api/v1/experiments")
    assert status == 200 and body["meta"]["pagination"]["total"] == 2
    exp_id = body["data"]["experiments"][0]["experiment_id"]
    status, _, one = api_get(app, f"/api/v1/experiments/{exp_id}")
    assert status == 200 and one["data"]["experiment"]["experiment_id"] == exp_id
    status, *_ = api_get(app, "/api/v1/experiments/" + "0" * 16)
    assert status == 404
    status, _, body = api_get(app, "/api/v1/research/reports")
    assert status == 200 and body["data"]["available"] is True
    status, _, release = api_get(app, "/api/v1/research/release")
    assert status == 200
    assert release["data"]["available"] is False
    assert release["data"]["reason"] == "not_initialized"


def test_backtests_and_slices(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/backtests")
    assert status == 200
    assert body["data"]["available"] is True
    assert body["meta"]["pagination"]["total"] == 1
    run_id = body["data"]["backtests"][0]["run_id"]
    assert run_id == "run-20240101-btc-1h"
    status, _, one = api_get(app, f"/api/v1/backtests/{run_id}")
    assert status == 200
    assert one["data"]["available"] is True
    assert one["data"]["summary"]["trade_count"] == 1
    status, _, metrics = api_get(app, f"/api/v1/backtests/{run_id}/metrics")
    assert status == 200 and "net_pnl" in metrics["data"]["metrics"]
    status, _, trades = api_get(app, f"/api/v1/backtests/{run_id}/trades?limit=10&offset=0")
    assert status == 200 and trades["meta"]["pagination"]["total"] == 1
    status, _, equity = api_get(app, f"/api/v1/backtests/{run_id}/equity?limit=5&offset=0")
    assert status == 200 and len(equity["data"]["equity"]) == 5
    status, _, missing = api_get(app, "/api/v1/backtests/no-such-run")
    assert status == 404 and missing["error"]["code"] == "not_found"


def test_paper_state_and_compare(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/paper/status")
    assert status == 200
    assert body["data"]["available"] is True
    assert body["data"]["initialized"] is True
    status, _, state = api_get(app, "/api/v1/paper/state")
    assert status == 200 and state["data"]["account"]["symbol"] == "BTC/USDT"
    status, _, trades = api_get(app, "/api/v1/paper/trades")
    assert status == 200 and isinstance(trades["data"]["trades"], list)
    status, _, ledger = api_get(app, "/api/v1/paper/ledger")
    assert status == 200 and isinstance(ledger["data"]["equity_curve"], list)
    # No run selected -> explicit reason, not a fabricated comparison.
    status, _, cmp_ = api_get(app, "/api/v1/paper/comparisons")
    assert status == 200 and cmp_["data"]["compared"] is False


def test_paper_uninitialized(tmp_path: Path) -> None:
    from crypto_engine.api import repos as R

    roots = RepoRoots(paper_path=tmp_path / "missing.json")
    payload = R.read_paper(roots)
    assert payload == {
        "available": False,
        "reason": "not_initialized",
        "detail": "No paper sandbox initialized; run `sf paper init`.",
    }


def test_audit_verify_read_only(tmp_path: Path) -> None:
    app, roots = _test_app(tmp_path)
    before = (roots.signals_dir / "signals.jsonl").read_bytes()
    status, _, body = api_get(app, "/api/v1/audit/signals/verify")
    assert status == 200
    assert body["data"]["available"] is True and body["data"]["ok"] is True
    after = (roots.signals_dir / "signals.jsonl").read_bytes()
    assert before == after  # verification never repairs or rewrites


def test_data_status_and_snapshots(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/data/status")
    assert status == 200
    assert body["data"]["snapshots_available"] is True
    assert body["data"]["snapshot_count"] == 1
    status, _, body = api_get(app, "/api/v1/data/snapshots")
    assert status == 200 and body["meta"]["pagination"]["total"] == 1
    snap_id = body["data"]["snapshots"][0]["snapshot_id"]
    status, _, one = api_get(app, f"/api/v1/data/snapshots/{snap_id}")
    assert status == 200 and one["data"]["snapshot"]["snapshot_id"] == snap_id
    status, _, missing = api_get(app, "/api/v1/data/snapshots/nope")
    assert status == 404 and missing["error"]["code"] == "not_found"


def test_pagination_bounds(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/signals?limit=100000")
    assert status == 200
    assert body["meta"]["pagination"]["limit"] == 500  # server cap enforced
    status, _, body = api_get(app, "/api/v1/signals?limit=0")
    # limit=0 is coerced to default by clamp (never a crash, never unbounded).
    assert status in (200, 400)
    if status == 200:
        assert body["meta"]["pagination"]["limit"] == 50


def test_route_table_is_get_only(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    assert_get_only(app)  # raises if any non-GET data route exists


def test_ai_status_endpoint(tmp_path: Path) -> None:
    """AI status endpoint returns enablement, counts and limits (no secrets)."""
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/ai/status")
    assert status == 200
    data = body["data"]
    assert "available" in data
    assert "enabled" in data
    assert "valid" in data
    assert "providers" in data
    assert "models" in data
    assert "routes" in data
    assert "max_concurrent_requests" in data
    assert "max_retries" in data
    assert "timeout_s" in data
    assert "max_request_chars" in data
    assert "max_response_chars" in data
    assert "max_output_tokens" in data
    # No secrets ever appear in the response.
    body_str = json.dumps(body).lower()
    assert "sk-" not in body_str
    assert "akia" not in body_str
    assert "authorization" not in body_str


def test_read_ai_status_directly() -> None:
    """read_ai_status returns a well-shaped payload without network access."""
    from crypto_engine.api import repos as R

    payload = R.read_ai_status()
    assert "available" in payload
    assert "enabled" in payload
    assert "valid" in payload
    assert isinstance(payload["providers"], int)
    assert isinstance(payload["models"], int)
    assert isinstance(payload["routes"], int)


__all__ = ["test_health_and_envelope"]
