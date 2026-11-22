"""Read-only prospective collection monitoring script.

Reads the Q5.2 prospective collection report, the collection manifest,
the data quality report, and the Q6 preflight report, then prints a
JSON status summary to stdout.

Usage:
    uv run python scripts/monitor_prospective_collection.py

Exit codes:
    0 - success
    1 - file read error
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COLLECTION_REPORT = ROOT / "docs" / "validation" / "results" / "prospective_collection_q5.json"
MANIFEST = ROOT / "docs" / "validation" / "PROSPECTIVE_COLLECTION_MANIFEST.json"
QUALITY_REPORT = ROOT / "docs" / "validation" / "results" / "prospective_data_quality_q5.json"
PREFLIGHT_REPORT = ROOT / "docs" / "validation" / "results" / "q6_preflight.json"

MINIMUM_OBSERVATIONS_REQUIRED = 168
MINIMUM_COVERAGE_REQUIRED = 0.95
MINIMUM_DURATION_SECONDS = 7 * 24 * 3600
MAX_INGESTION_LATENCY_S = 900
FINALIZATION_MARGIN_S = 300


def load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value)


def format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def main() -> int:
    collection_report = load_json(COLLECTION_REPORT)
    manifest = load_json(MANIFEST)
    quality_report = load_json(QUALITY_REPORT)
    load_json(PREFLIGHT_REPORT)

    checks = collection_report.get("quality_checks", {})

    collection_id = collection_report.get("collection_id")
    freeze_id = collection_report.get("research_freeze_id")
    collection_status = quality_report.get("state", checks.get("state"))

    observation_count = quality_report.get("observation_count", checks.get("observation_count", 0))
    expected_intervals = quality_report.get(
        "expected_intervals", checks.get("expected_intervals", 0)
    )
    coverage_fraction_decimal = quality_report.get(
        "coverage_fraction", checks.get("coverage_fraction", 0.0)
    )
    coverage_fraction_pct = round(coverage_fraction_decimal * 100.0, 2)

    duplicates_count = quality_report.get("duplicate_count", checks.get("duplicate_count", 0))
    conflicts_count = quality_report.get("conflict_count", checks.get("conflict_count", 0))
    rejection_count = quality_report.get("rejection_count", checks.get("rejection_count", 0))
    evaluation_blocked = bool(
        quality_report.get("evaluation_blocked", checks.get("evaluation_blocked", False))
    )
    evaluation_block_reasons = list(
        quality_report.get("evaluation_block_reasons", checks.get("evaluation_block_reasons", []))
        or []
    )

    collection_started_raw = manifest.get("collection_started_at_utc")
    if collection_started_raw is None:
        report = {
            "collection_id": collection_id,
            "freeze_id": freeze_id,
            "collection_status": "NOT_STARTED",
            "observation_count": observation_count,
            "expected_intervals": expected_intervals,
            "coverage_fraction": coverage_fraction_pct,
            "collection_started_at_utc": None,
            "seven_day_deadline_utc": None,
            "current_time_utc": format_utc(datetime.now(UTC)),
            "elapsed_duration_seconds": None,
            "remaining_duration_seconds": None,
            "remaining_duration_days": None,
            "observations_needed_for_168": max(
                0, MINIMUM_OBSERVATIONS_REQUIRED - observation_count
            ),
            "coverage_needed_for_95pct": max(
                0.0, MINIMUM_COVERAGE_REQUIRED - coverage_fraction_decimal
            ),
            "duplicates_count": duplicates_count,
            "conflicts_count": conflicts_count,
            "rejection_count": rejection_count,
            "rejection_reasons": [],
            "evaluation_blocked": True,
            "evaluation_block_reasons": ["No accepted observations; anchor not established."],
            "eligibility": False,
            "ledger_integrity": checks.get("ledger_status"),
            "ingestion_status": checks.get("ingestion_status"),
            "provenance_status": checks.get("provenance_status"),
            "minimum_observations_required": MINIMUM_OBSERVATIONS_REQUIRED,
            "minimum_coverage_required": MINIMUM_COVERAGE_REQUIRED,
            "minimum_duration_seconds": MINIMUM_DURATION_SECONDS,
            "max_ingestion_latency_s": MAX_INGESTION_LATENCY_S,
            "finalization_margin_s": FINALIZATION_MARGIN_S,
        }
        print(json.dumps(report, indent=2))
        return 0

    collection_started_at = parse_utc(collection_started_raw)
    collection_started_at_utc = format_utc(collection_started_at)
    seven_day_deadline = collection_started_at + timedelta(days=7)
    seven_day_deadline_utc = format_utc(seven_day_deadline)

    now = datetime.now(UTC)
    current_time_utc = format_utc(now)

    elapsed_duration_seconds = (now - collection_started_at).total_seconds()
    remaining_duration_seconds = (seven_day_deadline - now).total_seconds()
    remaining_duration_days = round(remaining_duration_seconds / 86400.0, 2)

    observations_needed_for_168 = max(0, MINIMUM_OBSERVATIONS_REQUIRED - observation_count)
    coverage_needed_for_95pct = max(0.0, MINIMUM_COVERAGE_REQUIRED - coverage_fraction_decimal)

    rejections = quality_report.get("rejections", []) or collection_report.get("rejections", [])
    rejection_reasons = []
    seen_reasons = set()
    for rejection in rejections:
        reason = rejection.get("reason")
        if reason and reason not in seen_reasons:
            seen_reasons.add(reason)
            rejection_reasons.append(reason)

    eligibility = (
        observation_count >= MINIMUM_OBSERVATIONS_REQUIRED
        and coverage_fraction_decimal >= MINIMUM_COVERAGE_REQUIRED
        and elapsed_duration_seconds >= MINIMUM_DURATION_SECONDS
        and not evaluation_blocked
    )

    report = {
        "collection_id": collection_id,
        "freeze_id": freeze_id,
        "collection_status": collection_status,
        "observation_count": observation_count,
        "expected_intervals": expected_intervals,
        "coverage_fraction": coverage_fraction_pct,
        "collection_started_at_utc": collection_started_at_utc,
        "seven_day_deadline_utc": seven_day_deadline_utc,
        "current_time_utc": current_time_utc,
        "elapsed_duration_seconds": elapsed_duration_seconds,
        "remaining_duration_seconds": remaining_duration_seconds,
        "remaining_duration_days": remaining_duration_days,
        "observations_needed_for_168": observations_needed_for_168,
        "coverage_needed_for_95pct": coverage_needed_for_95pct,
        "duplicates_count": duplicates_count,
        "conflicts_count": conflicts_count,
        "rejection_count": rejection_count,
        "rejection_reasons": rejection_reasons,
        "evaluation_blocked": evaluation_blocked,
        "evaluation_block_reasons": evaluation_block_reasons,
        "eligibility": eligibility,
        "ledger_integrity": checks.get("ledger_status"),
        "ingestion_status": checks.get("ingestion_status"),
        "provenance_status": checks.get("provenance_status"),
        "minimum_observations_required": MINIMUM_OBSERVATIONS_REQUIRED,
        "minimum_coverage_required": MINIMUM_COVERAGE_REQUIRED,
        "minimum_duration_seconds": MINIMUM_DURATION_SECONDS,
        "max_ingestion_latency_s": MAX_INGESTION_LATENCY_S,
        "finalization_margin_s": FINALIZATION_MARGIN_S,
    }

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
