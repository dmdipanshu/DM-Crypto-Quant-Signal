"""Progress event schema, validation, and status transitions.

The ledger is append-only JSONL. Every event is validated before it is
written and again when it is read, so a hand-edited or corrupted line is
reported rather than silently trusted. The schema rejects:

* unknown top-level fields (schema drift),
* non-UTC or naive timestamps,
* absolute paths and ``..`` traversal in ``files``,
* secret-like keys and credential-shaped values,
* invalid status transitions for the same work item.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

SCHEMA_VERSION = 1

LEVELS = frozenset(
    {
        "project",
        "workstream",
        "phase",
        "stage",
        "subsystem",
        "task",
        "step",
        "verification",
        "evidence",
        "test",
        "security",
        "file",
        "error",
        "agent",
    }
)

STATUSES = frozenset(
    {
        "NOT_STARTED",
        "PLANNED",
        "IN_PROGRESS",
        "REVIEW",
        "BLOCKED",
        "PASS",
        "PASS_WITH_FINDINGS",
        "FAILED",
        "FROZEN",
        "HISTORICAL",
        "DEFERRED",
        "NOT_VERIFIED",
    }
)

STATUS_DESCRIPTIONS = {
    "NOT_STARTED": "Work has not begun and no plan is recorded yet.",
    "PLANNED": "Work is scheduled but implementation has not started.",
    "IN_PROGRESS": "Work is actively being implemented or investigated.",
    "REVIEW": "Implementation exists and is being reviewed or audited.",
    "BLOCKED": "Work cannot proceed until an explicit blocker is removed.",
    "PASS": "Recorded evidence supports the claimed result.",
    "PASS_WITH_FINDINGS": "The result passed with documented, non-blocking findings.",
    "FAILED": "Recorded evidence contradicts the claimed result.",
    "FROZEN": "The accepted result is frozen; reopening needs explicit authorization.",
    "HISTORICAL": "Recorded historical context, not current filesystem evidence.",
    "DEFERRED": "Work is intentionally postponed and is not currently active.",
    "NOT_VERIFIED": "No recorded evidence exists for this claim yet.",
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "NOT_STARTED": {"PLANNED", "IN_PROGRESS", "BLOCKED", "DEFERRED"},
    "PLANNED": {"NOT_STARTED", "IN_PROGRESS", "BLOCKED", "DEFERRED"},
    "IN_PROGRESS": {"REVIEW", "PASS", "PASS_WITH_FINDINGS", "FAILED", "BLOCKED", "DEFERRED"},
    "REVIEW": {"PASS", "PASS_WITH_FINDINGS", "FAILED", "IN_PROGRESS", "BLOCKED"},
    "BLOCKED": {"IN_PROGRESS", "PLANNED", "DEFERRED", "FAILED"},
    "PASS": {"FROZEN", "REVIEW", "FAILED", "IN_PROGRESS"},
    "PASS_WITH_FINDINGS": {"FROZEN", "REVIEW", "FAILED", "IN_PROGRESS", "PASS"},
    "FAILED": {"IN_PROGRESS", "REVIEW", "BLOCKED", "DEFERRED"},
    "FROZEN": set(),
    "HISTORICAL": set(),
    "DEFERRED": {"PLANNED", "IN_PROGRESS"},
    "NOT_VERIFIED": {"PASS", "PASS_WITH_FINDINGS", "FAILED", "IN_PROGRESS", "BLOCKED"},
}

REQUIRED_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "timestamp_utc",
        "level",
        "workstream",
        "status",
        "title",
        "summary",
    }
)

OPTIONAL_FIELDS = frozenset(
    {
        "phase",
        "stage",
        "task_id",
        "details",
        "files",
        "tests",
        "evidence",
        "blockers",
        "next_action",
        "agent",
        "simple",
        "technical",
    }
)

EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
WORKSTREAM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.:\-]{0,63}$")
PHASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,31}$")
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")

SECRET_KEY_HINTS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "private_key",
    "credential",
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
)

SIMPLE_FIELDS = frozenset({"what", "why", "working", "not_verified", "next"})
TECHNICAL_FIELDS = frozenset(
    {"module_paths", "functions", "config_keys", "error_codes", "commands"}
)


class ProgressValidationError(ValueError):
    """Raised when an event is structurally invalid or unsafe."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) if self.errors else "invalid progress event")


def _add(errors: list[str], message: str) -> None:
    errors.append(message)


def _normalize_utc(value: Any, field: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        _add(errors, f"{field} must be an ISO-8601 UTC string")
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        _add(errors, f"{field} is not valid ISO-8601")
        return None
    if parsed.tzinfo is None:
        _add(errors, f"{field} must include a timezone (use Z or +00:00)")
        return None
    if parsed.utcoffset() != timedelta(0):
        _add(errors, f"{field} must be UTC (use Z or +00:00)")
        return None
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    parts = normalized.split("/")
    return ".." not in parts and all(part not in ("", ".") for part in parts)


def _scan_secrets(value: Any, trail: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(hint in lowered for hint in SECRET_KEY_HINTS) and not lowered.endswith(
                ("_env", "_ref")
            ):
                _add(errors, f"{trail}.{key}: secret-like field names are not allowed")
            _scan_secrets(child, f"{trail}.{key}", errors)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _scan_secrets(child, f"{trail}[{index}]", errors)
        return
    if isinstance(value, str):
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(value):
                _add(errors, f"{trail}: value looks like a credential and was rejected")
                return


def _string(
    value: Any,
    field: str,
    errors: list[str],
    *,
    max_length: int,
    required: bool = True,
) -> str | None:
    if value is None:
        if required:
            _add(errors, f"{field} is required")
        return None
    if not isinstance(value, str):
        _add(errors, f"{field} must be a string")
        return None
    if required and not value.strip():
        _add(errors, f"{field} must not be empty")
        return None
    if len(value) > max_length:
        _add(errors, f"{field} exceeds {max_length} characters")
        return None
    return value


def _string_list(
    value: Any,
    field: str,
    errors: list[str],
    *,
    max_items: int,
    max_length: int,
    path_items: bool = False,
) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        _add(errors, f"{field} must be a list of strings")
        return None
    if len(value) > max_items:
        _add(errors, f"{field} exceeds {max_items} items")
        return None
    output: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            _add(errors, f"{field}[{index}] must be a non-empty string")
            continue
        if len(item) > max_length:
            _add(errors, f"{field}[{index}] exceeds {max_length} characters")
            continue
        if path_items and not _safe_relative_path(item):
            _add(errors, f"{field}[{index}] must be a repository-relative path")
            continue
        output.append(item)
    return output


def _normalize_tests(value: Any, errors: list[str]) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        _add(errors, "tests must be an object")
        return None
    allowed = {"passed", "failed", "skipped", "total", "command", "report", "note"}
    for key in value:
        if key not in allowed:
            _add(errors, f"tests.{key} is not a recognized field")
    counts: dict[str, int] = {}
    for key in ("passed", "failed", "skipped", "total"):
        raw = value.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            _add(errors, f"tests.{key} must be a non-negative integer")
            continue
        counts[key] = raw
    computed = counts.get("passed", 0) + counts.get("failed", 0) + counts.get("skipped", 0)
    if "total" not in counts:
        counts["total"] = computed
    elif counts["total"] < computed:
        _add(errors, "tests.total must be >= passed + failed + skipped")
    output: dict[str, Any] = dict(counts)
    for key in ("command", "report", "note"):
        raw = value.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str) or len(raw) > 1000:
            _add(errors, f"tests.{key} must be a string of at most 1000 characters")
            continue
        output[key] = raw
    return output


def _normalize_simple(value: Any, errors: list[str]) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        _add(errors, "simple must be an object")
        return None
    for key in value:
        if key not in SIMPLE_FIELDS:
            _add(errors, f"simple.{key} is not a recognized field")
    output: dict[str, str] = {}
    for key in SIMPLE_FIELDS:
        raw = value.get(key)
        if raw is None:
            continue
        if not isinstance(raw, str) or len(raw) > 2000:
            _add(errors, f"simple.{key} must be a string of at most 2000 characters")
            continue
        output[key] = raw
    return output


def _normalize_technical(value: Any, errors: list[str]) -> dict[str, list[str]] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        _add(errors, "technical must be an object")
        return None
    for key in value:
        if key not in TECHNICAL_FIELDS:
            _add(errors, f"technical.{key} is not a recognized field")
    output: dict[str, list[str]] = {}
    for key in TECHNICAL_FIELDS:
        raw = value.get(key)
        if raw is None:
            continue
        cleaned = _string_list(raw, f"technical.{key}", errors, max_items=200, max_length=500)
        if cleaned is not None:
            output[key] = cleaned
    return output


def normalize_event(event: Any) -> dict[str, Any]:
    """Validate and normalize one event; raise ``ProgressValidationError``."""
    if not isinstance(event, dict):
        raise ProgressValidationError(["event must be a JSON object"])

    errors: list[str] = []
    allowed = REQUIRED_FIELDS | OPTIONAL_FIELDS
    for key in event:
        if key not in allowed:
            _add(errors, f"unknown field '{key}'")

    missing = sorted(REQUIRED_FIELDS - set(event))
    for key in missing:
        _add(errors, f"{key} is required")

    schema_version = event.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        _add(errors, "schema_version must be an integer")
    elif schema_version != SCHEMA_VERSION:
        _add(errors, f"schema_version must be {SCHEMA_VERSION}")

    event_id = _string(event.get("event_id"), "event_id", errors, max_length=128)
    if event_id is not None and not EVENT_ID_PATTERN.match(event_id):
        _add(errors, "event_id must match [A-Za-z0-9][A-Za-z0-9_.:-]*")

    timestamp = _normalize_utc(event.get("timestamp_utc"), "timestamp_utc", errors)

    level = _string(event.get("level"), "level", errors, max_length=32)
    if level is not None and level not in LEVELS:
        _add(errors, f"level must be one of {sorted(LEVELS)}")

    workstream = _string(event.get("workstream"), "workstream", errors, max_length=64)
    if workstream is not None and not WORKSTREAM_PATTERN.match(workstream):
        _add(errors, "workstream must be lowercase [a-z0-9][a-z0-9_.:-]*")

    status = _string(event.get("status"), "status", errors, max_length=32)
    if status is not None and status not in STATUSES:
        _add(errors, f"status must be one of {sorted(STATUSES)}")

    title = _string(event.get("title"), "title", errors, max_length=200)
    summary = _string(event.get("summary"), "summary", errors, max_length=2000)
    details = _string(event.get("details"), "details", errors, max_length=20000, required=False)
    next_action = _string(
        event.get("next_action"), "next_action", errors, max_length=2000, required=False
    )
    agent = _string(event.get("agent"), "agent", errors, max_length=100, required=False)

    phase = event.get("phase")
    if phase is not None and (not isinstance(phase, str) or not PHASE_PATTERN.match(phase)):
        _add(errors, "phase must be a short alphanumeric label")
        phase = None

    stage = event.get("stage")
    if stage is not None and (
        isinstance(stage, bool) or not isinstance(stage, int) or not 0 <= stage <= 99
    ):
        _add(errors, "stage must be an integer between 0 and 99")
        stage = None

    task_id = event.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or not TASK_ID_PATTERN.match(task_id)):
        _add(errors, "task_id must match [A-Za-z0-9][A-Za-z0-9_.:-]*")
        task_id = None

    files = _string_list(
        event.get("files"), "files", errors, max_items=200, max_length=500, path_items=True
    )
    evidence = _string_list(
        event.get("evidence"), "evidence", errors, max_items=200, max_length=2000
    )
    blockers = _string_list(
        event.get("blockers"), "blockers", errors, max_items=100, max_length=2000
    )
    tests = _normalize_tests(event.get("tests"), errors)
    simple = _normalize_simple(event.get("simple"), errors)
    technical = _normalize_technical(event.get("technical"), errors)

    _scan_secrets(event, "$", errors)

    if errors:
        raise ProgressValidationError(errors)

    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "timestamp_utc": timestamp,
        "level": level,
        "workstream": workstream,
        "status": status,
        "title": title,
        "summary": summary,
    }
    if phase is not None:
        normalized["phase"] = phase
    if stage is not None:
        normalized["stage"] = stage
    if task_id is not None:
        normalized["task_id"] = task_id
    if details is not None:
        normalized["details"] = details
    if files:
        normalized["files"] = files
    if tests is not None:
        normalized["tests"] = tests
    if evidence:
        normalized["evidence"] = evidence
    if blockers:
        normalized["blockers"] = blockers
    if next_action is not None:
        normalized["next_action"] = next_action
    if agent is not None:
        normalized["agent"] = agent
    if simple:
        normalized["simple"] = simple
    if technical:
        normalized["technical"] = technical

    try:
        json.dumps(normalized, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ProgressValidationError([f"event is not safely serializable: {exc}"]) from exc
    return normalized


def check_transition(previous: str, new: str) -> bool:
    """Return True when ``previous -> new`` is an allowed status transition."""
    if previous == new:
        return True
    return new in ALLOWED_TRANSITIONS.get(previous, set())


def event_transition_key(event: dict[str, Any]) -> str:
    """Stable key used to check transitions for one work item."""
    stage = event.get("stage")
    return "|".join(
        (
            str(event.get("workstream", "")),
            str(event.get("phase") or ""),
            "" if stage is None else str(stage),
            str(event.get("task_id") or event.get("level") or ""),
        )
    )


def schema_document() -> dict[str, Any]:
    """Machine-readable schema description written next to the ledger."""
    return {
        "schema_version": SCHEMA_VERSION,
        "levels": sorted(LEVELS),
        "statuses": sorted(STATUSES),
        "status_descriptions": STATUS_DESCRIPTIONS,
        "allowed_transitions": {key: sorted(value) for key, value in ALLOWED_TRANSITIONS.items()},
        "required_fields": sorted(REQUIRED_FIELDS),
        "optional_fields": sorted(OPTIONAL_FIELDS),
        "simple_fields": sorted(SIMPLE_FIELDS),
        "technical_fields": sorted(TECHNICAL_FIELDS),
        "notes": [
            "The JSONL ledger is append-only and is the source of truth.",
            "manifest.json is derived and replaced atomically after each append.",
            "Timestamps must be UTC (Z or +00:00); local offsets are rejected.",
            "Files must be repository-relative; absolute paths and '..' are rejected.",
            "Secret-like keys and credential-shaped values are rejected.",
            "Invalid status transitions are rejected unless the recorder forces them.",
        ],
    }


__all__ = [
    "ALLOWED_TRANSITIONS",
    "LEVELS",
    "OPTIONAL_FIELDS",
    "REQUIRED_FIELDS",
    "SCHEMA_VERSION",
    "STATUSES",
    "STATUS_DESCRIPTIONS",
    "ProgressValidationError",
    "check_transition",
    "event_transition_key",
    "normalize_event",
    "schema_document",
]
