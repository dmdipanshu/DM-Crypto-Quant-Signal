"""Snapshot store port (DR-2).

Protocol for immutable Parquet snapshot storage with SHA-256 provenance
manifests. Implementations live in the infrastructure layer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SnapshotStore(Protocol):
    """Protocol for reading and writing immutable dataset snapshots."""

    def write(
        self,
        snapshot_id: str,
        parquet_bytes: bytes,
        manifest: dict,
    ) -> str:
        """Write an immutable snapshot atomically.

        Returns the snapshot id. Raises if the snapshot id already exists
        (no overwrite).
        """
        ...

    def read(self, snapshot_id: str) -> tuple[bytes, dict]:
        """Read a snapshot by id.

        Returns ``(parquet_bytes, manifest)``. Recomputes and verifies the
        SHA-256 checksum; raises on mismatch.
        """
        ...

    def exists(self, snapshot_id: str) -> bool:
        """Return True if a snapshot with the given id exists."""
        ...

    def checksum(self, snapshot_id: str) -> str:
        """Return the SHA-256 checksum of a snapshot's Parquet bytes."""
        ...


__all__ = ["SnapshotStore"]
