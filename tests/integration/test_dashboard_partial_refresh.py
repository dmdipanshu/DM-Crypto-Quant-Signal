"""Regression: Q5 governance endpoint failure must not prevent other dashboard data."""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from crypto_engine.api.app import create_app  # noqa: E402
from crypto_engine.api.repos import RepoRoots  # noqa: E402
from crypto_engine.api.security import ApiError  # noqa: E402
from crypto_engine.config.settings import load_settings  # noqa: E402
from crypto_engine.observability.health import health_checker  # noqa: E402
from tests.fixtures.api_harness import api_get  # noqa: E402
from tests.fixtures.api_stores import build_api_stores  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_auth_env(monkeypatch):
    monkeypatch.delenv("SIGNALFORGE_API_TOKEN", raising=False)
    monkeypatch.delenv("SIGNALFORGE_AUTH_REQUIRED", raising=False)


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
        health_checker=health_checker,
    )
    return create_app(roots, settings), roots


def test_q52_governance_500_does_not_block_other_endpoints(tmp_path: Path, monkeypatch) -> None:
    """Q5.2 governance 500 while paper/health succeed: other endpoints remain usable and UI isolates failure."""
    app, _ = _test_app(tmp_path)

    def boom(_roots):
        raise ApiError(500, "internal_error", "manifest_not_found")

    # Force the governance repo reader to fail (simulates manifest/validation failure path).
    import crypto_engine.api.repos as repos

    monkeypatch.setattr(repos, "read_q5_prospective_governance", boom)

    status_h, _, body_h = api_get(app, "/api/v1/health")
    status_p, _, body_p = api_get(app, "/api/v1/paper/status")
    status_g, _, body_g = api_get(app, "/api/v1/q5/prospective/governance")

    assert status_h == 200
    assert body_h.get("data", {}).get("status") == "degraded"
    assert status_p == 200
    assert body_p.get("data", {}).get("available") is True
    # Governance fails (HTTP 500 error envelope) but must not be treated as healthy/success.
    assert status_g == 500
    assert isinstance(body_g, dict)
    assert "error" in body_g
    assert body_g.get("error", {}).get("code") == "internal_error"

    monitor_js = Path("src/crypto_engine/dashboard/monitor/monitor.js").read_text(encoding="utf-8")
    # Per-endpoint fetch isolation and non-success handling (no single rejected promise aborting refresh).
    assert "async function fetchResult(url)" in monitor_js
    assert 'return { kind: "http", status: error.status' in monitor_js
    assert "function resultValue(result)" in monitor_js
    assert 'if (result.kind === "http" || result.kind === "error") return "UNAVAILABLE";' in monitor_js
    # 401-only auth path; HTTP 500 is not promoted to success/auth.
    assert 'if (error instanceof AuthError) return { kind: "auth", status: 401' in monitor_js
    assert 'results.some((result) => result.kind === "auth")' in monitor_js
    # Independent panel rendering: Q5 failure cannot block paper/health rendering.
    assert "safe(renderQ52);" in monitor_js
    assert "safe(renderPaper);" in monitor_js
    assert "safe(renderHealth);" in monitor_js
    assert "safe(renderStatusBar);" in monitor_js
    # Q5 panel surfaces actual blocked/unavailable state and reason; does not fabricate values.
    assert 'setMetric("q52-last-result", "UNAVAILABLE: " + govReason' in monitor_js
    assert 'govVal = "UNAVAILABLE: " + govReason' in monitor_js
    # Paper/health render from their own successful endpoint data independently.
    assert "const health = dataOrNull(state.health);" in monitor_js
    assert "const status = dataOrNull(state.paperStatus);" in monitor_js
    assert "const account = dataOrNull(state.paperState);" in monitor_js
    # API connectivity vs system health separation:
    # API connectivity chip exists and is based on reachability (kind === "ok"),
    # never overridden by backend overall status.
    assert "chip-api-connectivity" in monitor_js
    assert "chip-system-health" in monitor_js
    # API connectivity derived from health.kind, system health from health.status.
    assert 'apiLabel = state.health && state.health.kind === "network"' in monitor_js
    assert 'systemLabel = health ? text(health.status).toUpperCase()' in monitor_js
    # health-api chip set from kind, health-system-health chip set from status.
    assert 'setStatus("health-api", "HEALTHY", "HEALTHY");' in monitor_js
    assert 'setStatus("health-system-health", health ? text(health.status).toUpperCase()' in monitor_js


def test_health_endpoint_degraded_status_api_reachable(tmp_path: Path, monkeypatch) -> None:
    """API reachable (HTTP 200) but backend reports status=degraded.

    Regression: dashboard must not override backend overall health status merely
    because the endpoint returns HTTP 200. API connectivity = HEALTHY,
    system health = DEGRADED.
    """
    app, _ = _test_app(tmp_path)

    from crypto_engine.observability.health import HealthStatus

    def _degraded_overall(self):
        return HealthStatus.DEGRADED

    monkeypatch.setattr(type(app.state.signalforge_roots.health_checker), "overall_status", _degraded_overall)

    status_h, _, body_h = api_get(app, "/api/v1/health")
    assert status_h == 200
    # API is reachable (HTTP 200) so connectivity is healthy.
    assert body_h.get("data", {}).get("status") == "degraded"
    assert body_h.get("data", {}).get("checks", {}).get("status") == "degraded"
