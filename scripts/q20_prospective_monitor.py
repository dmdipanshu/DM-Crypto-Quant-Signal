"""Read-only Q20 Phase D prospective collection monitoring script.

Reads canonical prospective storage, the immutable collection manifest,
and the Q5 collection report, then prints a JSON status summary to stdout.

Makes no writes to any data file, manifest, or storage, and performs no
network calls. Uses only stdlib plus the permitted project modules:
crypto_engine.research.prospective.storage,
crypto_engine.research.prospective.config,
crypto_engine.data.validation.

Usage:
    uv run python scripts/q20_prospective_monitor.py

Exit codes:
    0 - success
    1 - error
"""

from __future__ import annotations

import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crypto_engine.data.validation import timeframe_to_ms
from crypto_engine.research.prospective.config import (
    DEFAULT_MIN_COVERAGE_FRACTION,
    DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S,
    DEFAULT_MIN_OBSERVATION_COUNT,
    ProspectiveCollectionPolicy,
)
from crypto_engine.research.prospective.storage import ProspectiveStorage

ROOT = Path(__file__).resolve().parents[1]

STORAGE_ROOT = ROOT / "data" / "prospective" / "binance" / "spot" / "BTC/USDT" / "1h"
MANIFEST_PATH = ROOT / "docs" / "validation" / "PROSPECTIVE_COLLECTION_MANIFEST.json"
COLLECTION_REPORT_PATH = ROOT / "docs" / "validation" / "results" / "prospective_collection_q5.json"

NO_SUCCESSFUL_INGESTION_WINDOW_S = 2 * 60 * 60  # 2 hours
REPEATED_THRESHOLD = 2  # "multiple" means more than one


def load_json_required(path: Path) -> dict[str, Any]:
    """Load a JSON file that must exist and be valid; exit 1 on failure."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def load_json_optional(path: Path) -> dict[str, Any]:
    """Load a JSON file if present; return {} on absence or invalid content."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def parse_utc(value: str | None) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def format_utc(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        dt = parse_utc(value)
        if dt is None:
            return None
        return dt.isoformat()
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


def solve_projection_hours(
    accepted: int, expected: int, min_coverage: float = DEFAULT_MIN_COVERAGE_FRACTION
) -> int:
    """Minimum integer N such that (A + N) / (E + N) >= min_coverage.

    Returns 0 when coverage already meets the threshold or when no
    meaningful projection can be made (expected <= 0).
    """
    if expected <= 0:
        return 0
    if accepted / expected >= min_coverage:
        return 0
    # A + N >= c * (E + N)  =>  N * (1 - c) >= c * E - A  =>  N >= (c*E - A) / (1 - c)
    raw = (min_coverage * expected - accepted) / (1.0 - min_coverage)
    return max(0, math.ceil(raw))


def count_rejection_reasons(rejections: list[dict[str, Any]], keywords: list[str]) -> int:
    """Count rejections whose reason contains any of the given keywords."""
    count = 0
    for rejection in rejections:
        reason = str(rejection.get("reason", "")).lower()
        if any(kw in reason for kw in keywords):
            count += 1
    return count


def unique_reasons(rejections: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    reasons: list[str] = []
    for rejection in rejections:
        reason = rejection.get("reason")
        if reason and reason not in seen:
            seen.add(reason)
            reasons.append(reason)
    return reasons


def main() -> int:
    manifest = load_json_required(MANIFEST_PATH)
    collection_report = load_json_optional(COLLECTION_REPORT_PATH)

    collection_id = manifest.get("collection_id")
    freeze_id = manifest.get("associated_freeze_id")
    collection_started_at = parse_utc(manifest.get("collection_started_at_utc"))
    if collection_started_at is None:
        anchor = ProspectiveStorage(STORAGE_ROOT).verify_collection_start_anchor()
        collection_started_at = parse_utc(anchor.get("collection_started_at_utc"))
    if collection_started_at is None:
        now = datetime.now(UTC)
        report: dict[str, Any] = {
            "schema": "q20-prospective-monitor.v1",
            "generated_at_utc": format_utc(now),
            "collection_id": collection_id,
            "freeze_id": freeze_id,
            "collection_started_at_utc": None,
            "current_time_utc": format_utc(now),
            "accepted_observations": 0,
            "expected_intervals": 0,
            "coverage_fraction": 0.0,
            "observations_needed": DEFAULT_MIN_OBSERVATION_COUNT,
            "coverage_needed": DEFAULT_MIN_COVERAGE_FRACTION,
            "projected_eligibility_date": None,
            "last_successful_candle": None,
            "last_collection_attempt": None,
            "current_ingestion_latency_s": None,
            "rejected_observations": 0,
            "rejection_reasons": [],
            "duplicate_count": 0,
            "conflict_count": 0,
            "exchange_api_failures": 0,
            "retry_exhaustion": 0,
            "ledger_integrity": {"valid": False, "event_count": 0},
            "provenance_status": "NOT_STARTED",
            "collection_eligibility_state": "NOT_STARTED",
            "seven_day_deadline_utc": None,
            "remaining_duration_days": None,
            "seven_day_threshold_elapsed": False,
            "warnings": ["collection_not_started_no_anchor"],
            "raw_observation_count": 0,
            "normalized_candle_count": 0,
            "unique_candle_count": 0,
            "minimum_observations_required": DEFAULT_MIN_OBSERVATION_COUNT,
            "minimum_coverage_required": DEFAULT_MIN_COVERAGE_FRACTION,
            "minimum_duration_seconds": DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S,
        }
        print(json.dumps(report, indent=2))
        return 0

    symbols = list(manifest.get("symbols", []))
    timeframes = list(manifest.get("timeframes", []))
    policy = ProspectiveCollectionPolicy(
        exchange=manifest.get("exchange", "binance"),
        market_type=manifest.get("market_type", "spot"),
        symbols=tuple(symbols) if symbols else ("BTC/USDT",),
        timeframes=tuple(timeframes) if timeframes else ("1h",),
    )

    now = datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    # collection_started_at resolved above with NOT_STARTED handling

    collection_start_ms = int(collection_started_at.timestamp() * 1000)
    expected_intervals = expected_intervals_formula(
        now_ms, collection_start_ms, symbols, timeframes
    )

    # Read-only storage access: no writes, no network.
    storage = ProspectiveStorage(STORAGE_ROOT)
    raw_records = storage.read_raw_observations()
    normalized_records = storage.read_normalized_candles()
    unique_candles = storage.read_unique_normalized_candles()
    summary = storage.get_observation_summary()

    raw_count = len(raw_records)
    normalized_count = len(normalized_records)
    accepted_observations = len(unique_candles)

    coverage_fraction = (
        accepted_observations / expected_intervals if expected_intervals > 0 else 0.0
    )

    observations_needed = max(0, DEFAULT_MIN_OBSERVATION_COUNT - accepted_observations)
    coverage_needed = max(0.0, DEFAULT_MIN_COVERAGE_FRACTION - coverage_fraction)

    projection_hours = solve_projection_hours(
        accepted_observations, expected_intervals, DEFAULT_MIN_COVERAGE_FRACTION
    )
    projected_eligibility_date = format_utc(now + timedelta(hours=projection_hours))

    last_successful_candle: str | None = None
    current_ingestion_latency_s: float | None = None
    if unique_candles:
        last_candle = unique_candles[-1]
        last_successful_candle = last_candle.get("open_time")
        close_time = parse_utc(last_candle.get("close_time"))
        if close_time is not None:
            current_ingestion_latency_s = round((now - close_time).total_seconds(), 3)

    last_collection_attempt: str | None = None
    if raw_records:
        sorted_raw = sorted(
            raw_records,
            key=lambda r: str(r.get("ingested_at_utc", r.get("observed_at_utc", ""))),
        )
        most_recent = sorted_raw[-1]
        last_collection_attempt = most_recent.get(
            "ingested_at_utc", most_recent.get("observed_at_utc")
        )

    rejections = collection_report.get("rejections", []) or []
    rejected_observations = len(rejections)
    rejection_reasons = unique_reasons(rejections)

    exchange_api_failures = count_rejection_reasons(
        rejections, ["exchange fetch failed", "api error", "api failure", "429", "rate limit"]
    )
    retry_exhaustion = count_rejection_reasons(rejections, ["retry", "exhaust", "max retries"])

    duplicate_count = int(summary.get("duplicate_ingestion_attempts", 0))
    conflict_count = int(summary.get("conflicting_observations", 0))

    ledger_integrity = storage.verify_ledger_chain()
    ledger_valid = bool(ledger_integrity.get("valid"))

    # Provenance: healthy when every raw observation carries first-seen provenance.
    provenance_status = "HEALTHY"
    if raw_records and any(not r.get("first_seen_at_utc") for r in raw_records):
        provenance_status = "PROVENANCE_INVALID"

    # Collection eligibility state.
    elapsed_s = (now - collection_started_at).total_seconds()
    if accepted_observations == 0 or conflict_count > 0 or not ledger_valid:
        collection_eligibility_state = "DATA_INVALID"
    elif (
        accepted_observations >= policy.min_observation_count
        and coverage_fraction >= policy.min_coverage_fraction
        and elapsed_s >= policy.min_elapsed_observation_period_s
    ):
        collection_eligibility_state = "ELIGIBLE_FOR_EVALUATION"
    else:
        collection_eligibility_state = "ACCUMULATING"

    # Seven-day window.
    seven_day_deadline = collection_started_at + timedelta(
        seconds=DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S
    )
    seven_day_deadline_utc = format_utc(seven_day_deadline)
    remaining_duration_days = round((seven_day_deadline - now).total_seconds() / 86400.0, 2)
    seven_day_threshold_elapsed = now >= seven_day_deadline

    # Warnings.
    warnings: list[str] = []

    if last_collection_attempt is None:
        warnings.append("no_successful_ingestion_for_more_than_two_cycles")
    else:
        last_attempt = parse_utc(last_collection_attempt)
        if (
            last_attempt is not None
            and (now - last_attempt).total_seconds() > NO_SUCCESSFUL_INGESTION_WINDOW_S
        ):
            warnings.append("no_successful_ingestion_for_more_than_two_cycles")

    http_429_count = count_rejection_reasons(rejections, ["429", "rate limit"])
    if http_429_count > REPEATED_THRESHOLD:
        warnings.append("repeated_http_429")

    timeout_count = count_rejection_reasons(rejections, ["timeout"])
    if timeout_count > REPEATED_THRESHOLD:
        warnings.append("repeated_exchange_timeouts")

    if rejected_observations > 0 and coverage_fraction < DEFAULT_MIN_COVERAGE_FRACTION:
        warnings.append("increasing_rejection_rate")

    if not ledger_valid:
        warnings.append("invalid_ledger_hash")

    if (raw_records or normalized_records) and any(
        str(r.get("collection_id")) != str(collection_id)
        for r in (*raw_records, *normalized_records)
    ):
        warnings.append("unexpected_collection_id_change")

    if (raw_records or normalized_records) and any(
        str(r.get("research_freeze_id")) != str(freeze_id)
        for r in (*raw_records, *normalized_records)
    ):
        warnings.append("unexpected_freeze_id_change")

    if raw_count != normalized_count or normalized_count != accepted_observations:
        warnings.append("storage_reader_disagreement")

    report: dict[str, Any] = {
        "schema": "q20-prospective-monitor.v1",
        "generated_at_utc": format_utc(now),
        "collection_id": collection_id,
        "freeze_id": freeze_id,
        "collection_started_at_utc": format_utc(collection_started_at),
        "current_time_utc": format_utc(now),
        "accepted_observations": accepted_observations,
        "expected_intervals": expected_intervals,
        "coverage_fraction": round(coverage_fraction, 4),
        "observations_needed": observations_needed,
        "coverage_needed": round(coverage_needed, 4),
        "projected_eligibility_date": projected_eligibility_date,
        "last_successful_candle": last_successful_candle,
        "last_collection_attempt": last_collection_attempt,
        "current_ingestion_latency_s": current_ingestion_latency_s,
        "rejected_observations": rejected_observations,
        "rejection_reasons": rejection_reasons,
        "duplicate_count": duplicate_count,
        "conflict_count": conflict_count,
        "exchange_api_failures": exchange_api_failures,
        "retry_exhaustion": retry_exhaustion,
        "ledger_integrity": {
            "valid": ledger_valid,
            "event_count": int(ledger_integrity.get("event_count", 0)),
        },
        "provenance_status": provenance_status,
        "collection_eligibility_state": collection_eligibility_state,
        "seven_day_deadline_utc": seven_day_deadline_utc,
        "remaining_duration_days": remaining_duration_days,
        "seven_day_threshold_elapsed": seven_day_threshold_elapsed,
        "warnings": warnings,
        "raw_observation_count": raw_count,
        "normalized_candle_count": normalized_count,
        "unique_candle_count": accepted_observations,
        "minimum_observations_required": policy.min_observation_count,
        "minimum_coverage_required": policy.min_coverage_fraction,
        "minimum_duration_seconds": policy.min_elapsed_observation_period_s,
    }

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
