"""Q4 revised — deterministic research release from corrected Q2.2.1/Q3 artifacts.

Consumes barrier-aligned methodology-correction diagnostics (Q2.2.1) and
strategy-validation results (Q3 revised), applies fail-closed promotion
gates with comprehensive integrity verification, and emits the canonical
Q4 research-release artifact. AI is disabled by default and may only
summarize allowlisted evidence; it cannot select, promote, or authorize
any strategy.

Cross-phase evidence integration (BUG 1 fix):
  Q2 provides predictive fold_results (accuracy, balanced_accuracy, macro_f1, MCC,
  per-class metrics, ROC-AUC, prediction collapse).
  Q3 provides strategy fold results (gross/net returns, costs, trade counts,
  cost stress) and fold boundaries.
  Q4 independently consumes both, matching by model ID and fold ID, and validates
  that Q2 and Q3 fold boundaries are compatible. Predictive metrics are never
  mislabeled as economic metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.ai.advisory.research import summarize_research  # noqa: E402
from crypto_engine.research.freeze import (  # noqa: E402
    DEFAULT_HISTORY_PATH,
    DEFAULT_MANIFEST_PATH,
    FREEZE_STATUS_MISSING,
    FREEZE_STATUS_VALID,
    _load_json,
    verify_freeze_integrity,
)
from crypto_engine.research.governance import (  # noqa: E402
    PromotionThresholds,
    assess_holdout_integrity,
    evaluate_promotion_eligibility,
)

Q2_PATH = ROOT / "docs/validation/results/quantitative_methodology_correction_q2_2.json"
Q3_PATH = ROOT / "docs/validation/results/strategy_validation_q3_revised.json"
DEFAULT_OUT_JSON = ROOT / "docs/validation/results/quantitative_release_q4.json"
OUT_JSON = DEFAULT_OUT_JSON
OUT_MD = ROOT / "docs/validation/QUANTITATIVE_RELEASE_Q4.md"
FREEZE_MANIFEST_PATH = DEFAULT_MANIFEST_PATH
FREEZE_HISTORY_PATH = DEFAULT_HISTORY_PATH


def _output_json_path() -> Path:
    if OUT_JSON != DEFAULT_OUT_JSON:
        return OUT_JSON
    from crypto_engine.config.settings import load_settings

    configured = Path(load_settings().api.research_release_path)
    return configured if configured.is_absolute() else ROOT / configured


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Required source artifact missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Source artifact must contain a JSON object: {path}")
    return payload


def _artifact_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return path.name


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_gateway():
    from crypto_engine.ai.composition import build_configured_gateway
    from crypto_engine.ai.env_loader import load_env
    from crypto_engine.config.settings import load_settings

    load_env()
    settings = load_settings()
    if not settings.ai.enabled or not settings.ai.providers:
        return None
    return build_configured_gateway(settings.ai)


# ---------------------------------------------------------------------------
# BUG 1: Q2/Q3 cross-phase evidence integration helpers
# ---------------------------------------------------------------------------


def _build_q2_model_lookup(q2: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build a lookup from model name to Q2 model record with predictive data.

    Q2 stores models as a dict keyed by model name. Each model record contains
    ``fold_results`` (predictive metrics), ``aggregate``, ``collapse``,
    ``stability``, and other predictive evidence.
    """
    models = q2.get("models", {})
    if not isinstance(models, dict):
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for name, record in models.items():
        if not isinstance(record, dict):
            continue
        lookup[str(name)] = record
    return lookup


def _build_q3_model_lookup(q3: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build a lookup from model name to Q3 model record with strategy data.

    Q3 stores models as a list. Each record contains ``strategy`` (economic
    metrics), ``fold_strategy_results`` (per-fold economic results),
    ``folds`` (fold boundaries), ``collapse``, ``cost_adjusted_robustness``.
    """
    models = q3.get("models", [])
    if not isinstance(models, list):
        return {}
    lookup: dict[str, dict[str, Any]] = {}
    for record in models:
        if not isinstance(record, dict):
            continue
        name = str(record.get("model", "unknown"))
        lookup[name] = record
    return lookup


def _validate_fold_boundary_compatibility(
    q2_model_record: dict[str, Any],
    q3_model_record: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Validate that Q2 and Q3 fold boundaries are compatible.

    Q2 fold_results contain train/validation boundaries.
    Q3 folds contain train/validation boundaries.
    The fold IDs must match and the boundaries must be consistent.
    """
    reasons: list[str] = []

    q2_fold_results = q2_model_record.get("fold_results", [])
    q3_folds = q3_model_record.get("folds", [])

    if not isinstance(q2_fold_results, list) or not isinstance(q3_folds, list):
        reasons.append("Missing fold_results or folds for boundary validation.")
        return False, reasons

    q2_fold_ids = {int(fr.get("fold", -1)) for fr in q2_fold_results if isinstance(fr, dict)}
    q3_fold_ids = {int(f.get("fold", -1)) for f in q3_folds if isinstance(f, dict)}

    if q2_fold_ids != q3_fold_ids:
        reasons.append(
            f"Fold ID mismatch: Q2 has {sorted(q2_fold_ids)}, Q3 has {sorted(q3_fold_ids)}."
        )
        return False, reasons

    # Validate per-fold boundary compatibility
    q2_by_fold = {int(fr.get("fold", -1)): fr for fr in q2_fold_results if isinstance(fr, dict)}
    q3_by_fold = {int(f.get("fold", -1)): f for f in q3_folds if isinstance(f, dict)}

    for fold_id in q2_fold_ids & q3_fold_ids:
        q2_fr = q2_by_fold.get(fold_id, {})
        q3_fr = q3_by_fold.get(fold_id, {})

        q2_val_start = str(q2_fr.get("validation_start", ""))
        q2_val_end = str(q2_fr.get("validation_end", ""))
        q3_val_start = str(q3_fr.get("validation_start", ""))
        q3_val_end = str(q3_fr.get("validation_end", ""))

        if q2_val_start and q3_val_start and q2_val_start != q3_val_start:
            reasons.append(
                f"Fold {fold_id}: validation_start mismatch Q2={q2_val_start} vs Q3={q3_val_start}."
            )
        if q2_val_end and q3_val_end and q2_val_end != q3_val_end:
            reasons.append(
                f"Fold {fold_id}: validation_end mismatch Q2={q2_val_end} vs Q3={q3_val_end}."
            )

    return len(reasons) == 0, reasons


def _merge_model_evidence(
    model_name: str,
    q2_model_record: dict[str, Any] | None,
    q3_model_record: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Merge Q2 predictive evidence with Q3 strategy evidence for one model.

    Returns (merged_record, integrity_flags, reasons).
    Predictive metrics come exclusively from Q2; economic metrics from Q3.
    They are never mixed or mislabeled.
    """
    reasons: list[str] = []
    flags: dict[str, Any] = {
        "predictive_evidence_integrity_ok": False,
        "strategy_evidence_integrity_ok": False,
        "accounting_integrity_ok": False,
        "artifact_integrity_ok": False,
    }

    q3_record = q3_model_record if isinstance(q3_model_record, dict) else {}
    strategy = q3_record.get("strategy", {})
    strategy = strategy if isinstance(strategy, dict) else {}

    # --- Strategy (economic) evidence from Q3 ---
    fold_strategy_results = q3_record.get("fold_strategy_results", [])
    if not isinstance(fold_strategy_results, list):
        fold_strategy_results = []
    folds = q3_record.get("folds", [])
    if not isinstance(folds, list):
        folds = []

    economic_ok = True
    economic_reasons: list[str] = []

    # Gross/net reconciliation
    gross = strategy.get("gross_return_bp")
    net = strategy.get("total_net_return_bp")
    costs = strategy.get("total_trading_costs_bp", strategy.get("costs_total_bp"))
    if gross is None or net is None:
        economic_ok = False
        economic_reasons.append("Missing gross_return_bp or total_net_return_bp.")
    if costs is not None and gross is not None and net is not None:
        expected_net = float(gross) - float(costs)
        if abs(expected_net - float(net)) > 1e-6:
            economic_ok = False
            economic_reasons.append(f"Gross/net reconciliation failed for {model_name}.")

    # Per-fold economic accounting
    for fr in fold_strategy_results:
        if not isinstance(fr, dict):
            continue
        if "gross_return_bp" not in fr:
            economic_ok = False
            economic_reasons.append(f"Fold {fr.get('fold', '?')} missing gross_return_bp.")
        if "net_return_bp" not in fr:
            economic_ok = False
            economic_reasons.append(f"Fold {fr.get('fold', '?')} missing net_return_bp.")
        # Only claim fields that exist; do not invent PF/drawdown/cost details.
        for optional_key in ("profit_factor", "max_drawdown", "total_trading_costs_bp"):
            if optional_key not in fr:
                # Not an error — optional fields may not have been calculated.
                pass

    flags["strategy_evidence_integrity_ok"] = economic_ok and len(fold_strategy_results) > 0
    flags["accounting_integrity_ok"] = economic_ok

    # --- Predictive evidence from Q2 ---
    q2_record = q2_model_record if isinstance(q2_model_record, dict) else {}
    q2_fold_results = q2_record.get("fold_results", [])
    if not isinstance(q2_fold_results, list):
        q2_fold_results = []

    predictive_ok = True
    predictive_reasons: list[str] = []

    if not q2_fold_results:
        predictive_ok = False
        predictive_reasons.append(f"No predictive fold_results found for model {model_name} in Q2.")
    else:
        required_keys = {"accuracy", "balanced_accuracy", "macro_f1", "mcc"}
        for fr in q2_fold_results:
            if not isinstance(fr, dict):
                continue
            missing = required_keys - set(fr.keys())
            if missing:
                predictive_ok = False
                predictive_reasons.append(
                    f"Fold {fr.get('fold', '?')} missing predictive metrics: {sorted(missing)}."
                )

    flags["predictive_evidence_integrity_ok"] = predictive_ok

    # --- Fold boundary compatibility ---
    boundary_ok = True
    boundary_reasons: list[str] = []
    if q2_model_record is not None and q3_model_record is not None:
        boundary_ok, boundary_reasons = _validate_fold_boundary_compatibility(
            q2_model_record, q3_model_record
        )

    # --- Merge into single record ---
    merged: dict[str, Any] = {
        "model": model_name,
        "trade_count": int(strategy.get("trade_count", 0)),
        "gross_return_bp": strategy.get("gross_return_bp"),
        "net_return_bp": strategy.get("total_net_return_bp"),
        "total_trading_costs_bp": costs,
        "profit_factor": strategy.get("net_profit_factor") or strategy.get("profit_factor"),
        "gross_profit_factor": strategy.get("gross_profit_factor"),
        "win_rate": strategy.get("win_rate"),
        "maximum_drawdown": strategy.get("maximum_drawdown"),
        "exposure": strategy.get("exposure_fraction"),
        "cost_stress": strategy.get("cost_stress", []),
        "fold_strategy_results": fold_strategy_results,
        "folds": folds,
        "q2_predictive_fold_results": q2_fold_results,
        "q2_predictive_aggregate": q2_record.get("aggregate", {}),
        "q2_stability": q2_record.get("stability", {}),
        "q2_collapse": q2_record.get("collapse", {}),
        "q3_cost_adjusted_robustness": q3_record.get("cost_adjusted_robustness", {}),
        "boundary_validation_ok": boundary_ok,
        "boundary_validation_reasons": boundary_reasons,
    }

    flags["artifact_integrity_ok"] = (
        economic_ok
        and predictive_ok
        and boundary_ok
        and len(fold_strategy_results) > 0
        and len(q2_fold_results) > 0
    )

    reasons.extend(economic_reasons + predictive_reasons + boundary_reasons)
    return merged, flags, reasons


# ---------------------------------------------------------------------------
# Q4 release build
# ---------------------------------------------------------------------------


def build_release(*, ai_enabled: bool = False) -> dict[str, Any]:
    q2 = _load(Q2_PATH)
    q3 = _load(Q3_PATH)
    if q2.get("phase") != "Q2.2":
        raise ValueError("Q4 revised requires Q2.2 methodology-correction artifact.")
    if q3.get("phase") != "Q3-revised":
        raise ValueError("Q4 revised requires Q3-revised strategy-validation artifact.")

    # --- BUG 2: Load immutable freeze manifest; propagate its timestamp ---
    freeze_manifest = _load_json(FREEZE_MANIFEST_PATH)
    if freeze_manifest is None:
        freeze_status = FREEZE_STATUS_MISSING
        freeze_reason = "manifest_not_found"
        freeze_id = None
        freeze_created_at = None
        freeze_commit_sha = None
        freeze_schema_version = None
        freeze_checks = {}
    else:
        freeze_check_result = verify_freeze_integrity(
            FREEZE_MANIFEST_PATH,
            expected_config_hashes=None,
            expected_dataset_hash=None,
            expected_dataset_id=None,
        )
        freeze_status = freeze_check_result["status"]
        freeze_reason = freeze_check_result.get("reason")
        freeze_id = freeze_check_result.get("freeze_id")
        freeze_created_at = freeze_check_result.get("created_at_utc")
        freeze_commit_sha = freeze_check_result.get("commit_sha")
        freeze_schema_version = freeze_check_result.get("schema_version")
        freeze_checks = freeze_check_result.get("checks", {})

    freeze_integrity_ok = freeze_status == FREEZE_STATUS_VALID

    # --- Holdout integrity (use freeze timestamp from manifest) ---
    freeze_ts_for_holdout = freeze_created_at
    historical_integrity = q3.get("historical_holdout_integrity")
    if not isinstance(historical_integrity, dict):
        historical_integrity = assess_holdout_integrity(
            holdout_start=None,
            training_end=None,
            research_freeze_timestamp=freeze_ts_for_holdout,
            access_count=1,
            model_frozen=False,
            parameters_frozen=False,
        )
    # Historical holdout is permanently contaminated — never allow it to become
    # pristine due to a later pipeline run.
    historical_integrity = {**historical_integrity, "pristine": False}

    q2_holdout = q2.get("holdout", {}) if isinstance(q2.get("holdout"), dict) else {}
    prospective = (
        q2_holdout.get("prospective", {}) if isinstance(q2_holdout.get("prospective"), dict) else {}
    )
    prospective_validated = bool(
        prospective.get("status") == "pristine"
        and prospective.get("evaluation_status") == "assessed_pristine"
        and prospective.get("eligible_for_prospective_validation") is True
    )
    holdout_integrity_ok = (
        historical_integrity.get("status") != "pristine"
        and historical_integrity.get("evaluation_status") != "assessed_pristine"
        and not prospective_validated
    )

    # --- Cross-phase model evidence integration ---
    q2_lookup = _build_q2_model_lookup(q2)
    q3_lookup = _build_q3_model_lookup(q3)

    q3_dataset = q3.get("dataset", {})
    q3_target = q3.get("target", {})
    gateway = _optional_gateway() if ai_enabled else None

    models: list[dict[str, Any]] = []
    all_model_integrity_ok = True
    all_integrity_reasons: list[str] = []

    for model_name, q3_record in q3_lookup.items():
        q2_record = q2_lookup.get(model_name)
        merged, flags, reasons = _merge_model_evidence(model_name, q2_record, q3_record)

        model_integrity_ok = all(
            [
                flags["predictive_evidence_integrity_ok"],
                flags["strategy_evidence_integrity_ok"],
                flags["accounting_integrity_ok"],
                merged["boundary_validation_ok"],
            ]
        )
        all_model_integrity_ok = all_model_integrity_ok and model_integrity_ok
        all_integrity_reasons.extend(reasons)

        fold_returns = [
            float(row.get("net_return_bp", 0.0))
            for row in merged["fold_strategy_results"]
            if isinstance(row, dict)
        ]
        n_folds = len([f for f in merged["folds"] if isinstance(f, dict)])

        gate = evaluate_promotion_eligibility(
            strategy_metrics={
                "trade_count": merged["trade_count"],
                "profit_factor": merged["profit_factor"],
                "cost_stress": merged["cost_stress"],
            },
            fold_net_returns_bp=fold_returns,
            holdout_integrity=historical_integrity,
            prospective_holdout_validated=prospective_validated,
            thresholds=PromotionThresholds(),
            prediction_collapse=merged["q2_collapse"]
            if isinstance(merged["q2_collapse"], dict)
            else None,
            n_folds=n_folds,
        )
        candidate_advisory = summarize_research(
            _candidate_advisory_metrics(merged, q3_dataset, q3_target),
            gateway=gateway,
        )
        models.append(
            {
                "model": model_name,
                "trade_count": merged["trade_count"],
                "gross_return_bp": merged["gross_return_bp"],
                "net_return_bp": merged["net_return_bp"],
                "total_trading_costs_bp": merged["total_trading_costs_bp"],
                "profit_factor": merged["profit_factor"],
                "gross_profit_factor": merged["gross_profit_factor"],
                "win_rate": merged["win_rate"],
                "maximum_drawdown": merged["maximum_drawdown"],
                "exposure": merged["exposure"],
                "cost_stress": merged["cost_stress"],
                "fold_strategy_results": merged["fold_strategy_results"],
                "folds": merged["folds"],
                "predictive_fold_results": merged["q2_predictive_fold_results"],
                "predictive_aggregate": merged["q2_predictive_aggregate"],
                "predictive_stability": merged["q2_stability"],
                "prediction_collapse": merged["q2_collapse"],
                "cost_adjusted_robustness": merged["q3_cost_adjusted_robustness"],
                "boundary_validation_ok": merged["boundary_validation_ok"],
                "boundary_validation_reasons": merged["boundary_validation_reasons"],
                # BUG 4: Separate integrity fields
                "artifact_integrity_ok": flags["artifact_integrity_ok"],
                "accounting_integrity_ok": flags["accounting_integrity_ok"],
                "predictive_evidence_integrity_ok": flags["predictive_evidence_integrity_ok"],
                "strategy_evidence_integrity_ok": flags["strategy_evidence_integrity_ok"],
                "model_integrity_ok": model_integrity_ok,
                "model_integrity_reasons": reasons,
                "promotion_gate": gate,
                "ai_advisory": candidate_advisory.to_dict(),
            }
        )

    # --- BUG 4: Separate artifact integrity from promotion eligibility ---
    # Valid artifacts can still produce NO_STRATEGY_ELIGIBLE due to poor
    # performance or contaminated holdout.
    artifact_integrity_ok = (
        freeze_integrity_ok
        and holdout_integrity_ok
        and all(m["artifact_integrity_ok"] for m in models)
    )
    accounting_integrity_ok = all(m["accounting_integrity_ok"] for m in models)
    predictive_evidence_integrity_ok = all(m["predictive_evidence_integrity_ok"] for m in models)
    strategy_evidence_integrity_ok = all(m["strategy_evidence_integrity_ok"] for m in models)

    # Promotion is fail-closed: only eligible if all integrity checks pass
    # AND the promotion gate says ELIGIBLE_FOR_HUMAN_REVIEW.
    promotion_eligible_models = [
        m
        for m in models
        if m["promotion_gate"]["status"] == "ELIGIBLE_FOR_HUMAN_REVIEW" and m["model_integrity_ok"]
    ]

    # If any mandatory integrity check fails, no model can be promoted.
    if not (
        freeze_integrity_ok
        and holdout_integrity_ok
        and artifact_integrity_ok
        and accounting_integrity_ok
        and predictive_evidence_integrity_ok
        and strategy_evidence_integrity_ok
    ):
        promotion_eligible_models = []

    research_start = str(q3_dataset.get("research_start") or "NOT_AVAILABLE")
    research_end = str(q3_dataset.get("research_end") or "NOT_AVAILABLE")
    metrics: dict[str, Any] = {
        "dataset_id": str(q3_dataset.get("sha256") or "NOT_AVAILABLE"),
        "exchange": "Binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "sample_size": q3_dataset.get("research_rows"),
        "train_period": "purged walk-forward (actual observable duration)",
        "validation_period": f"{research_start} to {research_end}",
        "holdout_period": str(q2_holdout.get("start") or "NOT_AVAILABLE"),
        "trade_count": None,
        "failure_diagnostics": (
            "No model selected. Candidate performance remains model-specific; "
            "historical holdout is not pristine and no prospective holdout is validated."
        )[:500],
        "total_return_bp": None,
        "max_drawdown_bp": None,
        "profit_factor": None,
        "win_rate": None,
        "cost_stress_return_bp": None,
        "walk_forward_return_bp": None,
        "holdout_return_bp": None,
        "robustness_score": None,
    }
    ai_summary = summarize_research(metrics, gateway=gateway)

    overall_verdict = (
        "NO_STRATEGY_ELIGIBLE" if not promotion_eligible_models else "HUMAN_REVIEW_REQUIRED"
    )

    release = {
        "schema": "signalforge.quantitative-release.q4.v1",
        "phase": "Q4-revised",
        # BUG 2: freeze timestamp is immutable, never generated_at
        "research_freeze_timestamp": freeze_created_at,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_artifacts": {
            "q2_2": {"path": _artifact_label(Q2_PATH), "sha256": _sha256(Q2_PATH)},
            "q3_revised": {"path": _artifact_label(Q3_PATH), "sha256": _sha256(Q3_PATH)},
        },
        # BUG 3: Explicit freeze status, never "unknown"
        "research_freeze": {
            "status": freeze_status,
            "reason": freeze_reason,
            "freeze_id": freeze_id,
            "created_at_utc": freeze_created_at,
            "commit_sha": freeze_commit_sha,
            "schema_version": freeze_schema_version,
            "checks": freeze_checks,
            "valid": freeze_integrity_ok,
        },
        "research_status": {
            "q2_2": q2.get("conclusion", "NOT_AVAILABLE"),
            "historical_holdout_integrity": historical_integrity,
            "prospective_holdout_validated": prospective_validated,
            "models_evaluated": len(models),
            "models_eligible_for_human_review": len(promotion_eligible_models),
            "eligible_model_ids": [m["model"] for m in promotion_eligible_models],
            "overall_verdict": overall_verdict,
            "model_selection": "NONE — Q4 does not select or rank a model",
            # BUG 4: Separate integrity fields at top level too
            "artifact_integrity_ok": artifact_integrity_ok,
            "accounting_integrity_ok": accounting_integrity_ok,
            "predictive_evidence_integrity_ok": predictive_evidence_integrity_ok,
            "strategy_evidence_integrity_ok": strategy_evidence_integrity_ok,
            "freeze_integrity_ok": freeze_integrity_ok,
            "holdout_integrity_ok": holdout_integrity_ok,
            "promotion_eligible": len(promotion_eligible_models) > 0,
            "integrity_ok": (
                artifact_integrity_ok
                and accounting_integrity_ok
                and predictive_evidence_integrity_ok
                and strategy_evidence_integrity_ok
                and freeze_integrity_ok
                and holdout_integrity_ok
            ),
            "integrity_reasons": all_integrity_reasons,
        },
        "models": models,
        "deterministic_metrics": metrics,
        "ai_advisory": ai_summary.to_dict(),
        "authority": {
            "ai_can_select_strategy": False,
            "ai_can_override_risk": False,
            "ai_can_promote": False,
            "human_approval_required": True,
            "human_authorized": False,
            "paper_trading_authorized": False,
            "order_execution_authorized": False,
        },
        "limitations": [
            "Historical holdout is permanently contaminated and cannot be used as "
            "prospective validation evidence.",
            "No genuinely prospective, untouched holdout has been validated after "
            "the frozen methodology/research state.",
            "Valid research artifacts do not imply a promotion-eligible strategy.",
            "Barrier-aligned targets and simulator correct prior "
            "horizon/execution misalignment, but economic edge remains unproven.",
            "AI output is optional advisory interpretation only; deterministic "
            "results and gates remain authoritative.",
            "Candidate metrics are kept separate; no cross-model aggregation or "
            "synthetic selection is reported.",
            "No strategy is approved for paper or live execution by this release.",
        ],
    }
    output_json = _output_json_path()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(release, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    # --- Markdown report ---
    lines = [
        "# Quantitative Research Release — Q4 (Revised)",
        "",
        f"Research freeze timestamp: {freeze_created_at}",
        f"Generated: {release['generated_at']}",
        "",
        "## Decision",
        "",
        f"**{overall_verdict}**",
        "",
        f"- Models reviewed: {len(models)}",
        f"- Models eligible for human review: {len(promotion_eligible_models)}",
        f"- Historical holdout integrity: {historical_integrity.get('status', 'unknown')}",
        f"- Prospective holdout validated: {'Yes' if prospective_validated else 'No'}",
        f"- Freeze status: {freeze_status}",
        f"- Artifact integrity OK: {artifact_integrity_ok}",
        f"- Accounting integrity OK: {accounting_integrity_ok}",
        f"- Predictive evidence integrity OK: {predictive_evidence_integrity_ok}",
        f"- Strategy evidence integrity OK: {strategy_evidence_integrity_ok}",
        f"- Freeze integrity OK: {freeze_integrity_ok}",
        f"- Holdout integrity OK: {holdout_integrity_ok}",
        f"- Promotion eligible: {len(promotion_eligible_models) > 0}",
        "- AI advisory status: " + ai_summary.status.value,
        "",
        "## Integrity verification",
        "",
    ]
    if all_integrity_reasons:
        for reason in all_integrity_reasons:
            lines.append(f"- {reason}")
    else:
        lines.append("- All integrity checks passed.")
    lines.extend(
        [
            "",
            "## Model research gates",
            "",
        ]
    )
    for row in models:
        lines.extend(
            [
                f"### {row['model']}",
                "",
                f"- Trades: {row['trade_count']}",
                f"- Gross return: {row['gross_return_bp']} bp",
                f"- Net return: {row['net_return_bp']} bp",
                f"- Total trading costs: {row['total_trading_costs_bp']} bp",
                f"- Gross profit factor: {row['gross_profit_factor']}",
                f"- Net profit factor: {row['profit_factor']}",
                f"- Max drawdown: {row['maximum_drawdown']} bp",
                f"- Exposure: {row['exposure']}",
                f"- Artifact integrity OK: {row['artifact_integrity_ok']}",
                f"- Accounting integrity OK: {row['accounting_integrity_ok']}",
                f"- Predictive evidence integrity OK: {row['predictive_evidence_integrity_ok']}",
                f"- Strategy evidence integrity OK: {row['strategy_evidence_integrity_ok']}",
                f"- Boundary validation OK: {row['boundary_validation_ok']}",
                f"- Gate: {row['promotion_gate']['status']}",
                "- Reasons: " + "; ".join(row["promotion_gate"]["reasons"]),
            ]
        )
        if row["boundary_validation_reasons"]:
            lines.append("- Boundary issues: " + "; ".join(row["boundary_validation_reasons"]))
        if row["model_integrity_reasons"]:
            lines.append("- Integrity issues: " + "; ".join(row["model_integrity_reasons"]))
        lines.extend(
            [
                "- Candidate AI advisory: "
                + str(
                    row["ai_advisory"].get("summary")
                    or row["ai_advisory"].get("note")
                    or "disabled"
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## AI advisory",
            "",
            ai_summary.summary
            or ai_summary.note
            or "AI advisory disabled; deterministic report remains authoritative.",
            "",
            "AI cannot select, approve, promote or execute a strategy. Human approval remains mandatory.",
            "",
            "No live orders, paper-state mutations, or execution authorization are present.",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    return release


def _candidate_advisory_metrics(
    model: dict[str, Any], q3_dataset: dict[str, Any], q3_target: dict[str, Any]
) -> dict[str, Any]:
    strategy = model.get("strategy", {})
    strategy = strategy if isinstance(strategy, dict) else {}
    fold_rows = [row for row in model.get("fold_strategy_results", []) if isinstance(row, dict)]
    fold_returns = [float(row.get("net_return_bp", 0.0)) for row in fold_rows]
    positive_fraction = (
        sum(value > 0 for value in fold_returns) / len(fold_returns) if fold_returns else None
    )
    stress_rows = strategy.get("cost_stress", [])
    stress_2x = next(
        (
            row
            for row in stress_rows
            if isinstance(row, dict) and float(row.get("cost_multiplier", 0)) == 2.0
        ),
        None,
    )
    gate = model.get("promotion_gate", {})
    reasons = gate.get("reasons", []) if isinstance(gate, dict) else []
    diagnostics = (
        f"candidate={model.get('model', 'unknown')}; "
        f"target={q3_target.get('name', 'unknown')}; "
        f"promotion_gate={gate.get('status', 'unknown') if isinstance(gate, dict) else 'unknown'}; "
        + "; ".join(str(reason) for reason in reasons)
    )
    return {
        "dataset_id": str(q3_dataset.get("sha256") or "NOT_AVAILABLE"),
        "exchange": "Binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "sample_size": q3_dataset.get("research_rows"),
        "train_period": "purged walk-forward (actual observable duration)",
        "validation_period": (
            f"{q3_dataset.get('research_start', 'NOT_AVAILABLE')} to "
            f"{q3_dataset.get('research_end', 'NOT_AVAILABLE')}"
        ),
        "holdout_period": "NOT_AVAILABLE (no prospective holdout validated)",
        "trade_count": strategy.get("trade_count"),
        "failure_diagnostics": diagnostics[:500],
        "total_return_bp": strategy.get("total_net_return_bp"),
        "max_drawdown_bp": strategy.get("maximum_drawdown") or strategy.get("max_drawdown_bp"),
        "profit_factor": strategy.get("net_profit_factor") or strategy.get("profit_factor"),
        "win_rate": strategy.get("win_rate"),
        "cost_stress_return_bp": (
            stress_2x.get("total_net_return_bp") if isinstance(stress_2x, dict) else None
        ),
        "walk_forward_return_bp": sum(fold_returns) if fold_returns else None,
        "holdout_return_bp": None,
        "robustness_score": positive_fraction,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable configured AI provider for advisory interpretation only.",
    )
    args = parser.parse_args()
    release = build_release(ai_enabled=args.ai)
    print(f"Q4 revised release: {release['research_status']['overall_verdict']}")
    print(f"Freeze status: {release['research_freeze']['status']}")
    print(f"Integrity OK: {release['research_status']['integrity_ok']}")
    print(f"Promotion eligible: {release['research_status']['promotion_eligible']}")
    print(f"AI advisory: {release['ai_advisory']['status']}")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {_output_json_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
