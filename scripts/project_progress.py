#!/usr/bin/env python
"""Record and inspect SignalForge project progress (standard library only).

The append-only JSONL ledger under ``data/project_progress/`` is the single
source of truth for the progress dashboard. This utility is the only supported
way to add records; it validates the event schema, enforces status transitions,
and refreshes the derived manifest atomically.

Examples
--------

    python scripts/project_progress.py seed
    python scripts/project_progress.py record --level task \
        --workstream progress_dashboard --task-id progress_dashboard \
        --status IN_PROGRESS --title "Frontend started" \
        --summary "The dashboard frontend implementation started."
    python scripts/project_progress.py list --limit 20
    python scripts/project_progress.py validate
    python scripts/project_progress.py snapshot

Safety: the ledger root is confined to the repository, no secret-like values
are accepted, and no trading, AI provider, network, or Git write is performed.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_HERE = Path(__file__).resolve()
_SRC = _HERE.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from crypto_engine.progress.git_state import collect_git_state  # noqa: E402
from crypto_engine.progress.ledger import (  # noqa: E402
    ProgressLedger,
    resolve_progress_root,
)
from crypto_engine.progress.schema import ProgressValidationError  # noqa: E402
from crypto_engine.progress.seed_data import seed_events  # noqa: E402

DEFAULT_PROGRESS_DIR = "data/project_progress"


def find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` looking for ``pyproject.toml``."""
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def build_ledger(args: argparse.Namespace) -> tuple[ProgressLedger, Path]:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root(_HERE)
    root = resolve_progress_root(repo_root, args.progress_dir or DEFAULT_PROGRESS_DIR)
    return ProgressLedger(root, scan_cap=int(args.scan_cap)), repo_root


def _new_event_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"evt-{stamp}-{uuid4().hex[:8]}"


def _tests_payload(args: argparse.Namespace) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    for key in ("passed", "failed", "skipped", "total"):
        value = getattr(args, f"tests_{key}", None)
        if value is not None:
            payload[key] = value
    for key in ("command", "report", "note"):
        value = getattr(args, f"tests_{key}", None)
        if value:
            payload[key] = value
    return payload or None


def _simple_payload(args: argparse.Namespace) -> dict[str, str] | None:
    payload = {
        "what": args.simple_what,
        "why": args.simple_why,
        "working": args.simple_working,
        "not_verified": args.simple_not_verified,
        "next": args.simple_next,
    }
    cleaned = {key: value for key, value in payload.items() if value}
    return cleaned or None


def _technical_payload(args: argparse.Namespace) -> dict[str, list[str]] | None:
    payload = {
        "module_paths": args.module_path or [],
        "functions": args.function or [],
        "config_keys": args.config_key or [],
        "error_codes": args.error_code or [],
        "commands": args.command or [],
    }
    cleaned = {key: value for key, value in payload.items() if value}
    return cleaned or None


def build_event(args: argparse.Namespace) -> dict[str, Any]:
    """Assemble one schema-shaped event from CLI arguments."""
    event: dict[str, Any] = {
        "schema_version": 1,
        "event_id": args.event_id or _new_event_id(),
        "timestamp_utc": args.timestamp or datetime.now(UTC).isoformat(timespec="seconds"),
        "level": args.level,
        "workstream": args.workstream,
        "status": args.status,
        "title": args.title,
        "summary": args.summary,
    }
    optional: dict[str, Any] = {
        "phase": args.phase,
        "stage": args.stage,
        "task_id": args.task_id,
        "details": args.details,
        "next_action": args.next_action,
        "agent": args.agent,
    }
    for key, value in optional.items():
        if value is not None:
            event[key] = value
    if args.files:
        event["files"] = args.files
    if args.evidence:
        event["evidence"] = args.evidence
    if args.blocker:
        event["blockers"] = args.blocker
    tests = _tests_payload(args)
    if tests is not None:
        event["tests"] = tests
    simple = _simple_payload(args)
    if simple is not None:
        event["simple"] = simple
    technical = _technical_payload(args)
    if technical is not None:
        event["technical"] = technical
    return event


def cmd_record(args: argparse.Namespace) -> int:
    ledger, _ = build_ledger(args)
    try:
        result = ledger.append(build_event(args), enforce_transitions=not args.force)
    except ProgressValidationError as exc:
        print("event rejected:", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    ledger, _ = build_ledger(args)
    payload = ledger.read_events(
        limit=max(1, int(args.limit)),
        offset=max(0, int(args.offset)),
        level=args.level,
        status=args.status,
        stage=args.stage,
        phase=args.phase,
        workstream=args.workstream,
        q=args.q,
    )
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    if not payload["available"]:
        print("progress ledger is not initialized (run: seed)")
        return 0
    for event in payload["events"]:
        print(
            f"{event['timestamp_utc']}  {event['status']:<18} {event['level']:<12} "
            f"{event['workstream']:<20} {event['title']}"
        )
    print(
        f"\n{payload['total']} matching event(s); scanned {payload['scanned']} record(s); "
        f"corrupt lines: {payload['corrupt_count']}"
    )
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    ledger, _ = build_ledger(args)
    report = ledger.validate()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


def cmd_snapshot(args: argparse.Namespace) -> int:
    ledger, repo_root = build_ledger(args)
    git_state = collect_git_state(repo_root)
    if ledger.exists() or ledger.manifest_path.is_file():
        ledger.update_git_state(git_state)
    snapshot = ledger.snapshot()
    snapshot["git"] = git_state
    print(json.dumps(snapshot, indent=2))
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    ledger, _ = build_ledger(args)
    result = ledger.seed(seed_events())
    print(json.dumps(result, indent=2))
    return 0 if not result["errors"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="project_progress",
        description="Append-only SignalForge progress ledger utility.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo-root", help="Repository root (default: auto-detect).")
    common.add_argument(
        "--progress-dir",
        default=DEFAULT_PROGRESS_DIR,
        help=f"Ledger directory relative to the repo root (default: {DEFAULT_PROGRESS_DIR}).",
    )
    common.add_argument(
        "--scan-cap",
        type=int,
        default=5000,
        help="Maximum newest records scanned by read operations (default: 5000).",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser("record", parents=[common], help="Validate and append one event.")
    record.add_argument("--event-id", help="Unique event id (default: generated).")
    record.add_argument("--timestamp", help="UTC ISO-8601 timestamp (default: now).")
    record.add_argument("--level", required=True, help="project|phase|stage|task|test|...")
    record.add_argument("--workstream", required=True, help="Lowercase workstream id.")
    record.add_argument("--status", required=True, help="One of the recorded status values.")
    record.add_argument("--title", required=True, help="Short event title.")
    record.add_argument("--summary", required=True, help="Plain-language summary.")
    record.add_argument("--details", help="Longer technical explanation.")
    record.add_argument("--phase", help="Phase label (A-L) when applicable.")
    record.add_argument("--stage", type=int, help="AI stage number when applicable.")
    record.add_argument("--task-id", help="Stable task id used for transition checks.")
    record.add_argument("--files", nargs="*", help="Repository-relative files changed.")
    record.add_argument("--evidence", nargs="*", help="Evidence references.")
    record.add_argument("--blocker", nargs="*", help="Current blockers.")
    record.add_argument("--next-action", help="Next authorized action.")
    record.add_argument("--agent", help="Responsible agent/tool.")
    record.add_argument("--tests-passed", type=int)
    record.add_argument("--tests-failed", type=int)
    record.add_argument("--tests-skipped", type=int)
    record.add_argument("--tests-total", type=int)
    record.add_argument("--tests-command")
    record.add_argument("--tests-report")
    record.add_argument("--tests-note")
    record.add_argument("--simple-what")
    record.add_argument("--simple-why")
    record.add_argument("--simple-working")
    record.add_argument("--simple-not-verified")
    record.add_argument("--simple-next")
    record.add_argument("--module-path", nargs="*")
    record.add_argument("--function", nargs="*")
    record.add_argument("--config-key", nargs="*")
    record.add_argument("--error-code", nargs="*")
    record.add_argument("--command", nargs="*")
    record.add_argument(
        "--force",
        action="store_true",
        help="Skip the status-transition check (schema validation still applies).",
    )
    record.set_defaults(func=cmd_record)

    listing = sub.add_parser("list", parents=[common], help="List recorded events.")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--offset", type=int, default=0)
    listing.add_argument("--level")
    listing.add_argument("--status")
    listing.add_argument("--phase")
    listing.add_argument("--stage", type=int)
    listing.add_argument("--workstream")
    listing.add_argument("--q", help="Case-insensitive text search.")
    listing.add_argument("--json", action="store_true", help="Emit the raw JSON payload.")
    listing.set_defaults(func=cmd_list)

    validate = sub.add_parser("validate", parents=[common], help="Validate the whole ledger.")
    validate.set_defaults(func=cmd_validate)

    snapshot = sub.add_parser(
        "snapshot", parents=[common], help="Refresh the manifest and record read-only Git state."
    )
    snapshot.set_defaults(func=cmd_snapshot)

    seed = sub.add_parser("seed", parents=[common], help="Append recorded historical seed events.")
    seed.set_defaults(func=cmd_seed)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
