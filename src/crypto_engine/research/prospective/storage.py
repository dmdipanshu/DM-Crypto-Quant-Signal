"""Append-only prospective data storage with tamper-evident hash chain.

Stores raw exchange observations and normalized candle data in separate
JSON Lines files. Each ingestion event is recorded in an immutable ledger
with a hash chain (each event hash includes the previous event hash).

The hash chain provides tamper-evident audit evidence; it does not
provide absolute protection against privileged filesystem modification.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_RAW_OBSERVATIONS_FILE = "raw_observations.jsonl"
DEFAULT_NORMALIZED_CANDLES_FILE = "normalized_candles.jsonl"
DEFAULT_LEDGER_FILE = "ingestion_ledger.jsonl"
DEFAULT_MANIFEST_FILE = "dataset_manifest.json"

LEDGER_SCHEMA_VERSION = "prospective-ledger.v1"
DATASET_SCHEMA_VERSION = "prospective-dataset.v1"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_json(value: Any) -> str:
    """Deterministic sha256 over a JSON-serialisable value."""
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_jsonl(path: Path, lines: list[str]) -> None:
    """Write JSON Lines atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line)
                f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _append_jsonl(path: Path, line: str) -> None:
    """Append a single JSON line to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
        f.write("\n")


class ProspectiveStorage:
    """Append-only storage for prospective observations.

    Parameters
    ----------
    root:
        Storage root directory, e.g. ``data/prospective/binance/spot/BTC/USDT/1h``.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.raw_path = self.root / DEFAULT_RAW_OBSERVATIONS_FILE
        self.normalized_path = self.root / DEFAULT_NORMALIZED_CANDLES_FILE
        self.ledger_path = self.root / DEFAULT_LEDGER_FILE
        self.manifest_path = self.root / DEFAULT_MANIFEST_FILE

    # ------------------------------------------------------------------
    # Raw observation persistence
    # ------------------------------------------------------------------
    def store_raw_observation(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        open_time: str,
        close_time: str,
        observed_at_utc: str,
        ingested_at_utc: str,
        source_endpoint: str,
        request_id: str | None,
        response_hash: str,
        raw_payload_hash: str,
        collector_version: str,
        collection_id: str,
        research_freeze_id: str,
        first_seen_at_utc: str | None = None,
    ) -> dict[str, Any]:
        """Store one raw exchange observation (append-only).

        Returns the observation record with its event ID and raw payload hash.
        """
        observation_id = (
            f"obs-{_sha256_json(raw_payload_hash + open_time + str(ingested_at_utc))[:16]}"
        )

        record = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "event_id": observation_id,
            "event_type": "raw_observation",
            "collection_id": collection_id,
            "exchange": exchange,
            "market_type": market_type,
            "symbol": symbol,
            "interval": interval,
            "open_time": open_time,
            "close_time": close_time,
            "first_seen_at_utc": first_seen_at_utc or observed_at_utc,
            "observed_at_utc": observed_at_utc,
            "ingested_at_utc": ingested_at_utc,
            "source_endpoint": source_endpoint,
            "request_id": request_id,
            "response_hash": response_hash,
            "raw_payload_hash": raw_payload_hash,
            "collector_version": collector_version,
            "research_freeze_id": research_freeze_id,
            "recorded_at_utc": _utc_now_iso(),
        }
        _append_jsonl(self.raw_path, json.dumps(record, sort_keys=True, default=str))
        return record

    # ------------------------------------------------------------------
    # Normalized candle persistence
    # ------------------------------------------------------------------
    def store_normalized_candle(
        self,
        *,
        collection_id: str,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        open_time: str,
        close_time: str,
        open_price: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        source_observation_id: str,
        first_seen_at_utc: str,
        observed_at_utc: str,
        ingested_at_utc: str,
        research_freeze_id: str,
    ) -> dict[str, Any]:
        """Store one normalized candle (append-only).

        Duplicate handling:
          - If a candle with the same identity key (exchange, market_type,
            symbol, interval, open_time) already exists and the OHLCV values
            match exactly, the existing record is returned unchanged (no new
            record is written). The first_seen_at_utc from the original record
            is preserved.
          - If a candle with the same identity key already exists but the OHLCV
            values differ, both records are kept (append-only) and the caller
            should treat the later record as a conflict.
        """
        candle_id = f"candle-{_sha256_json(open_time + symbol + str(close))[:16]}"

        # Check for existing candle with the same identity key
        existing = self._find_existing_candle(
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            interval=interval,
            open_time=open_time,
        )
        if existing is not None:
            existing_candle = existing
            # Same candle already recorded - check if values match
            same_values = (
                abs(float(existing_candle.get("open", 0)) - float(open_price)) < 1e-10
                and abs(float(existing_candle.get("high", 0)) - float(high)) < 1e-10
                and abs(float(existing_candle.get("low", 0)) - float(low)) < 1e-10
                and abs(float(existing_candle.get("close", 0)) - float(close)) < 1e-10
                and abs(float(existing_candle.get("volume", 0)) - float(volume)) < 1e-10
            )
            if same_values:
                # Identical duplicate - return existing record, preserve first_seen
                return dict(existing_candle)
            # Conflicting duplicate - both records are kept (append-only)
            # The caller should mark this as a conflict

        record = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "event_id": candle_id,
            "event_type": "normalized_candle",
            "collection_id": collection_id,
            "exchange": exchange,
            "market_type": market_type,
            "symbol": symbol,
            "interval": interval,
            "open_time": open_time,
            "close_time": close_time,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "source_observation_id": source_observation_id,
            "first_seen_at_utc": first_seen_at_utc,
            "observed_at_utc": observed_at_utc,
            "ingested_at_utc": ingested_at_utc,
            "research_freeze_id": research_freeze_id,
            "recorded_at_utc": _utc_now_iso(),
        }
        _append_jsonl(self.normalized_path, json.dumps(record, sort_keys=True, default=str))
        return record

    def _find_existing_candle(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        open_time: str,
    ) -> dict[str, Any] | None:
        """Find an existing normalized candle with the same identity key.

        Returns the first matching record or None if not found.
        """
        if not self.normalized_path.is_file():
            return None
        with self.normalized_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    record.get("exchange") == exchange
                    and record.get("market_type") == market_type
                    and record.get("symbol") == symbol
                    and record.get("interval") == interval
                    and record.get("open_time") == open_time
                ):
                    return record
        return None

    # ------------------------------------------------------------------
    # Canonical unique reader
    # ------------------------------------------------------------------
    def read_unique_normalized_candles(self) -> list[dict[str, Any]]:
        """Read all unique normalized candle records.

        Deduplicates by candle identity key
        (exchange, market_type, symbol, interval, open_time).
        When multiple records exist for the same candle, the first one
        (earliest recorded_at_utc) is returned. All subsequent records
        are treated as duplicates (identical or conflicting) and excluded.

        Returns a list of unique candle records sorted by open_time.
        """
        if not self.normalized_path.is_file():
            return []
        seen_keys: set[str] = set()
        records: list[dict[str, Any]] = []
        with self.normalized_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = candle_identity_key(
                    exchange=record.get("exchange", ""),
                    market_type=record.get("market_type", ""),
                    symbol=record.get("symbol", ""),
                    interval=record.get("interval", ""),
                    open_time=record.get("open_time", ""),
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                records.append(record)
        # Sort by open_time for deterministic ordering
        records.sort(key=lambda r: r.get("open_time", ""))
        return records

    def get_observation_summary(self) -> dict[str, Any]:
        """Get a summary of all observations in storage.

        Returns counts of:
          - raw ingestion events (total raw observation records)
          - accepted unique observations (unique candles after dedup)
          - duplicate ingestion attempts (extra records for same candle)
          - conflicting observations (same candle, different OHLCV)
          - rejected observations (none stored here; from collector)

        A ledger event is NOT counted as an observation.
        """
        raw_records = self.read_raw_observations()
        unique_candles = self.read_unique_normalized_candles()

        # Count duplicates: raw records minus unique candles
        # We match by identity key
        unique_keys: set[str] = set()
        for c in unique_candles:
            key = candle_identity_key(
                exchange=c.get("exchange", ""),
                market_type=c.get("market_type", ""),
                symbol=c.get("symbol", ""),
                interval=c.get("interval", ""),
                open_time=c.get("open_time", ""),
            )
            unique_keys.add(key)

        duplicate_count = 0
        for r in raw_records:
            key = candle_identity_key(
                exchange=r.get("exchange", ""),
                market_type=r.get("market_type", ""),
                symbol=r.get("symbol", ""),
                interval=r.get("interval", ""),
                open_time=r.get("open_time", ""),
            )
            if key not in unique_keys:
                # Raw observation that never made it to a candle
                continue
            # Count how many raw records map to this unique key
        # Count total raw records per unique key
        from collections import Counter

        raw_key_counts: Counter[str] = Counter()
        for r in raw_records:
            key = candle_identity_key(
                exchange=r.get("exchange", ""),
                market_type=r.get("market_type", ""),
                symbol=r.get("symbol", ""),
                interval=r.get("interval", ""),
                open_time=r.get("open_time", ""),
            )
            raw_key_counts[key] += 1

        # Duplicates = total raw records for keys that have >= 1 unique candle,
        # minus 1 (the first one is the accepted observation)
        duplicate_count = sum(
            count - 1 for key, count in raw_key_counts.items() if key in unique_keys
        )

        # Conflicts: unique candles where multiple raw records have different values
        # We detect this by checking if any unique candle has more than one raw
        # record with different OHLCV values
        conflict_count = 0
        candle_by_key: dict[str, list[dict[str, Any]]] = {}
        for c in self.read_normalized_candles():
            key = candle_identity_key(
                exchange=c.get("exchange", ""),
                market_type=c.get("market_type", ""),
                symbol=c.get("symbol", ""),
                interval=c.get("interval", ""),
                open_time=c.get("open_time", ""),
            )
            candle_by_key.setdefault(key, []).append(c)

        for _key, candles in candle_by_key.items():
            if len(candles) > 1:
                # Check if any have different OHLCV values
                first = candles[0]
                for other in candles[1:]:
                    if (
                        abs(float(first.get("open", 0)) - float(other.get("open", 0))) > 1e-10
                        or abs(float(first.get("high", 0)) - float(other.get("high", 0))) > 1e-10
                        or abs(float(first.get("low", 0)) - float(other.get("low", 0))) > 1e-10
                        or abs(float(first.get("close", 0)) - float(other.get("close", 0))) > 1e-10
                        or abs(float(first.get("volume", 0)) - float(other.get("volume", 0)))
                        > 1e-10
                    ):
                        conflict_count += 1
                        break

        return {
            "raw_ingestion_events": len(raw_records),
            "accepted_unique_observations": len(unique_candles),
            "duplicate_ingestion_attempts": duplicate_count,
            "conflicting_observations": conflict_count,
            "unique_keys": len(unique_keys),
        }

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    def _last_ledger_hash(self) -> str | None:
        """Return the current_event_hash of the last ledger entry, or None."""
        if not self.ledger_path.is_file():
            return None
        last_line = None
        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
        if last_line is None:
            return None
        try:
            entry = json.loads(last_line)
            return entry.get("current_event_hash")
        except json.JSONDecodeError:
            return None

    def append_ledger_event(
        self,
        *,
        event_type: str,
        collection_id: str,
        source_record_hash: str,
        previous_event_hash: str | None = None,
    ) -> dict[str, Any]:
        """Append one event to the tamper-evident ingestion ledger.

        Each event's hash is computed over the event content plus the
        previous event's hash, forming a hash chain.
        """
        prev_hash = (
            previous_event_hash if previous_event_hash is not None else self._last_ledger_hash()
        )

        event_id = (
            f"evt-{_sha256_json(source_record_hash + str(prev_hash or '') + _utc_now_iso())[:16]}"
        )

        content = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "event_id": event_id,
            "event_type": event_type,
            "collection_id": collection_id,
            "source_record_hash": source_record_hash,
            "previous_event_hash": prev_hash,
            "recorded_at_utc": _utc_now_iso(),
        }
        content_for_hash = {k: v for k, v in content.items() if k not in ("current_event_hash",)}
        current_hash = _sha256_json(content_for_hash)
        content["current_event_hash"] = current_hash

        _append_jsonl(self.ledger_path, json.dumps(content, sort_keys=True, default=str))
        return content

    def verify_ledger_chain(self) -> dict[str, Any]:
        """Verify the tamper-evident hash chain integrity.

        Returns a dict with ``valid``, ``event_count``, ``first_event_id``,
        ``last_event_hash``, and ``broken_at`` (if invalid).
        """
        if not self.ledger_path.is_file():
            return {
                "valid": True,
                "event_count": 0,
                "first_event_id": None,
                "last_event_hash": None,
                "broken_at": None,
                "reason": "no ledger file",
            }

        previous_hash: str | None = None
        event_count = 0
        first_event_id = None
        last_event_hash = None

        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    return {
                        "valid": False,
                        "event_count": event_count,
                        "first_event_id": first_event_id,
                        "last_event_hash": last_event_hash,
                        "broken_at": line_no,
                        "reason": f"invalid JSON at line {line_no}: {exc}",
                    }

                event_id = entry.get("event_id")
                if first_event_id is None:
                    first_event_id = event_id

                stored_hash = entry.get("current_event_hash")
                content_for_hash = {
                    k: v for k, v in entry.items() if k not in ("current_event_hash",)
                }
                expected_hash = _sha256_json(content_for_hash)

                if stored_hash != expected_hash:
                    return {
                        "valid": False,
                        "event_count": event_count,
                        "first_event_id": first_event_id,
                        "last_event_hash": last_event_hash,
                        "broken_at": line_no,
                        "reason": f"hash mismatch at line {line_no}: expected {expected_hash}, stored {stored_hash}",
                    }

                entry_prev_hash = entry.get("previous_event_hash")
                if entry_prev_hash != previous_hash:
                    return {
                        "valid": False,
                        "event_count": event_count,
                        "first_event_id": first_event_id,
                        "last_event_hash": last_event_hash,
                        "broken_at": line_no,
                        "reason": f"chain break at line {line_no}: expected previous {previous_hash}, found {entry_prev_hash}",
                    }

                previous_hash = stored_hash
                last_event_hash = stored_hash
                event_count += 1

        return {
            "valid": True,
            "event_count": event_count,
            "first_event_id": first_event_id,
            "last_event_hash": last_event_hash,
            "broken_at": None,
            "reason": None,
        }

    # ------------------------------------------------------------------
    # Dataset manifest
    # ------------------------------------------------------------------
    def write_dataset_manifest(self, manifest: dict[str, Any]) -> None:
        """Write the prospective dataset manifest (overwrites previous)."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )

    def read_dataset_manifest(self) -> dict[str, Any] | None:
        """Read the prospective dataset manifest."""
        if not self.manifest_path.is_file():
            return None
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------
    def read_raw_observations(self) -> list[dict[str, Any]]:
        """Read all raw observation records."""
        if not self.raw_path.is_file():
            return []
        records: list[dict[str, Any]] = []
        with self.raw_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def read_normalized_candles(self) -> list[dict[str, Any]]:
        """Read all normalized candle records."""
        if not self.normalized_path.is_file():
            return []
        records: list[dict[str, Any]] = []
        with self.normalized_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def read_ledger_events(self) -> list[dict[str, Any]]:
        """Read all ledger events."""
        if not self.ledger_path.is_file():
            return []
        records: list[dict[str, Any]] = []
        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records

    def compute_dataset_hash(self) -> str | None:
        """Compute sha256 over all normalized candle records.

        Uses only canonical OHLCV fields and sorts by open_time so the
        hash is deterministic regardless of insertion order or metadata
        differences (event_id, recorded_at_utc, etc.).
        """
        candles = self.read_normalized_candles()
        if not candles:
            return _sha256_json([])
        canonical = [
            {
                "open_time": c["open_time"],
                "close_time": c["close_time"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
            for c in candles
        ]
        canonical_sorted = sorted(canonical, key=lambda c: c["open_time"])
        return _sha256_json(canonical_sorted)

    def compute_raw_payload_hash(self) -> str | None:
        """Compute sha256 over all raw observation records.

        Uses raw_payload_hash values (already deterministic exchange-response
        hashes) sorted lexicographically so the result is deterministic
        regardless of insertion order or metadata differences.
        """
        observations = self.read_raw_observations()
        if not observations:
            return _sha256_json([])
        raw_hashes = sorted(o["raw_payload_hash"] for o in observations)
        return _sha256_json(raw_hashes)


# ------------------------------------------------------------------
# Candle identity helper
# ------------------------------------------------------------------


def candle_identity_key(
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    interval: str,
    open_time: str,
) -> str:
    """Build a stable unique key for a candle observation.

    The unique identity of a prospective candle is the combination of
    exchange, market type, symbol, interval (timeframe), and open time.
    Two records with the same key refer to the same market observation;
    any difference in OHLCV values for the same key is a conflict.
    """
    payload = json.dumps(
        {
            "exchange": exchange,
            "market_type": market_type,
            "symbol": symbol,
            "interval": interval,
            "open_time": open_time,
        },
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "DATASET_SCHEMA_VERSION",
    "DEFAULT_LEDGER_FILE",
    "DEFAULT_MANIFEST_FILE",
    "DEFAULT_NORMALIZED_CANDLES_FILE",
    "DEFAULT_RAW_OBSERVATIONS_FILE",
    "LEDGER_SCHEMA_VERSION",
    "ProspectiveStorage",
    "candle_identity_key",
]
