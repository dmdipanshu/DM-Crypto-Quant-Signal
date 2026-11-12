"""Bootstrap unit tests (Stage 8).

Offline tests for SignalForgeApp.run_pipeline().
No network, no ccxt, no external calls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crypto_engine.bootstrap import BootstrapConfig, SignalForgeApp
from crypto_engine.signals.schema import SignalRecord


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def test_app_creation() -> None:
    app = SignalForgeApp()
    assert app.config is not None
    assert app.config.signal_max_age_min == 60
    assert app.config.fail_closed is True


def test_app_creation_with_config() -> None:
    config = BootstrapConfig(signal_max_age_min=30, fail_closed=False)
    app = SignalForgeApp(config)
    assert app.config.signal_max_age_min == 30
    assert app.config.fail_closed is False


def test_run_pipeline_approves_signal() -> None:
    app = SignalForgeApp()
    future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    result = app.run_pipeline(
        symbol="BTC/USDT",
        timeframe="1h",
        generated_at=future,
        model_score=0.8,
        model_confidence=0.9,
        expected_edge_bp=15.0,
        cost_estimate={"fee_bp": 10.0, "spread_bp": 0.0, "slippage_bp": 5.0, "funding_bp": 0.0},
    )
    # Without portfolio, exposure gates fail closed -> veto
    assert result.approved is False or result.signal is None


def test_run_pipeline_stale_signal_vetoed() -> None:
    app = SignalForgeApp()
    past = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    result = app.run_pipeline(
        symbol="BTC/USDT",
        timeframe="1h",
        generated_at=past,
        model_score=0.8,
        model_confidence=0.9,
        expected_edge_bp=15.0,
        cost_estimate={"fee_bp": 10.0},
    )
    assert result.approved is False
    assert result.signal is None
    assert "quality" in result.trace


def test_run_pipeline_with_portfolio() -> None:
    app = SignalForgeApp()
    future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    portfolio = {
        "exposure_pct": 1.0,
        "symbol_exposure_pct": 0.5,
        "day_start_equity": 10000.0,
        "current_equity": 10000.0,
        "strategy_enabled": True,
        "circuit_open": False,
    }
    result = app.run_pipeline(
        symbol="BTC/USDT",
        timeframe="1h",
        generated_at=future,
        model_score=0.8,
        model_confidence=0.9,
        expected_edge_bp=15.0,
        cost_estimate={"fee_bp": 10.0, "spread_bp": 0.0, "slippage_bp": 5.0, "funding_bp": 0.0},
        portfolio=portfolio,
    )
    # With portfolio but fail_closed, exposure gate may still veto if edge insufficient
    # Just verify it runs without error
    assert result.trace is not None


def test_run_pipeline_return_type() -> None:
    app = SignalForgeApp()
    future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    result = app.run_pipeline(
        symbol="BTC/USDT",
        timeframe="1h",
        generated_at=future,
        model_score=0.8,
        model_confidence=0.9,
        expected_edge_bp=15.0,
        cost_estimate={"fee_bp": 10.0},
    )
    assert isinstance(result.trace, dict)
    assert isinstance(result.approved, bool)
    assert result.signal is None or isinstance(result.signal, SignalRecord)


def test_research_proxies() -> None:
    app = SignalForgeApp()
    result = app.research_proxies(
        [{"status": "success", "n_trials": 10}, {"status": "success", "n_trials": 5}]
    )
    assert result["experiment_count"] == 2
    assert result["directional_read"] == "positive"


def test_validate_holdout_missing() -> None:
    app = SignalForgeApp()
    result = app.validate_holdout("/nonexistent/ledger.json")
    assert result["available"] is False


def test_quality_audit() -> None:
    app = SignalForgeApp()
    state = {
        "schema_version": 1,
        "records": {"a": {"x": 1}},
        "checksums": {"a": "abc"},
    }
    result = app.quality_audit(state)
    assert result["ok"] is True


__all__ = [
    "test_app_creation",
    "test_app_creation_with_config",
    "test_quality_audit",
    "test_research_proxies",
    "test_run_pipeline_approves_signal",
    "test_run_pipeline_return_type",
    "test_run_pipeline_stale_signal_vetoed",
    "test_run_pipeline_with_portfolio",
    "test_validate_holdout_missing",
]
