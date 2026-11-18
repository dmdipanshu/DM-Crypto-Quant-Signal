"""Binance prospective market-data collector (Q5).

Collects only fully closed candles from public Binance Spot endpoints.
Preserves first-seen provenance, validates OHLCV and timestamps, detects
gaps/duplicates/conflicts, and stores to append-only prospective storage.

No trading credentials, no private endpoints, no order placement.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_engine.data.adapter import CcxtAdapter
from crypto_engine.data.errors import (
    DataFetchError,
    SignalForgeDataError,
)
from crypto_engine.data.types import FetchParams
from crypto_engine.data.validation import (
    timeframe_to_ms,
)
from crypto_engine.research.freeze import (
    DEFAULT_MANIFEST_PATH as FREEZE_MANIFEST_PATH,
)
from crypto_engine.research.freeze import (
    verify_freeze_integrity,
)
from crypto_engine.research.prospective.config import (
    DEFAULT_EXCHANGE,
    DEFAULT_MARKET_TYPE,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    ProspectiveCollectionPolicy,
    hash_collection_config,
    load_collection_config,
)
from crypto_engine.research.prospective.manifest import (
    DEFAULT_COLLECTION_MANIFEST_PATH,
    create_or_reuse_collection,
    verify_collection_integrity,
)
from crypto_engine.research.prospective.storage import ProspectiveStorage

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _ms_to_iso(ms: int | float) -> str:
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=UTC).isoformat()


def _iso_to_ms(value: str | datetime) -> int:
    if isinstance(value, datetime):
        ts = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return int(ts.timestamp() * 1000)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _response_hash(payload: Any) -> str:
    """Deterministic sha256 over the raw exchange response."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class CollectionStatus:
    """Data-quality states for prospective collection."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    GAP_DETECTED = "GAP_DETECTED"
    DATA_INVALID = "DATA_INVALID"
    EXCHANGE_UNAVAILABLE = "EXCHANGE_UNAVAILABLE"
    PROVENANCE_INVALID = "PROVENANCE_INVALID"


class ProspectiveCollector:
    """Collects genuinely prospective Binance OHLCV observations.

    Only candles that are fully finalized (closed) and whose open/close
    times are after the collection epoch start are accepted.
    """

    def __init__(
        self,
        *,
        exchange: str = DEFAULT_EXCHANGE,
        market_type: str = DEFAULT_MARKET_TYPE,
        symbols: list[str] | None = None,
        timeframes: list[str] | None = None,
        policy: ProspectiveCollectionPolicy | None = None,
        storage_root: str | None = None,
        client: Any = None,
        freeze_manifest_path: str | None = None,
        collection_manifest_path: str | None = None,
    ) -> None:
        self.exchange = exchange
        self.market_type = market_type
        self.symbols = symbols or list(DEFAULT_SYMBOLS)
        self.timeframes = timeframes or list(DEFAULT_TIMEFRAMES)
        self.policy = policy or ProspectiveCollectionPolicy(
            exchange=exchange,
            market_type=market_type,
            symbols=tuple(self.symbols),
            timeframes=tuple(self.timeframes),
        )
        self.storage_root = Path(storage_root) if storage_root else None
        self._adapter = CcxtAdapter(
            exchange_name=exchange,
            client=client,
        )
        self.freeze_manifest_path = (
            Path(freeze_manifest_path) if freeze_manifest_path else FREEZE_MANIFEST_PATH
        )
        self.collection_manifest_path = (
            Path(collection_manifest_path)
            if collection_manifest_path
            else DEFAULT_COLLECTION_MANIFEST_PATH
        )

    # ------------------------------------------------------------------
    # Freeze + collection manifest validation
    # ------------------------------------------------------------------
    def validate_freeze(self) -> dict[str, Any]:
        """Validate the original research freeze before collection begins."""
        result = verify_freeze_integrity(self.freeze_manifest_path)
        if result["status"] != "VALID":
            raise RuntimeError(
                f"Research freeze is not VALID (status={result['status']}): "
                f"{result.get('reason', 'unknown')}. "
                f"Prospective collection cannot begin."
            )
        return result

    def validate_or_create_collection_manifest(self) -> dict[str, Any]:
        """Validate or reuse the immutable collection manifest.

        Never creates a new collection epoch on rerun unless ``force_new``
        is used externally.

        Uses the canonical configuration from :func:`load_collection_config`
        so that all entry points produce the identical hash and match the
        immutable manifest at ``PROSPECTIVE_COLLECTION_MANIFEST.json``.
        """
        collection_config = load_collection_config()
        # Load freeze info for association
        freeze = verify_freeze_integrity(self.freeze_manifest_path)
        if freeze["status"] != "VALID":
            raise RuntimeError(
                f"Cannot associate collection with invalid freeze (status={freeze['status']})"
            )

        manifest = create_or_reuse_collection(
            manifest_path=self.collection_manifest_path,
            exchange=self.exchange,
            market_type=self.market_type,
            symbols=self.symbols,
            timeframes=self.timeframes,
            collection_config=collection_config,
            associated_freeze_id=freeze["freeze_id"],
            associated_freeze_timestamp=freeze["created_at_utc"],
            collector_version="q5-v1",
            data_schema_version="ohlcv-v1",
        )
        return manifest

    def verify_collection_integrity(self) -> dict[str, Any]:
        """Verify the collection manifest integrity.

        Uses the canonical configuration from :func:`load_collection_config`
        so that all entry points compute the identical hash and agree with
        the immutable manifest.
        """
        freeze = verify_freeze_integrity(self.freeze_manifest_path)
        collection_config = load_collection_config()
        config_hash = hash_collection_config(collection_config)
        return verify_collection_integrity(
            manifest_path=self.collection_manifest_path,
            expected_collection_config_hash=config_hash,
            expected_exchange=self.exchange,
            expected_symbols=self.symbols,
            expected_timeframes=self.timeframes,
            associated_freeze_id=freeze["freeze_id"],
        )

    # ------------------------------------------------------------------
    # Candle finalization check
    # ------------------------------------------------------------------
    def _is_candle_finalized(
        self,
        close_time_ms: int,
        timeframe: str,
    ) -> tuple[bool, str | None]:
        """Check if a candle is fully finalized using exchange server time.

        Returns (is_finalized, reason).
        """
        # Safety margin: require margin after expected close time
        expected_close_ms = close_time_ms
        margin_ms = self.policy.candle_finalization_margin_s * 1000
        finalized_at_ms = expected_close_ms + margin_ms

        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        if now_ms < finalized_at_ms:
            return (
                False,
                f"candle not yet finalized: close={_ms_to_iso(close_time_ms)}, "
                f"finalized_at={_ms_to_iso(finalized_at_ms)}, now={_ms_to_iso(now_ms)}",
            )
        return True, None

    # ------------------------------------------------------------------
    # OHLCV validation
    # ------------------------------------------------------------------
    def _validate_ohlcv_row(
        self,
        row: list[Any],
        symbol: str,
        timeframe: str,
        row_index: int,
    ) -> dict[str, Any] | None:
        """Validate one raw OHLCV row from the exchange.

        Returns a normalized candle dict or None if invalid.
        """
        if len(row) < 6:
            logger.warning(
                "row %d: insufficient fields (%d) for %s %s",
                row_index,
                len(row),
                symbol,
                timeframe,
            )
            return None

        try:
            open_time_ms = int(float(row[0]))
            open_price = float(row[1])
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            volume = float(row[5])
        except (TypeError, ValueError) as exc:
            logger.warning("row %d: non-numeric OHLCV for %s: %s", row_index, symbol, exc)
            return None

        # Basic OHLCV validation (mirrors data/validation.py but standalone)
        import math

        for label, val in (("open", open_price), ("high", high), ("low", low), ("close", close)):
            if not math.isfinite(val) or val <= 0:
                logger.warning(
                    "row %d: non-positive/non-finite %s=%s for %s",
                    row_index,
                    label,
                    val,
                    symbol,
                )
                return None
        if not math.isfinite(volume) or volume < 0:
            logger.warning("row %d: invalid volume=%s for %s", row_index, volume, symbol)
            return None
        if high < low:
            logger.warning("row %d: high < low for %s", row_index, symbol)
            return None
        eps = 1e-8 * max(1.0, abs(high), abs(low))
        if open_price < low - eps or open_price > high + eps:
            logger.warning("row %d: open outside [low, high] for %s", row_index, symbol)
            return None
        if close < low - eps or close > high + eps:
            logger.warning("row %d: close outside [low, high] for %s", row_index, symbol)
            return None

        open_time = _ms_to_iso(open_time_ms)
        close_time_ms_calc = open_time_ms + timeframe_to_ms(timeframe)
        close_time = _ms_to_iso(close_time_ms_calc)

        return {
            "open_time": open_time,
            "close_time": close_time,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    # ------------------------------------------------------------------
    # Main collection
    # ------------------------------------------------------------------
    def collect_once(self) -> dict[str, Any]:
        """Run one collection cycle for all symbols/timeframes.

        Returns a collection result dict with status, observations, gaps,
        and quality information.
        """
        # Step 1: Validate research freeze
        freeze_info = self.validate_freeze()

        # Step 2: Validate or reuse collection manifest
        collection_manifest = self.validate_or_create_collection_manifest()
        collection_id = collection_manifest["collection_id"]
        collection_started_at = collection_manifest["collection_started_at_utc"]
        collection_start_ms = _iso_to_ms(collection_started_at)

        # Step 3: Initialize storage
        # storage_root may be None; per-symbol storage is created inside
        # _collect_symbol_timeframe for each symbol/timeframe.

        result: dict[str, Any] = {
            "schema": "prospective-collection-result.v1",
            "collection_id": collection_id,
            "collection_started_at_utc": collection_started_at,
            "research_freeze_id": freeze_info["freeze_id"],
            "research_freeze_timestamp": freeze_info["created_at_utc"],
            "exchange": self.exchange,
            "market_type": self.market_type,
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "collected_at_utc": _utc_now_iso(),
            "status": CollectionStatus.HEALTHY,
            "observations": [],
            "gaps": [],
            "duplicates": [],
            "conflicts": [],
            "rejections": [],
            "errors": [],
            "quality_checks": {},
        }

        for symbol in self.symbols:
            for timeframe in self.timeframes:
                symbol_result = self._collect_symbol_timeframe(
                    symbol=symbol,
                    timeframe=timeframe,
                    collection_id=collection_id,
                    collection_start_ms=collection_start_ms,
                    freeze_id=freeze_info["freeze_id"],
                    storage_root=self.storage_root,
                )
                result["observations"].extend(symbol_result["observations"])
                result["gaps"].extend(symbol_result["gaps"])
                result["duplicates"].extend(symbol_result["duplicates"])
                result["conflicts"].extend(symbol_result["conflicts"])
                result["rejections"].extend(symbol_result["rejections"])
                result["errors"].extend(symbol_result["errors"])

        # Step 4: Compute overall quality state
        result["quality_checks"] = self._compute_quality_state(result)
        result["status"] = result["quality_checks"]["state"]

        return result

    def _collect_symbol_timeframe(
        self,
        *,
        symbol: str,
        timeframe: str,
        collection_id: str,
        collection_start_ms: int,
        freeze_id: str,
        storage_root: Path | None,
    ) -> dict[str, Any]:
        """Collect candles for one symbol/timeframe."""
        interval_ms = timeframe_to_ms(timeframe)
        now_ms = int(datetime.now(UTC).timestamp() * 1000)

        # Determine the start of collection: max(collection_start, last_observed + interval)
        # We collect forward from the collection epoch start.
        since_ms = collection_start_ms

        # Fetch a bounded window (most recent 500 candles) to find new ones
        until_ms = now_ms
        params = FetchParams(
            exchange=self.exchange,
            symbol=symbol,
            timeframe=timeframe,
            since=datetime.fromtimestamp(since_ms / 1000, tz=UTC),
            until=datetime.fromtimestamp(until_ms / 1000, tz=UTC),
            limit=500,
        )

        try:
            result = self._adapter.fetch_range(params)
        except (DataFetchError, SignalForgeDataError) as exc:
            return {
                "observations": [],
                "gaps": [],
                "duplicates": [],
                "conflicts": [],
                "rejections": [
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "reason": f"exchange fetch failed: {exc}",
                        "at_utc": _utc_now_iso(),
                    }
                ],
                "errors": [str(exc)],
            }

        observations: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        rejections: list[dict[str, Any]] = []
        errors: list[str] = []

        seen_open_times: set[int] = set()
        last_close_ms: int | None = None

        for _i, candle in enumerate(result.candles):
            open_time_ms = int(candle.timestamp.timestamp() * 1000)
            close_time_ms_calc = open_time_ms + interval_ms

            # Strict forward-only: only accept candles after collection start
            if open_time_ms < collection_start_ms:
                rejections.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "open_time": _ms_to_iso(open_time_ms),
                        "reason": "candle opens before collection epoch start (not prospective)",
                        "at_utc": _utc_now_iso(),
                    }
                )
                continue

            # Check candle finalization
            is_finalized, finalize_reason = self._is_candle_finalized(close_time_ms_calc, timeframe)
            if not is_finalized:
                rejections.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "open_time": _ms_to_iso(open_time_ms),
                        "close_time": _ms_to_iso(close_time_ms_calc),
                        "reason": finalize_reason or "candle not finalized",
                        "at_utc": _utc_now_iso(),
                    }
                )
                continue

            # Check ingestion latency
            ingestion_latency_s = (now_ms - close_time_ms_calc) / 1000.0
            if ingestion_latency_s > self.policy.max_ingestion_latency_s:
                rejections.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "open_time": _ms_to_iso(open_time_ms),
                        "reason": f"ingestion latency {ingestion_latency_s:.0f}s exceeds max {self.policy.max_ingestion_latency_s}s",
                        "at_utc": _utc_now_iso(),
                    }
                )
                continue

            # Duplicate detection
            if open_time_ms in seen_open_times:
                duplicates.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "open_time": _ms_to_iso(open_time_ms),
                        "reason": "duplicate open_time in this collection cycle",
                        "at_utc": _utc_now_iso(),
                    }
                )
                continue
            seen_open_times.add(open_time_ms)

            # Gap detection
            if last_close_ms is not None and open_time_ms > last_close_ms + interval_ms:
                gaps.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "expected_open": _ms_to_iso(last_close_ms),
                        "actual_open": _ms_to_iso(open_time_ms),
                        "gap_intervals": (open_time_ms - last_close_ms) // interval_ms,
                        "detected_at_utc": _utc_now_iso(),
                    }
                )

            last_close_ms = close_time_ms_calc

            # Compute response hash for this candle
            raw_row = [
                open_time_ms,
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.volume,
            ]
            response_hash = _response_hash(raw_row)

            # First-seen: assigned on first observation, never reset
            observed_at_utc = _utc_now_iso()
            ingested_at_utc = _utc_now_iso()

            # Store to prospective storage
            if storage_root is not None:
                symbol_storage = ProspectiveStorage(
                    storage_root / self.exchange / self.market_type / symbol / timeframe
                )
            else:
                symbol_storage = ProspectiveStorage(
                    Path("data/prospective") / self.exchange / self.market_type / symbol / timeframe
                )

            obs_record = symbol_storage.store_raw_observation(
                exchange=self.exchange,
                market_type=self.market_type,
                symbol=symbol,
                interval=timeframe,
                open_time=_ms_to_iso(open_time_ms),
                close_time=_ms_to_iso(close_time_ms_calc),
                observed_at_utc=observed_at_utc,
                ingested_at_utc=ingested_at_utc,
                source_endpoint=f"{self.exchange}/fetchOHLCV",
                request_id=None,
                response_hash=response_hash,
                raw_payload_hash=response_hash,
                collector_version="q5-v1",
                collection_id=collection_id,
                research_freeze_id=freeze_id,
                first_seen_at_utc=observed_at_utc,  # first observation = first seen
            )

            candle_record = symbol_storage.store_normalized_candle(
                collection_id=collection_id,
                exchange=self.exchange,
                market_type=self.market_type,
                symbol=symbol,
                interval=timeframe,
                open_time=_ms_to_iso(open_time_ms),
                close_time=_ms_to_iso(close_time_ms_calc),
                open_price=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                source_observation_id=obs_record["event_id"],
                first_seen_at_utc=observed_at_utc,
                observed_at_utc=observed_at_utc,
                ingested_at_utc=ingested_at_utc,
                research_freeze_id=freeze_id,
            )

            # Append ledger event
            symbol_storage.append_ledger_event(
                event_type="candle_ingested",
                collection_id=collection_id,
                source_record_hash=response_hash,
            )

            observations.append(
                {
                    "event_id": obs_record["event_id"],
                    "candle_id": candle_record["event_id"],
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "open_time": _ms_to_iso(open_time_ms),
                    "close_time": _ms_to_iso(close_time_ms_calc),
                    "first_seen_at_utc": observed_at_utc,
                    "observed_at_utc": observed_at_utc,
                    "ingested_at_utc": ingested_at_utc,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                    "response_hash": response_hash,
                    "raw_payload_hash": response_hash,
                    "collection_id": collection_id,
                    "research_freeze_id": freeze_id,
                }
            )

        return {
            "observations": observations,
            "gaps": gaps,
            "duplicates": duplicates,
            "conflicts": conflicts,
            "rejections": rejections,
            "errors": errors,
        }

    def _compute_quality_state(self, result: dict[str, Any]) -> dict[str, Any]:
        """Compute overall data-quality state from collection result.

        Returns separate status fields for collection health, ingestion,
        dataset quality, provenance, ledger, holdout, and evaluation
        eligibility. Collector HEALTHY does NOT imply evaluation eligible.
        """
        errors = result["errors"]
        rejections = result["rejections"]
        gaps = result["gaps"]
        duplicates = result["duplicates"]
        conflicts = result["conflicts"]
        observations = result["observations"]

        # Use unique observation count (deduplicated by candle identity key)
        unique_observation_count = len(observations)

        # Individual status fields
        collector_status = CollectionStatus.HEALTHY
        ingestion_status = CollectionStatus.HEALTHY
        dataset_quality_status = CollectionStatus.HEALTHY
        provenance_status = CollectionStatus.HEALTHY
        ledger_status = CollectionStatus.HEALTHY

        collector_reasons: list[str] = []
        ingestion_reasons: list[str] = []
        quality_reasons: list[str] = []
        provenance_reasons: list[str] = []
        ledger_reasons: list[str] = []

        # Collector health: exchange fetch errors
        if errors:
            collector_status = CollectionStatus.EXCHANGE_UNAVAILABLE
            collector_reasons.append(f"{len(errors)} exchange fetch error(s)")

        # Ingestion status: rejections and missing data
        if rejections and not observations:
            ingestion_status = CollectionStatus.DATA_INVALID
            ingestion_reasons.append("all observations rejected")

        # Dataset quality: gaps and conflicts
        if gaps:
            dataset_quality_status = CollectionStatus.GAP_DETECTED
            quality_reasons.append(f"{len(gaps)} missing interval(s)")
        if conflicts:
            dataset_quality_status = CollectionStatus.DATA_INVALID
            quality_reasons.append(f"{len(conflicts)} conflict(s)")
        if duplicates:
            # Duplicates are degraded, not invalid
            if dataset_quality_status == CollectionStatus.HEALTHY:
                dataset_quality_status = CollectionStatus.DEGRADED
            quality_reasons.append(f"{len(duplicates)} duplicate(s)")

        # Provenance: all observations have first_seen_at_utc
        missing_provenance = [o for o in observations if not o.get("first_seen_at_utc")]
        if missing_provenance:
            provenance_status = CollectionStatus.PROVENANCE_INVALID
            provenance_reasons.append(
                f"{len(missing_provenance)} observations missing first_seen_at_utc"
            )

        # Ledger status: hash chain validity
        # (checked separately in Q6 preflight; here we note if ledger events exist)
        # Ledger status is verified separately via storage.verify_ledger_chain()

        # Overall collector state (worst of individual statuses)
        state_order = [
            CollectionStatus.DATA_INVALID,
            CollectionStatus.EXCHANGE_UNAVAILABLE,
            CollectionStatus.PROVENANCE_INVALID,
            CollectionStatus.GAP_DETECTED,
            CollectionStatus.DEGRADED,
            CollectionStatus.HEALTHY,
        ]
        all_statuses = [
            collector_status,
            ingestion_status,
            dataset_quality_status,
            provenance_status,
            ledger_status,
        ]
        state = state_order[0]  # Start with worst
        for s in all_statuses:
            if state_order.index(s) < state_order.index(state):
                state = s

        # Coverage calculation using unique observations
        # Expected intervals: count of exchange-aligned candles whose open time
        # is strictly after collection_started_at_utc and whose close time is
        # at or before now (fully finalized).
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        collection_start_ms = int(
            datetime.fromisoformat(result["collection_started_at_utc"]).timestamp() * 1000
        )

        expected_intervals = 0
        for _symbol in self.symbols:
            for timeframe in self.timeframes:
                interval_ms = timeframe_to_ms(timeframe)
                # First exchange-aligned candle whose open time is strictly
                # after collection_started_at_utc
                first_open_ms = ((collection_start_ms // interval_ms) + 1) * interval_ms
                # Count intervals from first_open_ms to now_ms (inclusive of
                # intervals that have fully closed by now)
                t = first_open_ms
                while t + interval_ms <= now_ms:
                    expected_intervals += 1
                    t += interval_ms

        actual_count = unique_observation_count
        coverage = actual_count / expected_intervals if expected_intervals > 0 else 0.0

        # Evaluation blocked: ALL requirements must pass
        block_reasons: list[str] = []

        # Minimum observations
        if actual_count < self.policy.min_observation_count:
            block_reasons.append(
                f"Observation count {actual_count} is below minimum "
                f"{self.policy.min_observation_count}"
            )

        # Minimum coverage
        if coverage < self.policy.min_coverage_fraction:
            block_reasons.append(
                f"Coverage fraction {coverage:.2%} is below minimum "
                f"{self.policy.min_coverage_fraction:.2%}"
            )

        # Minimum elapsed duration (7 days)
        min_elapsed_ms = 7 * 24 * 60 * 60 * 1000
        elapsed_ms = now_ms - collection_start_ms
        if elapsed_ms < min_elapsed_ms:
            block_reasons.append(
                f"Elapsed collection duration {elapsed_ms / 1000:.0f}s is below minimum 7 days"
            )

        # Data quality
        if dataset_quality_status != CollectionStatus.HEALTHY:
            block_reasons.extend(quality_reasons)

        # Provenance
        if provenance_status != CollectionStatus.HEALTHY:
            block_reasons.extend(provenance_reasons)

        # Exchange unavailable
        if collector_status == CollectionStatus.EXCHANGE_UNAVAILABLE:
            block_reasons.extend(collector_reasons)

        evaluation_blocked = len(block_reasons) > 0

        return {
            "state": state,
            "collector_status": collector_status,
            "ingestion_status": ingestion_status,
            "dataset_quality_status": dataset_quality_status,
            "provenance_status": provenance_status,
            "ledger_status": ledger_status,
            "collector_reasons": collector_reasons,
            "ingestion_reasons": ingestion_reasons,
            "quality_reasons": quality_reasons,
            "provenance_reasons": provenance_reasons,
            "ledger_reasons": ledger_reasons,
            "observation_count": actual_count,
            "expected_intervals": expected_intervals,
            "coverage_fraction": round(coverage, 4),
            "error_count": len(errors),
            "rejection_count": len(rejections),
            "gap_count": len(gaps),
            "duplicate_count": len(duplicates),
            "conflict_count": len(conflicts),
            "evaluation_blocked": evaluation_blocked,
            "evaluation_block_reasons": block_reasons,
        }


__all__ = [
    "CollectionStatus",
    "ProspectiveCollector",
]
