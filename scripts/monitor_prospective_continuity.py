"""Read-only prospective collection continuity monitor.

Reads canonical prospective storage, the collection manifest, the data
quality report, and the collection report, then prints a JSON status
summary to stdout. Makes no writes to any data file, manifest, or
storage, and performs no network calls.

Usage:
    uv run python scripts/monitor_prospective_continuity.py

Exit codes:
    0 - success
    1 - error
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crypto_engine.data.validation import timeframe_to_ms
from crypto_engine.research.prospective.config import (
    DEFAULT_MIN_COVERAGE_FRACTION,
    DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S,
    DEFAULT_MIN_OBSERVATION_COUNT,
)
from crypto_engine.research.prospective.storage import ProspectiveStorage

ROOT = Path(__file__).resolve().parents[1]

STORAGE_ROOT = ROOT / "data" / "prospective" / "binance" / "spot" / "BTC/USDT" / "1h"
MANIFEST_PATH = ROOT / "docs" / "validation" / "PROSPECTIVE_COLLECTION_MANIFEST.json"
QUALITY_REPORT_PATH = (
    ROOT / "docs" / "validation" / "results" / "prospective_data_quality_q5.json"
)
COLLECTION_REPORT_PATH = (
    ROOT / "docs" / "validation" / "results" / "prospective_collection_q5.json"
)

PERFECT_COLLECTIONS_TO_ELIGIBILITY = 1082


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def expected_intervals_formula(
    now_ms: int,
    collection_start_ms: int,
    symbols: list[str],
    timeframes: list[str],
) -> int:
    """Exact coverage denominator from collector.py _compute_quality_state.

    Counts exchange-aligned candle intervals whose open time is strictly
    after collection_started_at_utc and whose close time is at or before
    now. The denominator grows with time.
    """
    total = 0
    for _symbol in symbols:
        for timeframe in timeframes:
            interval_ms = timeframe_to_ms(timeframe)
            first_open_ms = ((collection_start_ms // interval_ms) + 1) * interval_ms
            t = first_open_ms
            while t + interval_ms <= now_ms:
                total += 1
                t += interval_ms
    return total


def main() -> int:
    manifest = load_json(MANIFEST_PATH)
    collection_report = load_json(COLLECTION_REPORT_PATH)
    quality_report = load_json(QUALITY_REPORT_PATH)

    checks = collection_report.get("quality_checks", {})

    now = datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)

    collection_started_at = parse_utc(manifest["collection_started_at_utc"])
    collection_start_ms = int(collection_started_at.timestamp() * 1000)

    symbols = list(manifest.get("symbols", []))
    timeframes = list(manifest.get("timeframes", []))

    expected_intervals = expected_intervals_formula(
        now_ms, collection_start_ms, symbols, timeframes
    )

    # Read-only storage access: no writes, no network.
    storage = ProspectiveStorage(STORAGE_ROOT)
    unique_candles = storage.read_unique_normalized_candles()
    accepted_observations = len(unique_candles)

    coverage_fraction = (
        accepted_observations / expected_intervals if expected_intervals > 0 else 0.0
    )

    observations_needed = max(0, DEFAULT_MIN_OBSERVATION_COUNT - accepted_observations)
    coverage_needed = max(0.0, DEFAULT_MIN_COVERAGE_FRACTION - coverage_fraction)

    projected_eligibility_date = format_utc(
        now + timedelta(hours=PERFECT_COLLECTIONS_TO_ELIGIBILITY)
    )

    last_successful_candle = None
    current_ingestion_latency_s = None
    if unique_candles:
        last_candle = unique_candles[-1]
        last_successful_candle = last_candle.get("open_time")
        close_time = last_candle.get("close_time")
        if close_time:
            current_ingestion_latency_s = round(
                (now - parse_utc(close_time)).total_seconds(), 3
            )

    ledger_integrity = storage.verify_ledger_chain()

    rejections = collection_report.get("rejections", []) or []
    latest_rejected_candle = None
    if rejections:
        sorted_rejections = sorted(rejections, key=lambda r: str(r.get("at_utc", "")))
        latest = sorted_rejections[-1]
        latest_rejected_candle = {
            "open_time": latest.get("open_time"),
            "reason": latest.get("reason"),
            "at_utc": latest.get("at_utc"),
            "symbol": latest.get("symbol"),
            "timeframe": latest.get("timeframe"),
        }

    seven_day_deadline = collection_started_at + timedelta(
        seconds=DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S
    )
    remaining_duration_days = round(
        (seven_day_deadline - now).total_seconds() / 86400.0, 2
    )

    collection_status = quality_report.get(
        "state", checks.get("state", collection_report.get("status"))
    )
    ingestion_status = checks.get("ingestion_status")
    provenance_status = checks.get("provenance_status")
    evaluation_blocked = bool(
        quality_report.get(
            "evaluation_blocked", checks.get("evaluation_blocked", False)
        )
    )
    evaluation_block_reasons = list(
        quality_report.get(
            "evaluation_block_reasons", checks.get("evaluation_block_reasons", [])
        )
        or []
    )

    report = {
        "collection_id": manifest.get("collection_id"),
        "collection_started_at_utc": format_utc(collection_started_at),
        "current_time_utc": format_utc(now),
        "accepted_observations": accepted_observations,
        "expected_intervals": expected_intervals,
        "coverage_fraction": round(coverage_fraction, 4),
        "observations_needed": observations_needed,
        "coverage_needed": round(coverage_needed, 4),
        "projected_eligibility_date": projected_eligibility_date,
        "last_successful_candle": last_successful_candle,
        "latest_rejected_candle": latest_rejected_candle,
        "current_ingestion_latency_s": current_ingestion_latency_s,
        "ledger_integrity": {
            "valid": bool(ledger_integrity.get("valid")),
            "event_count": int(ledger_integrity.get("event_count", 0)),
        },
        "ingestion_status": ingestion_status,
        "provenance_status": provenance_status,
        "seven_day_deadline_utc": format_utc(seven_day_deadline),
        "remaining_duration_days": remaining_duration_days,
        "collection_status": collection_status,
        "evaluation_blocked": evaluation_blocked,
        "evaluation_block_reasons": evaluation_block_reasons,
        "minimum_observations_required": DEFAULT_MIN_OBSERVATION_COUNT,
        "minimum_coverage_required": DEFAULT_MIN_COVERAGE_FRACTION,
        "minimum_duration_seconds": DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S,
    }

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
