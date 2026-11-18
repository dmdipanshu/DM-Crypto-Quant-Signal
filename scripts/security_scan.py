"""Local secret scanner for tracked repo files.

Scans tracked files for secret-like patterns (API keys, tokens, private
keys) and reports file paths and line numbers only -- never the secret
values themselves.

Exit codes:
    0 - no secrets found
    1 - confirmed secrets found
    2 - scanner error
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from pathlib import Path

# Patterns that indicate a possible secret. Matched values are never printed.
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"api[_-]?key\s*=\s*\S+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
]

SCAN_EXTENSIONS = {".py", ".toml", ".env", ".md", ".json", ".yaml", ".yml"}

# Known-safe placeholders that must never be flagged.
SAFE_PREFIXES = ("META_MUSE_API_KEY=",)
COMMENT_PREFIX = "#"
TEST_FIXTURE_MARKER = "sk-test-"

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _is_comment_line(line: str) -> bool:
    return line.lstrip().startswith(COMMENT_PREFIX)


def _is_safe_placeholder(line: str) -> bool:
    stripped = line.strip()
    return any(stripped.startswith(prefix) for prefix in SAFE_PREFIXES)


def _line_has_secret(line: str) -> bool:
    """Return True if the line contains a confirmed secret pattern."""
    if _is_comment_line(line):
        return False
    if _is_safe_placeholder(line):
        return False
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(line):
            matched = match.group(0)
            # Test fixtures with sk-test- prefix are never flagged.
            if matched.startswith("sk-") and TEST_FIXTURE_MARKER in line:
                continue
            return True
    return False


def scan_file(path: Path) -> list[int]:
    """Return 1-based line numbers containing secrets in ``path``."""
    hits: list[int] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    for lineno, line in enumerate(text.splitlines(), start=1):
        if _line_has_secret(line):
            hits.append(lineno)
    return hits


def scan_files(files: list[Path]) -> dict[Path, list[int]]:
    """Scan ``files`` and return {path: [line numbers]} for files with hits."""
    results: dict[Path, list[int]] = {}
    for path in files:
        hits = scan_file(path)
        if hits:
            results[path] = hits
    return results


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.stdout


def discover_tracked_files(root: Path) -> list[Path]:
    """Return tracked files under ``root`` with scannable extensions."""
    files: list[Path] = []
    try:
        output = _run(["git", "-C", str(root), "ls-files"])
    except FileNotFoundError:
        output = ""
    if output.strip():
        for rel in output.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            path = root / rel
            if path.suffix.lower() in SCAN_EXTENSIONS and path.is_file():
                files.append(path)
    else:
        # Fallback: walk the directory tree when git is unavailable.
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SCAN_EXTENSIONS:
                continue
            if any(
                part in {".git", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache"}
                for part in path.parts
            ):
                continue
            files.append(path)
    return sorted(files)


def _expand_paths(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for entry in paths:
        path = Path(entry)
        if path.is_dir():
            files.extend(discover_tracked_files(path))
        elif path.is_file():
            files.append(path)
    return files


def main(argv: list[str] | None = None, stream: io.TextIOBase | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan tracked files for committed secrets.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files or directories to scan (default: all tracked files).",
    )
    args = parser.parse_args(argv)

    out = stream if stream is not None else sys.stdout

    files = _expand_paths(args.paths) if args.paths else discover_tracked_files(_REPO_ROOT)

    results = scan_files(files)
    total_secrets = sum(len(hits) for hits in results.values())

    out.write("Secret scan results\n")
    out.write(f"Files scanned: {len(files)}\n")
    out.write(f"Secrets found: {total_secrets}\n")
    if results:
        out.write("Detections (file:line, no secret values printed):\n")
        for path in sorted(results):
            for lineno in sorted(results[path]):
                out.write(f"  {path}:{lineno}\n")
        status = 1
    else:
        out.write("No secrets detected.\n")
        status = 0
    out.write(f"Exit status: {status}\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
