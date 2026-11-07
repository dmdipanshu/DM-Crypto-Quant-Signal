"""SQLite WAL transactional storage foundation (Stage 0 / A0).

Rules (frozen): SQLite only, WAL journal, foreign keys enabled, bounded short
write transactions, local filesystem only. This is **not** a historical OHLCV
warehouse: canonical candles belong in Parquet (see ``storage/contracts.py``).
No scheduler or paper-trading tables are created here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from crypto_engine.storage.contracts import (
    StorageRole,
    StorageRoleError,
    assert_storage_role,
)

BUSY_TIMEOUT_MS = 5_000
DEFAULT_TIMEOUT_S = 5.0
SYNCHRONOUS = "NORMAL"

_WAL_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}",
    f"PRAGMA synchronous={SYNCHRONOUS}",
)


def connect_wal(path: str | Path, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> sqlite3.Connection:
    """Open a local SQLite database in WAL mode with foreign keys enabled.

    The caller supplies the path explicitly: there is no default database, so
    importing or testing this module never creates a production ``*.db`` file.
    """
    target = assert_storage_role(path, StorageRole.TRANSACTIONAL)
    if not target.parent.exists():
        raise StorageRoleError(
            f"parent directory does not exist for SQLite database: {target.parent}"
        )
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    connection = sqlite3.connect(str(target), timeout=timeout_s, isolation_level=None)
    try:
        for pragma in _WAL_PRAGMAS:
            connection.execute(pragma)
        if not wal_enabled(connection):
            raise StorageRoleError(f"SQLite refused WAL journal mode for {target.name!r}")
        connection.row_factory = sqlite3.Row
    except BaseException:
        connection.close()
        raise
    return connection


def wal_enabled(connection: sqlite3.Connection) -> bool:
    """Whether the connection is actually journaling in WAL mode."""
    row = connection.execute("PRAGMA journal_mode").fetchone()
    return bool(row) and str(row[0]).lower() == "wal"


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, immediate: bool = True
) -> Iterator[sqlite3.Connection]:
    """One bounded write transaction; nested transactions are rejected."""
    if connection.in_transaction:
        raise StorageRoleError("nested SQLite transactions are not supported (short writes only)")
    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()
