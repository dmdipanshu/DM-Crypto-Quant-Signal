"""Centralized structured logging for SignalForge (Phase Q9).

Advisory-only observability: no live trading, no automatic promotion,
no order execution. Logging is fully separate from ``SignalHistory`` and
``EventLedger`` — no shared state, no modification of any ledger hash
chain.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import logging.handlers
import re
import sys
import uuid
from datetime import UTC, datetime
from logging import LogRecord
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from crypto_engine.config.settings import LoggingConfig

__all__ = [
    "LoggingConfig",
    "LoggingContext",
    "SecretRedactingFilter",
    "StructuredFormatter",
    "clear_context",
    "clear_correlation_id",
    "clear_run_id",
    "configure_logging",
    "get_correlation_id",
    "get_logger",
    "get_run_id",
    "set_correlation_id",
    "set_run_id",
]

_REDACTED = "[REDACTED]"

_SECRET_KEYS = [
    "api[_ -]?key",
    "apikey",
    "secret",
    "token",
    "password",
    "private",
    "authorization",
    "bearer",
    "x-api-key",
]

_SECRET_KEY_RE = re.compile(
    r"\b(" + "|".join(_SECRET_KEYS) + r")\b",
    re.IGNORECASE,
)

_SECRET_KV_RE = re.compile(
    r"\b(" + "|".join(_SECRET_KEYS) + r")\s*([:=])\s*(\S+)",
    re.IGNORECASE,
)

_BEARER_RE = re.compile(r"\b[bB]earer\s+\S+", re.IGNORECASE)

_SECRET_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}", re.IGNORECASE)

_STANDARD_LEVELS = frozenset(
    {
        logging.DEBUG,
        logging.INFO,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
        logging.NOTSET,
    }
)

_STANDARD_RECORD_ATTRS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "taskName",
    }
)

_CORRELATION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "run_id", default=None
)


def set_correlation_id(value: str | None) -> None:
    """Set the correlation ID for the current async context."""
    _CORRELATION_ID.set(value)


def get_correlation_id() -> str | None:
    """Get the correlation ID for the current async context."""
    return _CORRELATION_ID.get()


def clear_correlation_id() -> None:
    """Clear the correlation ID for the current async context."""
    _CORRELATION_ID.set(None)


def set_run_id(value: str | None) -> None:
    """Set the run ID for the current async context."""
    _RUN_ID.set(value)


def get_run_id() -> str | None:
    """Get the run ID for the current async context."""
    return _RUN_ID.get()


def clear_run_id() -> None:
    """Clear the run ID for the current async context."""
    _RUN_ID.set(None)


def clear_context() -> None:
    """Clear both correlation ID and run ID for the current async context."""
    _CORRELATION_ID.set(None)
    _RUN_ID.set(None)


class LoggingContext:
    """Context manager that temporarily binds correlation_id and run_id."""

    def __init__(
        self,
        correlation_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self._correlation_id = correlation_id
        self._run_id = run_id
        self._corr_token: Any = None
        self._run_token: Any = None

    def __enter__(self) -> LoggingContext:
        if self._correlation_id is not None:
            self._corr_token = _CORRELATION_ID.set(self._correlation_id)
        if self._run_id is not None:
            self._run_token = _RUN_ID.set(self._run_id)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> bool:
        if self._corr_token is not None:
            _CORRELATION_ID.reset(self._corr_token)
        if self._run_token is not None:
            _RUN_ID.reset(self._run_token)
        return False


def _redact_string(value: str) -> str:
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    value = _SECRET_KV_RE.sub(r"\1\2[REDACTED]", value)
    value = _SECRET_TOKEN_RE.sub("[REDACTED]", value)
    return value
def _redact_any(value: Any) -> Any:
    if isinstance(value, dict):
        return _redact_dict(value)
    if isinstance(value, list):
        return [_redact_any(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_dict(value: dict[Any, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in value.items():
        key_str = str(key)
        if _SECRET_KEY_RE.search(key_str):
            out[_REDACTED] = _REDACTED
        else:
            out[key_str] = _redact_any(val)
    return out


class SecretRedactingFilter(logging.Filter):
    """Redacts secret-like key-value pairs from log records."""

    def filter(self, record: LogRecord) -> bool:
        record.msg = _redact_any(record.msg)
        if record.args:
            record.args = tuple(_redact_any(arg) for arg in record.args)  # type: ignore[assignment]
        self._redact_extra(record)
        return True

    def _redact_extra(self, record: LogRecord) -> None:
        """Redact secret keys/values carried in ``record.__dict__`` (``extra=``)."""
        for key in list(record.__dict__.keys()):
            if key in _STANDARD_RECORD_ATTRS:
                continue
            value = record.__dict__[key]
            if _SECRET_KEY_RE.search(str(key)):
                del record.__dict__[key]
                record.__dict__[_REDACTED] = _REDACTED
            else:
                record.__dict__[key] = _redact_any(value)


class StructuredFormatter(logging.Formatter):
    """JSON or text formatter emitting structured log records."""

    def __init__(self, fmt: str = "json") -> None:
        super().__init__()
        self.fmt = fmt

    def format(self, record: LogRecord) -> str:
        correlation_id = get_correlation_id()
        run_id = get_run_id()

        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": self._safe_message(record),
            "event_id": getattr(record, "event_id", uuid.uuid4().hex),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if correlation_id:
            payload["correlation_id"] = correlation_id
        if run_id:
            payload["run_id"] = run_id
        if record.exc_info:
            payload["exception"] = _redact_string(
                self.formatException(record.exc_info)
            )

        if self.fmt == "json":
            return json.dumps(payload, default=str, ensure_ascii=False)

        parts = [f"{key}={value}" for key, value in payload.items()]
        return " ".join(parts)

    def _safe_message(self, record: LogRecord) -> str:
        try:
            return record.getMessage()
        except Exception:
            return str(record.msg)


class _SafeHandler(logging.Handler):
    """Wraps a handler so emit failures never crash the application."""

    def __init__(self, handler: logging.Handler) -> None:
        super().__init__(level=handler.level)
        self._handler = handler
        self.setFormatter(handler.formatter)

    def emit(self, record: LogRecord) -> None:
        try:
            if not self.filter(record):
                return
            self._handler.emit(record)
        except Exception:
            pass

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self._handler.flush()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._handler.close()


def _build_handler(
    handler: logging.Handler, formatter: logging.Formatter
) -> _SafeHandler:
    handler.setFormatter(formatter)
    safe = _SafeHandler(handler)
    safe.addFilter(SecretRedactingFilter())
    return safe


def configure_logging(settings: LoggingConfig | None = None) -> None:
    """Bootstrap structured logging from a :class:`LoggingConfig`.

    Idempotent: existing handlers on the root logger are cleared first so
    repeated calls do not duplicate output. Missing/unreadable settings fall
    back to safe defaults. A missing or unwritable log file degrades
    gracefully to console-only output.
    """
    if settings is None:
        settings = LoggingConfig()

    root = logging.getLogger()

    for handler in list(root.handlers):
        root.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()

    log_cfg = settings.logging if hasattr(settings, "logging") else settings
    log_cfg = settings.logging if hasattr(settings, "logging") else settings
    level_value = logging.getLevelName(str(log_cfg.level).upper())
    if not isinstance(level_value, int) or level_value not in _STANDARD_LEVELS:
        level_value = logging.INFO

    formatter = StructuredFormatter(fmt=log_cfg.format)

    handlers: list[_SafeHandler] = []

    if log_cfg.console_enabled:
        handlers.append(_build_handler(logging.StreamHandler(sys.stderr), formatter))

    if log_cfg.file_enabled:
        log_path = Path(log_cfg.file_path)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=log_cfg.rotation_max_bytes,
                backupCount=log_cfg.rotation_backup_count,
                encoding="utf-8",
            )
            handlers.append(_build_handler(file_handler, formatter))
        except OSError as exc:
            root.warning("File logging disabled, continuing console-only: %s", exc)

    for handler in handlers:
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for ``name``, auto-configuring defaults."""
    if not logging.getLogger().handlers:
        configure_logging()
    return logging.getLogger(name)
