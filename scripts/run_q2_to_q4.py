"""Run the corrected Q2.2.1 → Q3 revised → Q4 revised research build.

This orchestrator runs the barrier-aligned, methodology-corrected pipeline:
  1. Validate/reuse immutable research freeze (never auto-create on rerun)
  2. Q2.2.1 diagnostics (execution-aligned targets, class weighting, collapse/holdout checks,
     real walk-forward fold stability, gross-vs-net accounting)
  3. Q3 revised strategy validation (barrier-aligned simulator, same geometry as Q2.2,
     corrected gross-vs-net fold accounting, cost-adjusted robustness)
  4. Q4 revised research release (fail-closed governance/promotion, integrity verification,
     canonical artifact with freeze identity and holdout state)
  5. Generate final consolidated evidence manifest

It stops on the first failure, passes cost assumptions explicitly to Q3, and
keeps AI off unless ``--ai`` is supplied. It never fetches data, retrains/tunes
production models, changes trading state, or places orders.

Examples:
    uv run python scripts/run_q2_to_q4.py
    uv run python scripts/run_q2_to_q4.py --ai
    uv run python scripts/run_q2_to_q4.py --fee-bp 10 --spread-bp 5 --slippage-bp 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.research.freeze import (  # noqa: E402
    DEFAULT_HISTORY_PATH,
    DEFAULT_MANIFEST_PATH,
    FREEZE_STATUS_MISSING,
    FREEZE_STATUS_VALID,
    FreezeConfigMismatch,
    create_or_reuse_freeze,
    verify_freeze_integrity,
)

DATA_PATH = ROOT / "data/canonical/binance/BTC-USDT/1h/candles.parquet"
OUT_MANIFEST = ROOT / "docs/validation/RESEARCH_EVIDENCE_MANIFEST.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(script: str, args: list[str]) -> None:
    command = [sys.executable, str(ROOT / "scripts" / script), *args]
    print(f"\n=== Running {script} ===", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def _validate_freeze() -> dict[str, Any]:
    """Validate or reuse the immutable research freeze.

    Never auto-creates a new freeze on rerun. If configuration has changed
    since the last freeze, raises FreezeConfigMismatch.

    Returns dict with ``freeze_id``, ``created_at_utc`` (immutable freeze
    timestamp), ``status``, and ``valid``.
    """
    verification = verify_freeze_integrity(DEFAULT_MANIFEST_PATH)
    status = verification.get("status", FREEZE_STATUS_MISSING)

    if status == FREEZE_STATUS_VALID:
        # Reuse existing manifest; never modify or regenerate timestamp.
        print(
            f"Research freeze reused: {verification['freeze_id']} "
            f"created at {verification['created_at_utc']}",
            flush=True,
        )
        return {
            "valid": True,
            "status": status,
            "freeze_id": verification["freeze_id"],
            "created_at_utc": verification["created_at_utc"],
            "commit_sha": verification.get("commit_sha"),
            "checks": verification.get("checks", {}),
        }

    if status == FREEZE_STATUS_MISSING:
        # First authorized freeze: create manifest. The created_at_utc from
        # this call is the immutable freeze timestamp for all future reruns.
        feature_config = {
            "vol_window": 20,
            "entry_convention": "next_bar_open",
            "pt_mult": 2.0,
            "sl_mult": 1.0,
            "max_horizon_bars": 24,
        }
        target_config = {
            "name": "directional_barrier_next_open_v1",
            "pt_mult": 2.0,
            "sl_mult": 1.0,
            "max_horizon_bars": 24,
            "vol_window": 20,
        }
        model_config = {
            "class_weight": "balanced",
            "logistic_C": 1.0,
            "logistic_max_iter": 300,
            "lightgbm_n_estimators": 75,
            "lightgbm_num_leaves": 15,
            "lightgbm_min_child_samples": 30,
        }
        strategy_config = {
            "simulator": "research_triple_barrier_next_open_v1",
            "position_pct": 1.0,
            "starting_equity": 10_000.0,
        }
        cost_config = {
            "fee_bp": 10.0,
            "spread_bp": 5.0,
            "slippage_bp": 5.0,
            "funding_bp_per_bar": 0.0,
        }
        thresholds_config = {
            "min_trades": 100,
            "min_profit_factor": 1.10,
            "min_positive_fold_fraction": 0.75,
            "required_stress_multiplier": 2.0,
            "max_prediction_collapse_share": 0.90,
            "min_folds": 3,
        }

        dataset_hash = _sha256(DATA_PATH) if DATA_PATH.is_file() else None
        manifest = create_or_reuse_freeze(
            manifest_path=DEFAULT_MANIFEST_PATH,
            history_path=DEFAULT_HISTORY_PATH,
            dataset_ids=["binance-BTC-USDT-1h-candles"],
            dataset_hashes={"binance-BTC-USDT-1h-candles": dataset_hash} if dataset_hash else {},
            dataset_start="2024-11-01T00:00:00+00:00",
            dataset_end="2026-11-12T13:00:00+00:00",
            feature_config=feature_config,
            target_config=target_config,
            model_config=model_config,
            strategy_config=strategy_config,
            cost_config=cost_config,
            thresholds_config=thresholds_config,
            config_version="q2.2.1",
        )
        print(
            f"First authorized research freeze created: {manifest['freeze_id']} "
            f"at {manifest['created_at_utc']}",
            flush=True,
        )
        return {
            "valid": True,
            "status": FREEZE_STATUS_VALID,
            "freeze_id": manifest["freeze_id"],
            "created_at_utc": manifest["created_at_utc"],
            "commit_sha": manifest.get("repository_commit_sha"),
            "checks": {},
        }

    # CONFIG_MISMATCH, DATASET_MISMATCH, SCHEMA_INVALID — fail closed.
    raise FreezeConfigMismatch(
        f"Freeze validation failed with status {status}: "
        f"{verification.get('reason', 'unknown reason')}. "
        f"Do not overwrite the original manifest."
    )


def _build_evidence_manifest(
    freeze_info: dict[str, Any],
    q2_path: Path,
    q3_path: Path,
    q4_path: Path,
) -> dict[str, Any]:
    """Build the final consolidated evidence manifest."""
    q2 = json.loads(q2_path.read_text(encoding="utf-8")) if q2_path.is_file() else {}
    q3 = json.loads(q3_path.read_text(encoding="utf-8")) if q3_path.is_file() else {}
    q4 = json.loads(q4_path.read_text(encoding="utf-8")) if q4_path.is_file() else {}
    q4_status = q4.get("research_status", {})

    return {
        "schema": "signalforge.research-evidence-manifest.v1",
        "phase": "Q2.2.1-Q3-Q4",
        "research_freeze_timestamp": freeze_info.get("created_at_utc"),
        "generated_at": _utc_now(),
        "research_freeze": {
            "status": freeze_info.get("status", "MISSING"),
            "freeze_id": freeze_info.get("freeze_id"),
            "created_at_utc": freeze_info.get("created_at_utc"),
            "commit_sha": freeze_info.get("commit_sha"),
            "valid": freeze_info.get("valid", False),
            "checks": freeze_info.get("checks", {}),
        },
        "source_artifacts": {
            "q2_2": {
                "path": str(q2_path),
                "sha256": _sha256(q2_path) if q2_path.is_file() else None,
                "phase": q2.get("phase"),
                "conclusion": q2.get("conclusion"),
            },
            "q3_revised": {
                "path": str(q3_path),
                "sha256": _sha256(q3_path) if q3_path.is_file() else None,
                "phase": q3.get("phase"),
            },
            "q4_revised": {
                "path": str(q4_path),
                "sha256": _sha256(q4_path) if q4_path.is_file() else None,
                "phase": q4.get("phase"),
                "verdict": q4_status.get("overall_verdict"),
                "integrity_ok": q4_status.get("integrity_ok"),
                "promotion_eligible": q4_status.get("promotion_eligible"),
            },
        },
        "dataset": {
            "path": str(DATA_PATH),
            "sha256": _sha256(DATA_PATH) if DATA_PATH.is_file() else None,
        },
        "holdout": {
            "status": q3.get("historical_holdout_integrity", {}).get("status", "unknown"),
            "permanently_contaminated": True,
            "prospective_validated": q4_status.get("prospective_holdout_validated", False),
        },
        "integrity": {
            "artifact_integrity_ok": q4_status.get("artifact_integrity_ok", False),
            "accounting_integrity_ok": q4_status.get("accounting_integrity_ok", False),
            "predictive_evidence_integrity_ok": q4_status.get(
                "predictive_evidence_integrity_ok", False
            ),
            "strategy_evidence_integrity_ok": q4_status.get(
                "strategy_evidence_integrity_ok", False
            ),
            "freeze_integrity_ok": q4_status.get("freeze_integrity_ok", False),
            "holdout_integrity_ok": q4_status.get("holdout_integrity_ok", False),
        },
        "authority": {
            "ai_can_select_strategy": False,
            "ai_can_override_risk": False,
            "ai_can_promote": False,
            "human_approval_required": True,
            "human_authorized": False,
            "paper_trading_authorized": False,
            "order_execution_authorized": False,
        },
        "overall_verdict": q4_status.get("overall_verdict", "NO_STRATEGY_ELIGIBLE"),
        "integrity_ok": q4_status.get("integrity_ok", False),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ai", action="store_true", help="Enable optional Q4 advisory summarization"
    )
    parser.add_argument("--fee-bp", type=float, default=10.0, help="Fee in bp per side")
    parser.add_argument("--spread-bp", type=float, default=5.0, help="Round-trip spread in bp")
    parser.add_argument("--slippage-bp", type=float, default=5.0, help="Slippage in bp per side")
    parser.add_argument(
        "--funding-bp-per-bar",
        type=float,
        default=0.0,
        help="Funding assumption in bp per held bar",
    )
    args = parser.parse_args()

    # Step 1: Validate/reuse immutable research freeze (never auto-create on rerun)
    freeze_info = _validate_freeze()

    # Step 2: Q2.2.1 corrected research diagnostics
    _run("q2_2_methodology_correction.py", [])

    # Step 3: Q3 corrected economic accounting and fold validation
    _run(
        "q3_strategy_validation_revised.py",
        [
            "--fee-bp",
            str(args.fee_bp),
            "--spread-bp",
            str(args.spread_bp),
            "--slippage-bp",
            str(args.slippage_bp),
            "--funding-bp-per-bar",
            str(args.funding_bp_per_bar),
        ],
    )

    # Step 4: Q4 revised fail-closed release
    _run("q4_research_release_revised.py", ["--ai"] if args.ai else [])

    # Step 5: Generate final consolidated evidence manifest
    evidence = _build_evidence_manifest(
        freeze_info,
        ROOT / "docs/validation/results/quantitative_methodology_correction_q2_2.json",
        ROOT / "docs/validation/results/strategy_validation_q3_revised.json",
        ROOT / "docs/validation/results/quantitative_release_q4.json",
    )
    OUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    OUT_MANIFEST.write_text(
        json.dumps(evidence, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(f"\nWrote {OUT_MANIFEST}")

    verdict = evidence["overall_verdict"]
    integrity = evidence["integrity_ok"]
    print(
        f"\nQ2.2.1 -> Q3 revised -> Q4 revised finished. "
        f"Verdict: {verdict}; Integrity OK: {integrity}. "
        f"No model, paper or execution authority was granted."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
