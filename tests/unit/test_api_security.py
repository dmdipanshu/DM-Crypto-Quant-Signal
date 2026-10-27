"""API security tests (Phase 7, offline).

Proves: GET-only route table, no mutating methods, path-traversal rejection,
ID validation, secret hygiene, error sanitization, CORS defaults.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from crypto_engine.api.app import assert_get_only, create_app  # noqa: E402
from crypto_engine.api.repos import RepoRoots  # noqa: E402
from crypto_engine.api.security import ApiError, confine, scan_for_secrets, validate_id  # noqa: E402
from crypto_engine.config.settings import load_settings  # noqa: E402
from tests.fixtures.api_harness import api_get, api_request  # noqa: E402
from tests.fixtures.api_stores import build_api_stores  # noqa: E402


def _test_app(tmp_path: Path, **overrides):
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


def test_no_mutating_routes_registered(tmp_path: Path) -> None:
    from crypto_engine.api.app import list_data_routes

    app, _ = _test_app(tmp_path)
    assert_get_only(app)
    data_paths = [path for path, _ in list_data_routes(app) if path.startswith("/api/")]
    assert data_paths, "expected v1 data routes"
    for path, methods in list_data_routes(app):
        assert methods == {"GET"}, f"{path}: {methods}"


def test_mutating_methods_rejected(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        status, _, _ = api_request(app, method, "/api/v1/signals")
        assert status == 405, method
        status, _, _ = api_request(app, method, "/health")
        assert status == 405, method


def test_path_traversal_rejected(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    for evil in ("..", "../secret", "%2e%2e", "/etc/passwd", "a/../../b"):
        status, _, body = api_get(app, f"/api/v1/signals/{evil}")
        assert status in (400, 404, 422), evil
        if isinstance(body, dict) and "error" in body:
            assert "traceback" not in body["error"]["message"].lower()


def test_confine_blocks_escapes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ApiError):
        confine(root, "..", "etc", "passwd")
    with pytest.raises(ApiError):
        confine(root, "/absolute")
    ok = confine(root, "sub", "file.json")
    assert str(ok).startswith(str(root.resolve()))


def test_validate_id_rejects(tmp_path: Path) -> None:
    for bad in ["", "a" * 200, "a b", "a;b", "../../../etc", "..\\win"]:
        with pytest.raises(ApiError):
            validate_id(bad)
    assert validate_id("BTC_USDT-1h:latest") == "BTC_USDT-1h:latest"


def test_no_tracebacks_or_paths_in_errors(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, body = api_get(app, "/api/v1/signals/" + "f" * 16)
    assert status == 404
    assert isinstance(body, dict) and "error" in body
    assert "traceback" not in body["error"]["message"].lower()
    assert "C:\\" not in body["error"]["message"] and "/app/" not in body["error"]["message"]


def test_no_secret_leakage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNALFORGE_TEST_API_KEY", "sk-test-SECRET-abc123")
    monkeypatch.setenv("MY_API_TOKEN", "tok-test-SECRET-xyz789")
    app, _ = _test_app(tmp_path)
    seen: list[str] = []
    for path in (
        "/health",
        "/api/v1/health",
        "/api/v1/readiness",
        "/api/v1/signals",
        "/api/v1/models",
        "/api/v1/models/status",
        "/api/v1/validation/summary",
        "/api/v1/experiments",
        "/api/v1/research/reports",
        "/api/v1/backtests",
        "/api/v1/paper/status",
        "/api/v1/paper/state",
        "/api/v1/audit/status",
        "/api/v1/data/status",
        "/api/v1/data/snapshots",
    ):
        status, _, body = api_get(app, path)
        assert status == 200, path
        seen.append(str(body))
    blob = "\n".join(seen)
    assert "sk-test-SECRET-abc123" not in blob
    assert "tok-test-SECRET-xyz789" not in blob
    assert scan_for_secrets({"api_key": "x"}) is not None
    assert scan_for_secrets({"nested": [{"token": 1}]}) is not None
    assert scan_for_secrets({"model_id": "m", "count": 2}) is None


def test_cors_default_deny_and_allowlist(tmp_path: Path) -> None:
    import asyncio

    async def _preflight(app, origin: str) -> dict[str, str]:
        status_holder: dict[str, int] = {}
        headers_out: dict[str, str] = {}

        async def receive():
            await asyncio.sleep(0)
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                headers_out.update(
                    {
                        k.decode("latin-1").lower(): v.decode("latin-1")
                        for k, v in message.get("headers", [])
                    }
                )

        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "OPTIONS",
            "scheme": "http",
            "path": "/api/v1/signals",
            "raw_path": b"/api/v1/signals",
            "query_string": b"",
            "headers": [(b"origin", origin.encode()), (b"access-control-request-method", b"GET")],
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "state": {},
        }
        await app(scope, receive, send)
        return headers_out

    app, _ = _test_app(tmp_path)
    headers = asyncio.run(_preflight(app, "https://evil.example"))
    assert "access-control-allow-origin" not in headers


def test_injection_payload_rendered_as_data(tmp_path: Path) -> None:
    """Untrusted text in records is escaped, never executed, in dashboard HTML."""
    from crypto_engine.dashboard import pages as P

    evil = {
        "signal_id": "abc",
        "direction": "<script>alert(1)</script>",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "generated_at": "2024-01-01T00:00:00+00:00",
        "valid_until": "2024-01-01T01:00:00+00:00",
        "decision": {"status": "approved", "reason_codes": ["OK"]},
        "prediction": {"calibration_status": "unavailable"},
        "data_quality": "validated",
    }
    html = P.signals_page([evil], 1)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


__all__: list[str] = []
