"""Append-only storage for historical accelerated validation data.

Completely separate from the prospective storage at
data/prospective/binance/spot/BTC-USDT/1h/.
This module must never write to or read from the prospective dataset.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_engine.data.validation import timeframe_to_ms

DEFAULT_HISTORICAL_STORAGE_ROOT = Path("data/historical_accelerated")
HISTORICAL_SCHEMA_VERSION = "historical_accelerated_v1"
LEDGER_SCHEMA_VERSION = "historical_ledger_v1"
DATASET_SCHEMA_VERSION = "historical_dataset_v1"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_json(value: Any) -> str:
    """Deterministic sha256 over a JSON-serialisable value."""
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_jsonl(path: Path, lines: list[str]) -> None:
    """Write JSON Lines atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkdtemp(dir=str(path.parent), prefix=".tmp_", suffix=".jsonl")
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


class HistoricalStorage:
    """Append-only storage for historical accelerated validation observations.

    Path: data/historical_accelerated/binance/spot/BTC-USDT/1h/
    Completely separate from data/prospective/.
    """

    def __init__(self, root: Path | str = DEFAULT_HISTORICAL_STORAGE_ROOT) -> None:
        self.root = Path(root)
        self.raw_path = self.root / "raw_observations.jsonl"
        self.normalized_path = self.root / "normalized_candles.jsonl"
        self.ledger_path = self.root / "ingestion_ledger.jsonl"
        self.manifest_path = self.root / "dataset_manifest.json"

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
        dataset_id: str,
    ) -> dict[str, Any]:
        """Store one raw exchange observation (append-only)."""
        observation_id = (
            f"obs-{_sha256_json(raw_payload_hash + open_time + str(ingested_at_utc))[:16]}"
        )

        record = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "event_id": observation_id,
            "event_type": "raw_observation",
            "dataset_id": dataset_id,
            "exchange": exchange,
            "market_type": market_type,
            "symbol": symbol,
            "interval": interval,
            "open_time": open_time,
            "close_time": close_time,
            "observed_at_utc": observed_at_utc,
            "ingested_at_utc": ingested_at_utc,
            "source_endpoint": source_endpoint,
            "request_id": request_id,
            "response_hash": response_hash,
            "raw_payload_hash": raw_payload_hash,
            "collector_version": collector_version,
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
        dataset_id: str,
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
        observed_at_utc: str,
        ingested_at_utc: str,
    ) -> dict[str, Any]:
        """Store one normalized candle (append-only)."""
        candle_id = f"candle-{_sha256_json(open_time + symbol + str(close))[:16]}"

        record = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "event_id": candle_id,
            "event_type": "normalized_candle",
            "dataset_id": dataset_id,
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
            "observed_at_utc": observed_at_utc,
            "ingested_at_utc": ingested_at_utc,
            "recorded_at_utc": _utc_now_iso(),
        }
        _append_jsonl(self.normalized_path, json.dumps(record, sort_keys=True, default=str))
        return record

    # ------------------------------------------------------------------
    # Ingestion ledger (tamper-evident hash chain)
    # ------------------------------------------------------------------
    def _last_ledger_hash(self) -> str | None:
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
        dataset_id: str,
        source_record_hash: str,
        previous_event_hash: str | None = None,
    ) -> dict[str, Any]:
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
            "dataset_id": dataset_id,
            "source_record_hash": source_record_hash,
            "previous_event_hash": prev_hash,
            "recorded_at_utc": _utc_now_iso(),
        }
        content_for_hash = {k: v for k, v in content.items() if k != "current_event_hash"}
        current_hash = _sha256_json(content_for_hash)
        content["current_event_hash"] = current_hash

        _append_jsonl(self.ledger_path, json.dumps(content, sort_keys=True, default=str))
        return content

    def verify_ledger_chain(self) -> dict[str, Any]:
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
                content_for_hash = {k: v for k, v in entry.items() if k != "current_event_hash"}
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
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )

    def read_dataset_manifest(self) -> dict[str, Any] | None:
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
        candles = self.read_normalized_candles()
        if not candles:
            return None
        return _sha256_json(candles)

    def compute_raw_payload_hash(self) -> str | None:
        observations = self.read_raw_observations()
        if not observations:
            return None
        return _sha256_json(observations)

    def get_observation_summary(self) -> dict[str, Any]:
        raw_records = self.read_raw_observations()
        unique_candles = self.read_normalized_candles()
        manifest = self.read_dataset_manifest() or {}

        unique_keys: set[str] = set()
        for c in unique_candles:
            key = f"{c.get('exchange', '')}|{c.get('market_type', '')}|{c.get('symbol', '')}|{c.get('interval', '')}|{c.get('open_time', '')}"
            unique_keys.add(key)

        raw_key_counts: dict[str, int] = {}
        for r in raw_records:
            key = f"{r.get('exchange', '')}|{r.get('market_type', '')}|{r.get('symbol', '')}|{r.get('interval', '')}|{r.get('open_time', '')}"
            raw_key_counts[key] = raw_key_counts.get(key, 0) + 1

        duplicate_count = sum(
            count - 1 for key, count in raw_key_counts.items() if key in unique_keys
        )

        candle_by_key: dict[str, list[dict[str, Any]]] = {}
        for c in self.read_normalized_candles():
            key = f"{c.get('exchange', '')}|{c.get('market_type', '')}|{c.get('symbol', '')}|{c.get('interval', '')}|{c.get('open_time', '')}"
            candle_by_key.setdefault(key, []).append(c)

        conflict_count = 0
        for _key, candles in candle_by_key.items():
            if len(candles) > 1:
                first = candles[0]
                for other in candles[1:]:
                    if (
                        abs(float(first.get("open", 0)) - float(other.get("open", 0))) > 1e-9
                        or abs(float(first.get("high", 0)) - float(other.get("high", 0))) > 1e-9
                        or abs(float(first.get("low", 0)) - float(other.get("low", 0))) > 1e-9
                        or abs(float(first.get("close", 0)) - float(other.get("close", 0))) > 1e-9
                        or abs(float(first.get("volume", 0)) - float(other.get("volume", 0))) > 1e-9
                    ):
                        conflict_count += 1
                        break

        expected_intervals = int(manifest.get("expected_intervals") or 0)
        if (
            expected_intervals <= 0
            and manifest.get("requested_start_utc")
            and manifest.get("requested_end_utc")
        ):
            try:
                start = datetime.fromisoformat(
                    str(manifest["requested_start_utc"]).replace("Z", "+00:00")
                )
                end = datetime.fromisoformat(
                    str(manifest["requested_end_utc"]).replace("Z", "+00:00")
                )
                interval_ms = timeframe_to_ms(str(manifest.get("timeframe", "1h")))
                expected_intervals = max(
                    0,
                    int(
                        (int(end.timestamp() * 1000) - int(start.timestamp() * 1000)) // interval_ms
                    ),
                )
            except Exception:
                expected_intervals = 0
        accepted = len(unique_candles)
        coverage_fraction = (accepted / expected_intervals) if expected_intervals > 0 else 0.0

        return {
            "raw_ingestion_events": len(raw_records),
            "accepted_unique_observations": accepted,
            "duplicate_ingestion_attempts": duplicate_count,
            "conflicting_observations": conflict_count,
            "unique_keys": len(unique_keys),
            "expected_intervals": expected_intervals,
            "coverage_fraction": coverage_fraction,
            "missing_interval_count": max(0, expected_intervals - accepted),
        }


__all__ = [
    "DATASET_SCHEMA_VERSION",
    "DEFAULT_HISTORICAL_STORAGE_ROOT",
    "LEDGER_SCHEMA_VERSION",
    "HistoricalStorage",
]
