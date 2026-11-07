"""Deterministic JSON metadata/manifest foundation (Stage 0 / A0).

JSON owns manifests, provenance, checksums, validation/model metadata and
verification records. It must never hold credentials and must never become a
dataset warehouse: large OHLCV history belongs in Parquet.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from crypto_engine.storage.contracts import (
    StorageRole,
    assert_storage_role,
)

_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "authorization",
    "private_key",
)
_REFERENCE_SUFFIXES = ("_env", "_ref", "_reference")


class MetadataError(ValueError):
    """Raised when metadata cannot be serialized deterministically."""


class SecretInMetadataError(MetadataError):
    """Raised when metadata would persist a credential value."""


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized.endswith(_REFERENCE_SUFFIXES):
        return False
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def find_secret_fields(payload: Any, *, _prefix: str = "") -> list[str]:
    """Return dotted paths of credential-shaped keys anywhere in ``payload``."""
    found: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            name = str(key)
            path = f"{_prefix}.{name}" if _prefix else name
            if _is_secret_key(name):
                found.append(path)
            found.extend(find_secret_fields(value, _prefix=path))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found.extend(find_secret_fields(value, _prefix=f"{_prefix}[{index}]"))
    return found


def canonical_json_dumps(payload: Any) -> str:
    """Deterministic compact JSON: sorted keys, no NaN, ASCII-safe."""
    if not isinstance(payload, Mapping):
        raise MetadataError("metadata payload must be a mapping")
    secrets = find_secret_fields(payload)
    if secrets:
        raise SecretInMetadataError(
            "metadata must not contain credential fields: " + ", ".join(sorted(secrets))
        )
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MetadataError(f"metadata is not JSON-serializable: {exc}") from exc


def write_metadata(
    path: str | Path, payload: Mapping[str, Any], *, indent: int | None = None
) -> Path:
    """Atomically write metadata; secrets are rejected before any file exists."""
    target = assert_storage_role(path, StorageRole.METADATA)
    secrets = find_secret_fields(payload)
    if secrets:
        raise SecretInMetadataError(
            "metadata must not contain credential fields: " + ", ".join(sorted(secrets))
        )
    if indent is None:
        text = canonical_json_dumps(payload)
    else:
        try:
            text = json.dumps(
                payload,
                sort_keys=True,
                indent=indent,
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise MetadataError(f"metadata is not JSON-serializable: {exc}") from exc
        text += "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    return target


def read_metadata(path: str | Path) -> dict[str, Any]:
    """Read a JSON metadata object; never returns a non-object document."""
    target = assert_storage_role(path, StorageRole.METADATA)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MetadataError(f"metadata file is not valid JSON: {target.name}") from exc
    if not isinstance(data, dict):
        raise MetadataError("metadata document must be a JSON object")
    return data
