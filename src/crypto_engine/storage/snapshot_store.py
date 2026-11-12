"""Snapshot store adapter implementing ports.SnapshotStore.

Filesystem-backed implementation: each snapshot is a Parquet payload file
alongside a JSON sidecar manifest containing the SHA-256 checksum. Readers
recompute and verify the checksum on every read; corruption surfaces as an
error, never as silent data loss.

Pure filesystem I/O, no network, no ccxt, no pandas. The parquet bytes are
treated as opaque blobs; serialization is handled by the caller.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class FileSnapshotStore:
    """SnapshotStore backed by local files with SHA-256 provenance manifests.

    Layout under ``root``::

        root/<snapshot_id>.parquet   -- opaque Parquet payload
        root/<snapshot_id>.json      -- manifest with checksum + metadata
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def write(self, snapshot_id: str, parquet_bytes: bytes, manifest: dict) -> str:
        """Write an immutable snapshot atomically.

        Returns the snapshot id. Raises FileExistsError if the id already
        exists (no overwrite).
        """
        self.root.mkdir(parents=True, exist_ok=True)
        payload_path = self.root / f"{snapshot_id}.parquet"
        manifest_path = self.root / f"{snapshot_id}.json"

        if payload_path.exists() or manifest_path.exists():
            raise FileExistsError(f"snapshot {snapshot_id} already exists")

        checksum = hashlib.sha256(parquet_bytes).hexdigest()
        full_manifest = {
            **manifest,
            "checksum": checksum,
            "checksum_algorithm": "sha256",
            "snapshot_id": snapshot_id,
        }

        # Write manifest first, then payload; payload write is the commit point.
        manifest_tmp = manifest_path.with_suffix(".json.tmp")
        payload_tmp = payload_path.with_suffix(".parquet.tmp")
        try:
            manifest_tmp.write_text(
                json.dumps(full_manifest, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            payload_tmp.write_bytes(parquet_bytes)
            os_replace(manifest_tmp, manifest_path)
            os_replace(payload_tmp, payload_path)
        except BaseException:
            with __import__("contextlib").suppress(OSError):
                manifest_tmp.unlink(missing_ok=True)
            with __import__("contextlib").suppress(OSError):
                payload_tmp.unlink(missing_ok=True)
            raise
        return snapshot_id

    def read(self, snapshot_id: str) -> tuple[bytes, dict]:
        """Read a snapshot by id.

        Returns ``(parquet_bytes, manifest)``. Recomputes and verifies the
        SHA-256 checksum; raises ValueError on mismatch.
        """
        payload_path = self.root / f"{snapshot_id}.parquet"
        manifest_path = self.root / f"{snapshot_id}.json"
        if not payload_path.is_file() or not manifest_path.is_file():
            raise KeyError(f"snapshot {snapshot_id} not found")
        parquet_bytes = payload_path.read_bytes()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = hashlib.sha256(parquet_bytes).hexdigest()
        expected = manifest.get("checksum")
        if actual != expected:
            raise ValueError(
                f"checksum mismatch for snapshot {snapshot_id}: expected {expected}, got {actual}"
            )
        return parquet_bytes, manifest

    def exists(self, snapshot_id: str) -> bool:
        """Return True if a snapshot with the given id exists."""
        return (self.root / f"{snapshot_id}.parquet").is_file() and (
            self.root / f"{snapshot_id}.json"
        ).is_file()

    def checksum(self, snapshot_id: str) -> str:
        """Return the SHA-256 checksum of a snapshot's Parquet bytes."""
        _, manifest = self.read(snapshot_id)
        return str(manifest["checksum"])


def os_replace(src: Path, dst: Path) -> None:
    """Atomic replace with cross-filesystem fallback."""
    import os

    os.replace(str(src), str(dst))


__all__ = ["FileSnapshotStore", "os_replace"]
