#!/usr/bin/env python3
"""Regenerate release candidate manifest for Q16."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(r"D:\DM-Crypto-Quant-Signal")
OUTPUT = ROOT / "docs" / "validation" / "results" / "release_candidate_manifest.json"

EXCLUDE_DIRS = {
    "__pycache__",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    ".git",
    "data",
    "backups",
    "logs",
    "reports",
    "htmlcov",
    "SignalForge_Q2_to_Q4_Build",
}

EXCLUDE_FILES = {
    ".env",
    "artifact.pkl",
}

EXCLUDE_FILE_SUFFIXES = (".pyc", ".pyo", ".pkl")

EXCLUDE_FILE_PREFIXES = (".env",)


def is_excluded(path: Path, rel: str) -> bool:
    parts = Path(rel).parts
    for part in parts:
        if part in EXCLUDE_DIRS:
            return True
    name = path.name
    if name in EXCLUDE_FILES:
        return True
    if name.startswith(EXCLUDE_FILE_PREFIXES):
        return True
    return name.endswith(EXCLUDE_FILE_SUFFIXES)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fname in filenames:
            full = Path(dirpath) / fname
            rel = full.relative_to(ROOT).as_posix()
            if is_excluded(full, rel):
                continue
            stat = full.stat()
            files.append({
                "relative_path": rel,
                "size_bytes": stat.st_size,
                "sha256": sha256_file(full),
            })

    files.sort(key=lambda f: f["relative_path"])

    manifest = {
        "phase": "Q16",
        "title": "Release Candidate Manifest",
        "version": "0.1.0",
        "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "release_type": "release_candidate_final",
        "python_requires": ">=3.12,<3.14",
        "files": files,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Manifest written to {OUTPUT}")
    print(f"Total files: {len(files)}")


if __name__ == "__main__":
    main()
