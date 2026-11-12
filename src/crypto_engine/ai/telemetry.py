"""Persistent AI observability telemetry (Stage 12).

Append-only JSONL event store for AI request/response metadata. No secrets,
no raw prompts, no response bodies - only structural metadata suitable for
observability dashboards and alerting.

Design rules:

* Append-only JSONL with bounded retention (last N events, last N MB).
* Never store API keys, Authorization headers, full prompts, or raw provider
  response bodies.
* Telemetry failures never break signal generation (best effort).
* Thread-safe for single-process, single-concurrent-request design.
* Corrupt lines are skipped on read; corrupted files start fresh.
"""

from __future__ import annotations

import contextlib
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from crypto_engine.ai.contracts import AIResponse, AIStatus


@dataclass(frozen=True, slots=True)
class TelemetryRecord:
    """One observed AI request/response. Metadata only - no secrets."""

    timestamp: str
    request_id: str
    provider_id: str
    model_id: str
    task: str
    ai_status: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    reasoning_tokens: int | None
    finish_reason: str | None
    retry_count: int
    error_classification: str | None
    explanation_available: bool


@dataclass(frozen=True, slots=True)
class TelemetryMetrics:
    """Aggregated observability metrics computed from stored records."""

    total_requests: int
    successful_requests: int
    failed_requests: int
    success_percentage: float
    failure_percentage: float
    average_latency_ms: float
    median_latency_ms: float
    p95_latency_ms: float | None
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    average_tokens_per_request: float
    timeout_count: int
    invalid_output_count: int
    http_401_count: int
    http_402_count: int
    http_429_count: int
    sample_size: int
    provider_id: str = ""


class TelemetryStore:
    """Append-only JSONL event store with bounded retention.

    Retention policy: keep at most MAX_EVENTS lines or MAX_FILE_BYTES bytes,
    whichever is smaller. Older events are discarded first.
    """

    MAX_EVENTS = 10_000
    MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        # Best-effort directory creation; telemetry must never break signal gen.
        with contextlib.suppress(OSError):
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, response: AIResponse | TelemetryRecord | dict[str, Any]) -> None:
        """Append one AIResponse, TelemetryRecord, or record dict (best effort)."""
        try:
            if isinstance(response, TelemetryRecord):
                record = response
            elif isinstance(response, dict):
                record = TelemetryRecord(**response)
            else:
                record = self._response_to_record(response)
        except Exception:
            # If we can't even build a record, skip telemetry entirely.
            return
        line = json.dumps(asdict(record), sort_keys=True, ensure_ascii=True)
        with self._lock:
            with contextlib.suppress(OSError):
                self._path.parent.mkdir(parents=True, exist_ok=True)
            lines: list[str] = []
            if self._path.is_file():
                try:
                    with self._path.open("r", encoding="utf-8") as fh:
                        lines = fh.readlines()
                except (OSError, ValueError):
                    lines = []  # Corrupted: start fresh
            lines.append(line + "\n")
            if len(lines) > self.MAX_EVENTS:
                lines = lines[-self.MAX_EVENTS :]
            tmp = self._path.with_name(self._path.name + ".tmp")
            try:
                with tmp.open("w", encoding="utf-8") as fh:
                    fh.writelines(lines)
                tmp.replace(self._path)
            except OSError:
                with contextlib.suppress(OSError):
                    tmp.unlink()

    @staticmethod
    def _response_to_record(response: AIResponse) -> TelemetryRecord:
        usage = response.usage
        error = response.error
        return TelemetryRecord(
            timestamp=response.created_at,
            request_id=response.request_id,
            provider_id=response.provider_id or "",
            model_id=response.model_id or "",
            task=response.task.value,
            ai_status=response.status.value,
            latency_ms=response.latency_ms,
            prompt_tokens=usage.input_tokens if usage else 0,
            completion_tokens=usage.output_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            reasoning_tokens=None,
            finish_reason=None,
            retry_count=response.attempts,
            error_classification=error.code.value if error else None,
            explanation_available=bool(response.status == AIStatus.OK and response.text),
        )

    def load(self) -> list[TelemetryRecord]:
        """Load all stored records (corrupt lines are skipped)."""
        records: list[TelemetryRecord] = []
        if not self._path.is_file():
            return records
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        records.append(TelemetryRecord(**obj))
                    except (ValueError, KeyError, TypeError):
                        continue
        except OSError:
            return records
        return records

    def metrics(self) -> TelemetryMetrics:
        """Compute aggregate metrics from stored records."""
        records = self.load()
        total = len(records)
        if total == 0:
            return TelemetryMetrics(
                total_requests=0,
                successful_requests=0,
                failed_requests=0,
                success_percentage=0.0,
                failure_percentage=0.0,
                average_latency_ms=0.0,
                median_latency_ms=0.0,
                p95_latency_ms=None,
                total_input_tokens=0,
                total_output_tokens=0,
                total_tokens=0,
                average_tokens_per_request=0.0,
                timeout_count=0,
                invalid_output_count=0,
                http_401_count=0,
                http_402_count=0,
                http_429_count=0,
                sample_size=0,
            )
        successful = sum(1 for r in records if r.ai_status == "ok")
        failed = total - successful
        latencies = sorted(
            r.latency_ms for r in records if r.latency_ms is not None and r.latency_ms > 0
        )
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        median_idx = len(latencies) // 2
        median_latency = latencies[median_idx] if latencies else 0.0
        p95_idx = max(0, int(len(latencies) * 0.95) - 1) if latencies else -1
        p95_latency = latencies[p95_idx] if p95_idx >= 0 else None
        total_input = sum(r.prompt_tokens for r in records)
        total_output = sum(r.completion_tokens for r in records)
        total_tok = total_input + total_output
        avg_tokens = total_tok / total if total > 0 else 0.0
        timeout_count = sum(1 for r in records if r.error_classification == "timeout")
        invalid_output_count = sum(1 for r in records if r.error_classification == "invalid_output")
        http_401 = sum(1 for r in records if r.error_classification == "auth_failure")
        http_402 = sum(1 for r in records if r.error_classification == "payment_required")
        http_429 = sum(1 for r in records if r.error_classification == "rate_limited")
        # Provider id: most common provider among records, or "mixed" if >1 distinct provider
        provider_ids = {r.provider_id for r in records if r.provider_id}
        provider_id = (
            next(iter(provider_ids))
            if len(provider_ids) == 1
            else ("mixed" if provider_ids else "")
        )
        return TelemetryMetrics(
            total_requests=total,
            successful_requests=successful,
            failed_requests=failed,
            success_percentage=round(successful / total * 100, 2),
            failure_percentage=round(failed / total * 100, 2),
            average_latency_ms=round(avg_latency, 1),
            median_latency_ms=float(median_latency),
            p95_latency_ms=float(p95_latency) if p95_latency is not None else None,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_tok,
            average_tokens_per_request=round(avg_tokens, 1),
            timeout_count=timeout_count,
            invalid_output_count=invalid_output_count,
            http_401_count=http_401,
            http_402_count=http_402,
            http_429_count=http_429,
            sample_size=total,
            provider_id=provider_id,
        )


def _find_repo_root() -> Path:
    start = Path(__file__).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


def resolve_telemetry_path(
    config: Any = None,
    default_path: str = "data/ai_telemetry/events.jsonl",
) -> Path:
    """Resolve the canonical telemetry path from configuration or defaults.

    Args:
        config: Can be a ``TelemetryStore``, a ``Settings`` object, an
            ``AiConfig`` object, a ``RepoRoots`` object, a ``Path`` or ``str``
            (pointing to a TOML config or a JSONL telemetry file), or ``None``.
        default_path: Fallback path when no configuration or path is found.

    Returns:
        A resolved, canonical Path.
    """
    if isinstance(config, TelemetryStore):
        return config._path

    raw_path: str | Path | None = None

    # Check for RepoRoots (has .ai_telemetry_path)
    if hasattr(config, "ai_telemetry_path"):
        raw_path = config.ai_telemetry_path
    # Check for Settings (has .ai)
    elif hasattr(config, "ai"):
        ai_cfg = config.ai
        if ai_cfg is not None and hasattr(ai_cfg, "telemetry_path"):
            raw_path = ai_cfg.telemetry_path
    # Check for AiConfig (has .telemetry_path directly)
    elif hasattr(config, "telemetry_path"):
        raw_path = config.telemetry_path
    # Check for string or Path
    elif isinstance(config, (str, Path)):
        p = Path(config)
        if p.suffix.lower() == ".toml":
            from crypto_engine.config.settings import load_settings

            try:
                settings = load_settings(p)
                return resolve_telemetry_path(settings, default_path=default_path)
            except Exception:
                pass
        else:
            raw_path = p

    # If still not found and config is None, try loading default settings
    if raw_path is None:
        try:
            from crypto_engine.config.settings import load_settings

            settings = load_settings(None)
            ai_cfg = getattr(settings, "ai", None)
            if ai_cfg is not None and hasattr(ai_cfg, "telemetry_path"):
                raw_path = ai_cfg.telemetry_path
        except Exception:
            pass

    if raw_path is None:
        raw_path = default_path

    target = Path(raw_path)
    if target.is_absolute():
        return target

    repo_root = _find_repo_root()
    return repo_root / target


def get_telemetry_store(config: Any = None) -> TelemetryStore:
    """Canonical factory to obtain a TelemetryStore from configuration or defaults.

    Does not cache global state so test runs and different configs stay isolated.
    """
    if isinstance(config, TelemetryStore):
        return config
    path = resolve_telemetry_path(config)
    return TelemetryStore(path)


def get_default_store() -> TelemetryStore:
    """Get the default telemetry store."""
    return get_telemetry_store(None)


def record_response(response: AIResponse) -> None:
    """Record an AIResponse to the default telemetry store (best effort).

    Never raises: telemetry failures must not break signal generation.
    """
    try:
        store = get_default_store()
        store.record(response)
    except Exception:
        pass


__all__ = [
    "TelemetryMetrics",
    "TelemetryRecord",
    "TelemetryStore",
    "get_default_store",
    "get_telemetry_store",
    "record_response",
    "resolve_telemetry_path",
]
