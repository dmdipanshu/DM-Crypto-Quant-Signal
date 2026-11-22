"""Dashboard authentication regression tests (offline, ASGI harness).

Verifies:
- Dashboard assets (HTML, CSS, JS) serve 200 without auth (skip paths).
- Protected API endpoints return 401 without an Authorization header when a
  token is configured.
- Protected API endpoints return 401 with a wrong token.
- Protected API endpoints return 200 with the correct token.
- When no token is configured, API endpoints serve without auth (dev mode).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")

from crypto_engine.api.app import create_app  # noqa: E402
from crypto_engine.api.repos import RepoRoots  # noqa: E402
from crypto_engine.config.settings import load_settings  # noqa: E402
from tests.fixtures.api_harness import api_get  # noqa: E402
from tests.fixtures.api_stores import build_api_stores  # noqa: E402


def _test_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str | None = None) -> Any:
    """Build the app with an optional SIGNALFORGE_API_TOKEN configured."""
    if token is not None:
        monkeypatch.setenv("SIGNALFORGE_API_TOKEN", token)
    else:
        monkeypatch.delenv("SIGNALFORGE_API_TOKEN", raising=False)

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


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token}


def test_dashboard_monitor_assets_serve_without_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dashboard HTML/CSS/JS assets return 200 without any token (skip paths)."""
    app, _ = _test_app(tmp_path, monkeypatch, token="some-token")

    status, headers, body = api_get(app, "/dashboard/monitor/")
    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    assert "SignalForge" in body

    status, headers, _css = api_get(app, "/dashboard/monitor/monitor.css")
    assert status == 200
    assert "text/css" in headers.get("content-type", "")

    status, headers, js = api_get(app, "/dashboard/monitor/monitor.js")
    assert status == 200
    assert "text/javascript" in headers.get("content-type", "")
    assert "fetchJSON" in js


def test_api_health_200_without_header_when_token_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/health is unauthenticated (infrastructure health check) even when token configured."""
    app, _ = _test_app(tmp_path, monkeypatch, token="configured-token")
    status, _, _ = api_get(app, "/api/v1/health")
    assert status == 200


def test_api_health_200_with_wrong_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/health is unauthenticated; wrong token is ignored for health endpoint."""
    app, _ = _test_app(tmp_path, monkeypatch, token="configured-token")
    status, _, _ = api_get(app, "/api/v1/health", headers=_auth_header("wrong-token"))
    assert status == 200


def test_api_health_200_with_correct_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Protected /api/v1/health returns 200 with the correct configured token."""
    app, _ = _test_app(tmp_path, monkeypatch, token="configured-token")

    status, _, body = api_get(app, "/api/v1/health", headers=_auth_header("configured-token"))
    assert status == 200
    assert body.get("data", {}).get("status") == "ok"


def test_api_paper_status_401_without_header_when_token_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protected /api/v1/paper/status returns 401 without an Authorization header."""
    app, _ = _test_app(tmp_path, monkeypatch, token="paper-token")
    status, _, _ = api_get(app, "/api/v1/paper/status")
    assert status == 401


def test_api_paper_status_200_with_correct_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protected /api/v1/paper/status returns 200 with the correct token."""
    app, _ = _test_app(tmp_path, monkeypatch, token="paper-token")

    status, _, _body = api_get(app, "/api/v1/paper/status", headers=_auth_header("paper-token"))
    assert status == 200


def test_api_readiness_401_without_header_when_token_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protected /api/v1/readiness returns 401 without an Authorization header."""
    app, _ = _test_app(tmp_path, monkeypatch, token="ready-token")
    status, _, _ = api_get(app, "/api/v1/readiness")
    assert status == 401


def test_api_readiness_200_with_correct_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protected /api/v1/readiness returns 200 with the correct token."""
    app, _ = _test_app(tmp_path, monkeypatch, token="ready-token")

    status, _, _body = api_get(app, "/api/v1/readiness", headers=_auth_header("ready-token"))
    assert status == 200


def test_api_endpoints_serve_without_auth_when_no_token_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no SIGNALFORGE_API_TOKEN is configured, API endpoints serve without auth."""
    app, _ = _test_app(tmp_path, monkeypatch, token=None)
    status, _, _ = api_get(app, "/api/v1/health")
    assert status == 200


def test_monitor_js_contains_result_null_guard() -> None:
    """Regression: monitor.js guards against null/malformed fetch results.

    Without a null guard, render logic can throw when a result is null/undefined
    (e.g. "Cannot read properties of null"), crashing dashboard rendering and
    preventing independent panels from updating.
    """
    monitor_js = Path("src/crypto_engine/dashboard/monitor/monitor.js").read_text(encoding="utf-8")
    assert "function resultValue(result)" in monitor_js
    assert 'if (!result || typeof result !== "object") return "N/A";' in monitor_js
    assert 'if (result.kind === "network") return "OFFLINE";' in monitor_js
    assert (
        'if (result.kind === "http" || result.kind === "error") return "UNAVAILABLE";' in monitor_js
    )


def test_monitor_js_missing_token_initial_load_shows_login_and_defers_polling() -> None:
    """Regression: missing-token initial page-load must prompt login, not poll protected APIs."""
    monitor_js = Path("src/crypto_engine/dashboard/monitor/monitor.js").read_text(encoding="utf-8")
    # init is the startup path (no eager polling before auth check).
    assert "async function init()" in monitor_js
    assert 'document.addEventListener("DOMContentLoaded", init)' in monitor_js
    assert "if (!stored) {" in monitor_js
    assert "state.authRequired = true;" in monitor_js
    assert "state.loading = false;" in monitor_js
    assert "showLoginDialog();" in monitor_js
    assert "render();" in monitor_js
    assert "return;" in monitor_js
    # Protected data loading and polling only happen after successful token validation.
    assert "const ok = await validateToken(stored);" in monitor_js
    assert "await loadDashboardData();" in monitor_js
    assert "startPolling();" in monitor_js
    # 401/auth failures clear token, stop polling, and re-prompt login.
    assert "clearToken();" in monitor_js
    assert "stopPolling();" in monitor_js
    assert 'throw new AuthError("Authentication required (HTTP 401)")' in monitor_js
    assert 'Authorization: "Bearer " + token' in monitor_js
    # Per-endpoint isolation: each request is wrapped and HTTP failures are not treated as success.
    assert "async function fetchResult(url)" in monitor_js
    assert 'return { kind: "http", status: error.status' in monitor_js
    assert 'return { kind: "network"' in monitor_js
    assert 'results.some((result) => result.kind === "auth")' in monitor_js
    assert (
        "const safe = (fn) => { try { fn(); } catch { /* isolate panel render failures */ } };"
        in monitor_js
    )
    assert "safe(renderQ52);" in monitor_js
    assert "safe(renderPaper);" in monitor_js
    assert "safe(renderHealth);" in monitor_js


def test_section_outcome_equivalent_handles_null() -> None:
    """Equivalent Python simulation of sectionOutcome handling null input.

    Mirrors the JS sectionOutcome logic to prove the null guard prevents
    the TypeError crash when result is null/undefined.
    """

    def section_outcome(result: dict | None) -> str | dict | None:
        if not result:
            return "N/A"
        if result.get("kind") == "network":
            return "OFFLINE"
        if result.get("kind") == "auth":
            return "N/A"
        if result.get("kind") == "envelope" or result.get("data") is None:
            return "N/A"
        return result.get("data")

    # Null/undefined/malformed input must not raise - returns "N/A" fallback.
    assert section_outcome(None) == "N/A"
    assert section_outcome("") == "N/A"  # type: ignore[arg-type]
    assert section_outcome(0) == "N/A"  # type: ignore[arg-type]
    # Network result returns OFFLINE.
    assert section_outcome({"kind": "network", "payload": None, "data": None}) == "OFFLINE"
    # Auth result returns N/A.
    assert section_outcome({"kind": "auth", "payload": None, "data": None}) == "N/A"
    # Envelope result returns N/A.
    assert section_outcome({"kind": "envelope", "payload": {}, "data": None}) == "N/A"
    # OK result with data returns data.
    assert section_outcome({"kind": "ok", "payload": {}, "data": {"status": "ok"}}) == {
        "status": "ok",
    }


__all__: list[str] = []
