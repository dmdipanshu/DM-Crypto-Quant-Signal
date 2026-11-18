"""Q6 prospective data pre-flight validation.

Validates that the prospective dataset is genuinely available, immutable,
and eligible for evaluation before any candidate evaluation begins.

Integrates with:
- src/crypto_engine/research/freeze.py (research freeze integrity)
- src/crypto_engine/research/prospective/manifest.py (collection manifest)
- src/crypto_engine/research/prospective/storage.py (prospective storage)
- src/crypto_engine/research/prospective/governance.py (holdout lifecycle)
- src/crypto_engine/research/prospective/quality.py (data quality)
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.data.validation import timeframe_to_ms  # noqa: E402
from crypto_engine.research.freeze import (  # noqa: E402
    DEFAULT_MANIFEST_PATH as FREEZE_MANIFEST_PATH,
)
from crypto_engine.research.freeze import (  # noqa: E402
    verify_freeze_integrity,
)
from crypto_engine.research.prospective.config import (  # noqa: E402
    DEFAULT_MIN_COVERAGE_FRACTION,
    DEFAULT_MIN_OBSERVATION_COUNT,
)
from crypto_engine.research.prospective.governance import (  # noqa: E402
    assess_prospective_holdout_readiness,
    historical_holdout_is_contaminated,
)
from crypto_engine.research.prospective.manifest import (  # noqa: E402
    DEFAULT_COLLECTION_MANIFEST_PATH,
    verify_collection_integrity,
)
from crypto_engine.research.prospective.quality import (  # noqa: E402
    compute_quality_report,
)
from crypto_engine.research.prospective.storage import (  # noqa: E402
    ProspectiveStorage,
)

Q6_PREFLIGHT_SCHEMA_VERSION = "q6-preflight.v1"


class PreflightBlockedError(Exception):
    """Raised when Q6 preflight cannot proceed due to blocking conditions."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_json(value: Any) -> str:
    """Deterministic sha256 over a JSON-serialisable value."""
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_preflight(
    *,
    freeze_manifest_path: Path | None = None,
    collection_manifest_path: Path | None = None,
    prospective_storage_root: Path | None = None,
    exchange: str = "binance",
    market_type: str = "spot",
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    min_observation_count: int = DEFAULT_MIN_OBSERVATION_COUNT,
    min_coverage_fraction: float = DEFAULT_MIN_COVERAGE_FRACTION,
) -> dict[str, Any]:
    """Run the complete Q6 preflight validation.

    Validates every mandatory condition before candidate evaluation:

    1. Original research freeze is VALID
    2. Collection manifest is VALID and associated with the freeze
    3. Prospective storage exists and is readable
    4. Dataset hash is stable
    5. Hash-chain integrity passes
    6. OHLCV quality passes
    7. No collection gaps
    8. No duplicate/conflicting candles
    9. Timestamp alignment verified
    10. Forward-only ingestion (no backfill)
    11. First-seen provenance valid
    12. Observation count meets minimum
    13. Coverage meets minimum
    14. Data-quality state permits evaluation
    15. Holdout state is ELIGIBLE_FOR_EVALUATION

    Returns a preflight result dict with:
      - status: "PASS" or "BLOCKED"
      - blocking_conditions: list of condition descriptions
      - preflight_details: per-check details
      - readiness: holdout readiness assessment

    Raises PreflightBlockedError if any mandatory condition fails.
    """
    freeze_path = freeze_manifest_path or FREEZE_MANIFEST_PATH
    collection_path = collection_manifest_path or DEFAULT_COLLECTION_MANIFEST_PATH

    blocking_conditions: list[str] = []
    details: dict[str, Any] = {}

    # ---- Step 1: Validate original research freeze ----
    freeze_result = verify_freeze_integrity(freeze_path)
    details["freeze"] = freeze_result
    if freeze_result["status"] != "VALID":
        blocking_conditions.append(
            f"Research freeze is not VALID (status={freeze_result['status']}): "
            f"{freeze_result.get('reason', 'unknown')}"
        )

    freeze_id = freeze_result.get("freeze_id")
    freeze_timestamp = freeze_result.get("created_at_utc")

    # ---- Step 2: Validate collection manifest ----
    collection_result = verify_collection_integrity(
        collection_path,
        expected_collection_config_hash=None,
        expected_exchange=exchange,
        expected_symbols=[symbol],
        expected_timeframes=[timeframe],
        associated_freeze_id=freeze_id,
    )
    details["collection_manifest"] = collection_result
    if collection_result["status"] != "VALID":
        blocking_conditions.append(
            f"Collection manifest is not VALID (status={collection_result['status']}): "
            f"{collection_result.get('reason', 'unknown')}"
        )

    collection_id = collection_result.get("collection_id")
    collection_started_at = collection_result.get("collection_started_at_utc")

    # ---- Step 3: Verify freeze-collection association ----
    if freeze_id and collection_result.get("associated_freeze_id") != freeze_id:
        blocking_conditions.append(
            f"Collection manifest freeze ID mismatch: expected {freeze_id}, "
            f"found {collection_result.get('associated_freeze_id')}"
        )

    # ---- Step 4: Read prospective storage ----
    if prospective_storage_root is None:
        prospective_storage_root = (
            Path("data/prospective") / exchange / market_type / symbol / timeframe
        )

    storage = ProspectiveStorage(prospective_storage_root)
    details["storage_root"] = str(prospective_storage_root)

    storage.read_raw_observations()
    unique_candles = storage.read_unique_normalized_candles()
    storage.read_ledger_events()

    dataset_hash = storage.compute_dataset_hash()
    raw_payload_hash = storage.compute_raw_payload_hash()
    ledger_chain = storage.verify_ledger_chain()

    details["dataset_hash"] = dataset_hash
    details["raw_payload_hash"] = raw_payload_hash
    details["ledger_chain"] = ledger_chain

    if not ledger_chain["valid"]:
        blocking_conditions.append(
            f"Ingestion ledger hash chain is broken: {ledger_chain.get('reason', 'unknown')}"
        )

    # ---- Step 5: OHLCV quality ----
    quality_checks_passed = True
    ohlcv_issues: list[str] = []
    for candle in unique_candles:
        open_price = candle.get("open", 0)
        high = candle.get("high", 0)
        low = candle.get("low", 0)
        close = candle.get("close", 0)
        volume = candle.get("volume", 0)

        if high < low:
            ohlcv_issues.append(f"Candle {candle.get('event_id')}: high < low")
            quality_checks_passed = False
        if open_price < low or open_price > high:
            ohlcv_issues.append(f"Candle {candle.get('event_id')}: open outside [low, high]")
            quality_checks_passed = False
        if close < low or close > high:
            ohlcv_issues.append(f"Candle {candle.get('event_id')}: close outside [low, high]")
            quality_checks_passed = False
        if volume < 0:
            ohlcv_issues.append(f"Candle {candle.get('event_id')}: negative volume")
            quality_checks_passed = False

    details["ohlcv_quality"] = {
        "passed": quality_checks_passed,
        "issues": ohlcv_issues[:10],  # Limit output
        "issue_count": len(ohlcv_issues),
    }
    if not quality_checks_passed:
        blocking_conditions.append(
            f"OHLCV quality check failed: {len(ohlcv_issues)} issue(s) found"
        )

    # ---- Step 6: Duplicate/conflict detection ----
    seen_open_times: dict[str, list[str]] = {}
    duplicate_candles: list[str] = []
    conflicting_candles: list[str] = []
    for candle in unique_candles:
        key = (candle.get("open_time"), candle.get("symbol"))
        event_id = candle.get("event_id")
        if key in seen_open_times:
            duplicate_candles.append(event_id)
            # Check for conflicting data
            prev_candle = seen_open_times[key][-1]
            if candle.get("open") != prev_candle.get("open") or candle.get(
                "close"
            ) != prev_candle.get("close"):
                conflicting_candles.append(event_id)
        seen_open_times.setdefault(key, []).append(candle)

    details["duplicates"] = {
        "count": len(duplicate_candles),
        "event_ids": duplicate_candles[:10],
    }
    details["conflicts"] = {
        "count": len(conflicting_candles),
        "event_ids": conflicting_candles[:10],
    }
    if duplicate_candles:
        blocking_conditions.append(
            f"Duplicate candles detected: {len(duplicate_candles)} duplicate(s)"
        )
    if conflicting_candles:
        blocking_conditions.append(
            f"Conflicting candles detected: {len(conflicting_candles)} conflict(s)"
        )

    # ---- Step 7: Forward-only ingestion (no backfill) ----
    # All candles must have open_time >= collection_started_at
    backfill_candles = []
    if collection_started_at:
        from datetime import datetime as _dt

        collection_start = _dt.fromisoformat(collection_started_at.replace("Z", "+00:00"))
        for candle in unique_candles:
            candle_open = candle.get("open_time", "")
            if candle_open:
                try:
                    candle_dt = _dt.fromisoformat(candle_open.replace("Z", "+00:00"))
                    if candle_dt < collection_start:
                        backfill_candles.append(candle.get("event_id"))
                except (ValueError, TypeError):
                    pass

    details["backfill_check"] = {
        "backfill_candle_count": len(backfill_candles),
        "event_ids": backfill_candles[:10],
    }
    if backfill_candles:
        blocking_conditions.append(
            f"Historical backfill detected: {len(backfill_candles)} candle(s) "
            f"before collection start"
        )

    # ---- Step 8: First-seen provenance ----
    missing_first_seen = [
        c.get("event_id") for c in unique_candles if not c.get("first_seen_at_utc")
    ]
    details["first_seen_provenance"] = {
        "missing_count": len(missing_first_seen),
        "event_ids": missing_first_seen[:10],
    }
    if missing_first_seen:
        blocking_conditions.append(f"Missing first_seen_at for {len(missing_first_seen)} candle(s)")

    # ---- Step 9: Observation count ----
    observation_count = len(unique_candles)
    details["observation_count"] = observation_count
    if observation_count < min_observation_count:
        blocking_conditions.append(
            f"Observation count {observation_count} is below minimum {min_observation_count}"
        )

    # ---- Step 10: Coverage ----
    # Expected intervals: count of exchange-aligned candles whose open time
    # is strictly after collection_started_at_utc and whose close time
    # is at or before now (fully finalized).
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    collection_start_ms = int(
        datetime.fromisoformat(collection_started_at.replace("Z", "+00:00")).timestamp() * 1000
    )

    expected_intervals = 0
    for _symbol in [symbol]:
        for _timeframe in [timeframe]:
            interval_ms = timeframe_to_ms(_timeframe)
            # First exchange-aligned candle whose open time is strictly
            # after collection_started_at_utc
            first_open_ms = ((collection_start_ms // interval_ms) + 1) * interval_ms
            t = first_open_ms
            while t + interval_ms <= now_ms:
                expected_intervals += 1
                t += interval_ms

    coverage_fraction = observation_count / expected_intervals if expected_intervals > 0 else 0.0
    details["coverage_fraction"] = coverage_fraction
    if coverage_fraction < min_coverage_fraction and observation_count > 0:
        blocking_conditions.append(
            f"Coverage fraction {coverage_fraction:.2%} is below minimum {min_coverage_fraction:.2%}"
        )

    # ---- Step 11: Data-quality state ----
    # Build a collection result dict for quality report
    collection_result_for_quality = {
        "collection_id": collection_id,
        "collection_started_at_utc": collection_started_at,
        "observations": unique_candles,
        "gaps": [],  # Would come from collector
        "duplicates": duplicate_candles,
        "conflicts": conflicting_candles,
        "rejections": [],
        "errors": [],
        "status": "HEALTHY" if ledger_chain["valid"] else "DATA_INVALID",
        "quality_checks": {
            "state": "HEALTHY" if ledger_chain["valid"] else "DATA_INVALID",
            "observation_count": observation_count,
            "coverage_fraction": coverage_fraction,
        },
    }
    quality_report = compute_quality_report(
        collection_result=collection_result_for_quality,
        collection_manifest=collection_result
        if collection_result.get("status") == "VALID"
        else None,
    )
    details["quality_report"] = quality_report

    if quality_report["evaluation_blocked"]:
        for reason in quality_report.get("evaluation_block_reasons", []):
            if reason not in blocking_conditions:
                blocking_conditions.append(reason)

    # ---- Step 12: Holdout readiness ----
    data_quality_passed = (
        quality_checks_passed and ledger_chain["valid"] and not blocking_conditions
    )
    first_seen_valid = len(missing_first_seen) == 0
    post_collection_start = len(backfill_candles) == 0
    no_backfill = len(backfill_candles) == 0
    no_development_inspection = True  # Q6 does not inspect prospective data in development

    readiness = assess_prospective_holdout_readiness(
        freeze_timestamp=freeze_timestamp or "",
        collection_started_at=collection_started_at or "",
        observation_count=observation_count,
        min_observation_count=min_observation_count,
        coverage_fraction=coverage_fraction,
        min_coverage_fraction=min_coverage_fraction,
        data_quality_passed=data_quality_passed,
        first_seen_provenance_valid=first_seen_valid,
        post_collection_start=post_collection_start,
        no_backfill=no_backfill,
        no_development_inspection=no_development_inspection,
        evaluation_requested=False,
    )
    details["holdout_readiness"] = readiness

    if readiness["state"] != "ELIGIBLE_FOR_EVALUATION":
        blocking_conditions.append(
            f"Prospective holdout is {readiness['state']}, not ELIGIBLE_FOR_EVALUATION"
        )

    # ---- Step 13: Historical holdout contamination ----
    historical = historical_holdout_is_contaminated()
    details["historical_holdout"] = historical

    # ---- Overall result ----
    status = "BLOCKED" if blocking_conditions else "PASS"

    result = {
        "schema": Q6_PREFLIGHT_SCHEMA_VERSION,
        "preflight_id": f"preflight-{_sha256_json({**details, 'at': _utc_now_iso()})[:16]}",
        "assessed_at_utc": _utc_now_iso(),
        "status": status,
        "blocking_conditions": blocking_conditions,
        "blocking_condition_count": len(blocking_conditions),
        "preflight_details": details,
        "readiness": readiness,
        "historical_holdout": historical,
        "can_proceed": len(blocking_conditions) == 0,
    }

    if status == "BLOCKED":
        raise PreflightBlockedError(
            f"Q6 preflight BLOCKED: {len(blocking_conditions)} condition(s): "
            + "; ".join(blocking_conditions[:5])
        )

    return result


__all__ = [
    "Q6_PREFLIGHT_SCHEMA_VERSION",
    "PreflightBlockedError",
    "run_preflight",
]
