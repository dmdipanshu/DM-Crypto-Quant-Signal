"""Read-only Git state collection for the progress manifest.

Only read-only git commands are executed (``rev-parse``, ``status``,
``diff --stat``). Nothing is staged, committed, reset, cleaned, checked out,
or pushed. Output is bounded so a large working tree cannot bloat the
manifest.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_STATUS_LINES = 500
MAX_DIFF_LINES = 200
MAX_TEXT_CHARS = 20_000

Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run_git(
    repo_root: Path,
    args: list[str],
    *,
    timeout_s: float,
    runner: Runner | None,
) -> subprocess.CompletedProcess[str] | None:
    run = runner or subprocess.run
    try:
        return run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def collect_git_state(
    repo_root: Path,
    *,
    timeout_s: float = 5.0,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Collect a bounded, read-only snapshot of the repository Git state."""
    root = Path(repo_root).resolve()
    collected = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    head_proc = _run_git(root, ["rev-parse", "HEAD"], timeout_s=timeout_s, runner=runner)
    branch_proc = _run_git(
        root, ["rev-parse", "--abbrev-ref", "HEAD"], timeout_s=timeout_s, runner=runner
    )
    status_proc = _run_git(root, ["status", "--porcelain=v1"], timeout_s=timeout_s, runner=runner)
    diff_proc = _run_git(root, ["diff", "--stat"], timeout_s=timeout_s, runner=runner)

    if head_proc is None or head_proc.returncode != 0:
        return {
            "available": False,
            "reason": "git_unavailable",
            "collected_utc": collected,
            "read_only": True,
            "note": "Read-only Git collection; no stage/commit/push was performed.",
        }

    head = (head_proc.stdout or "").strip()
    branch = (
        (branch_proc.stdout or "").strip() if branch_proc and branch_proc.returncode == 0 else ""
    )
    status_text = status_proc.stdout if status_proc and status_proc.returncode == 0 else ""
    diff_text = diff_proc.stdout if diff_proc and diff_proc.returncode == 0 else ""

    modified: list[str] = []
    untracked: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    status_lines = [line for line in status_text.splitlines() if line.strip()]
    truncated = len(status_lines) > MAX_STATUS_LINES
    for line in status_lines[:MAX_STATUS_LINES]:
        code = line[:2]
        path = line[3:].strip() if len(line) > 3 else ""
        if not path:
            continue
        if code == "??":
            untracked.append(path)
        elif "D" in code:
            deleted.append(path)
        elif "A" in code:
            added.append(path)
        else:
            modified.append(path)

    diff_lines = diff_text.splitlines()
    if len(diff_lines) > MAX_DIFF_LINES:
        diff_lines = [*diff_lines[:MAX_DIFF_LINES], "... diff stat truncated ..."]
    diff_stat = "\n".join(diff_lines)[:MAX_TEXT_CHARS]

    return {
        "available": True,
        "reason": None,
        "collected_utc": collected,
        "head": head,
        "branch": branch,
        "dirty": bool(status_lines),
        "modified": modified,
        "untracked": untracked,
        "added": added,
        "deleted": deleted,
        "modified_count": len(modified),
        "untracked_count": len(untracked),
        "added_count": len(added),
        "deleted_count": len(deleted),
        "diff_stat": diff_stat,
        "truncated": truncated,
        "read_only": True,
        "note": "Read-only Git snapshot; no stage/commit/push/reset/clean/checkout occurred.",
    }


__all__ = ["collect_git_state"]
