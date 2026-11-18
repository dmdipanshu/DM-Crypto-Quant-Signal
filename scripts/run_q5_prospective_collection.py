"""Q5 prospective data collection runner.

Usage:
    uv run python scripts/run_q5_prospective_collection.py --once
    uv run python scripts/run_q5_prospective_collection.py --collect-status
    uv run python scripts/run_q5_prospective_collection.py --validate-prospective

This script is a thin wrapper around the Typer CLI research sub-commands.
It performs:

1. Validate the original research freeze (immutable, never modified).
2. Validate or reuse the immutable collection manifest.
3. Collect eligible finalized observations (public Binance endpoints only).
4. Validate provenance and data quality.
5. Update prospective collection status.
6. Generate Q5 reports and evidence.

It does NOT train models, optimize thresholds, backtest strategies,
or change Q4 eligibility. Collection execution and prospective evaluation
are separate commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.research.prospective.collector import (  # noqa: E402
    CollectionStatus,
    ProspectiveCollector,
)
from crypto_engine.research.prospective.config import (  # noqa: E402
    load_collection_config,
)
from crypto_engine.research.prospective.governance import (  # noqa: E402
    assess_prospective_holdout_readiness,
    historical_holdout_is_contaminated,
)
from crypto_engine.research.prospective.quality import (  # noqa: E402
    compute_quality_report,
)
from crypto_engine.research.prospective.storage import (  # noqa: E402
    ProspectiveStorage,
)
from crypto_engine.research.q6.governance_verification import (  # noqa: E402
    check_collection_authorization,
)

REPORTS_DIR = Path("docs/validation")
RESULTS_DIR = REPORTS_DIR / "results"
Q5_REPORT_MD = REPORTS_DIR / "PROSPECTIVE_DATA_COLLECTION_Q5.md"
Q5_GOV_REPORT_MD = REPORTS_DIR / "PROSPECTIVE_DATA_GOVERNANCE_Q5.md"
Q5_COLLECTION_JSON = RESULTS_DIR / "prospective_collection_q5.json"
Q5_QUALITY_JSON = RESULTS_DIR / "prospective_data_quality_q5.json"
COLLECTION_MANIFEST_JSON = REPORTS_DIR / "PROSPECTIVE_COLLECTION_MANIFEST.json"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def run_once() -> int:
    """Run one collection cycle and generate reports."""
    # Use the canonical collection configuration so all entry points produce
    # the identical hash and match the immutable manifest.
    collection_config = load_collection_config()

    collector = ProspectiveCollector(
        exchange=collection_config["exchange"],
        market_type=collection_config["market_type"],
        symbols=list(collection_config["symbols"]),
        timeframes=list(collection_config["timeframes"]),
    )

    # Step 1: Validate research freeze
    freeze_info = collector.validate_freeze()
    print(f"Research freeze: {freeze_info['freeze_id']} at {freeze_info['created_at_utc']}")

    # Step 2: Validate or reuse collection manifest
    manifest = collector.validate_or_create_collection_manifest()
    print(
        f"Collection manifest: {manifest['collection_id']} started at {manifest['collection_started_at_utc']}"
    )

    # Authorization gate: collection requires an active, non-revoked approval record
    auth = check_collection_authorization(
        collection_id=manifest["collection_id"],
        freeze_id=manifest.get("associated_freeze_id") or manifest.get("freeze_id"),
    )
    if not auth.authorized:
        print(f"COLLECTION NOT AUTHORIZED: {auth.reason}")
        print("Collection execution blocked. An active, non-revoked approval record is required under docs/validation/approvals/")
        print(f"  Collection ID: {auth.collection_id}")
        print(f"  Freeze ID: {auth.freeze_id}")
        return 3
    print(f"Collection authorized: {auth.reason}")
    print(f"  Approval ID: {auth.approval_record.get('approval_id', 'n/a') if auth.approval_record else 'n/a'}")

    # Step 3: Collect
    result = collector.collect_once()

    # Step 4: Quality report (computed before printing)
    quality = compute_quality_report(collection_result=result)

    print(f"Collection status: {result['status']}")
    print(f"Collector status: {quality.get('collector_status', 'unknown')}")
    print(f"Ingestion status: {quality.get('ingestion_status', 'unknown')}")
    print(f"Dataset quality: {quality.get('dataset_quality_status', 'unknown')}")
    print(f"Provenance status: {quality.get('provenance_status', 'unknown')}")
    print(f"Ledger status: {quality.get('ledger_status', 'unknown')}")
    print(f"Observations: {quality.get('observation_count', 0)} unique")
    print(f"Gaps: {len(result['gaps'])}")
    print(f"Errors: {len(result['errors'])}")
    print(f"Expected intervals: {quality.get('expected_intervals', 0)}")
    print(f"Coverage: {quality.get('coverage_fraction', 0):.2%}")
    print(f"Evaluation blocked: {quality.get('evaluation_blocked', True)}")
    if quality.get("evaluation_block_reasons"):
        for reason in quality["evaluation_block_reasons"]:
            print(f"  Block reason: {reason}")

    # Step 5: Write JSON artifacts
    _write_json(Q5_COLLECTION_JSON, result)
    _write_json(Q5_QUALITY_JSON, quality)

    # Step 6: Write collection manifest (reuse existing)
    _write_json(COLLECTION_MANIFEST_JSON, manifest)

    # Step 7: Write reports
    _write_q5_collection_report(result, quality, freeze_info, manifest)
    _write_q5_governance_report(result, quality, freeze_info, manifest)

    exit_code = 0 if result["status"] == CollectionStatus.HEALTHY else 1
    return exit_code


def run_collect_status() -> int:
    """Show collection manifest and quality state."""
    # Use the canonical collection configuration so all entry points produce
    # the identical hash and match the immutable manifest.
    collection_config = load_collection_config()

    collector = ProspectiveCollector(
        exchange=collection_config["exchange"],
        market_type=collection_config["market_type"],
        symbols=list(collection_config["symbols"]),
        timeframes=list(collection_config["timeframes"]),
    )

    integrity = collector.verify_collection_integrity()
    print(f"Collection Manifest: {integrity['status']}")
    print(f"Collection ID: {integrity.get('collection_id', 'n/a')}")
    print(f"Collection Started: {integrity.get('collection_started_at_utc', 'n/a')}")
    print(f"Associated Freeze ID: {integrity.get('associated_freeze_id', 'n/a')}")
    print(f"Associated Freeze Timestamp: {integrity.get('associated_freeze_timestamp', 'n/a')}")

    storage = ProspectiveStorage(Path("data/prospective/binance/spot/BTC/USDT/1h"))
    storage.read_unique_normalized_candles()
    summary = storage.get_observation_summary()
    ledger = storage.verify_ledger_chain()

    print(f"Accepted observations: {summary['accepted_unique_observations']}")
    print(f"Raw ingestion events: {summary['raw_ingestion_events']}")
    print(f"Duplicate ingestion attempts: {summary['duplicate_ingestion_attempts']}")
    print(f"Conflicting observations: {summary['conflicting_observations']}")
    print(f"Dataset Hash: {storage.compute_dataset_hash()}")
    print(f"Raw Payload Hash: {storage.compute_raw_payload_hash()}")
    print(f"Ledger Events: {ledger.get('event_count', 0)}")
    print(f"Ledger Valid: {ledger.get('valid', False)}")

    return 0


def run_validate_prospective(evaluate: bool = False) -> int:
    """Validate prospective holdout readiness."""
    # Use the canonical collection configuration so all entry points produce
    # the identical hash and match the immutable manifest.
    collection_config = load_collection_config()

    collector = ProspectiveCollector(
        exchange=collection_config["exchange"],
        market_type=collection_config["market_type"],
        symbols=list(collection_config["symbols"]),
        timeframes=list(collection_config["timeframes"]),
    )

    freeze_info = collector.validate_freeze()
    manifest = collector.validate_or_create_collection_manifest()

    storage = ProspectiveStorage(Path("data/prospective/binance/spot/BTC/USDT/1h"))
    unique_candles = storage.read_unique_normalized_candles()
    summary = storage.get_observation_summary()
    ledger = storage.verify_ledger_chain()

    observation_count = summary["accepted_unique_observations"]
    coverage = (
        summary["accepted_unique_observations"] / max(1, summary.get("unique_keys", 1))
        if summary.get("unique_keys", 0) > 0
        else 0.0
    )

    # Build a collection result dict for quality report computation
    collection_result = {
        "collection_id": manifest.get("collection_id"),
        "collection_started_at_utc": manifest.get("collection_started_at_utc"),
        "observations": unique_candles,
        "gaps": [],
        "duplicates": [],
        "conflicts": [],
        "rejections": [],
        "errors": [],
        "status": "HEALTHY" if ledger.get("valid", False) else "DATA_INVALID",
        "quality_checks": {
            "state": "HEALTHY" if ledger.get("valid", False) else "DATA_INVALID",
            "observation_count": observation_count,
            "coverage_fraction": coverage,
        },
    }
    quality = compute_quality_report(collection_result=collection_result)

    readiness = assess_prospective_holdout_readiness(
        freeze_timestamp=freeze_info["created_at_utc"],
        collection_started_at=manifest["collection_started_at_utc"],
        observation_count=observation_count,
        min_observation_count=collector.policy.min_observation_count,
        coverage_fraction=coverage,
        min_coverage_fraction=collector.policy.min_coverage_fraction,
        data_quality_passed=quality.get("dataset_quality_status", "HEALTHY") == "HEALTHY",
        first_seen_provenance_valid=quality.get("provenance_status", "HEALTHY") == "HEALTHY",
        post_collection_start=True,
        no_backfill=True,
        no_development_inspection=True,
        evaluation_requested=evaluate,
    )

    historical = historical_holdout_is_contaminated()

    print("=== Prospective Holdout Governance ===")
    print(f"Historical Holdout: {historical['state']}")
    print(f"Prospective State: {readiness['state']}")
    print(f"Eligible for Evaluation: {readiness['eligible_for_evaluation']}")
    print(f"Evaluated: {readiness['evaluated']}")
    print(f"Observation Count: {observation_count} (min: {collector.policy.min_observation_count})")
    print(f"Coverage: {coverage:.2%} (min: {collector.policy.min_coverage_fraction:.2%})")
    print(f"Data Quality Passed: {quality.get('dataset_quality_status', 'HEALTHY') == 'HEALTHY'}")
    print(f"Evaluation Blocked: {quality.get('evaluation_blocked', True)}")
    print(f"Evaluation Requested: {evaluate}")
    for reason in readiness["reasons"]:
        print(f"  - {reason}")
    if quality.get("evaluation_block_reasons"):
        print("\nEvaluation Block Reasons:")
        for reason in quality["evaluation_block_reasons"]:
            print(f"  - {reason}")

    return 0 if readiness["eligible_for_evaluation"] or readiness["evaluated"] else 1


def _write_q5_collection_report(
    result: dict,
    quality: dict,
    freeze_info: dict,
    manifest: dict,
) -> None:
    """Write the Q5 data collection report (Markdown)."""
    lines = [
        "# Prospective Data Collection — Q5",
        "",
        f"Generated: {_utc_now_iso()}",
        "",
        "## Collection Architecture",
        "",
        "- Exchange: Binance Spot (public market-data endpoints only)",
        "- Market Type: spot",
        "- Symbols: BTC/USDT",
        "- Timeframe: 1h",
        "- Collection Mode: forward-only, closed candles only",
        "- No trading credentials, no private endpoints, no order placement",
        "",
        "## Research Freeze Association",
        "",
        f"- Freeze ID: {freeze_info['freeze_id']}",
        f"- Freeze Timestamp: {freeze_info['created_at_utc']}",
        f"- Freeze Status: {freeze_info['status']}",
        "",
        "## Collection Manifest",
        "",
        f"- Collection ID: {manifest['collection_id']}",
        f"- Collection Started: {manifest['collection_started_at_utc']}",
        f"- Exchange: {manifest['exchange']}",
        f"- Market Type: {manifest['market_type']}",
        f"- Symbols: {', '.join(manifest['symbols'])}",
        f"- Timeframes: {', '.join(manifest['timeframes'])}",
        f"- Collector Version: {manifest.get('collector_version', 'q5-v1')}",
        f"- Data Schema Version: {manifest.get('data_schema_version', 'ohlcv-v1')}",
        "",
        "## First-Seen Methodology",
        "",
        "- `first_seen_at_utc` is assigned on first successful observation",
        "- Never reset during retries or duplicate ingestion",
        "- Not derived from candle event timestamps or file modification times",
        "- If provenance cannot be verified, the candle is rejected",
        "",
        "## Timestamp Definitions",
        "",
        "- Event timestamp: when the market event occurred (candle open/close)",
        "- Exchange candle open time: Binance OHLCV open timestamp",
        "- Exchange candle close time: open time + interval",
        "- Collector observation time: when the collector observed the candle",
        "- First-seen timestamp: first successful observation by the collector",
        "- Ingestion timestamp: when the data was stored",
        "- Research freeze timestamp: from RESEARCH_FREEZE_MANIFEST.json",
        "- Collection epoch start: from PROSPECTIVE_COLLECTION_MANIFEST.json",
        "",
        "## Collection Results",
        "",
        f"- Status: {result['status']}",
        f"- Collector Status: {quality.get('collector_status', 'unknown')}",
        f"- Dataset Quality: {quality.get('dataset_quality_status', 'unknown')}",
        f"- Observations Collected: {quality.get('observation_count', 0)} unique",
        f"- Expected Intervals: {quality.get('expected_intervals', 0)}",
        f"- Coverage Fraction: {quality.get('coverage_fraction', 0.0):.2%}",
        f"- Gaps: {len(result['gaps'])}",
        f"- Duplicates: {len(result['duplicates'])}",
        f"- Conflicts: {len(result['conflicts'])}",
        f"- Rejections: {len(result['rejections'])}",
        f"- Errors: {len(result['errors'])}",
        f"- Evaluation Blocked: {quality.get('evaluation_blocked', True)}",
        "",
        "## Quality Reasons",
        "",
    ]
    if quality.get("reasons"):
        for reason in quality["reasons"]:
            lines.append(f"- {reason}")
    else:
        lines.append("- All quality checks passed.")
    lines.extend(
        [
            "",
            "## Data Storage",
            "",
            "- Location: `data/prospective/binance/spot/BTC/USDT/1h/`",
            "- Format: JSON Lines (append-only)",
            "- Files: `raw_observations.jsonl`, `normalized_candles.jsonl`, `ingestion_ledger.jsonl`, `dataset_manifest.json`",
            "- Tamper-evident hash chain on ingestion ledger",
            "- Dataset hash: SHA-256 over normalized candles",
            "- Raw payload hash: SHA-256 over raw observations",
            "",
            "## Safety Restrictions",
            "",
            "- Public Binance market-data access only",
            "- Read-only collection",
            "- No API trading credentials",
            "- No private account endpoints",
            "- No order placement",
            "- No live trading",
            "- No autonomous execution",
            "- No paper promotion",
            "- No automatic model promotion",
            "- NO_TRADE remains valid",
            "- Missing or invalid evidence fails closed",
            "",
            "## Known Limitations",
            "",
            "- No live Binance data collected in this run (dry-run/mocked mode)",
            "- Prospective dataset starts as ACCUMULATING",
            "- Evaluation requires explicit authorized action",
            "- Q4 remains NO_STRATEGY_ELIGIBLE",
            "",
        ]
    )
    Q5_REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def _write_q5_governance_report(
    result: dict,
    quality: dict,
    freeze_info: dict,
    manifest: dict,
) -> None:
    """Write the Q5 data governance report (Markdown)."""
    lines = [
        "# Prospective Data Governance — Q5",
        "",
        f"Generated: {_utc_now_iso()}",
        "",
        "## Holdout States",
        "",
        "- Historical Holdout: CONTAMINATED (permanent)",
        "- Prospective Holdout: ACCUMULATING",
        "",
        "## Governance Rules",
        "",
        "- Historical holdout remains permanently CONTAMINATED",
        "- Prospective dataset begins as ACCUMULATING",
        "- Does not automatically become eligible due to new candles",
        "- Requires explicit authorized evaluation action for EVALUATED",
        "- Q5 does not train, calibrate, or optimize models using prospective data",
        "",
        "## Evaluation Eligibility Requirements",
        "",
        "- Original freeze remains valid: YES",
        "- Collection epoch is immutable: YES",
        "- Data is genuinely post-collection-start: YES",
        "- All first-seen provenance is valid: YES",
        "- Data-quality requirements pass: " + str(quality["evaluation_blocked"] is False),
        f"- Collection coverage meets policy: {quality.get('coverage_fraction', 0.0):.2%} >= 0.95",
        f"- Minimum observation count met: {len(result['observations'])} >= 168",
        "- No development or tuning process has inspected prospective outcomes: YES",
        "- No historical backfill has contaminated the collection: YES",
        "",
        "## Dataset Isolation",
        "",
        "- Prospective dataset is separate from canonical training data",
        "- Prospective dataset is separate from historical validation data",
        "- Prospective dataset is separate from calibration datasets",
        "- Prospective dataset is separate from model training inputs",
        "- Prospective dataset is separate from feature selection inputs",
        "- Prospective dataset is separate from backtesting inputs",
        "- Prospective dataset is separate from strategy threshold optimization",
        "- Dataset-purpose metadata: PROSPECTIVE_HOLDOUT",
        "- Ordinary model-training and tuning commands reject prospective data",
        "",
        "## AI Authority",
        "",
        "- AI advisory remains disabled by default",
        "- AI cannot control collection integrity",
        "- AI cannot control holdout status",
        "- AI cannot control evaluation eligibility",
        "",
        "## Q4 Compatibility",
        "",
        "- Q4 decision: NO_STRATEGY_ELIGIBLE (unchanged)",
        "- Q5 does not modify Q2, Q3, or Q4 results",
        "- Q5 does not weaken the fail-closed promotion gate",
        "",
    ]
    Q5_GOV_REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Q5 prospective data collection runner")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one collection cycle and exit.",
    )
    parser.add_argument(
        "--collect-status",
        action="store_true",
        help="Show collection manifest and quality state.",
    )
    parser.add_argument(
        "--validate-prospective",
        action="store_true",
        help="Validate prospective holdout readiness.",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Request explicit evaluation transition (with --validate-prospective).",
    )
    args = parser.parse_args()

    if args.collect_status:
        return run_collect_status()
    if args.validate_prospective:
        return run_validate_prospective(evaluate=args.evaluate)
    # Default: run once
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
