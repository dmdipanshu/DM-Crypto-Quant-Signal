from __future__ import annotations

import pandas as pd
import pytest

from crypto_engine.research.execution import evaluate_fixed_horizon_strategy
from crypto_engine.research.governance import (
    PromotionThresholds,
    assess_holdout_integrity,
    evaluate_promotion_eligibility,
)


def _sim(**overrides):
    params = {
        "opens": [100, 100, 102, 103, 104, 105, 106],
        "predictions": [1, 1, 1, 0, -1, 0, 0],
        "decision_mask": [True, True, True, True, True, True, True],
        "timestamps": pd.date_range("2026-01-01", periods=7, freq="h", tz="UTC"),
        "horizon_bars": 1,
        "fee_bp": 1,
        "spread_bp": 2,
        "slippage_bp": 3,
        "funding_bp_per_bar": 0,
        "stress_multipliers": (1.0, 2.0),
    }
    params.update(overrides)
    return evaluate_fixed_horizon_strategy(**params)


def test_holdout_pristine_requires_no_overlap_no_access_and_frozen_params():
    result = assess_holdout_integrity(
        holdout_start="2026-04-01T00:00:00Z",
        training_end="2026-03-31T23:00:00Z",
        access_count=0,
        model_frozen=True,
        parameters_frozen=True,
    )
    assert result["status"] == "pristine"
    assert result["pristine"] is True


def test_holdout_overlap_is_contaminated_even_with_zero_access_count():
    result = assess_holdout_integrity(
        holdout_start="2026-04-01T00:00:00Z",
        training_end="2026-04-01T00:00:00Z",
        access_count=0,
        model_frozen=True,
        parameters_frozen=True,
    )
    assert result["status"] == "contaminated"
    assert result["pristine"] is False
    assert result["reasons"]


def test_holdout_access_consumes_it_even_when_training_precedes_boundary():
    result = assess_holdout_integrity(
        holdout_start="2026-04-01T00:00:00Z",
        training_end="2026-03-01T00:00:00Z",
        access_count=1,
        model_frozen=True,
        parameters_frozen=True,
    )
    assert result["status"] == "consumed"
    assert not result["pristine"]


def test_holdout_unknown_provenance_fails_closed():
    result = assess_holdout_integrity(
        holdout_start=None,
        training_end=None,
        access_count=0,
        model_frozen=False,
        parameters_frozen=False,
    )
    assert result["status"] == "unknown"
    assert not result["pristine"]


def test_fixed_horizon_uses_next_bar_open_and_full_round_trip_costs():
    result = _sim()
    # Decision at index 0 enters at index 1 (100) and exits at index 2 (102).
    first = result["trades"][0]
    assert first["decision_bar_open_at"] == "2026-01-01T00:00:00+00:00"
    assert first["decision_at"] == "2026-01-01T01:00:00+00:00"
    assert first["entry_price"] == 100
    assert first["exit_price"] == 102
    assert first["gross_return_bp"] == pytest.approx(200)
    # 2 fees + one round-trip spread + 2 slippage legs = 10 bp.
    assert first["net_return_bp"] == pytest.approx(190)


def test_fixed_horizon_suppresses_overlapping_positions_and_reports_unclosed_tail():
    result = _sim(predictions=[1, 1, 1, 0, -1, 0, 1])
    assert result["skipped"]["overlapping_signals"] >= 1
    assert result["skipped"]["insufficient_future_bars"] >= 1
    assert all(
        pd.Timestamp(a["exit_at"]) <= pd.Timestamp(b["decision_at"])
        for a, b in zip(result["trades"], result["trades"][1:], strict=False)
    )


def test_cost_stress_is_monotonically_worse_for_identical_trades():
    result = _sim()
    base, stressed = result["cost_stress"]
    assert stressed["total_net_return_bp"] < base["total_net_return_bp"]
    assert stressed["expectancy_bp"] < base["expectancy_bp"]


def test_fixed_horizon_rejects_unaligned_or_unsafe_inputs():
    with pytest.raises(ValueError, match="must align"):
        _sim(predictions=[1, 0])
    with pytest.raises(ValueError, match="finite and > 0"):
        _sim(opens=[100, 0, 102, 103, 104, 105, 106])
    with pytest.raises(ValueError, match="timezone-aware"):
        _sim(timestamps=pd.date_range("2026-01-01", periods=7, freq="h"))
    with pytest.raises(ValueError, match="only -1, 0 or 1"):
        _sim(predictions=[2, 1, 1, 0, -1, 0, 0])
    with pytest.raises(ValueError, match="only -1, 0 or 1"):
        _sim(predictions=[0.5, 1, 1, 0, -1, 0, 0])
    with pytest.raises(ValueError, match="decision_mask"):
        _sim(decision_mask=[0, 0.5, 1, 1, 1, 1, 1])
    with pytest.raises(ValueError, match="positive integers"):
        _sim(horizon_bars=1.5)


def test_promotion_gate_is_fail_closed_when_holdout_is_consumed():
    result = evaluate_promotion_eligibility(
        strategy_metrics={
            "trade_count": 200,
            "profit_factor": 1.5,
            "cost_stress": [{"cost_multiplier": 2.0, "total_net_return_bp": 100}],
        },
        fold_net_returns_bp=[100, 200, 50, 70],
        holdout_integrity={
            "status": "consumed",
            "pristine": False,
            "evaluation_status": "legacy_assessed",
        },
        prospective_holdout_validated=True,
    )
    assert result["status"] == "NO_STRATEGY_ELIGIBLE"
    assert any("consumed" in reason.lower() for reason in result["reasons"])
    assert result["human_authorized"] is False
    assert result["execution_authorized"] is False
    assert result["ai_promotion_authority"] is False


def test_promotion_gate_can_only_advance_to_human_review_not_execution():
    result = evaluate_promotion_eligibility(
        strategy_metrics={
            "trade_count": 200,
            "profit_factor": 1.5,
            "cost_stress": [{"cost_multiplier": 2.0, "total_net_return_bp": 100}],
        },
        fold_net_returns_bp=[100, 200, 50, 70],
        holdout_integrity={
            "status": "pristine",
            "pristine": True,
            "evaluation_status": "assessed_pristine",
        },
        prospective_holdout_validated=True,
        thresholds=PromotionThresholds(min_trades=100),
    )
    assert result["status"] == "ELIGIBLE_FOR_HUMAN_REVIEW"
    assert result["human_approval_required"] is True
    assert result["human_authorized"] is False
    assert result["execution_authorized"] is False


def test_promotion_gate_rejects_missing_or_nonpositive_stress_return():
    result = evaluate_promotion_eligibility(
        strategy_metrics={"trade_count": 1000, "profit_factor": 2.0, "cost_stress": []},
        fold_net_returns_bp=[100, 200, 50, 70],
        holdout_integrity={
            "status": "pristine",
            "pristine": True,
            "evaluation_status": "assessed_pristine",
        },
        prospective_holdout_validated=True,
    )
    assert result["status"] == "NO_STRATEGY_ELIGIBLE"
    assert any("2.0x costs" in reason for reason in result["reasons"])


def test_research_release_reader_accepts_only_safe_q4_release(tmp_path):
    import json

    from crypto_engine.api.repos import RepoRoots, read_research_release

    roots = RepoRoots(data_storage=tmp_path / "data")
    target = tmp_path / "docs/validation/results/quantitative_release_q4.json"
    target.parent.mkdir(parents=True)
    release = {
        "schema": "signalforge.quantitative-release.q4.v1",
        "phase": "Q4",
        "authority": {
            "ai_can_select_strategy": False,
            "ai_can_override_risk": False,
            "ai_can_promote": False,
            "human_approval_required": True,
            "human_authorized": False,
            "paper_trading_authorized": False,
            "order_execution_authorized": False,
        },
    }
    target.write_text(json.dumps(release), encoding="utf-8")
    result = read_research_release(roots)
    assert result["available"] is True
    assert result["release"]["phase"] == "Q4"


def test_research_release_reader_rejects_unsafe_authority(tmp_path):
    import json

    from crypto_engine.api.repos import RepoRoots, read_research_release

    roots = RepoRoots(data_storage=tmp_path / "data")
    target = tmp_path / "docs/validation/results/quantitative_release_q4.json"
    target.parent.mkdir(parents=True)
    release = {
        "schema": "signalforge.quantitative-release.q4.v1",
        "phase": "Q4",
        "authority": {
            "ai_can_select_strategy": False,
            "ai_can_override_risk": False,
            "ai_can_promote": True,
            "human_approval_required": True,
            "human_authorized": False,
            "paper_trading_authorized": False,
            "order_execution_authorized": False,
        },
    }
    target.write_text(json.dumps(release), encoding="utf-8")
    result = read_research_release(roots)
    assert result["available"] is False
    assert result["reason"] == "corrupt"


def test_research_page_escapes_release_model_and_ai_text():
    from crypto_engine.dashboard.pages import research_page

    html = research_page(
        {"experiments_available": False},
        [],
        {
            "available": True,
            "release": {
                "research_status": {
                    "overall_verdict": "NO_STRATEGY_ELIGIBLE",
                    "historical_holdout_integrity": {"status": "contaminated"},
                    "models_evaluated": 1,
                    "models_eligible_for_human_review": 0,
                },
                "models": [
                    {
                        "model": "<script>alert(1)</script>",
                        "trade_count": 0,
                        "net_return_bp": None,
                        "profit_factor": None,
                        "promotion_gate": {"status": "NOT_ELIGIBLE"},
                        "ai_advisory": {"status": "disabled", "summary": "<svg onload=alert(3)>"},
                    }
                ],
                "ai_advisory": {"status": "disabled", "summary": "<img src=x onerror=alert(2)>"},
                "authority": {"order_execution_authorized": False},
            },
        },
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(2)&gt;" in html
    assert "&lt;svg onload=alert(3)&gt;" in html


def test_q4_candidate_advisory_metrics_are_model_scoped_and_allowlisted():
    import importlib
    import sys
    from pathlib import Path

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4 = importlib.import_module("q4_research_intelligence")
    model = {
        "model": "logistic",
        "strategy": {
            "trade_count": 125,
            "total_net_return_bp": 240.0,
            "max_drawdown_bp": 300.0,
            "profit_factor": 1.2,
            "win_rate": 0.56,
            "cost_stress": [{"cost_multiplier": 2.0, "total_net_return_bp": 50.0}],
        },
        "fold_strategy_results": [
            {"net_return_bp": 100.0},
            {"net_return_bp": -20.0},
            {"net_return_bp": 160.0},
        ],
        "promotion_gate": {"status": "NOT_ELIGIBLE", "reasons": ["holdout consumed"]},
    }
    metrics = q4._candidate_advisory_metrics(
        model,
        {"sha256": "abc", "research_rows": 1000, "research_start": "start", "research_end": "end"},
        {"train_window": 600},
    )
    assert metrics["trade_count"] == 125
    assert metrics["total_return_bp"] == 240.0
    assert metrics["cost_stress_return_bp"] == 50.0
    assert metrics["walk_forward_return_bp"] == 240.0
    assert metrics["robustness_score"] == 2 / 3
    assert metrics["holdout_return_bp"] is None
    assert "logistic" in metrics["failure_diagnostics"]


def test_q4_release_never_aggregates_or_selects_a_candidate(tmp_path, monkeypatch):
    import importlib
    import json
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    scripts_dir = str(repo_root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4 = importlib.import_module("q4_research_intelligence")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"
    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.1",
                "conclusion": "DIAGNOSTIC_ONLY",
                "holdout": {"start": "2026-07-24T06:00:00Z"},
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3",
                "dataset": {
                    "sha256": "dataset-hash",
                    "research_rows": 10_000,
                    "research_start": "2024-11-01T00:00:00Z",
                    "research_end": "2026-07-24T05:00:00Z",
                },
                "protocol": {"train_window": 6_000},
                "historical_holdout_integrity": {"status": "contaminated", "pristine": False},
                "models": [
                    {
                        "model": "logistic",
                        "strategy": {
                            "trade_count": 200,
                            "total_net_return_bp": 500.0,
                            "profit_factor": 1.5,
                            "cost_stress": [{"cost_multiplier": 2.0, "total_net_return_bp": 100.0}],
                        },
                        "fold_strategy_results": [
                            {"fold": 1, "net_return_bp": 10.0},
                            {"fold": 2, "net_return_bp": 20.0},
                            {"fold": 3, "net_return_bp": 30.0},
                            {"fold": 4, "net_return_bp": 40.0},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4, "Q2_PATH", q2_path)
    monkeypatch.setattr(q4, "Q3_PATH", q3_path)
    monkeypatch.setattr(q4, "OUT_JSON", out_json)
    monkeypatch.setattr(q4, "OUT_MD", out_md)

    release = q4.build_release(ai_enabled=False)
    assert release["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"
    assert release["research_status"]["models_eligible_for_human_review"] == 0
    assert release["research_status"]["model_selection"].startswith("NONE")
    assert release["deterministic_metrics"]["trade_count"] is None
    assert release["deterministic_metrics"]["total_return_bp"] is None
    assert release["deterministic_metrics"]["profit_factor"] is None
    assert release["models"][0]["promotion_gate"]["status"] == "NO_STRATEGY_ELIGIBLE"
    assert release["models"][0]["ai_advisory"]["status"] == "disabled"
    assert "trade_count=200" in release["models"][0]["ai_advisory"]["facts"]
    assert release["authority"]["human_approval_required"] is True
    assert release["authority"]["human_authorized"] is False
    assert release["authority"]["order_execution_authorized"] is False
    assert out_json.is_file() and out_md.is_file()


def test_q2_1_horizon_grid_uses_distinct_canonical_label_horizons():
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    scripts_dir = str(repo_root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from q2_1_diagnostic_lab import HORIZONS, _label_config

    assert tuple(_label_config(h).max_horizon_bars for h in HORIZONS) == HORIZONS
