"""Persistent signal audit history (Phase 5 Track B).

:class:`SignalHistory` is an append-only JSONL audit log for canonical
:class:`SignalRecord` objects with hash-chained tamper evidence: every line
stores ``prev_hash`` (the previous line's ``record_hash``, genesis
``"0" * 64``) and ``record_hash = sha256(prev_hash + canonical_record_json)``.
:func:`verify` recomputes the chain, revalidates every record against the
schema, and reports hash mismatches, schema violations, corrupt lines, and
chain breaks.

This is NOT a portfolio ledger: it records signal decisions for audit, never
positions, fills, equity, or P&L. Registry atomic conventions are mirrored
(tmp file + ``os.replace`` only for init-time writes; per-record appends use
flush + fsync; OneDrive-safe small writes, no held locks).

No network, no randomness, single-process. Secrets policy: records must not
contain ``*api_key*``/``*secret*`` keys at any depth -- :func:`append`
rejects such records explicitly (schema already bans prompt/secret AI notes).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from crypto_engine.signals.schema import SignalRecord

GENESIS_HASH = "0" * 64
FILENAME = "signals.jsonl"

_SECRET_KEY_FRAGMENTS = ("api_key", "apikey", "secret")


class PersistenceError(OSError):
    """Raised when the history cannot be read or durably appended."""


def _scan_for_secret_keys(payload: Any, trail: str = "$") -> str | None:
    """Return the offending key path if a secret-like key exists, else None."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            if any(frag in lowered for frag in _SECRET_KEY_FRAGMENTS):
                return f"{trail}.{key}"
            hit = _scan_for_secret_keys(value, f"{trail}.{key}")
            if hit is not None:
                return hit
    elif isinstance(payload, list):
        for idx, value in enumerate(payload):
            hit = _scan_for_secret_keys(value, f"{trail}[{idx}]")
            if hit is not None:
                return hit
    return None


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _record_hash(prev_hash: str, record_payload: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + _canonical_json(record_payload)).encode("utf-8")).hexdigest()


class SignalHistory:
    """Append-only, hash-chained JSONL audit log of signal records."""

    def __init__(self, path: Path | str) -> None:
        """Bind to a history directory (created if missing).

        Raises:
            PersistenceError: If the directory cannot be created or is not
                a directory.
        """
        self.root = Path(path)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PersistenceError(f"SignalHistory: cannot create {self.root}: {exc}") from exc
        if not self.root.is_dir():
            raise PersistenceError(f"SignalHistory: not a directory: {self.root}")
        self.filepath = self.root / FILENAME

    def _read_lines(self) -> list[tuple[int, str]]:
        try:
            if not self.filepath.is_file():
                return []
            text = self.filepath.read_text(encoding="utf-8")
        except OSError as exc:
            raise PersistenceError(f"SignalHistory: cannot read {self.filepath}: {exc}") from exc
        return [(idx + 1, line) for idx, line in enumerate(text.splitlines()) if line.strip()]

    @staticmethod
    def _parse_line(lineno: int, line: str) -> dict[str, Any]:
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise PersistenceError(
                f"SignalHistory: corrupt JSONL at line {lineno} (chain unsafe to extend)."
            ) from exc
        if not isinstance(payload, dict):
            raise PersistenceError(
                f"SignalHistory: corrupt JSONL at line {lineno} (not an object)."
            )
        return payload

    def append(self, record: SignalRecord) -> str:
        """Append a record; idempotent on ``signal_id``.

        Returns the ``signal_id``. If it already exists, the file is left
        untouched and the existing id is returned. Secret-like keys are
        rejected with ``ValueError``. Corrupt existing lines raise
        :class:`PersistenceError` (a broken chain is never silently extended,
        and nothing is claimed as recorded on failure).
        """
        payload = record.model_dump(mode="json")
        hit = _scan_for_secret_keys(payload)
        if hit is not None:
            raise ValueError(
                f"SignalHistory: refusing to store secret-like key at {hit} "
                "(no secrets in audit history)."
            )
        lines = self._read_lines()
        prev = GENESIS_HASH
        for lineno, line in lines:
            entry = self._parse_line(lineno, line)
            if entry.get("signal_id") == record.signal_id:
                return str(entry["signal_id"])
            prev = str(entry.get("record_hash", ""))
        digest = _record_hash(prev, payload)
        out = {
            "signal_id": record.signal_id,
            "prev_hash": prev,
            "record_hash": digest,
            "record": payload,
        }
        raw = (json.dumps(out, sort_keys=True) + "\n").encode("utf-8")
        try:
            with open(self.filepath, "ab") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise PersistenceError(
                f"SignalHistory: cannot append to {self.filepath}: {exc} (NOT recorded)."
            ) from exc
        return record.signal_id

    def get(self, signal_id: str) -> SignalRecord | None:
        """Return the record with ``signal_id``, or None when absent."""
        for _, entry in self._iter_entries():
            if entry.get("signal_id") == signal_id:
                return SignalRecord.model_validate(entry["record"])
        return None

    def _iter_entries(self) -> list[tuple[int, dict[str, Any]]]:
        out: list[tuple[int, dict[str, Any]]] = []
        for lineno, line in self._read_lines():
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                out.append((lineno, payload))
        return out

    def list(self, symbol: str | None = None, direction: str | None = None) -> list[SignalRecord]:
        """List records in chain order, optionally filtered (small scans only)."""
        records: list[SignalRecord] = []
        for _, entry in self._iter_entries():
            try:
                record = SignalRecord.model_validate(entry.get("record", {}))
            except ValueError:
                continue
            if symbol is not None and record.symbol != symbol:
                continue
            if direction is not None and record.direction != direction:
                continue
            records.append(record)
        return records

    def verify(self) -> tuple[bool, list[str]]:
        """Verify chain integrity and schema validity.

        Returns ``(ok, errors)``: hash mismatches, schema violations,
        corrupt lines, and ``prev_hash`` breaks are all reported; ``ok`` is
        True only when every line verifies.
        """
        errors: list[str] = []
        prev = GENESIS_HASH
        lines = self._read_lines()
        if not lines:
            return True, []
        for lineno, line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                errors.append(f"line {lineno}: corrupt JSON")
                continue
            if not isinstance(entry, dict):
                errors.append(f"line {lineno}: not an object")
                continue
            for key in ("signal_id", "prev_hash", "record_hash", "record"):
                if key not in entry:
                    errors.append(f"line {lineno}: missing key {key!r}")
            if errors and errors[-1].startswith(f"line {lineno}:"):
                continue
            if entry.get("prev_hash") != prev:
                errors.append(f"line {lineno}: chain break (prev_hash mismatch)")
            try:
                SignalRecord.model_validate(entry["record"])
            except ValueError as exc:
                errors.append(f"line {lineno}: schema violation: {exc}")
                prev = str(entry.get("record_hash", prev))
                continue
            expected = _record_hash(str(entry["prev_hash"]), entry["record"])
            if expected != entry.get("record_hash"):
                errors.append(f"line {lineno}: record_hash mismatch (tamper-evident)")
            prev = str(entry.get("record_hash"))
        return (len(errors) == 0), errors


__all__ = ["FILENAME", "GENESIS_HASH", "PersistenceError", "SignalHistory"]
