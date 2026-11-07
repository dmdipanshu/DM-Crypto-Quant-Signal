"""Append-only JSONL progress ledger with an atomically replaced manifest.

Design rules:

* ``events.jsonl`` is the only source of truth; lines are never rewritten.
* ``manifest.json`` is derived and replaced atomically (temp + ``os.replace``).
* Reads are bounded: the API scans at most ``scan_cap`` newest records.
* Corrupt lines are reported with their line number, never silently dropped.
* The ledger root is confined to the repository by ``resolve_progress_root``.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections import Counter, deque
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_engine.progress.schema import (
    ProgressValidationError,
    check_transition,
    event_transition_key,
    normalize_event,
    schema_document,
)

EVENTS_FILENAME = "events.jsonl"
MANIFEST_FILENAME = "manifest.json"
SCHEMA_FILENAME = "schema.json"
DEFAULT_SCAN_CAP = 5000
DEFAULT_STALE_AFTER_S = 3600
MAX_MANIFEST_TASKS = 500


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp (``Z`` or ``+00:00``)."""
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            with contextlib.suppress(OSError):  # pragma: no cover - best-effort cleanup
                tmp_path.unlink()


def resolve_progress_root(repo_root: Path, requested: str | Path | None = None) -> Path:
    """Resolve the ledger root and reject paths outside ``repo_root``.

    ``None`` selects ``<repo_root>/data/project_progress``. A relative
    ``requested`` value is resolved under ``repo_root``; an absolute value is
    accepted only when it stays inside the repository.
    """
    base = Path(repo_root).resolve()
    if requested is None:
        candidate = base / "data" / "project_progress"
    else:
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = base / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(
            f"progress root must stay inside the repository ({base}); got {resolved}"
        ) from None
    return resolved


class ProgressLedger:
    """Validated append-only progress ledger rooted at one directory."""

    def __init__(
        self,
        root: Path,
        *,
        scan_cap: int = DEFAULT_SCAN_CAP,
        stale_after_s: int = DEFAULT_STALE_AFTER_S,
    ) -> None:
        self.root = Path(root)
        self.scan_cap = max(1, int(scan_cap))
        self.stale_after_s = max(1, int(stale_after_s))
        self.events_path = self.root / EVENTS_FILENAME
        self.manifest_path = self.root / MANIFEST_FILENAME
        self.schema_path = self.root / SCHEMA_FILENAME

    # -- low-level readers -------------------------------------------------

    def exists(self) -> bool:
        return self.events_path.is_file()

    def _iter_records(self) -> Iterator[tuple[int, dict[str, Any] | None, str | None]]:
        """Yield ``(line_no, event_or_none, error_or_none)`` for every line."""
        if not self.events_path.is_file():
            return
        with self.events_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError as exc:
                    yield line_no, None, f"invalid JSON: {exc.msg}"
                    continue
                try:
                    yield line_no, normalize_event(raw), None
                except ProgressValidationError as exc:
                    yield line_no, None, "; ".join(exc.errors)

    def _scan(self, cap: int | None = None) -> dict[str, Any]:
        total = 0
        tail: deque[tuple[int, dict[str, Any] | None, str | None]] = (
            deque(maxlen=cap) if cap else deque()
        )
        for record in self._iter_records():
            total += 1
            tail.append(record)
        events = [event for _, event, _ in tail if event is not None]
        corrupt = [
            {"line": line_no, "error": error}
            for line_no, event, error in tail
            if event is None and error is not None
        ]
        return {
            "events": events,
            "corrupt": corrupt,
            "total_records": total,
            "scanned": len(tail),
            "truncated": bool(cap and total > cap),
        }

    def _read_manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.is_file():
            return None
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    # -- manifest ----------------------------------------------------------

    def _build_manifest(self) -> dict[str, Any]:
        status_counts: Counter[str] = Counter()
        level_counts: Counter[str] = Counter()
        workstreams: set[str] = set()
        phases: set[str] = set()
        stages: set[int] = set()
        latest_by_key: dict[str, dict[str, Any]] = {}
        last_event: dict[str, Any] | None = None
        last_test: dict[str, Any] | None = None
        corrupt: list[dict[str, Any]] = []
        valid_count = 0
        total_records = 0

        for line_no, event, error in self._iter_records():
            total_records += 1
            if event is None:
                corrupt.append({"line": line_no, "error": error or "invalid record"})
                continue
            valid_count += 1
            status_counts[event["status"]] += 1
            level_counts[event["level"]] += 1
            workstreams.add(event["workstream"])
            if event.get("phase"):
                phases.add(str(event["phase"]))
            if event.get("stage") is not None:
                stages.add(int(event["stage"]))
            key = event_transition_key(event)
            latest_by_key[key] = event
            if last_event is None or event["timestamp_utc"] >= last_event["timestamp_utc"]:
                last_event = event
            if event.get("tests") and (
                last_test is None or event["timestamp_utc"] >= last_test["timestamp_utc"]
            ):
                last_test = event

        latest_statuses = [event["status"] for event in latest_by_key.values()]
        metrics = {
            "recorded_events": valid_count,
            "active_tasks": sum(
                1 for status in latest_statuses if status in {"IN_PROGRESS", "REVIEW"}
            ),
            "pending_tasks": sum(
                1 for status in latest_statuses if status in {"NOT_STARTED", "PLANNED"}
            ),
            "blocked_tasks": sum(1 for status in latest_statuses if status == "BLOCKED"),
            "failed_checks": sum(1 for status in latest_statuses if status == "FAILED"),
            "passing_items": sum(
                1 for status in latest_statuses if status in {"PASS", "PASS_WITH_FINDINGS"}
            ),
            "frozen_items": sum(1 for status in latest_statuses if status == "FROZEN"),
            "historical_items": sum(1 for status in latest_statuses if status == "HISTORICAL"),
            "not_verified_items": sum(1 for status in latest_statuses if status == "NOT_VERIFIED"),
        }

        def _summary(event: dict[str, Any]) -> dict[str, Any]:
            summary = {
                "event_id": event["event_id"],
                "timestamp_utc": event["timestamp_utc"],
                "level": event["level"],
                "workstream": event["workstream"],
                "status": event["status"],
                "title": event["title"],
            }
            for field in ("phase", "stage", "task_id"):
                if event.get(field) is not None:
                    summary[field] = event[field]
            if event.get("tests"):
                summary["tests"] = event["tests"]
            return summary

        tasks = [
            _summary(event)
            for event in sorted(
                latest_by_key.values(), key=lambda item: item["timestamp_utc"], reverse=True
            )[:MAX_MANIFEST_TASKS]
        ]

        previous = self._read_manifest() or {}
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "generated_utc": utc_now(),
            "updated_utc": utc_now(),
            "event_count": valid_count,
            "total_records": total_records,
            "corrupt_line_count": len(corrupt),
            "corrupt_samples": corrupt[:20],
            "status_counts": dict(sorted(status_counts.items())),
            "level_counts": dict(sorted(level_counts.items())),
            "workstreams": sorted(workstreams),
            "phases": sorted(phases),
            "stages": sorted(stages),
            "tasks": tasks,
            "metrics": metrics,
            "last_event": _summary(last_event) if last_event else None,
            "last_test": _summary(last_test) if last_test else None,
            "last_event_id": last_event["event_id"] if last_event else None,
            "last_timestamp_utc": last_event["timestamp_utc"] if last_event else None,
            "stale_after_s": self.stale_after_s,
            "source": self.events_path.name,
            "notes": "Derived manifest; events.jsonl is the append-only source of truth.",
        }
        if isinstance(previous.get("git"), dict):
            manifest["git"] = previous["git"]
        return manifest

    def rebuild_manifest(self) -> dict[str, Any]:
        """Rebuild and atomically persist the derived manifest."""
        manifest = self._build_manifest()
        _atomic_write_json(self.manifest_path, manifest)
        if not self.schema_path.is_file():
            _atomic_write_json(self.schema_path, schema_document())
        return manifest

    def update_git_state(self, git_state: dict[str, Any]) -> dict[str, Any]:
        """Attach a read-only Git snapshot to the derived manifest atomically."""
        manifest = self._read_manifest()
        if manifest is None:
            manifest = self.rebuild_manifest()
        manifest["git"] = git_state
        manifest["git_collected_utc"] = git_state.get("collected_utc")
        _atomic_write_json(self.manifest_path, manifest)
        return manifest

    # -- writes ------------------------------------------------------------

    def append(
        self,
        event: dict[str, Any],
        *,
        enforce_transitions: bool = True,
        skip_existing: bool = False,
    ) -> dict[str, Any]:
        """Validate and append one event, then rebuild the manifest."""
        normalized = normalize_event(event)
        self.root.mkdir(parents=True, exist_ok=True)

        existing_ids: set[str] = set()
        latest_by_key: dict[str, dict[str, Any]] = {}
        for _, existing, _ in self._iter_records():
            if existing is None:
                continue
            existing_ids.add(existing["event_id"])
            latest_by_key[event_transition_key(existing)] = existing

        if normalized["event_id"] in existing_ids:
            if skip_existing:
                return {
                    "appended": False,
                    "reason": "duplicate_event_id",
                    "event_id": normalized["event_id"],
                }
            raise ProgressValidationError([f"duplicate event_id '{normalized['event_id']}'"])

        key = event_transition_key(normalized)
        previous = latest_by_key.get(key)
        if (
            enforce_transitions
            and previous is not None
            and not check_transition(previous["status"], normalized["status"])
        ):
            raise ProgressValidationError(
                [f"invalid transition {previous['status']} -> {normalized['status']} for {key}"]
            )

        line = json.dumps(normalized, sort_keys=True, ensure_ascii=True, allow_nan=False)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        manifest = self.rebuild_manifest()
        return {
            "appended": True,
            "event_id": normalized["event_id"],
            "event_count": manifest["event_count"],
        }

    def seed(self, events: Iterable[dict[str, Any]]) -> dict[str, Any]:
        """Append deterministic seed events, skipping IDs already present."""
        appended = 0
        skipped = 0
        errors: list[dict[str, Any]] = []
        for event in events:
            try:
                result = self.append(event, enforce_transitions=True, skip_existing=True)
            except ProgressValidationError as exc:
                errors.append({"event_id": event.get("event_id"), "errors": exc.errors})
                continue
            if result["appended"]:
                appended += 1
            else:
                skipped += 1
        return {"appended": appended, "skipped": skipped, "errors": errors}

    # -- reads -------------------------------------------------------------

    def read_events(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        level: str | None = None,
        status: str | None = None,
        stage: int | None = None,
        phase: str | None = None,
        workstream: str | None = None,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, filtered, newest-first page of events."""
        if not self.events_path.is_file():
            return {
                "available": False,
                "reason": "not_initialized",
                "events": [],
                "total": 0,
                "corrupt": [],
                "corrupt_count": 0,
                "scanned": 0,
                "truncated": False,
                "source": self.events_path.name,
            }

        scan = self._scan(cap=self.scan_cap)
        since_dt = parse_utc(since) if since else None
        until_dt = parse_utc(until) if until else None
        needle = q.casefold().strip() if q else ""

        filtered: list[dict[str, Any]] = []
        for event in scan["events"]:
            if level and event["level"] != level:
                continue
            if status and event["status"] != status:
                continue
            if stage is not None and event.get("stage") != stage:
                continue
            if phase and str(event.get("phase") or "").casefold() != phase.casefold():
                continue
            if workstream and event["workstream"].casefold() != workstream.casefold():
                continue
            event_dt = parse_utc(event["timestamp_utc"])
            if since_dt is not None and event_dt < since_dt:
                continue
            if until_dt is not None and event_dt > until_dt:
                continue
            if needle:
                haystack = " ".join(
                    [
                        event["title"],
                        event["summary"],
                        str(event.get("details") or ""),
                        str(event.get("task_id") or ""),
                        event["workstream"],
                        " ".join(event.get("evidence") or []),
                        " ".join(event.get("files") or []),
                    ]
                ).casefold()
                if needle not in haystack:
                    continue
            filtered.append(event)

        filtered.sort(key=lambda item: (item["timestamp_utc"], item["event_id"]), reverse=True)
        total = len(filtered)
        page = filtered[offset : offset + limit]
        return {
            "available": True,
            "reason": None,
            "events": page,
            "total": total,
            "corrupt": scan["corrupt"][:20],
            "corrupt_count": len(scan["corrupt"]),
            "scanned": scan["scanned"],
            "truncated": scan["truncated"],
            "source": self.events_path.name,
        }

    def snapshot(self) -> dict[str, Any]:
        """Return the derived snapshot (manifest + latest events + staleness)."""
        if not self.events_path.is_file() and not self.manifest_path.is_file():
            return {
                "available": False,
                "reason": "not_initialized",
                "stale": True,
                "stale_after_s": self.stale_after_s,
                "manifest": None,
                "recent": [],
            }

        manifest = self._read_manifest()
        rebuilt = False
        if manifest is None:
            # Read-only derivation: a GET must never create or replace files.
            manifest = self._build_manifest()
            rebuilt = True

        timestamp = manifest.get("last_timestamp_utc") or manifest.get("updated_utc")
        stale = True
        if isinstance(timestamp, str):
            try:
                stale = (datetime.now(UTC) - parse_utc(timestamp)).total_seconds() > (
                    self.stale_after_s
                )
            except ValueError:
                stale = True

        recent = self.read_events(limit=5, offset=0)
        return {
            "available": True,
            "reason": None,
            "stale": stale,
            "stale_after_s": self.stale_after_s,
            "manifest": manifest,
            "manifest_rebuilt": rebuilt,
            "recent": recent.get("events", []),
        }

    def validate(self) -> dict[str, Any]:
        """Validate the whole ledger and report corrupt/duplicate/transition issues."""
        seen: set[str] = set()
        duplicates: list[dict[str, Any]] = []
        transitions: list[dict[str, Any]] = []
        corrupt: list[dict[str, Any]] = []
        latest_by_key: dict[str, dict[str, Any]] = {}
        valid = 0
        total = 0
        for line_no, event, error in self._iter_records():
            total += 1
            if event is None:
                corrupt.append({"line": line_no, "error": error or "invalid record"})
                continue
            valid += 1
            if event["event_id"] in seen:
                duplicates.append({"line": line_no, "event_id": event["event_id"]})
            seen.add(event["event_id"])
            key = event_transition_key(event)
            previous = latest_by_key.get(key)
            if previous is not None and not check_transition(previous["status"], event["status"]):
                transitions.append(
                    {
                        "line": line_no,
                        "key": key,
                        "from": previous["status"],
                        "to": event["status"],
                    }
                )
            latest_by_key[key] = event
        return {
            "ok": not corrupt and not duplicates and not transitions,
            "total_records": total,
            "valid_events": valid,
            "corrupt": corrupt,
            "duplicates": duplicates,
            "transition_warnings": transitions,
        }


__all__ = [
    "DEFAULT_SCAN_CAP",
    "DEFAULT_STALE_AFTER_S",
    "EVENTS_FILENAME",
    "MANIFEST_FILENAME",
    "SCHEMA_FILENAME",
    "ProgressLedger",
    "parse_utc",
    "resolve_progress_root",
    "utc_now",
]
