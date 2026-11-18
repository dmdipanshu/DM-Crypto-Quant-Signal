from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from crypto_engine.research.execution import evaluate_triple_barrier_strategy
from crypto_engine.research.governance import (
    PromotionThresholds,
    assess_holdout_integrity,
    assess_prospective_holdout,
    evaluate_promotion_eligibility,
)
from crypto_engine.research.modeling import class_distribution, detect_prediction_collapse
from crypto_engine.research.targets import TargetDefinition, label_execution_aligned


def _ohlcv(n: int = 80) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    base = 100 + np.linspace(0, 5, n)
    return pd.DataFrame(
        {
            "open": base,
            "high": base + 2.0,
            "low": base - 2.0,
            "close": base + 0.2,
            "volume": np.full(n, 10.0),
        },
        index=idx,
    )


def test_execution_aligned_labels_share_barrier_geometry_metadata():
    frame = _ohlcv(120)
    definition = TargetDefinition(pt_mult=2.0, sl_mult=1.0, max_horizon_bars=24, vol_window=20)
    labels = label_execution_aligned(frame, definition)
    assert {"label", "barrier_hit", "vol_at_entry", "observable_at"} <= set(labels.columns)
    assert set(np.unique(labels["label"].dropna().astype(int))).issubset({-1, 0, 1})
    assert definition.entry_convention == "next_bar_open"
    assert definition.definition_hash()


def test_triple_barrier_strategy_is_conservative_on_same_bar_touch_and_costed():
    n = 8
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    opens = np.array([100, 100, 100, 100, 100, 100, 100, 100], dtype=float)
    highs = np.array([101, 101, 110, 101, 101, 101, 101, 101], dtype=float)
    lows = np.array([99, 99, 90, 99, 99, 99, 99, 99], dtype=float)
    closes = np.array([100, 100, 100, 100, 100, 100, 100, 100], dtype=float)
    vol = np.full(n, 0.05)
    preds = np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.int8)
    mask = np.array([True, False, False, False, False, False, False, False])
    res = evaluate_triple_barrier_strategy(
        opens=opens,
        highs=highs,
        lows=lows,
        closes=closes,
        predictions=preds,
        decision_mask=mask,
        timestamps=idx,
        vol_at_entry=vol,
        pt_mult=2.0,
        sl_mult=1.0,
        max_horizon_bars=3,
        fee_bp=10.0,
        spread_bp=0.0,
        slippage_bp=0.0,
        funding_bp_per_bar=0.0,
    )
    assert res["trade_count"] == 1
    trade = res["trades"][0]
    assert trade["barrier"] == "stop_loss"
    assert trade["gross_return_bp"] < 0
    assert res["gross_return_bp"] < 0
    assert res["total_net_return_bp"] < res["gross_return_bp"]


def test_modeling_reports_collapse_and_distribution():
    preds = np.array([1] * 95 + [-1] * 5, dtype=int)
    collapse = detect_prediction_collapse(preds)
    assert collapse["collapsed"] is True
    dist = class_distribution(np.array([-1, 0, 1, 1, 1]))
    assert dist["counts"]["1"] == 3
    assert dist["total"] == 5


def test_holdout_integrity_statuses_fail_closed():
    pristine = assess_holdout_integrity(
        holdout_start="2026-10-10T00:00:00Z",
        training_end="2026-10-01T00:00:00Z",
        access_count=0,
        model_frozen=True,
        parameters_frozen=True,
        research_freeze_timestamp="2026-10-05T00:00:00Z",
        dataset_end="2026-10-20T00:00:00Z",
    )
    assert pristine["status"] == "pristine"
    assert pristine["evaluation_status"] == "assessed_pristine"

    contaminated = assess_holdout_integrity(
        holdout_start="2026-10-01T00:00:00Z",
        training_end="2026-10-01T00:00:00Z",
        access_count=0,
        model_frozen=True,
        parameters_frozen=True,
    )
    assert contaminated["status"] == "contaminated"

    prospective = assess_prospective_holdout(
        dataset_end="2026-10-01T00:00:00Z",
        research_freeze_timestamp="2026-10-05T00:00:00Z",
    )
    assert prospective["status"] == "not_yet_available"
    assert prospective["eligible_for_prospective_validation"] is False


def test_promotion_gate_requires_pristine_prospective_holdout():
    gate = evaluate_promotion_eligibility(
        strategy_metrics={
            "trade_count": 200,
            "profit_factor": 1.5,
            "cost_stress": [{"cost_multiplier": 2.0, "total_net_return_bp": 100}],
        },
        fold_net_returns_bp=[100, 200, 50, 70],
        holdout_integrity={
            "status": "not_yet_available",
            "pristine": False,
            "evaluation_status": "no_prospective_data",
        },
        prospective_holdout_validated=False,
        thresholds=PromotionThresholds(min_trades=100),
    )
    assert gate["status"] == "NO_STRATEGY_ELIGIBLE"
    assert gate["human_authorized"] is False
    assert gate["execution_authorized"] is False


def test_q4_revised_release_is_fail_closed_and_canonical(tmp_path, monkeypatch):
    import importlib
    import sys

    repo_root = Path(__file__).resolve().parents[3]
    scripts_dir = str(repo_root / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2_2.json"
    q3_path = tmp_path / "q3_rev.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"
    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "holdout": {
                    "start": "2026-07-24T09:00:00Z",
                    "prospective": {
                        "status": "not_yet_available",
                        "evaluation_status": "no_prospective_data",
                        "eligible_for_prospective_validation": False,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "target": {"name": "triple_barrier_next_open_v1"},
                "dataset": {
                    "sha256": "dhash",
                    "research_rows": 1000,
                    "research_start": "2024-11-01T00:00:00Z",
                    "research_end": "2026-07-24T05:00:00Z",
                },
                "protocol": {"simulator": "research_triple_barrier_next_open_v1"},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                    "evaluation_status": "legacy_assessed",
                },
                "models": [
                    {
                        "model": "lightgbm",
                        "collapse": {"collapsed": False, "max_class_share": 0.5},
                        "strategy": {
                            "trade_count": 120,
                            "total_net_return_bp": -100.0,
                            "max_drawdown_bp": 500.0,
                            "profit_factor": 0.8,
                            "win_rate": 0.4,
                            "cost_stress": [
                                {"cost_multiplier": 2.0, "total_net_return_bp": -200.0}
                            ],
                        },
                        "fold_strategy_results": [
                            {"net_return_bp": -10.0},
                            {"net_return_bp": -20.0},
                        ],
                        "folds": [{"fold": 0}, {"fold": 1}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4r, "Q2_PATH", q2_path)
    monkeypatch.setattr(q4r, "Q3_PATH", q3_path)
    monkeypatch.setattr(q4r, "OUT_JSON", out_json)
    monkeypatch.setattr(q4r, "OUT_MD", out_md)

    release = q4r.build_release(ai_enabled=False)
    assert release["schema"] == "signalforge.quantitative-release.q4.v1"
    assert release["phase"] == "Q4-revised"
    assert release["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"
    assert release["authority"]["human_authorized"] is False
    assert release["authority"]["order_execution_authorized"] is False
    assert release["models"][0]["promotion_gate"]["status"] == "NO_STRATEGY_ELIGIBLE"
    assert out_json.is_file() and out_md.is_file()
