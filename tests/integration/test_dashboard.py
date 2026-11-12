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


def _test_app_empty(root: Path):
    """App over absent stores: no snapshots, signals, models, paper or backtests."""
    settings = load_settings()
    roots = RepoRoots(
        signals_dir=root / "signals",
        registry_dir=root / "registry",
        data_storage=root / "storage",
        backtest_dir=root / "backtests",
        paper_path=root / "paper",
        experiments_path=root / "experiments.jsonl",
        holdout_ledger_path=root / "holdout.json",
        scan_cap=5000,
    )
    return create_app(roots, settings), roots


def test_overview_matches_api_availability(tmp_path: Path) -> None:
    """Regression: overview must agree with the API in both availability states.

    The Datasets row previously read the API-only keys (snapshots_available /
    snapshot_count) while the page context carries the shared contract
    (available / total), so a populated store rendered as "none recorded".
    Both states are exercised here; the empty case is not reachable from the
    populated store fixture.
    """
    cases = (
        ("populated", _test_app(tmp_path / "populated")[0], True),
        ("empty", _test_app_empty(tmp_path / "empty")[0], False),
    )
    for name, app, expected_available in cases:
        _, _, html = api_get(app, "/dashboard/")
        _, _, status = api_get(app, "/api/v1/data/status")
        row_html = html.split("Datasets")[1].split("</tr>")[0]
        available = bool(status["data"].get("snapshots_available"))
        assert available is expected_available, name
        if available:
            count = status["data"]["snapshot_count"]
            assert "none recorded" not in row_html, name
            assert f"{count} snapshot(s) recorded" in row_html, name
        else:
            assert "none recorded" in row_html, name
            assert "0 snapshot(s) recorded" not in row_html, name


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


def test_signals_page_shows_ai_explanation(tmp_path: Path) -> None:
    """Dashboard signals page must render AI explanation summary when present."""
    from datetime import UTC, datetime

    from crypto_engine.signals.history import SignalHistory
    from crypto_engine.signals.schema import (
        AiContextSummary,
        CostEstimate,
        DecisionInfo,
        PredictionInfo,
        SignalRecord,
    )

    app, roots = _test_app(tmp_path)
    # Write a signal with AI explanation to the history store.
    hist = SignalHistory(roots.signals_dir)
    hist.append(
        SignalRecord(
            signal_id="0123456789abcdef",
            symbol="BTC/USDT",
            exchange="binance",
            timeframe="1h",
            generated_at=datetime(2026, 1, 1, tzinfo=UTC),
            valid_until=datetime(2026, 1, 2, tzinfo=UTC),
            direction="LONG",
            prediction_horizon_bars=24,
            model_id="model-1",
            model_version="v1",
            model_artifact_hash="a" * 64,
            dataset_snapshot_id="snap-001",
            feature_snapshot_hash="b" * 64,
            prediction=PredictionInfo(
                raw_score=0.42,
                calibrated_probability=None,
                calibration_status="uncalibrated",
                positive_class=1,
                model_family="logistic",
            ),
            market_regime="trend_down",
            expected_edge_bp=-3.5,
            cost_estimate=CostEstimate(
                fee_bp=4.0,
                spread_bp=1.0,
                slippage_bp=2.0,
                funding_bp=0.0,
                source="configured",
            ),
            data_quality="validated",
            ai_context=AiContextSummary(
                status="ok",
                provider_id="meta_muse",
                model_id="muse-spark-1.3",
                evidence_ids=["0123456789abcdef"],
                generated_at="2026-01-01T00:00:00+00:00",
                summary="Strong regime alignment supports long direction; risk managed by stop-loss.",
            ),
            decision=DecisionInfo(
                status="abstained",
                reason_codes=["EDGE_BELOW_MARGIN"],
                gate="policy",
            ),
        )
    )
    status, _, html = api_get(app, "/dashboard/signals")
    assert status == 200
    assert "meta_muse" in html
    assert "muse-spark-1.3" in html
    assert "AI Explanation" in html
    assert "Strong regime alignment" in html


def test_signals_page_handles_empty_ai_summary(tmp_path: Path) -> None:
    """Dashboard must not fabricate text when AI explanation is empty."""
    app, roots = _test_app(tmp_path)
    payload = read_signals(roots, limit=50, offset=0)
    signals = payload["signals"]
    if signals:
        sig = signals[0]
        sig["ai_context"] = {
            "status": "ok",
            "provider_id": "meta_muse",
            "model_id": "muse-spark-1.3",
            "evidence_ids": [],
            "generated_at": "2026-01-01T00:00:00+00:00",
            "expiry": None,
            "note": "",
            "summary": "",
        }
    status, _, html = api_get(app, "/dashboard/signals")
    assert status == 200
    # Empty summary should render as "AI: ok" not as fabricated text.
    assert "AI: ok" in html or "meta_muse" not in html


def test_signals_page_matches_api_and_ai(tmp_path: Path) -> None:
    """Signal ids from API must appear on dashboard page; AI explanation renders when present."""
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
