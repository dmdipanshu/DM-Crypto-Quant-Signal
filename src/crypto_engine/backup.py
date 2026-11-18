"""Phase D disaster-recovery backup/restore.

Read-only snapshots of configured source directories written atomically
(temp file + ``os.replace``) into ``settings.backup.path``. One file at a
time, bounded read chunks: safe on a 4 GB laptop. Source directories are
never mutated.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from crypto_engine.config.settings import Settings

__all__ = [
    "BackupManifest",
    "_atomic_write_bytes",
    "_sha256_file",
    "create_backup",
    "list_backups",
    "restore_from_backup",
    "retention_cleanup",
    "verify_backup",
]

_MAX_FILE_BYTES = 50 * 1024 * 1024  # per-file cap: skip anything >= 50 MB
_READ_CHUNK = 1024 * 1024            # bounded read chunk (1 MiB)
_MANIFEST_NAME = "manifest.json"

logger = logging.getLogger(__name__)


class BackupManifest(BaseModel):
    """Manifest recorded for every completed backup."""

    backup_id: str
    created_utc: str
    version: str = "1"
    checksums: dict[str, str] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)
    source_paths: list[str] = Field(default_factory=list)
    total_bytes: int = 0
    status: Literal["completed", "failed"] = "completed"


def _sha256_file(path: Path) -> str:
    """SHA-256 hex digest of ``path``, read in bounded chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` via temp file + os.replace.

    The temp file is cleaned up on failure; nothing is left partially
    written at ``target``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise


def _configured_source_dirs(settings: Settings) -> list[Path]:
    """Ordered source directories to back up (logs parent only when it exists)."""
    dirs: list[Path] = [
        Path(settings.registry.path),
        Path(settings.data.storage_path),
        Path(settings.paper.account_path),
    ]
    log_parent = Path(settings.logging.file_path).parent
    if log_parent.is_dir():
        dirs.append(log_parent)
    return dirs


def _walk_source_files(source_dirs: list[Path]) -> Iterator[tuple[Path, Path]]:
    """Yield ``(source_dir, file_path)`` pairs, deduplicated by resolved path."""
    seen: set[Path] = set()
    for source in source_dirs:
        if not source.is_dir():
            continue
        for entry in sorted(source.rglob("*")):
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            resolved = entry.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield source, entry


def create_backup(settings: Settings) -> BackupManifest:
    """Back up configured source directories into ``settings.backup.path``.

    Only files under 50 MB are backed up; larger files are skipped with a
    warning. SHA-256 is computed for every backed-up file and the manifest
    is written as JSON to the backup directory. Source directories are read
    only.
    """
    backup_root = Path(settings.backup.path)
    backup_root.mkdir(parents=True, exist_ok=True)

    backup_id = uuid.uuid4().hex
    created_utc = datetime.now(UTC).isoformat()
    backup_id_dir = backup_root / backup_id
    backup_id_dir.mkdir(parents=True, exist_ok=True)

    checksums: dict[str, str] = {}
    files: list[str] = []
    total_bytes = 0

    for source, entry in _walk_source_files(_configured_source_dirs(settings)):
        try:
            size = entry.stat().st_size
        except OSError as exc:
            logger.warning("cannot stat %s: skipping (%s)", entry, exc)
            continue
        if size >= _MAX_FILE_BYTES:
            logger.warning(
                "skipping oversize file %s (%d bytes >= %d MB cap)",
                entry,
                size,
                _MAX_FILE_BYTES // (1024 * 1024),
            )
            continue
        try:
            data = entry.read_bytes()
        except OSError as exc:
            logger.warning("cannot read %s: skipping (%s)", entry, exc)
            continue
        rel_path = entry.relative_to(source).as_posix()
        _atomic_write_bytes(backup_id_dir / rel_path, data)
        checksums[rel_path] = _sha256_file(entry)
        files.append(rel_path)
        total_bytes += size

    files.sort()
    checksums = {name: checksums[name] for name in files}

    manifest = BackupManifest(
        backup_id=backup_id,
        created_utc=created_utc,
        checksums=checksums,
        files=files,
        source_paths=[str(path) for path in _configured_source_dirs(settings)],
        total_bytes=total_bytes,
        status="completed",
    )
    payload = (
        json.dumps(manifest.model_dump(), sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(backup_id_dir / _MANIFEST_NAME, payload)
    return manifest


def _load_manifest(backup_dir: Path) -> BackupManifest | None:
    manifest_path = backup_dir / _MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        return BackupManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


def _resolve_within(root: Path, rel_path: str) -> Path | None:
    """Resolve ``root / rel_path`` only if it stays inside ``root``."""
    try:
        resolved = (root / rel_path).resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root.resolve()):
        return None
    return resolved


def verify_backup(backup_dir: Path) -> bool:
    """Re-hash every manifest-listed file and compare with the manifest.

    Returns ``False`` when the manifest is missing or any check fails.
    """
    manifest = _load_manifest(backup_dir)
    if manifest is None:
        return False
    for rel_path in manifest.files:
        file_path = _resolve_within(backup_dir, rel_path)
        if file_path is None or not file_path.is_file():
            return False
        expected = manifest.checksums.get(rel_path)
        if expected is None or _sha256_file(file_path) != expected:
            return False
    return True


def restore_from_backup(
    backup_dir: Path, settings: Settings, dry_run: bool = True
) -> dict[str, Any]:
    """Restore manifest-listed files to their original locations.

    With ``dry_run=True`` nothing is written; the report describes what
    would be restored. With ``dry_run=False`` each file is written
    atomically to its original location.
    """
    result: dict[str, Any] = {
        "restored": [],
        "skipped": [],
        "errors": [],
        "dry_run": bool(dry_run),
    }

    manifest = _load_manifest(backup_dir)
    if manifest is None:
        result["errors"].append(
            f"manifest not found: {backup_dir / _MANIFEST_NAME}"
        )
        return result

    configured = [
        Path(settings.registry.path),
        Path(settings.data.storage_path),
        Path(settings.paper.account_path),
    ]
    log_parent = Path(settings.logging.file_path).parent
    if log_parent.is_dir():
        configured.append(log_parent)
    source_dirs = [Path(path) for path in manifest.source_paths] or configured

    for rel_path in manifest.files:
        source = _resolve_within(backup_dir, rel_path)
        if source is None or not source.is_file():
            result["errors"].append(f"{rel_path}: missing in backup")
            continue

        target: Path | None = None
        for source_dir in source_dirs:
            candidate = source_dir / rel_path
            if candidate.is_file():
                target = candidate
                break
        if target is None:
            result["skipped"].append(rel_path)
            continue

        if dry_run:
            result["restored"].append(rel_path)
            continue

        try:
            data = source.read_bytes()
        except OSError as exc:
            result["errors"].append(f"{rel_path}: cannot read backup file ({exc})")
            continue
        try:
            _atomic_write_bytes(target, data)
            result["restored"].append(rel_path)
        except OSError as exc:
            result["errors"].append(
                f"{rel_path}: cannot restore to {target} ({exc})"
            )

    return result


def list_backups(backup_dir: Path) -> list[BackupManifest]:
    """Scan ``backup_dir`` for backup manifests, newest created_utc first."""
    manifests: list[BackupManifest] = []
    if not backup_dir.is_dir():
        return manifests
    for child in sorted(backup_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = _load_manifest(child)
        if manifest is not None:
            manifests.append(manifest)
    manifests.sort(key=lambda item: item.created_utc, reverse=True)
    return manifests


def retention_cleanup(backup_dir: Path, keep: int) -> list[str]:
    """Keep the ``keep`` most recent backups; delete the older ones.

    Returns the list of deleted backup IDs.
    """
    if keep < 0:
        raise ValueError(f"keep must be non-negative, got {keep}")
    deleted: list[str] = []
    for manifest in list_backups(backup_dir)[keep:]:
        backup_id_dir = backup_dir / manifest.backup_id
        try:
            if backup_id_dir.is_dir():
                shutil.rmtree(backup_id_dir)
            elif backup_id_dir.exists():
                backup_id_dir.unlink()
            deleted.append(manifest.backup_id)
        except OSError as exc:
            logger.warning("cannot delete backup %s: %s", manifest.backup_id, exc)
    return deleted

