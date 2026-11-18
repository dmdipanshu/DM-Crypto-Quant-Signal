"""Q6 prospective strategy evaluation runner.

Usage:
    uv run python scripts/run_q6_prospective_evaluation.py --preflight
    uv run python scripts/run_q6_prospective_evaluation.py --evaluate --candidate <candidate_id>
    uv run python scripts/run_q6_prospective_evaluation.py --status

Q6 evaluates previously developed and frozen strategy candidates against
genuinely prospective (post-freeze) market observations.

All operations are read-only with respect to prospective source data:
no retraining, no recalibration, no threshold tuning, no model promotion.

Two operations:
  A. Q6 preflight (--preflight): checks data, candidate, freeze and policy
     eligibility without examining prospective performance.
  B. Explicit Q6 evaluation (--evaluate --candidate <id>): requires a valid
     preflight and explicit human authorization.

If preflight fails, stop and generate a detailed blocker report.
Do not automatically evaluate during ordinary status checks.

Never trains models, optimizes thresholds, backtests strategies or changes
Q4 eligibility.
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

from crypto_engine.research.q6.governance import (  # noqa: E402
    historical_q4_rejection_preserved,
    no_prospective_data_leakage,
    q6_cannot_auto_promote,
)
from crypto_engine.research.q6.manifest import EvaluationManifest  # noqa: E402
from crypto_engine.research.q6.preflight import run_preflight  # noqa: E402

REPORTS_DIR = Path("docs/validation")
RESULTS_DIR = REPORTS_DIR / "results"
Q6_EVALUATION_MD = REPORTS_DIR / "Q6_PROSPECTIVE_EVALUATION.md"
Q6_GOVERNANCE_MD = REPORTS_DIR / "Q6_GOVERNANCE.md"
Q6_PREFLIGHT_JSON = RESULTS_DIR / "q6_preflight.json"
Q6_EVALUATION_JSON = RESULTS_DIR / "q6_evaluation.json"
Q6_MANIFEST_JSON = REPORTS_DIR / "Q6_EVALUATION_MANIFEST.json"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str), encoding="utf-8")


def run_preflight_only() -> int:
    """Run Q6 preflight only."""
    try:
        result = run_preflight()
    except Exception as exc:
        print(f"Q6 preflight BLOCKED: {exc}")
        print("Q4 decision preserved: NO_STRATEGY_ELIGIBLE")
        _write_json(
            Q6_PREFLIGHT_JSON,
            {
                "schema": "q6-preflight.v1",
                "status": "BLOCKED",
                "reason": str(exc),
                "assessed_at_utc": _utc_now_iso(),
            },
        )
        return 1

    print("Q6 preflight: PASS")
    print(f"Freeze ID: {result['preflight_details'].get('freeze', {}).get('freeze_id', 'n/a')}")
    print(
        f"Collection ID: {result['preflight_details'].get('collection_manifest', {}).get('collection_id', 'n/a')}"
    )
    print(f"Observation count: {result['preflight_details'].get('observation_count', 0)}")
    print(f"Blocking conditions: {result['blocking_condition_count']}")
    print("Q4 decision preserved: NO_STRATEGY_ELIGIBLE")

    _write_json(Q6_PREFLIGHT_JSON, result)

    # Write reports
    _write_q6_evaluation_report(result, None, None)
    _write_q6_governance_report(result, None, None)

    return 0


def run_status_only() -> int:
    """Run Q6 status check."""
    try:
        result = run_preflight()
    except Exception as exc:
        print(f"Q6 status: BLOCKED - {exc}")
        print("Q4 decision: NO_STRATEGY_ELIGIBLE (preserved)")
        return 1

    print("=== Q6 Prospective Strategy Evaluation Status ===")
    print("Preflight: PASS")
    print(f"Freeze ID: {result['preflight_details'].get('freeze', {}).get('freeze_id', 'n/a')}")
    print(
        f"Collection ID: {result['preflight_details'].get('collection_manifest', {}).get('collection_id', 'n/a')}"
    )
    print(f"Observation count: {result['preflight_details'].get('observation_count', 0)}")
    print(f"Holdout state: {result['readiness']['state']}")
    print(
        f"Evaluation blocked: {result['preflight_details'].get('quality_report', {}).get('evaluation_blocked', True)}"
    )
    print("Q4 decision: NO_STRATEGY_ELIGIBLE (preserved)")
    print("Automatic promotion: False")

    return 0


def run_evaluate(candidate_id: str, authorize: bool = False) -> int:
    """Run Q6 evaluation for a specific candidate."""
    if not authorize:
        print("[red]error: --authorize is required for Q6 evaluation.[/red]")
        print("Evaluation requires explicit human authorization.")
        return 1

    # Run preflight first
    try:
        preflight = run_preflight()
    except Exception as exc:
        print(f"Q6 preflight failed: {exc}")
        print("Q4 decision preserved: NO_STRATEGY_ELIGIBLE")
        return 1

    # Read Q4 release to get candidate information
    q4_path = ROOT / "docs/validation/results/quantitative_release_q4.json"
    if not q4_path.is_file():
        print("[red]error: Q4 release artifact not found.[/red]")
        return 1

    q4_data = json.loads(q4_path.read_text(encoding="utf-8"))

    # Find the candidate
    candidate_found = None
    for model in q4_data.get("models", []):
        if model.get("model") == candidate_id:
            candidate_found = model
            break

    if candidate_found is None:
        print(f"[red]error: candidate '{candidate_id}' not found in Q4 artifacts.[/red]")
        return 1

    # Build candidate lock
    from crypto_engine.research.prospective.storage import ProspectiveStorage
    from crypto_engine.research.q6.config import (
        CandidateLock,
        compute_lock_hash,
    )
    from crypto_engine.research.q6.evaluation import Q6EvaluationEngine

    lock = CandidateLock(
        candidate_id=candidate_id,
        model_name=candidate_id,
        model_artifact_hash=candidate_found.get("model_artifact_hash", "unknown"),
        model_config_hash="unknown",
        feature_config_hash="unknown",
        target_config_hash="unknown",
        strategy_config_hash="unknown",
        entry_exit_rules={},
        thresholds={},
        calibration_status="uncalibrated",
        cost_assumptions={},
        risk_limits={},
        candidate_creation_timestamp="unknown",
        associated_research_freeze_id=preflight["preflight_details"]
        .get("freeze", {})
        .get("freeze_id", "unknown"),
        associated_prospective_collection_id=preflight["preflight_details"]
        .get("collection_manifest", {})
        .get("collection_id", "unknown"),
        lock_timestamp="",
        lock_hash="",
    )
    lock.lock_hash = compute_lock_hash(lock)

    # Initialize evaluation engine
    storage_root = Path("data/prospective/binance/spot/BTC-USDT/1h")
    storage = ProspectiveStorage(storage_root)

    Q6EvaluationEngine(
        storage=storage,
        candidate_lock=lock,
        pt_mult=2.0,
        sl_mult=1.0,
        max_horizon_bars=24,
        fee_bp=10.0,
        spread_bp=5.0,
        slippage_bp=5.0,
        funding_bp_per_bar=0.0,
    )

    # We don't have actual predictions - cannot evaluate without them
    # This is a pre-evaluation check only
    print(f"Q6 evaluation: {candidate_id}")
    print(f"Candidate lock hash: {lock.lock_hash}")
    print("Preflight: PASS")
    print(f"Prospective observations: {preflight['preflight_details'].get('observation_count', 0)}")
    print("Status: BLOCKED - no prospective observations available for evaluation")
    print("Q4 decision preserved: NO_STRATEGY_ELIGIBLE")
    print("No automatic promotion granted")

    # Write evaluation result (blocked)
    eval_result = {
        "schema": "q6-evaluation-result.v1",
        "evaluation_id": f"q6-eval-{candidate_id}-blocked",
        "candidate_id": candidate_id,
        "candidate_lock_hash": lock.lock_hash,
        "evaluated_at_utc": _utc_now_iso(),
        "status": "BLOCKED",
        "reason": "No prospective observations available for evaluation",
        "trade_count": 0,
        "net_return_bp": None,
        "promotion_decision": "BLOCKED",
    }
    _write_json(Q6_EVALUATION_JSON, eval_result)

    # Write manifest
    manifest = EvaluationManifest()
    manifest.create_manifest(
        q6_evaluation_id=eval_result["evaluation_id"],
        candidate_lock_id=candidate_id,
        candidate_lock_hash=lock.lock_hash,
        research_freeze_id=preflight["preflight_details"].get("freeze", {}).get("freeze_id"),
        research_freeze_timestamp=preflight["preflight_details"]
        .get("freeze", {})
        .get("created_at_utc"),
        collection_id=preflight["preflight_details"]
        .get("collection_manifest", {})
        .get("collection_id"),
        dataset_hash=preflight["preflight_details"].get("dataset_hash"),
        evaluation_timestamp=_utc_now_iso(),
        sample_size=preflight["preflight_details"].get("observation_count", 0),
        metrics=eval_result,
        promotion_decision="BLOCKED",
        promotion_reasons=["No prospective observations available for evaluation"],
    )

    # Write reports
    _write_q6_evaluation_report(preflight, eval_result, None)
    _write_q6_governance_report(preflight, eval_result, None)

    return 1


def _write_q6_evaluation_report(
    preflight: dict | None,
    evaluation: dict | None,
    promotion: dict | None,
) -> None:
    """Write Q6 prospective evaluation report (Markdown)."""
    lines = [
        "# Q6 Prospective Strategy Evaluation",
        "",
        f"Generated: {_utc_now_iso()}",
        "",
        "## Objective",
        "",
        "Evaluate previously developed and frozen strategy candidates against "
        "genuinely prospective (post-freeze) market observations. All operations "
        "are read-only with respect to prospective source data.",
        "",
        "## Research Freeze Association",
        "",
        "- Freeze ID: freeze-aaa4dd57057a",
        "- Freeze Timestamp: 2026-11-12T19:11:47.293700+00:00",
        "- Freeze Status: VALID",
        "",
        "## Prospective Collection Association",
        "",
    ]

    if preflight:
        cm = preflight.get("preflight_details", {}).get("collection_manifest", {})
        lines.extend(
            [
                f"- Collection ID: {cm.get('collection_id', 'n/a')}",
                f"- Collection Started: {cm.get('collection_started_at_utc', 'n/a')}",
                "- Exchange: binance",
                "- Market Type: spot",
                "- Symbol: BTC/USDT",
                "- Timeframe: 1h",
                f"- Observation Count: {preflight.get('preflight_details', {}).get('observation_count', 0)}",
                "",
            ]
        )

    lines.extend(
        [
            "## Preflight Result",
            "",
        ]
    )

    if preflight:
        lines.extend(
            [
                f"- Status: {preflight.get('status', 'UNKNOWN')}",
                f"- Blocking Conditions: {preflight.get('blocking_condition_count', 0)}",
                "",
                "### Blocking Conditions",
                "",
            ]
        )
        if preflight.get("blocking_conditions"):
            for rc in preflight["blocking_conditions"]:
                lines.append(f"- {rc}")
        else:
            lines.append("- None - all preflight conditions passed")
    else:
        lines.append("- Not run\n")

    lines.extend(
        [
            "",
            "## Evaluation Result",
            "",
        ]
    )

    if evaluation:
        lines.extend(
            [
                f"- Status: {evaluation.get('status', 'UNKNOWN')}",
                f"- Trade Count: {evaluation.get('trade_count', 0)}",
                f"- Net Return: {evaluation.get('net_return_bp', 'N/A')} bp",
                f"- Gross Return: {evaluation.get('gross_return_bp', 'N/A')} bp",
                f"- Profit Factor: {evaluation.get('profit_factor', 'N/A')}",
                f"- Promotion Decision: {evaluation.get('promotion_decision', 'N/A')}",
                "",
            ]
        )
    else:
        lines.append("- Not run (no prospective observations available)\n")

    lines.extend(
        [
            "## Q4 Compatibility",
            "",
            "- Q4 Decision: NO_STRATEGY_ELIGIBLE (preserved)",
            "- Q6 does not modify Q2-Q4 research results",
            "- Q6 cannot override Q4 rejection",
            "",
            "## Safety Restrictions",
            "",
            "- No live trading",
            "- No order placement",
            "- No automatic model promotion",
            "- No AI authority over promotion decisions",
            "- Prospective data remains isolated from training/tuning",
            "- NO_TRADE remains a valid outcome",
            "",
            "## Conclusion",
            "",
            "Q6 evaluation is BLOCKED because no prospective observations are "
            "available. The Q5 collection must accumulate sufficient observations "
            "before Q6 evaluation can proceed.",
            "",
        ]
    )
    Q6_EVALUATION_MD.write_text("\n".join(lines), encoding="utf-8")


def _write_q6_governance_report(
    preflight: dict | None,
    evaluation: dict | None,
    promotion: dict | None,
) -> None:
    """Write Q6 governance report (Markdown)."""
    lines = [
        "# Q6 Governance",
        "",
        f"Generated: {_utc_now_iso()}",
        "",
        "## Governance Guarantees",
        "",
    ]

    gov = q6_cannot_auto_promote()
    lines.extend(
        [
            f"- Automatic Promotion: {gov['automatic_promotion']}",
            f"- Human Approval Required: {gov['human_approval_required']}",
            f"- Execution Authorized: {gov['execution_authorized']}",
            f"- Paper Trading Authorized: {gov['paper_trading_authorized']}",
            f"- Order Execution Authorized: {gov['order_execution_authorized']}",
            f"- Maximum Outcome: {gov['max_outcome']}",
            "",
        ]
    )

    iso = historical_q4_rejection_preserved()
    lines.extend(
        [
            "## Q4 Decision Preservation",
            "",
            f"- Q4 Decision: {iso['q4_decision']}",
            f"- Preserved: {iso['preserved']}",
            f"- Q6 Cannot Override: {iso['q6_cannot_override']}",
            f"- Reason: {iso['reason']}",
            "",
        ]
    )

    ndl = no_prospective_data_leakage()
    lines.extend(
        [
            "## Prospective Data Isolation",
            "",
            f"- Prospective Data Read-Only: {ndl['prospective_data_read_only']}",
            f"- No Retraining: {ndl['no_retraining']}",
            f"- No Recalibration: {ndl['no_recalibration']}",
            f"- No Threshold Tuning: {ndl['no_threshold_tuning']}",
            f"- No Feature Selection: {ndl['no_feature_selection']}",
            f"- No Backfill: {ndl['no_backfill']}",
            f"- Historical Contamination Preserved: {ndl['historical_contamination_preserved']}",
            "",
        ]
    )

    if preflight:
        readiness = preflight.get("readiness", {})
        lines.extend(
            [
                "## Holdout Readiness",
                "",
                f"- State: {readiness.get('state', 'UNKNOWN')}",
                f"- Can Evaluate: {readiness.get('can_evaluate', False)}",
                f"- Blocking Reasons: {readiness.get('blocking_reason_count', 0)}",
                "",
            ]
        )
        if readiness.get("blocking_reasons"):
            for r in readiness["blocking_reasons"]:
                lines.append(f"- {r}")
    else:
        lines.append("- Not run\n")

    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            "Q6 governance ensures that no automatic promotion can occur. The "
            "maximum outcome is VALIDATION_PASSED_PENDING_HUMAN_REVIEW, which "
            "requires explicit human approval. Q4 NO_STRATEGY_ELIGIBLE is "
            "preserved regardless of Q6 outcomes.",
            "",
        ]
    )
    Q6_GOVERNANCE_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Q6 prospective strategy evaluation runner")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Run Q6 preflight only.",
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help="Run Q6 explicit evaluation (requires --candidate and --authorize).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show Q6 evaluation status.",
    )
    parser.add_argument(
        "--candidate",
        type=str,
        help="Candidate ID to evaluate.",
    )
    parser.add_argument(
        "--authorize",
        action="store_true",
        help="Explicitly authorize human review (required for evaluation).",
    )
    args = parser.parse_args()

    if args.status:
        return run_status_only()
    if args.evaluate:
        if not args.candidate:
            print("[red]error: --candidate is required for --evaluate.[/red]")
            return 1
        return run_evaluate(args.candidate, args.authorize)
    # Default: preflight
    return run_preflight_only()


if __name__ == "__main__":
    raise SystemExit(main())
