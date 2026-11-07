"""Stage 0 / A0 storage tests: role separation, SQLite WAL, JSON metadata.

Temporary databases only (pytest ``tmp_path``); no production ``*.db``.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from crypto_engine.storage import (
    BUSY_TIMEOUT_MS,
    MarketDataStore,
    StorageRole,
    StorageRoleError,
    connect_wal,
    read_metadata,
    transaction,
    wal_enabled,
    write_metadata,
)
from crypto_engine.storage.contracts import assert_storage_role, assert_store_role
from crypto_engine.storage.json_store import (
    SecretInMetadataError,
    canonical_json_dumps,
    find_secret_fields,
)


def test_storage_role_paths_are_enforced(tmp_path: Path) -> None:
    parquet = assert_storage_role(tmp_path / "candles.parquet", StorageRole.MARKET_DATA)
    assert parquet.suffix == ".parquet"
    with pytest.raises(StorageRoleError):
        assert_storage_role(tmp_path / "candles.db", StorageRole.MARKET_DATA)
    with pytest.raises(StorageRoleError):
        assert_storage_role(tmp_path / "state.parquet", StorageRole.TRANSACTIONAL)
    with pytest.raises(StorageRoleError):
        assert_storage_role(tmp_path / "manifest.parquet", StorageRole.METADATA)


def test_historical_ohlcv_cannot_be_redirected_into_sqlite(tmp_path: Path) -> None:
    with pytest.raises(StorageRoleError, match="parquet"):
        assert_storage_role(tmp_path / "ohlcv.sqlite3", StorageRole.MARKET_DATA)


@pytest.mark.parametrize(
    "bad_path",
    [":memory:", r"\\server\share\state.db", "https://example.test/state.db"],
)
def test_non_local_transactional_paths_are_rejected(bad_path: str) -> None:
    with pytest.raises(StorageRoleError):
        assert_storage_role(bad_path, StorageRole.TRANSACTIONAL)


def test_connect_wal_applies_required_pragmas(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    connection = connect_wal(database)
    try:
        assert wal_enabled(connection) is True
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == BUSY_TIMEOUT_MS
    finally:
        connection.close()
    assert database.exists()


def test_connect_wal_requires_an_explicit_local_path(tmp_path: Path) -> None:
    with pytest.raises(StorageRoleError):
        connect_wal(":memory:")
    with pytest.raises(StorageRoleError):
        connect_wal(tmp_path / "state.parquet")
    signature = inspect.signature(connect_wal)
    assert signature.parameters["path"].default is inspect.Parameter.empty


def test_transaction_commits_and_rolls_back(tmp_path: Path) -> None:
    connection = connect_wal(tmp_path / "state.db")
    try:
        connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT NOT NULL)")
        with transaction(connection):
            connection.execute("INSERT INTO events (kind) VALUES (?)", ("commit",))
        with pytest.raises(RuntimeError), transaction(connection):
            connection.execute("INSERT INTO events (kind) VALUES (?)", ("rollback",))
            raise RuntimeError("boom")
        rows = [row[0] for row in connection.execute("SELECT kind FROM events ORDER BY id")]
        assert rows == ["commit"]
    finally:
        connection.close()


def test_nested_transactions_are_rejected(tmp_path: Path) -> None:
    connection = connect_wal(tmp_path / "state.db")
    try:
        with (
            transaction(connection),
            pytest.raises(StorageRoleError),
            transaction(connection),
        ):
            pass
    finally:
        connection.close()


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    connection = connect_wal(tmp_path / "state.db")
    try:
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child ("
            "id INTEGER PRIMARY KEY, "
            "parent_id INTEGER NOT NULL REFERENCES parent(id))"
        )
        with pytest.raises(sqlite3.IntegrityError), transaction(connection):
            connection.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")
    finally:
        connection.close()


def test_metadata_serialization_is_deterministic() -> None:
    first = canonical_json_dumps({"b": 2, "a": {"y": [1, 2], "x": None}})
    second = canonical_json_dumps({"a": {"x": None, "y": [1, 2]}, "b": 2})
    assert first == second == '{"a":{"x":null,"y":[1,2]},"b":2}'


def test_metadata_write_is_atomic_and_readable(tmp_path: Path) -> None:
    target = write_metadata(
        tmp_path / "manifest.json",
        {"snapshot_id": "s1", "sha256": "abc", "rows": 10},
    )
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "rows": 10,
        "sha256": "abc",
        "snapshot_id": "s1",
    }
    assert read_metadata(target)["snapshot_id"] == "s1"
    leftovers = [path.name for path in tmp_path.iterdir() if path.name.endswith(".tmp")]
    assert leftovers == []


@pytest.mark.parametrize(
    "payload",
    [
        {"api_key": "sk-live-secret"},
        {"nested": {"authorization": "Bearer abc"}},
        {"items": [{"access_token": "abc"}]},
    ],
)
def test_metadata_rejects_credentials(tmp_path: Path, payload: dict) -> None:
    with pytest.raises(SecretInMetadataError):
        write_metadata(tmp_path / "manifest.json", payload)
    assert not (tmp_path / "manifest.json").exists()


def test_metadata_allows_secret_references(tmp_path: Path) -> None:
    target = write_metadata(
        tmp_path / "manifest.json",
        {"api_key_env": "CUSTOM_API_KEY", "secret_ref": "CUSTOM_SECRET"},
    )
    assert read_metadata(target)["api_key_env"] == "CUSTOM_API_KEY"
    assert find_secret_fields({"api_key_env": "CUSTOM_API_KEY"}) == []


def test_metadata_rejects_non_object_payload(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        canonical_json_dumps(["not", "a", "mapping"])
    target = write_metadata(tmp_path / "manifest.json", {"ok": True})
    assert target.suffix == ".json"


class _FakeMarketStore:
    storage_role = StorageRole.MARKET_DATA

    def write_candles(self, frame, *, exchange, symbol, timeframe):
        return f"{exchange}:{symbol}:{timeframe}"

    def read_candles(self, *, exchange, symbol, timeframe, limit=None):
        raise NotImplementedError


def test_store_protocols_declare_storage_roles() -> None:
    store = _FakeMarketStore()
    assert isinstance(store, MarketDataStore)
    assert_store_role(store, StorageRole.MARKET_DATA)
    with pytest.raises(StorageRoleError):
        assert_store_role(store, StorageRole.TRANSACTIONAL)


def test_no_database_ships_inside_the_package_or_config() -> None:
    repository = Path(__file__).resolve().parents[2]
    offenders = [
        path for folder in ("src", "config") for path in (repository / folder).rglob("*.db")
    ]
    assert offenders == []
