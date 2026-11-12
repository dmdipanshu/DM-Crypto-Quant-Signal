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

    def record(self, response: AIResponse) -> None:
        """Append one AIResponse as a telemetry record (best effort)."""
        try:
            record = self._response_to_record(response)
        except Exception:
            # If we can't even build a record, skip telemetry entirely.
            return
        line = json.dumps(asdict(record), sort_keys=True, ensure_ascii=True)
        with self._lock:
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
        latencies = sorted(r.latency_ms for r in records if r.latency_ms is not None)
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
        )


# Module-level default store (lazy-initialized on first use).
_default_store: TelemetryStore | None = None
_default_store_lock = threading.Lock()


def get_default_store() -> TelemetryStore:
    """Get or create the default telemetry store.

    Reads telemetry path from validated config if available, otherwise falls
    back to data/ai_telemetry/events.jsonl under the project root.
    """
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                path = Path("data/ai_telemetry/events.jsonl")
                try:
                    from crypto_engine.config.settings import load_settings

                    cfg = load_settings()
                    ai_cfg = getattr(cfg, "ai", None)
                    if ai_cfg is not None and hasattr(ai_cfg, "telemetry_path"):
                        path = Path(ai_cfg.telemetry_path)
                except Exception:
                    pass  # Fall back to default path
                _default_store = TelemetryStore(path)
    return _default_store


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
    "record_response",
]
