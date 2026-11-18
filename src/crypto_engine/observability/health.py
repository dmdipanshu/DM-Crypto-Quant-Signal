"""Read-only health check infrastructure for SignalForge (Phase Q9).

Advisory-only observability: no live trading, no automatic promotion,
no order execution. All checks are read-only, never access the network,
and never raise unhandled exceptions -- failures are reported as
UNHEALTHY with an error message.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_engine.config.settings import Settings, default_config_path, load_settings

try:
    import psutil  # type: ignore[import-not-found]
except ImportError:
    psutil = None


class HealthStatus(StrEnum):
    """Overall and per-component health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class ComponentHealth:
    """Result of a single component health check."""

    name: str
    status: HealthStatus
    message: str
    last_check_utc: str
    check_duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)


CheckFunc = Callable[[], tuple[HealthStatus, str, dict[str, Any]]]


def _iso_utc_now() -> str:
    """Current UTC time as ISO-8601 with a trailing Z."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class HealthChecker:
    """Registry of read-only component health checks.

    Attributes:
        started_at: Moment this checker was instantiated (UTC).
        _checks: Registered checks by name.
    """

    def __init__(self) -> None:
        self.started_at: datetime = datetime.now(UTC)
        self._checks: dict[str, CheckFunc] = {}

    @property
    def uptime_seconds(self) -> float:
        """Seconds elapsed since this checker was created."""
        return (datetime.now(UTC) - self.started_at).total_seconds()

    def register_check(
        self,
        name: str,
        check_func: CheckFunc,
    ) -> None:
        """Register a component health check function by name."""
        self._checks[name] = check_func

    def run_checks(self) -> dict[str, ComponentHealth]:
        """Run every registered check and return ``name -> ComponentHealth``.

        Check failures are caught and reported as UNHEALTHY; an exception
        in one check never prevents the remaining checks from running.
        """
        results: dict[str, ComponentHealth] = {}
        for name, check_func in self._checks.items():
            started = time.perf_counter()
            status = HealthStatus.UNHEALTHY
            message = "check did not return a result"
            details: dict[str, Any] = {}
            try:
                result_status, result_message, result_details = check_func()
                details = dict(result_details or {})
                if not isinstance(result_status, HealthStatus):
                    status = HealthStatus.UNHEALTHY
                    message = f"check returned invalid status: {result_status!r}"
                else:
                    status = result_status
                    message = str(result_message)
            except Exception as exc:
                status = HealthStatus.UNHEALTHY
                message = f"check raised {type(exc).__name__}: {exc}"
            duration_ms = (time.perf_counter() - started) * 1000.0
            results[name] = ComponentHealth(
                name=name,
                status=status,
                message=message,
                last_check_utc=_iso_utc_now(),
                check_duration_ms=duration_ms,
                details=details,
            )
        return results

    def overall_status(self) -> HealthStatus:
        """Aggregate status: UNHEALTHY if any check is UNHEALTHY, else
        DEGRADED if any check is DEGRADED, else HEALTHY."""
        statuses = [health.status for health in self.run_checks().values()]
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY


def check_config(settings: Settings) -> tuple[HealthStatus, str, dict[str, Any]]:
    """Config file exists and loads."""
    path = default_config_path()
    details: dict[str, Any] = {"config_path": str(path)}
    try:
        if not path.is_file():
            details["exists"] = False
            return HealthStatus.UNHEALTHY, "config file not found", details
        details["exists"] = True
        load_settings(path)
        details["loaded"] = True
        return HealthStatus.HEALTHY, "config file exists and loads", details
    except Exception as exc:
        details["loaded"] = False
        return HealthStatus.UNHEALTHY, f"config load failed: {exc}", details


def check_storage(settings: Settings) -> tuple[HealthStatus, str, dict[str, Any]]:
    """Data storage directory exists and is writable."""
    path = Path(settings.data.storage_path)
    details: dict[str, Any] = {"storage_path": str(path)}
    try:
        details["exists"] = path.is_dir()
        if not details["exists"]:
            return HealthStatus.UNHEALTHY, "storage directory missing", details
        details["writable"] = os.access(path, os.W_OK)
        if not details["writable"]:
            return HealthStatus.UNHEALTHY, "storage directory not writable", details
        return HealthStatus.HEALTHY, "storage directory exists and is writable", details
    except Exception as exc:
        return HealthStatus.UNHEALTHY, f"storage check failed: {exc}", details


def check_signal_ledger(settings: Settings) -> tuple[HealthStatus, str, dict[str, Any]]:
    """Signal history chain verifies."""
    from crypto_engine.signals.history import SignalHistory

    path = Path(settings.signal.history_path)
    details: dict[str, Any] = {"path": str(path)}
    try:
        if not path.is_dir():
            return HealthStatus.HEALTHY, "signal ledger absent (empty chain)", details
        history = SignalHistory(path)
        ok, errors = history.verify()
        details["verified"] = ok
        details["errors"] = errors
        if ok:
            return HealthStatus.HEALTHY, "signal ledger chain verifies", details
        return HealthStatus.UNHEALTHY, f"signal ledger chain broken ({len(errors)} error(s))", details
    except Exception as exc:
        return HealthStatus.UNHEALTHY, f"signal ledger check failed: {exc}", details


def check_paper_account(settings: Settings) -> tuple[HealthStatus, str, dict[str, Any]]:
    """Paper account checksum verifies."""
    from crypto_engine.paper_trading.state import load_account

    path = Path(settings.paper.account_path)
    details: dict[str, Any] = {"path": str(path)}
    try:
        if not path.is_file():
            return HealthStatus.UNHEALTHY, "paper account file missing", details
        load_account(path)
        details["verified"] = True
        return HealthStatus.HEALTHY, "paper account checksum verifies", details
    except Exception as exc:
        return HealthStatus.UNHEALTHY, f"paper account check failed: {exc}", details


def check_event_ledger(settings: Settings) -> tuple[HealthStatus, str, dict[str, Any]]:
    """Event ledger chain verifies."""
    from crypto_engine.paper_trading.resilience import EventLedger

    path = Path(settings.paper.account_path).parent / "events.json"
    details: dict[str, Any] = {"path": str(path)}
    try:
        if not path.is_file():
            return HealthStatus.HEALTHY, "event ledger absent (empty chain)", details
        ledger = EventLedger(path)
        ok = ledger.verify_chain()
        details["verified"] = ok
        if ok:
            return HealthStatus.HEALTHY, "event ledger chain verifies", details
        return HealthStatus.UNHEALTHY, "event ledger chain broken", details
    except Exception as exc:
        return HealthStatus.UNHEALTHY, f"event ledger check failed: {exc}", details


def check_model_registry(settings: Settings) -> tuple[HealthStatus, str, dict[str, Any]]:
    """Model registry directory exists."""
    path = Path(settings.registry.path)
    details: dict[str, Any] = {"registry_path": str(path)}
    try:
        details["exists"] = path.is_dir()
        if not details["exists"]:
            return HealthStatus.UNHEALTHY, "model registry directory missing", details
        return HealthStatus.HEALTHY, "model registry directory exists", details
    except Exception as exc:
        return HealthStatus.UNHEALTHY, f"model registry check failed: {exc}", details


def check_system_health() -> tuple[HealthStatus, str, dict[str, Any]]:
    """Process status, uptime, CPU/memory/disk usage (psutil optional)."""
    details: dict[str, Any] = {
        "pid": os.getpid(),
        "psutil_available": psutil is not None,
    }
    parts: list[str] = []
    try:
        process_status: str | None = None
        process_uptime_s: float | None = None
        cpu_percent: float | None = None
        memory_percent: float | None = None
        if psutil is not None:
            try:
                process = psutil.Process()
                process_status = str(process.status())
                process_uptime_s = time.time() - process.create_time()
                cpu_percent = psutil.cpu_percent(interval=None)
                memory = psutil.virtual_memory()
                memory_percent = memory.percent
                details["memory_available_bytes"] = memory.available
            except Exception:
                process_status = "unavailable"
        parts.append(f"process={process_status}")
        if process_uptime_s is not None:
            parts.append(f"uptime_s={process_uptime_s:.1f}")
        if cpu_percent is not None:
            parts.append(f"cpu_percent={cpu_percent}")
        if memory_percent is not None:
            parts.append(f"memory_percent={memory_percent}")
        disk = shutil.disk_usage(Path.cwd())
        details["disk_total_bytes"] = disk.total
        details["disk_free_bytes"] = disk.free
        details["disk_percent"] = round((disk.used / disk.total) * 100, 2) if disk.total else 0.0
        parts.append(f"disk_percent={details['disk_percent']}")
        details["process_status"] = process_status
        details["process_uptime_s"] = process_uptime_s
        details["cpu_percent"] = cpu_percent
        details["memory_percent"] = memory_percent
        if psutil is None:
            parts.append("psutil unavailable (stdlib only)")
        return HealthStatus.HEALTHY, "; ".join(parts), details
    except Exception as exc:
        return HealthStatus.UNHEALTHY, f"system health check failed: {exc}", details


def check_metrics_collector() -> tuple[HealthStatus, str, dict[str, Any]]:
    """Metrics registry is responsive."""
    from crypto_engine.observability.metrics import MetricsRegistry

    details: dict[str, Any] = {}
    try:
        registry = MetricsRegistry.get_instance()
        series = registry.collect_all()
        details["series_count"] = len(series)
        return HealthStatus.HEALTHY, "metrics registry responsive", details
    except Exception as exc:
        return HealthStatus.UNHEALTHY, f"metrics collector check failed: {exc}", details


def check_prospective_collection() -> tuple[HealthStatus, str, dict[str, Any]]:
    """Prospective collection storage is readable and ledger chain is valid.

    Read-only: no writes, no network, no credential access.
    """
    from crypto_engine.research.prospective.storage import ProspectiveStorage

    path = Path("data/prospective/binance/spot/BTC/USDT/1h")
    details: dict[str, Any] = {"storage_path": str(path)}
    try:
        if not path.is_dir():
            details["ledger_valid"] = False
            details["ledger_event_count"] = 0
            details["raw_ingestion_events"] = 0
            details["accepted_unique_observations"] = 0
            details["duplicate_count"] = 0
            details["conflict_count"] = 0
            return HealthStatus.UNHEALTHY, "prospective storage directory missing", details
        storage = ProspectiveStorage(path)
        chain = storage.verify_ledger_chain()
        summary = storage.get_observation_summary()
        details["ledger_valid"] = bool(chain.get("valid"))
        details["ledger_event_count"] = int(chain.get("event_count", 0))
        details["raw_ingestion_events"] = int(summary.get("raw_ingestion_events", 0))
        details["accepted_unique_observations"] = int(
            summary.get("accepted_unique_observations", 0)
        )
        details["duplicate_count"] = int(summary.get("duplicate_ingestion_attempts", 0))
        details["conflict_count"] = int(summary.get("conflicting_observations", 0))
        if not details["ledger_valid"]:
            return HealthStatus.UNHEALTHY, "prospective ledger chain invalid", details
        return HealthStatus.HEALTHY, "prospective collection readable and ledger valid", details
    except Exception as exc:
        return HealthStatus.UNHEALTHY, f"prospective collection check failed: {exc}", details


health_checker = HealthChecker()

health_checker.register_check("config", lambda: check_config(load_settings()))
health_checker.register_check("storage", lambda: check_storage(load_settings()))
health_checker.register_check(
    "signal_ledger", lambda: check_signal_ledger(load_settings())
)
health_checker.register_check(
    "paper_account", lambda: check_paper_account(load_settings())
)
health_checker.register_check(
    "event_ledger", lambda: check_event_ledger(load_settings())
)
health_checker.register_check(
    "model_registry", lambda: check_model_registry(load_settings())
)
health_checker.register_check("system_health", check_system_health)
health_checker.register_check("metrics_collector", check_metrics_collector)
health_checker.register_check(
    "prospective_collection", lambda: check_prospective_collection()
)

__all__ = [
    "ComponentHealth",
    "HealthChecker",
    "HealthStatus",
    "check_config",
    "check_event_ledger",
    "check_metrics_collector",
    "check_model_registry",
    "check_paper_account",
    "check_prospective_collection",
    "check_signal_ledger",
    "check_storage",
    "check_system_health",
    "health_checker",
]


