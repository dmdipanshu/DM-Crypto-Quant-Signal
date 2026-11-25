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
DEFAULT_ANCHOR_FILE = "collection_start_anchor.json"

LEDGER_SCHEMA_VERSION = "prospective-ledger.v1"
DATASET_SCHEMA_VERSION = "prospective-dataset.v1"
ANCHOR_SCHEMA_VERSION = "collection-start-anchor.v1"


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
        self.anchor_path = self.root / DEFAULT_ANCHOR_FILE

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

    def has_normalized_candle(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        open_time: str,
    ) -> bool:
        """Return True if a unique normalized candle exists for the identity key."""
        return (
            self._find_existing_candle(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                interval=interval,
                open_time=open_time,
            )
            is not None
        )

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

    def get_prospective_observation_count(self, first_open_ms: int, evaluation_ms: int) -> int:
        """Count prospectively eligible observations.

        An observation is prospectively eligible only if the full pipeline
        succeeded: raw observation stored, normalized candle stored, and
        ledger event appended.  The ledger chain must be valid for any
        observation to count.

        Criteria:
        - Raw observation record exists
        - Corresponding normalized candle exists (matching identity key)
        - Corresponding ledger event exists (matching source_record_hash)
        - Ledger chain is valid
        - open_time >= first_open_ms (within prospective window)
        - close_time <= evaluation_ms (candle finalized by evaluation time)
        - Not a duplicate (unique by identity key)

        Args:
            first_open_ms: First eligible hourly boundary (ms since epoch).
            evaluation_ms: Evaluation timestamp (ms since epoch) used as the
                upper bound for candle close time.

        Returns:
            Count of prospectively eligible observations.
        """
        # Ledger integrity is a hard prerequisite for any prospective evidence.
        if not self.verify_ledger_chain().get("valid", False):
            return 0

        raw_observations = self.read_raw_observations()
        normalized_candles = self.read_normalized_candles()
        ledger_events = self.read_ledger_events()

        # Build lookup: normalized candles by identity key.
        normalized_by_key: dict[str, dict[str, Any]] = {}
        for c in normalized_candles:
            key = candle_identity_key(
                exchange=c.get("exchange", ""),
                market_type=c.get("market_type", ""),
                symbol=c.get("symbol", ""),
                interval=c.get("interval", ""),
                open_time=c.get("open_time", ""),
            )
            normalized_by_key[key] = c

        # Build lookup: ledger events by source_record_hash.
        ledger_by_source_hash: dict[str, dict[str, Any]] = {}
        for event in ledger_events:
            source_hash = event.get("source_record_hash", "")
            ledger_by_source_hash[source_hash] = event

        count = 0
        seen_keys: set[str] = set()

        for obs in raw_observations:
            key = candle_identity_key(
                exchange=obs.get("exchange", ""),
                market_type=obs.get("market_type", ""),
                symbol=obs.get("symbol", ""),
                interval=obs.get("interval", ""),
                open_time=obs.get("open_time", ""),
            )

            # Skip duplicates: count each unique candle identity once.
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Check normalized candle exists for this observation.
            normalized = normalized_by_key.get(key)
            if normalized is None:
                continue

            # Check ledger event exists for this observation.
            source_hash = obs.get("raw_payload_hash", "")
            if source_hash not in ledger_by_source_hash:
                continue

            # Check open_time >= first_open_ms (within prospective window).
            open_time_str = obs.get("open_time", "")
            try:
                open_time_ms = int(datetime.fromisoformat(open_time_str).timestamp() * 1000)
            except (ValueError, TypeError):
                continue
            if open_time_ms < first_open_ms:
                continue

            # Check close_time <= evaluation_ms (candle finalized by eval time).
            close_time_str = normalized.get("close_time", "")
            try:
                close_time_ms = int(datetime.fromisoformat(close_time_str).timestamp() * 1000)
            except (ValueError, TypeError):
                continue
            if close_time_ms > evaluation_ms:
                continue

            count += 1

        return count

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
    # Collection start anchor (immutable first-observation timestamp)
    # ------------------------------------------------------------------
    def write_collection_start_anchor(self, anchor: dict[str, Any]) -> dict[str, Any]:
        """Atomically write the immutable collection start anchor.

        The anchor is established only on the first successfully accepted
        observation. It is derived from the actual ingestion event timestamp,
        never from candle open/close or file mtime.

        Write-once semantics: if an anchor already exists and matches, the
        existing anchor is returned. If it exists with different content, a
        RuntimeError is raised (never overwrite).
        """
        self.root.mkdir(parents=True, exist_ok=True)
        existing = self.read_collection_start_anchor()
        if existing is not None:
            existing_hash = _sha256_json({k: v for k, v in existing.items() if k != "anchor_hash"})
            new_hash = _sha256_json({k: v for k, v in anchor.items() if k != "anchor_hash"})
            if existing_hash == new_hash:
                return existing
            raise RuntimeError(
                "collection start anchor already exists and is immutable; refusing overwrite "
                f"(existing_anchor_hash={existing.get('anchor_hash')}, attempted={new_hash})"
            )

        anchor_payload = dict(anchor)
        anchor_payload.setdefault("schema_version", ANCHOR_SCHEMA_VERSION)
        anchor_payload["anchor_hash"] = _sha256_json(
            {k: v for k, v in anchor_payload.items() if k != "anchor_hash"}
        )

        fd, tmp_name = tempfile.mkstemp(dir=str(self.root), prefix=".tmp_anchor_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(anchor_payload, f, indent=2, sort_keys=True, default=str)
                f.flush()
                os.fsync(f.fileno())
            # Atomic create: fail if file appeared concurrently.
            try:
                fd2 = os.open(self.anchor_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                # Another writer won the race; verify immutability.
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
                raced = self.read_collection_start_anchor()
                if raced is None:
                    raise RuntimeError(
                        "collection start anchor exists but could not be read"
                    ) from None
                raced_hash = _sha256_json({k: v for k, v in raced.items() if k != "anchor_hash"})
                new_hash = _sha256_json(
                    {k: v for k, v in anchor_payload.items() if k != "anchor_hash"}
                )
                if raced_hash != new_hash:
                    raise RuntimeError(
                        "collection start anchor race detected with different content; refusing overwrite"
                    ) from None
                return raced
            try:
                with os.fdopen(fd2, "wb") as out:
                    out.write(Path(tmp_name).read_bytes())
                    out.flush()
                    os.fsync(out.fileno())
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
            return anchor_payload
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise

    def read_collection_start_anchor(self) -> dict[str, Any] | None:
        """Read the immutable collection start anchor, or None if not started."""
        if not self.anchor_path.is_file():
            return None
        try:
            data = json.loads(self.anchor_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def verify_collection_start_anchor(self) -> dict[str, Any]:
        """Verify anchor integrity and consistency with accepted observations."""
        anchor = self.read_collection_start_anchor()
        if anchor is None:
            return {
                "status": "NOT_STARTED",
                "anchor_present": False,
                "anchor_hash_valid": None,
                "collection_started_at_utc": None,
                "reason": "no accepted observations; anchor not established",
            }

        expected_hash = _sha256_json({k: v for k, v in anchor.items() if k != "anchor_hash"})
        hash_valid = anchor.get("anchor_hash") == expected_hash
        if not hash_valid:
            return {
                "status": "INVALID",
                "anchor_present": True,
                "anchor_hash_valid": False,
                "collection_started_at_utc": anchor.get("collection_started_at_utc"),
                "reason": "anchor hash mismatch; anchor may be corrupted",
                "anchor": anchor,
            }

        accepted = self.read_unique_normalized_candles()
        if not accepted:
            return {
                "status": "INVALID",
                "anchor_present": True,
                "anchor_hash_valid": True,
                "collection_started_at_utc": anchor.get("collection_started_at_utc"),
                "reason": "anchor exists but zero accepted observations found",
                "anchor": anchor,
            }

        first_obs_id = anchor.get("first_observation_id")
        if first_obs_id and not any(
            c.get("event_id") == first_obs_id or c.get("source_observation_id") == first_obs_id
            for c in accepted
        ):
            # Also check raw observations for event id match
            raw = self.read_raw_observations()
            raw_match = any(r.get("event_id") == first_obs_id for r in raw)
            if not raw_match:
                return {
                    "status": "INVALID",
                    "anchor_present": True,
                    "anchor_hash_valid": True,
                    "collection_started_at_utc": anchor.get("collection_started_at_utc"),
                    "reason": "anchor first_observation_id not found in accepted observations",
                    "anchor": anchor,
                }

        return {
            "status": "VALID",
            "anchor_present": True,
            "anchor_hash_valid": True,
            "collection_started_at_utc": anchor.get("collection_started_at_utc"),
            "anchor": anchor,
        }

    def get_collection_started_at_utc(self) -> str | None:
        """Return immutable collection start timestamp, or None if not started."""
        verification = self.verify_collection_start_anchor()
        if verification.get("status") == "VALID":
            return verification.get("collection_started_at_utc")
        return None

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
    "ANCHOR_SCHEMA_VERSION",
    "DATASET_SCHEMA_VERSION",
    "DEFAULT_ANCHOR_FILE",
    "DEFAULT_LEDGER_FILE",
    "DEFAULT_MANIFEST_FILE",
    "DEFAULT_NORMALIZED_CANDLES_FILE",
    "DEFAULT_RAW_OBSERVATIONS_FILE",
    "LEDGER_SCHEMA_VERSION",
    "ProspectiveStorage",
    "candle_identity_key",
]
