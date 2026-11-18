"""Advisory-only alerting infrastructure for SignalForge (Phase Q9).

Provides rule-based alerting with cooldown deduplication, bounded alert
history, and local console/log notification. No external services are
called unless explicitly configured, and alert failures never crash the
application. No live trading, no automatic promotion, no order execution.
"""

from __future__ import annotations

import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from crypto_engine.observability.logging import get_logger

_MAX_HISTORY = 1_000


def _iso_utc_now() -> str:
    """Current UTC time as ISO-8601 with a trailing Z."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class AlertSeverity(StrEnum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """A single alert emitted by :class:`AlertManager`.

    Attributes:
        id: Unique alert id (uuid4 hex).
        severity: Alert severity.
        component: Component that raised the condition.
        message: Human-readable alert message.
        timestamp_utc: Moment the alert was raised (ISO-8601 Z).
        rule_name: Name of the rule that fired, or ``"fail_closed"``.
        resolved: Whether the alert has been resolved.
        resolved_at_utc: Moment the alert was resolved, if any.
    """

    id: str
    severity: AlertSeverity
    component: str
    message: str
    timestamp_utc: str
    rule_name: str
    resolved: bool = False
    resolved_at_utc: str | None = None


@dataclass
class AlertRule:
    """A registered alert rule.

    Attributes:
        name: Unique rule name.
        condition_func: Callable returning ``(triggered, message)``.
        severity: Alert severity when triggered.
        cooldown_seconds: Minimum seconds between firings of this rule.
        last_triggered_utc: Moment the rule last fired (ISO-8601 Z).
    """

    name: str
    condition_func: Callable[[], tuple[bool, str]]
    severity: AlertSeverity
    cooldown_seconds: int
    last_triggered_utc: str | None = None
class AlertManager:
    """Rule-based alert manager with cooldown deduplication.

    All public operations are wrapped in try/except so alerting failures
    never crash the application. Alert history is bounded; the oldest
    entries are evicted once the limit is reached.

    Attributes:
        staleness_threshold_seconds: Market-data staleness threshold.
        ingestion_failure_threshold: Ingestion failure count threshold.
        missing_observations_min: Minimum acceptable observation count.
        memory_threshold_pct: Memory usage alert threshold (percent).
        disk_threshold_pct: Disk usage alert threshold (percent).
        _alerts: Bounded alert history (newest last).
        _active: Unresolved alerts by id.
        _rules: Registered rules by name.
        _state: Internal state read by rule conditions.
        _thresholds: Configured per-rule thresholds.
        _lock: Serializes state mutation and evaluation.
    """

    def __init__(
        self,
        *,
        staleness_threshold_seconds: int = 300,
        ingestion_failure_threshold: int = 5,
        missing_observations_min: int = 1,
        memory_threshold_pct: float = 90.0,
        disk_threshold_pct: float = 90.0,
        max_history: int = _MAX_HISTORY,
    ) -> None:
        self.staleness_threshold_seconds = staleness_threshold_seconds
        self.ingestion_failure_threshold = ingestion_failure_threshold
        self.missing_observations_min = missing_observations_min
        self.memory_threshold_pct = memory_threshold_pct
        self.disk_threshold_pct = disk_threshold_pct
        self._alerts: deque[Alert] = deque(maxlen=max_history)
        self._active: dict[str, Alert] = {}
        self._rules: dict[str, AlertRule] = {}
        self._state: dict[str, Any] = {
            "alert_component": "system",
            "exception_count": 0,
            "last_exception_component": "unknown",
            "last_exception_message": "",
            "market_data_seconds_stale": None,
            "ingestion_failures": 0,
            "observation_count": 0,
            "model_inference_failures": 0,
            "order_processing_failures": 0,
            "checkpoint_corruptions": 0,
            "audit_ledger_failures": 0,
            "risk_breaches": 0,
            "last_risk_limit_name": "unknown",
            "last_risk_current_value": "unknown",
            "kill_switch_activations": 0,
            "last_kill_switch_reason": "",
            "memory_pct": None,
            "disk_pct": None,
        }
        self._thresholds: dict[str, Any] = {
            "staleness_seconds": staleness_threshold_seconds,
            "ingestion_failures": ingestion_failure_threshold,
            "missing_observations_min": missing_observations_min,
            "memory_pct": memory_threshold_pct,
            "disk_pct": disk_threshold_pct,
        }
        self._lock = threading.RLock()
        self._register_default_rules()

    # -- registration -----------------------------------------------------

    def _register_default_rules(self) -> None:
        """Pre-register the default alert rules."""
        rules: list[AlertRule] = [
            AlertRule(
                name="critical_exception",
                condition_func=self._cond_critical_exception,
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=60,
            ),
            AlertRule(
                name="market_data_staleness",
                condition_func=self._cond_market_data_staleness,
                severity=AlertSeverity.WARNING,
                cooldown_seconds=300,
            ),
            AlertRule(
                name="repeated_ingestion_failure",
                condition_func=self._cond_repeated_ingestion_failure,
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=300,
            ),
            AlertRule(
                name="missing_observations",
                condition_func=self._cond_missing_observations,
                severity=AlertSeverity.WARNING,
                cooldown_seconds=300,
            ),
            AlertRule(
                name="model_inference_failure",
                condition_func=self._cond_model_inference_failure,
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=60,
            ),
            AlertRule(
                name="order_processing_failure",
                condition_func=self._cond_order_processing_failure,
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=60,
            ),
            AlertRule(
                name="checkpoint_corruption",
                condition_func=self._cond_checkpoint_corruption,
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=60,
            ),
            AlertRule(
                name="audit_ledger_integrity_failure",
                condition_func=self._cond_audit_ledger_integrity_failure,
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=60,
            ),
            AlertRule(
                name="risk_limit_breach",
                condition_func=self._cond_risk_limit_breach,
                severity=AlertSeverity.WARNING,
                cooldown_seconds=300,
            ),
            AlertRule(
                name="kill_switch_activation",
                condition_func=self._cond_kill_switch_activation,
                severity=AlertSeverity.CRITICAL,
                cooldown_seconds=60,
            ),
            AlertRule(
                name="abnormal_memory_usage",
                condition_func=self._cond_abnormal_memory_usage,
                severity=AlertSeverity.WARNING,
                cooldown_seconds=300,
            ),
            AlertRule(
                name="abnormal_disk_usage",
                condition_func=self._cond_abnormal_disk_usage,
                severity=AlertSeverity.WARNING,
                cooldown_seconds=300,
            ),
        ]
        for rule in rules:
            self._rules[rule.name] = rule

    def register_rule(self, rule: AlertRule) -> None:
        """Register ``rule`` (replacing any rule with the same name)."""
        try:
            with self._lock:
                self._rules[rule.name] = rule
        except Exception:
            pass

    # -- condition functions ---------------------------------------------

    def _event_triggered(self, state_key: str) -> bool:
        """Return True once per new event counted under ``state_key``."""
        current = int(self._state.get(state_key, 0))
        watermark_key = f"_wm:{state_key}"
        watermark = int(self._state.get(watermark_key, 0))
        if current > watermark:
            self._state[watermark_key] = current
            return True
        return False

    def _cond_critical_exception(self) -> tuple[bool, str]:
        if not self._event_triggered("exception_count"):
            return False, ""
        return (
            True,
            f"exception in {self._state['last_exception_component']}: "
            f"{self._state['last_exception_message']}",
        )

    def _cond_market_data_staleness(self) -> tuple[bool, str]:
        stale = self._state.get("market_data_seconds_stale")
        if stale is None:
            return False, ""
        threshold = self._thresholds["staleness_seconds"]
        if float(stale) > float(threshold):
            return (
                True,
                f"market data stale for {stale}s (threshold {threshold}s)",
            )
        return False, ""

    def _cond_repeated_ingestion_failure(self) -> tuple[bool, str]:
        count = int(self._state.get("ingestion_failures", 0))
        threshold = int(self._thresholds["ingestion_failures"])
        if count >= threshold:
            return True, f"{count} ingestion failures (threshold {threshold})"
        return False, ""

    def _cond_missing_observations(self) -> tuple[bool, str]:
        count = int(self._state.get("observation_count", 0))
        threshold = int(self._thresholds["missing_observations_min"])
        if count < threshold:
            return True, f"observation count {count} below minimum {threshold}"
        return False, ""

    def _cond_model_inference_failure(self) -> tuple[bool, str]:
        if not self._event_triggered("model_inference_failures"):
            return False, ""
        return True, "model inference failed"

    def _cond_order_processing_failure(self) -> tuple[bool, str]:
        if not self._event_triggered("order_processing_failures"):
            return False, ""
        return True, "order processing failed"

    def _cond_checkpoint_corruption(self) -> tuple[bool, str]:
        if not self._event_triggered("checkpoint_corruptions"):
            return False, ""
        return True, "checkpoint integrity check failed"

    def _cond_audit_ledger_integrity_failure(self) -> tuple[bool, str]:
        if not self._event_triggered("audit_ledger_failures"):
            return False, ""
        return True, "audit ledger hash chain verification failed"

    def _cond_risk_limit_breach(self) -> tuple[bool, str]:
        if not self._event_triggered("risk_breaches"):
            return False, ""
        return (
            True,
            f"risk limit breached: {self._state['last_risk_limit_name']} "
            f"={self._state['last_risk_current_value']}",
        )

    def _cond_kill_switch_activation(self) -> tuple[bool, str]:
        if not self._event_triggered("kill_switch_activations"):
            return False, ""
        return True, f"kill switch activated: {self._state['last_kill_switch_reason']}"

    def _cond_abnormal_memory_usage(self) -> tuple[bool, str]:
        pct = self._state.get("memory_pct")
        if pct is None:
            return False, ""
        threshold = float(self._thresholds["memory_pct"])
        if float(pct) > threshold:
            return True, f"memory usage {pct}% exceeds threshold {threshold}%"
        return False, ""

    def _cond_abnormal_disk_usage(self) -> tuple[bool, str]:
        pct = self._state.get("disk_pct")
        if pct is None:
            return False, ""
        threshold = float(self._thresholds["disk_pct"])
        if float(pct) > threshold:
            return True, f"disk usage {pct}% exceeds threshold {threshold}%"
        return False, ""
    # -- core evaluation --------------------------------------------------

    def evaluate_rules(self) -> list[Alert]:
        """Evaluate every registered rule and return newly fired alerts.

        Rules are deduplicated by cooldown: a rule will not fire again
        within ``cooldown_seconds`` of its last firing.
        """
        try:
            with self._lock:
                now = datetime.now(UTC)
                new_alerts: list[Alert] = []
                for rule in self._rules.values():
                    try:
                        triggered, message = rule.condition_func()
                    except Exception:
                        triggered, message = (
                            False,
                            f"rule {rule.name!r} condition raised an exception",
                        )
                    if not triggered:
                        continue
                    if rule.last_triggered_utc is not None:
                        try:
                            last = datetime.fromisoformat(rule.last_triggered_utc)
                        except ValueError:
                            last = None
                        if last is not None and (
                            now - last
                        ).total_seconds() < rule.cooldown_seconds:
                            continue
                    alert = Alert(
                        id=uuid.uuid4().hex,
                        severity=rule.severity,
                        component=str(self._state.get("alert_component", "system")),
                        message=message,
                        timestamp_utc=_iso_utc_now(),
                        rule_name=rule.name,
                    )
                    rule.last_triggered_utc = _iso_utc_now()
                    self._alerts.append(alert)
                    self._active[alert.id] = alert
                    new_alerts.append(alert)
                for alert in new_alerts:
                    self.notify(alert)
                return new_alerts
        except Exception:
            return []

    # -- external event recording ----------------------------------------

    def record_exception(self, component: str, exception: BaseException) -> list[Alert]:
        """Record an exception and trigger the critical_exception alert."""
        try:
            with self._lock:
                self._state["exception_count"] = (
                    int(self._state.get("exception_count", 0)) + 1
                )
                self._state["last_exception_component"] = str(component)
                self._state["last_exception_message"] = repr(exception)
                self._state["alert_component"] = str(component)
            return self.evaluate_rules()
        except Exception:
            return []

    def record_market_data_staleness(self, seconds_stale: float) -> list[Alert]:
        """Check market-data staleness against the configured threshold."""
        try:
            with self._lock:
                self._state["market_data_seconds_stale"] = float(seconds_stale)
                self._state["alert_component"] = "market_data"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_ingestion_failure(self) -> list[Alert]:
        """Increment the ingestion failure counter and alert if thresholded."""
        try:
            with self._lock:
                self._state["ingestion_failures"] = (
                    int(self._state.get("ingestion_failures", 0)) + 1
                )
                self._state["alert_component"] = "ingestion"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_model_inference_failure(self) -> list[Alert]:
        """Trigger the model_inference_failure alert."""
        try:
            with self._lock:
                self._state["model_inference_failures"] = (
                    int(self._state.get("model_inference_failures", 0)) + 1
                )
                self._state["alert_component"] = "model"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_order_processing_failure(self) -> list[Alert]:
        """Trigger the order_processing_failure alert."""
        try:
            with self._lock:
                self._state["order_processing_failures"] = (
                    int(self._state.get("order_processing_failures", 0)) + 1
                )
                self._state["alert_component"] = "order_processing"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_checkpoint_corruption(self) -> list[Alert]:
        """Trigger the checkpoint_corruption alert."""
        try:
            with self._lock:
                self._state["checkpoint_corruptions"] = (
                    int(self._state.get("checkpoint_corruptions", 0)) + 1
                )
                self._state["alert_component"] = "checkpoint"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_audit_ledger_integrity_failure(self) -> list[Alert]:
        """Trigger the audit_ledger_integrity_failure alert."""
        try:
            with self._lock:
                self._state["audit_ledger_failures"] = (
                    int(self._state.get("audit_ledger_failures", 0)) + 1
                )
                self._state["alert_component"] = "audit_ledger"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_risk_limit_breach(
        self, limit_name: str, current_value: Any
    ) -> list[Alert]:
        """Trigger the risk_limit_breach alert."""
        try:
            with self._lock:
                self._state["risk_breaches"] = (
                    int(self._state.get("risk_breaches", 0)) + 1
                )
                self._state["last_risk_limit_name"] = str(limit_name)
                self._state["last_risk_current_value"] = repr(current_value)
                self._state["alert_component"] = "risk"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_kill_switch_activation(self, reason: str) -> list[Alert]:
        """Trigger the kill_switch_activation alert."""
        try:
            with self._lock:
                self._state["kill_switch_activations"] = (
                    int(self._state.get("kill_switch_activations", 0)) + 1
                )
                self._state["last_kill_switch_reason"] = str(reason)
                self._state["alert_component"] = "kill_switch"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_memory_usage(self, pct: float) -> list[Alert]:
        """Check memory usage against the configured threshold."""
        try:
            with self._lock:
                self._state["memory_pct"] = float(pct)
                self._state["alert_component"] = "system"
            return self.evaluate_rules()
        except Exception:
            return []

    def record_disk_usage(self, pct: float) -> list[Alert]:
        """Check disk usage against the configured threshold."""
        try:
            with self._lock:
                self._state["disk_pct"] = float(pct)
                self._state["alert_component"] = "system"
            return self.evaluate_rules()
        except Exception:
            return []

    # -- query / mutation -------------------------------------------------

    def get_active_alerts(self) -> list[Alert]:
        """Return unresolved alerts."""
        try:
            with self._lock:
                return [
                    alert
                    for alert in self._active.values()
                    if not alert.resolved
                ]
        except Exception:
            return []

    def get_alert_history(self, limit: int = _MAX_HISTORY) -> list[Alert]:
        """Return up to ``limit`` most recent alerts (history capped at 1000)."""
        try:
            with self._lock:
                history = list(self._alerts)
        except Exception:
            return []
        try:
            limited = max(0, min(int(limit), _MAX_HISTORY))
        except (TypeError, ValueError):
            limited = _MAX_HISTORY
        return history[-limited:] if limited else []

    def resolve_alert(self, alert_id: str) -> bool:
        """Mark ``alert_id`` as resolved; return True if found."""
        try:
            with self._lock:
                alert = self._active.get(alert_id)
                if alert is None:
                    for candidate in self._alerts:
                        if candidate.id == alert_id:
                            alert = candidate
                            break
                if alert is None:
                    return False
                alert.resolved = True
                alert.resolved_at_utc = _iso_utc_now()
                self._active.pop(alert_id, None)
                return True
        except Exception:
            return False

    def notify(self, alert: Alert) -> None:
        """Emit ``alert`` to the local console/log (stderr via logging).

        Never calls external services.
        """
        try:
            logger = get_logger("crypto_engine.observability.alerting")
            text = (
                f"[alert] {alert.severity.value.upper()} "
                f"{alert.rule_name}: {alert.message} "
                f"(component={alert.component}, id={alert.id})"
            )
            if alert.severity == AlertSeverity.CRITICAL:
                logger.critical(text)
            elif alert.severity == AlertSeverity.WARNING:
                logger.warning(text)
            else:
                logger.info(text)
        except Exception:
            pass

    def fail_closed(
        self, condition: bool, message: str, component: str = "system"
    ) -> bool:
        """Emit a CRITICAL alert and return True if ``condition`` is True.

        The caller should fail closed when this returns True.
        """
        try:
            if not condition:
                return False
            alert = Alert(
                id=uuid.uuid4().hex,
                severity=AlertSeverity.CRITICAL,
                component=component,
                message=message,
                timestamp_utc=_iso_utc_now(),
                rule_name="fail_closed",
            )
            with self._lock:
                self._alerts.append(alert)
                self._active[alert.id] = alert
            self.notify(alert)
            return True
        except Exception:
            return False


alert_manager = AlertManager()

def fail_closed(condition: bool, message: str, component: str = "system") -> bool:
    """Emit a CRITICAL alert and return True if `condition` is True.

    The caller should fail closed when this returns True."""
    return alert_manager.fail_closed(condition, message, component)

__all__ = [
    "Alert",
    "AlertManager",
    "AlertRule",
    "AlertSeverity",
    "alert_manager",
    "fail_closed",
]
