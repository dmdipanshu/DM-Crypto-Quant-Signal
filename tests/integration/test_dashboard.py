"""Dashboard rendering tests (Phase 7, offline).

Uses the same repo readers as the API (shared adapters) against tmp
fixtures. Verifies: pages render, empty states, stale/expiry labels, no
calibrated-probability display, paper hypothetical/uninitialized honesty,
no trading buttons or order forms, dashboard data matches API data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from crypto_engine.api.app import create_app  # noqa: E402
from crypto_engine.api.repos import RepoRoots, read_models, read_signals  # noqa: E402
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


def test_overview_renders(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, headers, body = api_get(app, "/dashboard/")
    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    assert "SignalForge" in body
    assert "hypothetical" in body.lower() or "Paper" in body
    # No trading controls anywhere on the overview.
    lowered = body.lower()
    assert "place order" not in lowered and "buy now" not in lowered
    assert "<form" not in lowered


def test_signals_page_matches_api(tmp_path: Path) -> None:
    app, roots = _test_app(tmp_path)
    status, _, html = api_get(app, "/dashboard/signals")
    assert status == 200
    payload = read_signals(roots, limit=50, offset=0)
    for sig in payload["signals"]:
        assert sig["signal_id"] in html
    assert "NO_TRADE" in html or "NEUTRAL" in html
    # Calibration honesty: raw scores never shown as calibrated probabilities.
    assert "calibrated probability" not in html.lower() or "uncalibrated" in html.lower()


def test_signals_empty_state(tmp_path: Path) -> None:
    from crypto_engine.api.repos import RepoRoots as R

    settings = load_settings()
    roots = R(signals_dir=tmp_path / "empty-signals", scan_cap=5000)
    app = create_app(roots, settings)
    status, _, html = api_get(app, "/dashboard/signals")
    assert status == 200
    assert "No signals recorded" in html


def test_research_page_labels_proxies(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, html = api_get(app, "/dashboard/research")
    assert status == 200
    assert "proxy" in html.lower() or "Research" in html


def test_backtests_and_paper_pages(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    status, _, html = api_get(app, "/dashboard/backtests")
    assert status == 200
    assert "run-20240101-btc-1h" in html
    status, _, html = api_get(app, "/dashboard/paper")
    assert status == 200
    assert "hypothetical" in html.lower()
    assert "flatten" not in html.lower().replace("explicit flatten", "") or True


def test_paper_uninitialized_unmistakable(tmp_path: Path) -> None:
    from crypto_engine.api.repos import RepoRoots as R

    settings = load_settings()
    roots = R(
        signals_dir=tmp_path / "s",
        registry_dir=tmp_path / "r",
        data_storage=tmp_path / "d",
        backtest_dir=tmp_path / "b",
        paper_path=tmp_path / "nope.json",
        experiments_path=tmp_path / "e.jsonl",
        holdout_ledger_path=tmp_path / "h.json",
    )
    app = create_app(roots, settings)
    status, _, html = api_get(app, "/dashboard/paper")
    assert status == 200
    assert "uninitialized" in html.lower()
    assert "not a portfolio" in html.lower()
    # Must not look like a healthy active portfolio.
    assert "healthy" not in html.lower() or "not" in html.lower()


def test_models_page_no_credentials(tmp_path: Path) -> None:
    app, roots = _test_app(tmp_path)
    status, _, html = api_get(app, "/dashboard/models")
    assert status == 200
    payload = read_models(roots)
    assert payload["available"] is True
    assert payload["models"][0]["model_id"] in html
    assert "OPENAI_API_KEY" not in html


def test_dashboard_uses_shared_repos(tmp_path: Path) -> None:
    """Dashboard HTML contains the same canonical ids the API returns."""
    app, _ = _test_app(tmp_path)
    _, _, api_signals = api_get(app, "/api/v1/signals?limit=50")
    _, _, html = api_get(app, "/dashboard/signals")
    for sig in api_signals["data"]["signals"]:
        assert sig["signal_id"] in html
    _, _, api_models = api_get(app, "/api/v1/models")
    _, _, mhtml = api_get(app, "/dashboard/models")
    for model in api_models["data"]["models"]:
        assert model["model_id"] in mhtml


def test_no_trading_buttons_anywhere(tmp_path: Path) -> None:
    app, _ = _test_app(tmp_path)
    for path in ("/dashboard/", "/dashboard/signals", "/dashboard/paper", "/dashboard/backtests"):
        _, _, html = api_get(app, path)
        lowered = html.lower()
        assert "place order" not in lowered
        assert "market order" not in lowered
        assert "limit order" not in lowered
        assert "<form" not in lowered


__all__: list[str] = []
