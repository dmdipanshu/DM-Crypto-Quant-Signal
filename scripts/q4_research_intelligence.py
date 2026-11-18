"""Q4 — deterministic research release + optional advisory AI interpretation.

This script combines Q2.1 diagnostics and Q3 economic validation into a
machine-readable, read-only research release. AI is disabled by default; with
``--ai`` it may summarize only allowlisted evidence. AI cannot promote,
select, score, or authorize any strategy.

Run:
    uv run python scripts/q4_research_intelligence.py
    uv run python scripts/q4_research_intelligence.py --ai
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
from crypto_engine.research.governance import (  # noqa: E402
    PromotionThresholds,
    assess_holdout_integrity,
    evaluate_promotion_eligibility,
)

Q2_PATH = ROOT / "docs/validation/results/quantitative_diagnostics_q2_1.json"
Q3_PATH = ROOT / "docs/validation/results/strategy_validation_q3.json"
DEFAULT_OUT_JSON = ROOT / "docs/validation/results/quantitative_release_q4.json"
OUT_JSON = DEFAULT_OUT_JSON
OUT_MD = ROOT / "docs/validation/QUANTITATIVE_RELEASE_Q4.md"


def _output_json_path() -> Path:
    """Honor the configured API release path unless a test overrides OUT_JSON."""
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
    """Return a repository-relative source label without leaking temp paths."""
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



def _candidate_advisory_metrics(
    model: dict[str, Any], q3_dataset: dict[str, Any], q3_protocol: dict[str, Any]
) -> dict[str, Any]:
    """Build allowlisted, model-scoped facts without selecting/merging candidates."""
    strategy = model.get("strategy", {})
    strategy = strategy if isinstance(strategy, dict) else {}
    fold_rows = [
        row for row in model.get("fold_strategy_results", []) if isinstance(row, dict)
    ]
    fold_returns = [float(row.get("net_return_bp", 0.0)) for row in fold_rows]
    positive_fraction = (
        sum(value > 0 for value in fold_returns) / len(fold_returns)
        if fold_returns
        else None
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
        f"promotion_gate={gate.get('status', 'unknown') if isinstance(gate, dict) else 'unknown'}; "
        + "; ".join(str(reason) for reason in reasons)
    )
    return {
        "dataset_id": str(q3_dataset.get("sha256") or "NOT_AVAILABLE"),
        "exchange": "Binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "sample_size": q3_dataset.get("research_rows"),
        "train_period": (
            f"rolling walk-forward window: {q3_protocol.get('train_window')} bars"
            if q3_protocol.get("train_window") is not None
            else None
        ),
        "validation_period": (
            f"{q3_dataset.get('research_start', 'NOT_AVAILABLE')} to "
            f"{q3_dataset.get('research_end', 'NOT_AVAILABLE')}"
        ),
        "holdout_period": q3_dataset.get("historical_holdout_start"),
        "trade_count": strategy.get("trade_count"),
        "failure_diagnostics": diagnostics[:500],
        "total_return_bp": strategy.get("total_net_return_bp"),
        "max_drawdown_bp": strategy.get("max_drawdown_bp"),
        "profit_factor": strategy.get("profit_factor"),
        "win_rate": strategy.get("win_rate"),
        "cost_stress_return_bp": (
            stress_2x.get("total_net_return_bp") if isinstance(stress_2x, dict) else None
        ),
        "walk_forward_return_bp": sum(fold_returns) if fold_returns else None,
        "holdout_return_bp": None,
        "robustness_score": positive_fraction,
    }


def build_release(*, ai_enabled: bool = False) -> dict[str, Any]:
    q2 = _load(Q2_PATH)
    q3 = _load(Q3_PATH)
    if q2.get("phase") != "Q2.1" or q3.get("phase") != "Q3":
        raise ValueError("Q4 source artifacts have unexpected phase identifiers.")

    historical_integrity = q3.get("historical_holdout_integrity")
    if not isinstance(historical_integrity, dict):
        historical_integrity = assess_holdout_integrity(
            holdout_start=None,
            training_end=None,
            access_count=1,
            model_frozen=False,
            parameters_frozen=False,
        )

    q3_dataset = q3.get("dataset", {})
    q3_protocol = q3.get("protocol", {})
    gateway = _optional_gateway() if ai_enabled else None
    models: list[dict[str, Any]] = []
    for model in q3.get("models", []):
        if not isinstance(model, dict):
            continue
        strategy = model.get("strategy", {})
        fold_returns = [
            float(row.get("net_return_bp", 0.0))
            for row in model.get("fold_strategy_results", [])
            if isinstance(row, dict)
        ]
        gate = evaluate_promotion_eligibility(
            strategy_metrics=strategy if isinstance(strategy, dict) else {},
            fold_net_returns_bp=fold_returns,
            holdout_integrity=historical_integrity,
            prospective_holdout_validated=False,
            thresholds=PromotionThresholds(),
        )
        candidate_advisory = summarize_research(
            _candidate_advisory_metrics(model, q3_dataset, q3_protocol),
            gateway=gateway,
        )
        models.append(
            {
                "model": str(model.get("model", "unknown")),
                "trade_count": int(strategy.get("trade_count", 0))
                if isinstance(strategy, dict)
                else 0,
                "net_return_bp": strategy.get("total_net_return_bp")
                if isinstance(strategy, dict)
                else None,
                "profit_factor": strategy.get("profit_factor")
                if isinstance(strategy, dict)
                else None,
                "cost_stress": strategy.get("cost_stress", [])
                if isinstance(strategy, dict)
                else [],
                "promotion_gate": gate,
                "ai_advisory": candidate_advisory.to_dict(),
            }
        )

    eligible = [row for row in models if row["promotion_gate"]["status"] == "ELIGIBLE_FOR_HUMAN_REVIEW"]
    q3_dataset = q3.get("dataset", {})
    research_start = str(q3_dataset.get("research_start") or "NOT_AVAILABLE")
    research_end = str(q3_dataset.get("research_end") or "NOT_AVAILABLE")
    fold_window = q3.get("protocol", {}).get("train_window")
    metrics: dict[str, Any] = {
        "dataset_id": str(q3_dataset.get("sha256") or "NOT_AVAILABLE"),
        "exchange": "Binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "sample_size": q3_dataset.get("research_rows"),
        "train_period": (
            f"rolling walk-forward window: {fold_window} bars"
            if fold_window is not None
            else "NOT_AVAILABLE"
        ),
        "validation_period": f"{research_start} to {research_end}",
        "holdout_period": str(q2.get("holdout", {}).get("start") or "NOT_AVAILABLE"),
        # Candidate metrics remain isolated under `models`. There is no selected
        # strategy, so a maximum across models would be a misleading synthetic result.
        "trade_count": None,
        "failure_diagnostics": (
            "No model selected. Candidate performance remains model-specific; "
            "the historical holdout is contaminated and no prospective holdout exists."
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
    release = {
        "schema": "signalforge.quantitative-release.q4.v1",
        "phase": "Q4",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_artifacts": {
            "q2_1": {"path": _artifact_label(Q2_PATH), "sha256": _sha256(Q2_PATH)},
            "q3": {"path": _artifact_label(Q3_PATH), "sha256": _sha256(Q3_PATH)},
        },
        "research_status": {
            "q2_1": q2.get("conclusion", "NOT_AVAILABLE"),
            "historical_holdout_integrity": historical_integrity,
            "prospective_holdout_validated": False,
            "models_evaluated": len(models),
            "models_eligible_for_human_review": len(eligible),
            "eligible_model_ids": [row["model"] for row in eligible],
            "overall_verdict": "NO_STRATEGY_ELIGIBLE" if not eligible else "HUMAN_REVIEW_REQUIRED",
            "model_selection": "NONE — Q4 does not select or rank a model",
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
            "The previous Q2 holdout is consumed and contaminated by overlap with the production training period.",
            "Q2.1 and Q3 did not evaluate the historical holdout.",
            "No genuinely prospective holdout has been collected after the frozen Q3 strategy/model version.",
            "AI output is optional advisory interpretation only; deterministic results and gates remain authoritative.",
            "Candidate metrics are kept separate; no cross-model maximum or synthetic portfolio result is reported.",
            "No strategy is approved for paper or live execution by this release.",
        ],
    }
    output_json = _output_json_path()
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(release, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    lines = [
        "# Quantitative Research Release — Q4",
        "",
        f"Generated: {release['generated_at']}",
        "",
        "## Decision",
        "",
        f"**{release['research_status']['overall_verdict']}**",
        "",
        f"- Models reviewed: {len(models)}",
        f"- Models eligible for human review: {len(eligible)}",
        f"- Historical holdout integrity: {historical_integrity.get('status', 'unknown')}",
        "- Prospective holdout validated: No",
        "- AI advisory status: " + ai_summary.status.value,
        "",
        "## Model research gates",
        "",
    ]
    for row in models:
        lines.extend(
            [
                f"### {row['model']}",
                "",
                f"- Trades: {row['trade_count']}",
                f"- Net return: {row['net_return_bp']} bp",
                f"- Profit factor: {row['profit_factor']}",
                f"- Gate: {row['promotion_gate']['status']}",
                "- Reasons: " + "; ".join(row["promotion_gate"]["reasons"]),
                "- Candidate AI advisory: "
                + str(row["ai_advisory"].get("summary") or row["ai_advisory"].get("note") or "disabled"),
                "",
            ]
        )
    lines.extend(
        [
            "## AI advisory",
            "",
            ai_summary.summary or ai_summary.note or "AI advisory disabled; deterministic report remains authoritative.",
            "",
            "AI cannot select, approve, promote or execute a strategy. Human approval remains mandatory.",
            "",
            "No live orders, paper-state mutations, or execution authorization are present.",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    return release


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Enable the configured AI provider for advisory interpretation only.",
    )
    args = parser.parse_args()
    release = build_release(ai_enabled=args.ai)
    print(f"Q4 release: {release['research_status']['overall_verdict']}")
    print(f"AI advisory: {release['ai_advisory']['status']}")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {_output_json_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
