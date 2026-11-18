"""12-month BTC/USDT 1h historical dataset expansion for Q6.

Builds a 12-month BTC/USDT 1h dataset from the Binance public API, stored
separately from the existing 1-week historical dataset at
data/historical_accelerated/. This module is read-only: no retraining,
refitting, calibration, tuning, or trading.

The 12-month data lives at data/historical_accelerated_12m/ (a separate
storage root) and is never written to or read from data/historical_accelerated/.

Includes dedup, hash-chained ingestion ledger, missing-interval reporting,
dataset hashing, and provenance-aware candidate filtering based on the
temporal provenance audit (TEMPORAL_PROVENANCE_VERIFIED candidates may be
evaluated from train_end; unverified candidates get no valid eval window).
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.research.q6.historical_adapter import (  # noqa: E402
    HistoricalDataAdapter,
)
from crypto_engine.research.q6.historical_manifest import (  # noqa: E402
    build_historical_manifest,
)
from crypto_engine.research.q6.historical_storage import (  # noqa: E402
    HistoricalStorage,
)
from crypto_engine.research.q6.temporal_provenance_audit import (  # noqa: E402
    TEMPORAL_PROVENANCE_VERIFIED,
)

DEFAULT_12M_STORAGE_ROOT = Path("data/historical_accelerated_12m")
MONTHS_BACK = 12
SCHEMA_VERSION = "dataset_expansion.v1"
DATASET_TYPE = "HISTORICAL_ACCELERATED_VALIDATION_12M"

FETCH_SCHEMA_VERSION = "dataset_expansion_fetch.v1"


def _utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _sha256_json(value: Any) -> str:
    """Deterministic sha256 over a JSON-serialisable value."""
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _subtract_months(dt: datetime, months: int) -> datetime:
    """Subtract calendar months from an aware datetime, clamping day-of-month."""
    total = dt.year * 12 + (dt.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    if month == 12:
        first_of_next = datetime(year + 1, 1, 1, tzinfo=UTC)
    else:
        first_of_next = datetime(year, month + 1, 1, tzinfo=UTC)
    last_day = (first_of_next - timedelta(days=1)).day
    return dt.replace(year=year, month=month, day=min(dt.day, last_day))


@dataclass(frozen=True, slots=True)
class DatasetExpansionResult:
    """Read-only result of a 12-month historical dataset expansion."""

    dataset_id: str
    storage_root: str
    start_utc: str
    end_utc: str
    months_requested: int
    observation_count: int
    expected_intervals: int
    coverage_fraction: float
    duplicate_count: int
    conflicting_count: int
    missing_interval_count: int
    dataset_hash: str
    actual_first_candle_utc: str | None
    actual_last_candle_utc: str | None
    retrieval_timestamp: str
    manifest_written: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    candidate_valid_windows: dict[str, dict[str, str | None]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "dataset_type": DATASET_TYPE,
            "dataset_id": self.dataset_id,
            "storage_root": self.storage_root,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "months_requested": self.months_requested,
            "observation_count": self.observation_count,
            "expected_intervals": self.expected_intervals,
            "coverage_fraction": self.coverage_fraction,
            "duplicate_count": self.duplicate_count,
            "conflicting_count": self.conflicting_count,
            "missing_interval_count": self.missing_interval_count,
            "dataset_hash": self.dataset_hash,
            "actual_first_candle_utc": self.actual_first_candle_utc,
            "actual_last_candle_utc": self.actual_last_candle_utc,
            "retrieval_timestamp": self.retrieval_timestamp,
            "manifest_written": self.manifest_written,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "candidate_valid_windows": {
                cid: dict(windows) for cid, windows in self.candidate_valid_windows.items()
            },
        }


class TwelveMonthDatasetExpander:
    """Fetch, store, and manifest a 12-month BTC/USDT 1h dataset."""

    def __init__(
        self,
        *,
        exchange: str = "binance",
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        storage_root: Path | str | None = None,
        client: Any = None,
    ) -> None:
        self.exchange = exchange
        self.symbol = symbol
        self.timeframe = timeframe
        self.storage_root = Path(storage_root or DEFAULT_12M_STORAGE_ROOT)
        self.months_back = MONTHS_BACK
        self.adapter = HistoricalDataAdapter(
            exchange_name=exchange, symbol=symbol, timeframe=timeframe, client=client
        )
        self.storage = HistoricalStorage(self.storage_root)

    # ------------------------------------------------------------------
    # Fetch + store
    # ------------------------------------------------------------------
    def fetch_12_months(self) -> dict[str, Any]:
        """Fetch 12 months of candles ending at present and store them.

        Returns a fetch result dict mirroring HistoricalDataAdapter output,
        plus storage_root and reuse flag. Existing data is reused when a
        manifest already exists at the storage root.
        """
        end_utc = datetime.now(UTC)
        start_utc = _subtract_months(end_utc, self.months_back)

        existing = self.storage.read_dataset_manifest()
        if existing is not None:
            return {
                "schema_version": FETCH_SCHEMA_VERSION,
                "exchange": self.exchange,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "start_utc": start_utc.isoformat(),
                "end_utc": end_utc.isoformat(),
                "actual_since": existing.get("actual_first_candle_utc"),
                "actual_until": existing.get("actual_last_candle_utc"),
                "observation_count": int(existing.get("observation_count") or 0),
                "expected_intervals": int(existing.get("expected_intervals") or 0),
                "coverage_fraction": float(existing.get("coverage_fraction") or 0.0),
                "duplicate_count": int(existing.get("duplicate_count") or 0),
                "conflicting_count": int(existing.get("conflicting_count") or 0),
                "missing_interval_count": int(existing.get("missing_interval_count") or 0),
                "raw_candles": [],
                "dataset_id": existing.get("dataset_id", ""),
                "dataset_hash": existing.get("dataset_hash", ""),
                "warnings": [],
                "source": self.exchange,
                "storage_root": str(self.storage_root),
                "reused": True,
            }

        fetch_result = self.adapter.fetch_historical(start_utc, end_utc)
        candles = fetch_result["raw_candles"]

        dataset_id = f"hist-accel-12m-{uuid.uuid4().hex[:12]}"

        # Store raw observations (append-only, provenance-preserving).
        for c in candles:
            payload = [
                c.timestamp.isoformat(),
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            ]
            row_hash = _sha256_json(payload)
            self.storage.store_raw_observation(
                exchange=self.exchange,
                market_type="spot",
                symbol=self.symbol,
                interval=self.timeframe,
                open_time=c.timestamp.isoformat(),
                close_time=(c.timestamp + timedelta(hours=1)).isoformat(),
                observed_at_utc=_utc_now_iso(),
                ingested_at_utc=_utc_now_iso(),
                source_endpoint=f"{self.exchange}/fetchOHLCV",
                request_id=None,
                response_hash=row_hash,
                raw_payload_hash=row_hash,
                collector_version="q6-dataset-expansion-v1",
                dataset_id=dataset_id,
            )

        # Store normalized candles and append hash-chained ledger events.
        previous_hash: str | None = None
        for c in candles:
            payload = [
                c.timestamp.isoformat(),
                c.open,
                c.high,
                c.low,
                c.close,
                c.volume,
            ]
            source_record_hash = _sha256_json(payload)
            self.storage.store_normalized_candle(
                dataset_id=dataset_id,
                exchange=self.exchange,
                market_type="spot",
                symbol=self.symbol,
                interval=self.timeframe,
                open_time=c.timestamp.isoformat(),
                close_time=(c.timestamp + timedelta(hours=1)).isoformat(),
                open_price=c.open,
                high=c.high,
                low=c.low,
                close=c.close,
                volume=c.volume,
                source_observation_id=f"obs-{c.timestamp.isoformat()}",
                observed_at_utc=_utc_now_iso(),
                ingested_at_utc=_utc_now_iso(),
            )
            self.storage.append_ledger_event(
                event_type="candle_ingested",
                dataset_id=dataset_id,
                source_record_hash=source_record_hash,
                previous_event_hash=previous_hash,
            )
            previous_hash = self.storage._last_ledger_hash()

        # Build and write the storage-local manifest (separate root).
        manifest = build_historical_manifest(
            dataset_id=dataset_id,
            dataset_started_at_utc=start_utc.isoformat(),
            provider=self.exchange,
            exchange=self.exchange,
            market_type="spot",
            symbol=self.symbol,
            timeframe=self.timeframe,
            requested_start_utc=start_utc.isoformat(),
            requested_end_utc=end_utc.isoformat(),
            actual_first_candle_utc=(candles[0].timestamp.isoformat() if candles else None),
            actual_last_candle_utc=(candles[-1].timestamp.isoformat() if candles else None),
            observation_count=len(candles),
            missing_interval_count=len(fetch_result.get("missing_intervals", [])),
            duplicate_count=fetch_result.get("duplicate_count", 0),
            conflicting_count=fetch_result.get("conflicting_count", 0),
            dataset_hash=fetch_result["dataset_hash"],
            retrieval_timestamp=_utc_now_iso(),
            source_metadata={
                "exchange": self.exchange,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "api_endpoint": "public/fetchOHLCV",
                "months_back": self.months_back,
                "schema_version": SCHEMA_VERSION,
            },
            normalization_version="v1",
        )
        manifest["schema_version"] = SCHEMA_VERSION
        manifest["dataset_type"] = DATASET_TYPE
        manifest["expected_intervals"] = int(fetch_result.get("expected_intervals", 0) or 0)
        manifest["coverage_fraction"] = float(fetch_result.get("coverage_fraction", 0.0) or 0.0)
        self.storage.write_dataset_manifest(manifest)

        return {
            **fetch_result,
            "dataset_id": dataset_id,
            "storage_root": str(self.storage_root),
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "reused": False,
            "manifest": manifest,
        }

    # ------------------------------------------------------------------
    # Provenance-aware candidate valid windows
    # ------------------------------------------------------------------
    def compute_candidate_valid_windows(
        self,
        *,
        provenance_report: Any,
    ) -> dict[str, dict[str, str | None]]:
        """Compute per-candidate valid evaluation windows.

        For TEMPORAL_PROVENANCE_VERIFIED candidates the earliest valid eval
        timestamp is train_end. For unverified candidates there is no valid
        eval window (None).
        """
        windows: dict[str, dict[str, str | None]] = {}
        if provenance_report is None:
            return windows

        candidate_results: dict[str, Any] = {}
        if hasattr(provenance_report, "candidate_results"):
            candidate_results = dict(provenance_report.candidate_results)
        elif isinstance(provenance_report, dict):
            candidate_results = dict(provenance_report.get("candidate_results", {}))

        for candidate_id, result in candidate_results.items():
            status = (
                getattr(result, "temporal_provenance_status", None)
                if not isinstance(result, dict)
                else result.get("temporal_provenance_status")
            )
            earliest = (
                getattr(result, "earliest_permissible_eval_ts", None)
                if not isinstance(result, dict)
                else result.get("earliest_permissible_eval_ts")
            )

            if status == TEMPORAL_PROVENANCE_VERIFIED:
                windows[candidate_id] = {"earliest_valid_eval_utc": earliest}
            else:
                windows[candidate_id] = {"earliest_valid_eval_utc": None}

        return windows

    # ------------------------------------------------------------------
    # Full expansion
    # ------------------------------------------------------------------
    def run_expansion(
        self,
        *,
        provenance_report: Any = None,
    ) -> DatasetExpansionResult:
        """Run the full 12-month expansion: fetch, store, manifest, windows."""
        fetch_result = self.fetch_12_months()
        manifest_written = self.storage.read_dataset_manifest() is not None

        windows = self.compute_candidate_valid_windows(provenance_report=provenance_report)

        warnings: list[str] = list(fetch_result.get("warnings", []) or [])
        errors: list[str] = []

        if not manifest_written:
            errors.append(f"dataset manifest was not written to {self.storage_root}")

        return DatasetExpansionResult(
            dataset_id=str(fetch_result.get("dataset_id", "")),
            storage_root=str(self.storage_root),
            start_utc=str(fetch_result.get("start_utc", "")),
            end_utc=str(fetch_result.get("end_utc", "")),
            months_requested=int(self.months_back),
            observation_count=int(fetch_result.get("observation_count", 0) or 0),
            expected_intervals=int(fetch_result.get("expected_intervals", 0) or 0),
            coverage_fraction=float(fetch_result.get("coverage_fraction", 0.0) or 0.0),
            duplicate_count=int(fetch_result.get("duplicate_count", 0) or 0),
            conflicting_count=int(fetch_result.get("conflicting_count", 0) or 0),
            missing_interval_count=int(fetch_result.get("missing_interval_count", 0) or 0),
            dataset_hash=str(fetch_result.get("dataset_hash", "")),
            actual_first_candle_utc=fetch_result.get("actual_since"),
            actual_last_candle_utc=fetch_result.get("actual_until"),
            retrieval_timestamp=str(
                (fetch_result.get("manifest") or {}).get("retrieval_timestamp") or _utc_now_iso()
            ),
            manifest_written=bool(manifest_written),
            errors=errors,
            warnings=warnings,
            candidate_valid_windows=windows,
        )


# ----------------------------------------------------------------------
# Module-level convenience functions
# ----------------------------------------------------------------------


def expand_historical_dataset(
    *,
    months_back: int = 12,
    provenance_report: Any = None,
) -> DatasetExpansionResult:
    """Convenience entry point: expand the 12-month historical dataset."""
    expander = TwelveMonthDatasetExpander()
    expander.months_back = int(months_back)
    return expander.run_expansion(provenance_report=provenance_report)


def write_expansion_report(result: DatasetExpansionResult, output_path: Path | str) -> Path:
    """Write the expansion result as JSON and return the output path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return output_path


__all__ = [
    "DATASET_TYPE",
    "DEFAULT_12M_STORAGE_ROOT",
    "FETCH_SCHEMA_VERSION",
    "MONTHS_BACK",
    "SCHEMA_VERSION",
    "DatasetExpansionResult",
    "TwelveMonthDatasetExpander",
    "_sha256_json",
    "_utc_now_iso",
    "expand_historical_dataset",
    "write_expansion_report",
]
