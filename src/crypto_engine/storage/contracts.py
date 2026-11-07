"""Storage role contracts (Stage 0 / A0).

Ownership is fixed by ``docs/architecture/STORAGE_OWNERSHIP.md``:

* ``MarketDataStore``   -> Parquet: canonical historical OHLCV and snapshots
* ``TransactionalStore``-> SQLite WAL: canonical transactional state
* ``MetadataStore``     -> JSON: manifests, provenance, checksums, verification

These protocols and path guards exist so historical market data can never be
silently redirected into SQLite (and transactional state never into Parquet).
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

import pandas as pd


class StorageRole(StrEnum):
    """Closed storage-role vocabulary."""

    MARKET_DATA = "market_data"
    TRANSACTIONAL = "transactional"
    METADATA = "metadata"


_ROLE_SUFFIXES: dict[StorageRole, frozenset[str]] = {
    StorageRole.MARKET_DATA: frozenset({".parquet"}),
    StorageRole.TRANSACTIONAL: frozenset({".db", ".sqlite", ".sqlite3"}),
    StorageRole.METADATA: frozenset({".json"}),
}

ROLE_OWNERSHIP: dict[StorageRole, str] = {
    StorageRole.MARKET_DATA: "Parquet (canonical historical OHLCV)",
    StorageRole.TRANSACTIONAL: "SQLite WAL (canonical transactional state)",
    StorageRole.METADATA: "JSON (manifests, provenance, checksums)",
}


class StorageRoleError(ValueError):
    """Raised when a path or store violates the fixed storage-role ownership."""


def assert_storage_role(path: str | Path, role: StorageRole) -> Path:
    """Validate that ``path`` can only serve ``role``; return it as a Path."""
    raw = str(path)
    if raw == ":memory:" or "://" in raw:
        raise StorageRoleError(
            f"{role.value} storage requires a local filesystem path, got {raw!r}"
        )
    if raw.startswith("\\\\"):
        raise StorageRoleError(f"{role.value} storage does not support UNC/network paths: {raw!r}")
    candidate = Path(raw)
    suffixes = _ROLE_SUFFIXES[role]
    if candidate.suffix.lower() not in suffixes:
        expected = ", ".join(sorted(suffixes))
        raise StorageRoleError(
            f"{role.value} storage must use {expected} files; refusing {candidate.name!r}"
        )
    return candidate


def assert_store_role(store: object, role: StorageRole) -> None:
    """Validate a store implementation's declared role."""
    declared = getattr(store, "storage_role", None)
    if declared != role:
        raise StorageRoleError(f"store declares role {declared!r}, expected {role.value!r}")


@runtime_checkable
class MarketDataStore(Protocol):
    """Canonical historical market data lives in Parquet, keyed by identity."""

    storage_role: ClassVar[StorageRole]

    def write_candles(
        self, frame: pd.DataFrame, *, exchange: str, symbol: str, timeframe: str
    ) -> str:
        """Persist a bounded candle frame; return its immutable snapshot id."""
        ...

    def read_candles(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Read a bounded slice of canonical candles."""
        ...


@runtime_checkable
class TransactionalStore(Protocol):
    """Transactional state lives in SQLite with WAL, never OHLCV history."""

    storage_role: ClassVar[StorageRole]

    def transaction(self) -> contextlib.AbstractContextManager[Any]:
        """Open one bounded, short write transaction."""
        ...


@runtime_checkable
class MetadataStore(Protocol):
    """Lightweight metadata lives in JSON; large datasets do not."""

    storage_role: ClassVar[StorageRole]

    def write(self, payload: Mapping[str, Any]) -> Path:
        """Persist deterministic metadata; return the written path."""
        ...

    def read(self) -> dict[str, Any]:
        """Read the metadata document."""
        ...
