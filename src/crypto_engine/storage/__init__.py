"""Storage ownership foundation (Stage 0 / A0).

Three fixed roles, no overlap: Parquet for canonical historical OHLCV, SQLite
WAL for canonical transactional state, JSON for lightweight metadata. This
package defines boundaries only; it is not a complete storage system.
"""

from crypto_engine.storage.contracts import (
    ROLE_OWNERSHIP,
    MarketDataStore,
    MetadataStore,
    StorageRole,
    StorageRoleError,
    TransactionalStore,
    assert_storage_role,
    assert_store_role,
)
from crypto_engine.storage.json_store import (
    MetadataError,
    SecretInMetadataError,
    canonical_json_dumps,
    find_secret_fields,
    read_metadata,
    write_metadata,
)
from crypto_engine.storage.sqlite import (
    BUSY_TIMEOUT_MS,
    connect_wal,
    transaction,
    wal_enabled,
)

__all__ = [
    "BUSY_TIMEOUT_MS",
    "ROLE_OWNERSHIP",
    "MarketDataStore",
    "MetadataError",
    "MetadataStore",
    "SecretInMetadataError",
    "StorageRole",
    "StorageRoleError",
    "TransactionalStore",
    "assert_storage_role",
    "assert_store_role",
    "canonical_json_dumps",
    "connect_wal",
    "find_secret_fields",
    "read_metadata",
    "transaction",
    "wal_enabled",
    "write_metadata",
]
