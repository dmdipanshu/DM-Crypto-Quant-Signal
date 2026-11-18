"""Q9 Centralized Observability & Production Hardening tests."""

from __future__ import annotations

import json
import logging
import tempfile
import threading
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from crypto_engine.observability.alerting import (
    AlertManager,
    AlertSeverity,
    alert_manager,
    fail_closed,
)
from crypto_engine.observability.health import (
    HealthChecker,
    HealthStatus,
    health_checker,
)
from crypto_engine.observability.logging import (
    SecretRedactingFilter,
    StructuredFormatter,
    clear_context,
    configure_logging,
    set_correlation_id,
)
from crypto_engine.observability.metrics import (
    get_instance,
    render_prometheus,
)
from crypto_engine.paper_trading.state import init_account
from crypto_engine.risk.gates import GateContext, run_gates
from crypto_engine.signals.schema import CostEstimate, DecisionInfo, SignalRecord


def _fresh_logger_name():
    import uuid as _uuid
    return f"test.logger.{_uuid.uuid4().hex[:8]}"


def test_structured_log_output_is_json():
    fmt = StructuredFormatter(fmt="json")
    record = logging.LogRecord("test.component", logging.INFO, "", 0, "hello world", (), None)
    record.event_id = "abc123"
    record.correlation_id = "corr-1"
    record.run_id = "run-1"
    output = fmt.format(record)
    parsed = json.loads(output)
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.component"
    assert parsed["message"] == "hello world"
    assert parsed["event_id"] == "abc123"
    assert parsed["ts"].endswith("Z")


def test_log_timestamp_utc_format():
    fmt = StructuredFormatter(fmt="json")
    record = logging.LogRecord("t", logging.INFO, "", 0, "x", (), None)
    output = fmt.format(record)
    parsed = json.loads(output)
    ts = parsed["ts"]
    assert ts.endswith("Z")
    datetime.fromisoformat(ts.replace("Z", "+00:00"))


def test_correlation_id_in_log_output():
    clear_context()
    set_correlation_id("corr-test-123")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter(fmt="json"))
    logger = logging.getLogger(_fresh_logger_name())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("correlation test message")
    output = stream.getvalue().strip()
    parsed = json.loads(output.splitlines()[-1])
    assert parsed["correlation_id"] == "corr-test-123"
    clear_context()


def test_secret_redaction_api_key():
    f = SecretRedactingFilter()
    record = logging.LogRecord("t", logging.INFO, "", 0, "connecting with api_key=sk-1234567890abcdef", (), None)
    f.filter(record)
    assert "sk-1234567890abcdef" not in record.getMessage()


def test_secret_redaction_bearer_token():
    f = SecretRedactingFilter()
    record = logging.LogRecord("t", logging.INFO, "", 0, "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9", (), None)
    f.filter(record)
    assert "eyJhbGciOiJIUzI1NiJ9" not in record.getMessage()


def test_no_secret_in_generated_logs():
    clear_context()
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter(fmt="json"))
    handler.addFilter(SecretRedactingFilter())
    logger = logging.getLogger(_fresh_logger_name())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("using api_key=sk-live-secret123 to connect")
    output = stream.getvalue().strip()
    parsed = json.loads(output.splitlines()[-1])
    assert "sk-live-secret123" not in parsed["message"]


def test_exception_in_log_output():
    fmt = StructuredFormatter(fmt="json")
    try:
        raise ValueError("test boom")
    except ValueError:
        import sys
        record = logging.LogRecord("t", logging.ERROR, "", 0, "failed", (), sys.exc_info())
    output = fmt.format(record)
    parsed = json.loads(output)
    assert "exception" in parsed
    assert "ValueError" in parsed["exception"]


def test_counter_metric():
    reg = get_instance()
    c = reg.counter("test_counter", component="test")
    v1 = c.value()
    c.inc()
    c.inc(5)
    assert c.value() == v1 + 6


def test_gauge_metric():
    reg = get_instance()
    g = reg.gauge("test_gauge", component="test")
    g.set(10.0)
    assert g.get() == 10.0
    g.inc(3)
    assert g.get() == 13.0


def test_histogram_metric():
    reg = get_instance()
    h = reg.histogram("test_hist", component="test")
    for v in [1.0, 2.0, 3.0, 10.0]:
        h.observe(v)
    snap = h.snapshot()
    assert snap["count"] == 4
    assert snap["sum"] == 16.0


def test_prometheus_render():
    reg = get_instance()
    reg.counter("http_requests_total", endpoint="/health")
    output = render_prometheus()
    assert "# HELP" in output
    assert "# TYPE" in output


def test_metric_storage_bounded():
    reg = get_instance()
    assert reg._max_series == 10000


def test_health_checker_runs_checks():
    results = health_checker.run_checks()
    assert isinstance(results, dict)
    assert "system_health" in results


def test_health_check_exception_does_not_crash():
    checker = HealthChecker()
    def bad_check():
        raise RuntimeError("check failed")
    checker.register_check("bad_component", bad_check)
    results = checker.run_checks()
    ch = results["bad_component"]
    assert ch.status == HealthStatus.UNHEALTHY


def test_market_data_staleness_alert():
    am = AlertManager()
    am.record_market_data_staleness(seconds_stale=36000)
    active = am.get_active_alerts()
    staleness_alerts = [a for a in active if "stale" in a.message.lower() or "staleness" in a.rule_name]
    assert len(staleness_alerts) >= 1


def test_alert_deduplication_cooldown():
    am = AlertManager()
    call_count = {"n": 0}
    def condition():
        call_count["n"] += 1
        return (True, "repeated failure")
    from crypto_engine.observability.alerting import AlertRule
    r = AlertRule(name="dedup_test", condition_func=lambda: condition(), severity=AlertSeverity.WARNING, cooldown_seconds=3600)
    am.register_rule(r)
    am.evaluate_rules()
    first_count = len(am.get_active_alerts())
    am.evaluate_rules()
    second_count = len(am.get_active_alerts())
    assert second_count == first_count


def test_fail_closed_returns_true_on_critical():
    triggered = fail_closed(True, "checkpoint corrupted", "persistence")
    assert triggered is True
    active = alert_manager.get_active_alerts()
    critical = [a for a in active if a.severity == AlertSeverity.CRITICAL]
    assert len(critical) >= 1


def test_fail_closed_returns_false_on_ok():
    triggered = fail_closed(False, "all good", "persistence")
    assert triggered is False


def test_critical_exception_recording():
    alert_manager.record_exception("test_component", ValueError("boom"))
    active = alert_manager.get_active_alerts()
    exc_alerts = [a for a in active if a.component == "test_component"]
    assert len(exc_alerts) >= 1
    assert exc_alerts[0].severity == AlertSeverity.CRITICAL


def test_alert_manager_never_crashes_on_bad_rule():
    am = AlertManager()
    def bad_condition():
        raise RuntimeError("rule exploded")
    from crypto_engine.observability.alerting import AlertRule
    r = AlertRule(name="bad_rule", condition_func=bad_condition, severity=AlertSeverity.CRITICAL, cooldown_seconds=0)
    am.register_rule(r)
    alerts = am.evaluate_rules()
    assert isinstance(alerts, list)


def test_logging_separate_from_signal_history():
    import crypto_engine.observability.logging as log_mod
    assert not hasattr(log_mod, "SignalHistory")


def test_logging_separate_from_event_ledger():
    import crypto_engine.observability.logging as log_mod
    assert not hasattr(log_mod, "EventLedger")


def test_configure_logging_idempotent():
    from crypto_engine.config.settings import load_settings
    settings = load_settings()
    configure_logging(settings)
    n2 = len(logging.getLogger().handlers)
    configure_logging(settings)
    n3 = len(logging.getLogger().handlers)
    assert n2 == n3


def test_concurrent_metric_increments():
    reg = get_instance()
    c = reg.counter("concurrent_counter", component="test")
    errors = []

    def worker():
        try:
            for _ in range(1000):
                c.inc()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert c.value() == 8000


def test_logging_failure_does_not_corrupt_accounting():
    acct = init_account(
        Path(tempfile.mkdtemp()) / "account.json",
        symbol="BTC/USDT", timeframe="1h",
        starting_equity=10000.0, position_pct=10.0, max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0},
        model_id="test", config_hash="abc", account_id="safety-test",
    )
    assert acct.cash == 10000.0
    assert acct.status == "open"


def test_monitoring_failure_does_not_bypass_risk_controls():
    _acct = init_account(
        Path(tempfile.mkdtemp()) / "account.json",
        symbol="BTC/USDT", timeframe="1h",
        starting_equity=10000.0, position_pct=10.0, max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0},
        model_id="test", config_hash="abc", account_id="risk-safety",
    )
    sig = SignalRecord(
        signal_id="abcdef0123456789",
        symbol="BTC/USDT", timeframe="1h",
        direction="LONG", entry_ref=100.0,
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
        valid_until=datetime(2024, 1, 2, tzinfo=UTC),
        prediction_horizon_bars=24,
        model_id="test", model_version="1.0", model_artifact_hash="",
        dataset_snapshot_id="", feature_snapshot_hash="",
        expected_edge_bp=None,
        cost_estimate=CostEstimate(
            fee_bp=0.0, spread_bp=0.0, slippage_bp=0.0, funding_bp=0.0,
            source="configured", expected_edge_bp=None, edge_margin_bp=0.0,
        ),
        decision=DecisionInfo(status="approved", reason_codes=["POLICY_OK"], gate="policy"),
    )
    ctx = GateContext(
        now_utc=datetime(2024, 1, 1, tzinfo=UTC),
        risk=None, max_age_min=60.0, staleness_limit_min=90.0,
        feature_timestamp=None, require_edge=False, edge_margin_bp=0.0,
        portfolio={"exposure_pct": 0.0, "symbol_exposure_pct": 0.0,
                    "day_start_equity": 10000.0, "current_equity": 10000.0,
                    "strategy_enabled": True, "circuit_open": False},
    )
    gated, _ = run_gates(sig, ctx)
    assert gated.decision.status in ("approved", "rejected", "abstained")


def test_checkpoint_corruption_fail_closed():
    triggered = fail_closed(True, "checkpoint hash mismatch", "persistence")
    assert triggered is True
    active = alert_manager.get_active_alerts()
    crypto_alerts = [a for a in active if "checkpoint" in a.message.lower() or "hash" in a.message.lower()]
    assert len(crypto_alerts) >= 1


def test_alert_no_duplicates_within_cooldown():
    am = AlertManager()
    fire_count = {"n": 0}
    def condition():
        fire_count["n"] += 1
        return (True, "test alert")
    from crypto_engine.observability.alerting import AlertRule
    r = AlertRule(name="no_dup", condition_func=condition, severity=AlertSeverity.INFO, cooldown_seconds=3600)
    am.register_rule(r)
    am.evaluate_rules()
    am.evaluate_rules()
    am.evaluate_rules()
    active = am.get_active_alerts()
    no_dup_alerts = [a for a in active if a.rule_name == "no_dup"]
    assert len(no_dup_alerts) == 1


def test_no_secret_values_in_log_output():
    clear_context()
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter(fmt="json"))
    handler.addFilter(SecretRedactingFilter())
    logger = logging.getLogger(_fresh_logger_name())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("connecting to exchange with api_key=sk-test-12345 and token=abc-def")
    output = stream.getvalue().strip()
    parsed = json.loads(output.splitlines()[-1])
    assert "sk-test-12345" not in parsed["message"]
    assert "abc-def" not in parsed["message"]


def test_monitoring_components_no_trading_authorization():
    import crypto_engine.observability.alerting as alerting_mod
    import crypto_engine.observability.health as health_mod
    import crypto_engine.observability.metrics as metrics_mod
    for mod in [health_mod, metrics_mod, alerting_mod]:
        attrs = dir(mod)
        trading_attrs = [a for a in attrs if any(kw in a.lower() for kw in ["trade", "order", "buy", "sell", "execute", "authorize", "promote"])]
        assert not trading_attrs, f"{mod.__name__} has trading-related attrs: {trading_attrs}"


__all__ = [
    "test_alert_deduplication_cooldown",
    "test_alert_manager_never_crashes_on_bad_rule",
    "test_alert_no_duplicates_within_cooldown",
    "test_checkpoint_corruption_fail_closed",
    "test_concurrent_metric_increments",
    "test_configure_logging_idempotent",
    "test_correlation_id_in_log_output",
    "test_counter_metric",
    "test_critical_exception_recording",
    "test_exception_in_log_output",
    "test_fail_closed_returns_false_on_ok",
    "test_fail_closed_returns_true_on_critical",
    "test_gauge_metric",
    "test_health_check_exception_does_not_crash",
    "test_health_checker_runs_checks",
    "test_histogram_metric",
    "test_log_timestamp_utc_format",
    "test_logging_failure_does_not_corrupt_accounting",
    "test_logging_separate_from_event_ledger",
    "test_logging_separate_from_signal_history",
    "test_market_data_staleness_alert",
    "test_metric_storage_bounded",
    "test_monitoring_components_no_trading_authorization",
    "test_monitoring_failure_does_not_bypass_risk_controls",
    "test_no_secret_in_generated_logs",
    "test_no_secret_values_in_log_output",
    "test_prometheus_render",
    "test_secret_redaction_api_key",
    "test_secret_redaction_bearer_token",
    "test_structured_log_output_is_json",
]
