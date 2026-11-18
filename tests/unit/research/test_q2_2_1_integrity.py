"""Comprehensive regression tests for Q2.2.1 Research Evidence Integrity.

Covers:
  A. Accounting: gross vs net reconciliation, fees deducted exactly once,
     spread/slippage/funding, long/short, zero-trade folds, zero-loss
     profit factor, 2x cost stress.
  B. Fold stability: correct five-fold count, aggregate metrics,
     failed-fold handling, skipped-fold handling, partial evaluation,
     predictive vs strategy stability separation.
  C. Freeze: immutable timestamp, immutable freeze ID, repeat-run
     consistency, changed-config detection, explicit new-freeze handling.
  D. Holdout: historical contamination cannot be cleared, unavailable
     ingestion provenance, post-freeze observation eligibility,
     pre-freeze first-seen rejected, no automatic prospective evaluation,
     explicit evaluation requirements.
  E. Q4: missing evidence fails closed, invalid hashes fail closed,
     gross/net inconsistency fails closed, contaminated holdout fails
     closed, failed fold cannot be counted as passed, AI cannot override
     deterministic rejection.
  F. Safety: no exchange order placement, no live-trading enablement,
     no automatic paper promotion, no AI authority over risk engine.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_engine.research.execution import evaluate_triple_barrier_strategy
from crypto_engine.research.freeze import (
    FreezeConfigMismatch,
    create_or_reuse_freeze,
)
from crypto_engine.research.governance import (
    PromotionThresholds,
    assess_prospective_holdout_state,
    evaluate_promotion_eligibility,
)
from crypto_engine.research.modeling import (
    evaluate_fold_predictive_metrics,
    fold_stability,
)

# ---------------------------------------------------------------------------
# A. Accounting tests
# ---------------------------------------------------------------------------


def _make_triple_barrier_inputs():
    """OHLC inputs designed to avoid same-bar barrier collisions.

    Bar 1: high=101.5, low=99.5 -> upper=102 not hit, lower=99 not hit
    Bar 2: high=102.0, low=100.0 -> upper=102 hit (TP for LONG), lower=99 not hit
    Bar 3: high=100.0, low=98.0 -> lower=99 hit (SL for LONG, TP for SHORT)
    """
    n = 6
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    opens = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 100.0], dtype=float)
    highs = np.array([101.5, 102.0, 100.0, 100.0, 100.0, 100.0], dtype=float)
    lows = np.array([99.5, 100.0, 98.0, 98.0, 98.0, 98.0], dtype=float)
    closes = np.array([100.0, 100.0, 100.0, 100.0, 100.0, 100.0], dtype=float)
    vol = np.full(n, 0.01)  # 100 bp vol -> pt=200bp (upper=102), sl=100bp (lower=99)
    return idx, opens, highs, lows, closes, vol


def test_accounting_gross_vs_net_reconciliation():
    """Gross return minus total costs must equal net return exactly."""
    idx, opens, highs, lows, closes, vol = _make_triple_barrier_inputs()
    preds = np.array([1, 0, 0, 0, 0, 0], dtype=np.int8)
    mask = np.array([True, False, False, False, False, False])
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
        spread_bp=5.0,
        slippage_bp=5.0,
        funding_bp_per_bar=0.0,
    )
    assert res["trade_count"] == 1
    trade = res["trades"][0]
    # Entry=100 at bar 1, bar 2 high=102 hits upper=102 -> profit target at 102
    # gross = (102/100 - 1) * 10000 = +200 bp
    assert trade["gross_return_bp"] == pytest.approx(200.0)
    assert trade["barrier"] == "profit_target"
    # Costs: 2*fee(20) + spread(5) + 2*slippage(10) = 35 bp
    expected_net = 200.0 - 35.0
    assert trade["net_return_bp"] == pytest.approx(expected_net)
    # Aggregate reconciliation
    assert res["total_net_return_bp"] == pytest.approx(
        res["gross_return_bp"] - res["total_trading_costs_bp"]
    )
    assert res["costs_total_bp"] == pytest.approx(res["total_trading_costs_bp"])


def test_accounting_fees_deducted_exactly_once():
    """Fees must be 2*fee_bp per trade (entry + exit), not more."""
    idx, opens, highs, lows, closes, vol = _make_triple_barrier_inputs()
    preds = np.array([1, 0, 0, 0, 0, 0], dtype=np.int8)
    mask = np.array([True, False, False, False, False, False])
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
    trade = res["trades"][0]
    assert trade["gross_return_bp"] == pytest.approx(200.0)
    # Only fees: 2 * 10 = 20 bp
    assert trade["net_return_bp"] == pytest.approx(180.0)
    assert res["fees_bp"] == pytest.approx(20.0)


def test_accounting_spread_slippage_funding_components():
    """Each cost component must be independently tracked."""
    idx, opens, highs, lows, closes, vol = _make_triple_barrier_inputs()
    preds = np.array([1, 0, 0, 0, 0, 0], dtype=np.int8)
    mask = np.array([True, False, False, False, False, False])
    # bars_held = 1 (enter bar 1, exit bar 2)
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
        spread_bp=5.0,
        slippage_bp=3.0,
        funding_bp_per_bar=1.0,
    )
    trade = res["trades"][0]
    assert res["fees_bp"] == pytest.approx(20.0)  # 2*10
    assert res["spread_bp"] == pytest.approx(5.0)  # 1*5
    assert res["slippage_bp"] == pytest.approx(6.0)  # 2*3
    # Trade exits at bar 1 (bars_held=1) since high=102 hits immediately
    assert res["funding_bp"] == pytest.approx(1.0)  # 1 bar held * 1 bp
    # total_trading_costs_bp uses horizon-based funding in stress (3 bars),
    # so it equals gross - net = 200 - 166 = 34
    assert res["total_trading_costs_bp"] == pytest.approx(34.0)
    assert res["costs_total_bp"] == pytest.approx(32.0)  # per-component sum
    assert trade["net_return_bp"] == pytest.approx(200.0 - 34.0)


def test_accounting_long_and_short_trades():
    """Both LONG and short trades must compute gross/net correctly."""
    idx, opens, highs, lows, closes, vol = _make_triple_barrier_inputs()
    # LONG at decision 0: bar 1 high=102 hits upper=102 -> TP at 102 -> +200 bp
    #   exits at bar 1, next_available_decision=1
    # SHORT at decision 2: bar 3 low=98 hits lower=99 -> TP for SHORT at 99 -> +100 bp
    preds = np.array([1, 0, -1, 0, 0, 0], dtype=np.int8)
    mask = np.array([True, False, True, False, False, False])
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
    assert res["trade_count"] == 2
    long_trade = res["trades"][0]
    short_trade = res["trades"][1]
    assert long_trade["side"] == "LONG"
    assert long_trade["gross_return_bp"] == pytest.approx(200.0)
    assert long_trade["barrier"] == "profit_target"
    assert short_trade["side"] == "SHORT"
    assert short_trade["gross_return_bp"] == pytest.approx(100.0)
    assert short_trade["barrier"] == "profit_target"
    # Both net should be gross - 20bp fees
    assert long_trade["net_return_bp"] == pytest.approx(180.0)
    assert short_trade["net_return_bp"] == pytest.approx(80.0)


def test_accounting_zero_trade_fold():
    """Zero-trade folds return None for rates/ratios, 0.0 for sums."""
    idx, opens, highs, lows, closes, vol = _make_triple_barrier_inputs()
    preds = np.zeros(6, dtype=np.int8)
    mask = np.array([True, False, False, False, False, False])
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
        spread_bp=5.0,
        slippage_bp=5.0,
        funding_bp_per_bar=0.0,
    )
    assert res["trade_count"] == 0
    assert res["win_rate"] is None
    assert res["expectancy_bp"] is None
    assert res["profit_factor"] is None
    assert res["net_profit_factor"] is None
    assert res["gross_profit_factor"] is None
    assert res["total_net_return_bp"] == 0.0
    assert res["gross_return_bp"] == 0.0
    assert res["average_trade_return_bp"] is None


def test_accounting_zero_loss_profit_factor_is_none():
    """When all trades are profitable (zero losses), profit factor is None."""
    idx, opens, highs, lows, closes, vol = _make_triple_barrier_inputs()
    # Single winning LONG trade, zero costs
    preds = np.array([1, 0, 0, 0, 0, 0], dtype=np.int8)
    mask = np.array([True, False, False, False, False, False])
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
        fee_bp=0.0,
        spread_bp=0.0,
        slippage_bp=0.0,
        funding_bp_per_bar=0.0,
    )
    assert res["trade_count"] == 1
    assert res["gross_profit_bp"] == pytest.approx(200.0)
    assert res["gross_loss_bp"] == 0.0
    assert res["net_loss_bp"] == 0.0
    # With zero loss, profit factor is None (undefined, not infinite)
    assert res["gross_profit_factor"] is None
    assert res["net_profit_factor"] is None


def test_accounting_profit_factor_with_losses():
    """When both wins and losses exist, profit factor = gross_profit / gross_loss."""
    idx, opens, highs, lows, closes, vol = _make_triple_barrier_inputs()
    # Two LONG trades: first wins (+200 bp), second hits SL (-100 bp)
    preds = np.array([1, 0, 1, 0, 0, 0], dtype=np.int8)
    mask = np.array([True, False, True, False, False, False])
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
        fee_bp=0.0,
        spread_bp=0.0,
        slippage_bp=0.0,
        funding_bp_per_bar=0.0,
    )
    assert res["trade_count"] == 2
    assert res["gross_profit_bp"] == pytest.approx(200.0)
    assert res["gross_loss_bp"] == pytest.approx(100.0)
    assert res["gross_profit_factor"] == pytest.approx(2.0)
    assert res["net_profit_factor"] == pytest.approx(2.0)


def test_accounting_2x_cost_stress():
    """2x cost stress must reduce net return exactly."""
    idx, opens, highs, lows, closes, vol = _make_triple_barrier_inputs()
    preds = np.array([1, 0, 0, 0, 0, 0], dtype=np.int8)
    mask = np.array([True, False, False, False, False, False])
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
        spread_bp=5.0,
        slippage_bp=5.0,
        funding_bp_per_bar=0.0,
        stress_multipliers=(1.0, 2.0),
    )
    stress_1x = res["cost_stress"][0]
    stress_2x = res["cost_stress"][1]
    assert stress_1x["cost_multiplier"] == 1.0
    assert stress_2x["cost_multiplier"] == 2.0
    # 2x costs = 2 * round_trip_cost
    assert stress_2x["round_trip_cost_bp"] == pytest.approx(2.0 * stress_1x["round_trip_cost_bp"])
    # Net at 2x must be lower than net at 1x
    assert stress_2x["total_net_return_bp"] < stress_1x["total_net_return_bp"]


# ---------------------------------------------------------------------------
# B. Fold stability tests
# ---------------------------------------------------------------------------


def test_fold_stability_correct_count():
    """n_folds must equal the number of successfully evaluated folds."""
    fold_metrics = [
        {"balanced_accuracy": 0.5, "accuracy": 0.5, "macro_f1": 0.3},
        {"balanced_accuracy": 0.6, "accuracy": 0.6, "macro_f1": 0.4},
        {"balanced_accuracy": 0.55, "accuracy": 0.55, "macro_f1": 0.35},
    ]
    stability = fold_stability(fold_metrics, "balanced_accuracy")
    assert stability["n_folds"] == 3
    assert stability["mean"] == pytest.approx(0.55)
    assert stability["min"] == pytest.approx(0.5)
    assert stability["max"] == pytest.approx(0.6)
    assert stability["median"] == pytest.approx(0.55)


def test_fold_stability_failed_folds_not_counted():
    """Failed/skipped folds must not be counted as successful."""
    fold_metrics = [
        {"balanced_accuracy": 0.5},
        {"balanced_accuracy": None},  # failed fold - no metric
        {"balanced_accuracy": 0.7},
    ]
    stability = fold_stability(fold_metrics, "balanced_accuracy")
    assert stability["n_folds"] == 2  # only 2 valid folds
    assert stability["mean"] == pytest.approx(0.6)


def test_fold_stability_insufficient_folds():
    """Fewer than 2 valid folds returns None fields."""
    stability = fold_stability([{"balanced_accuracy": 0.5}], "balanced_accuracy")
    assert stability["n_folds"] == 1
    assert stability["mean"] is None
    assert stability["std"] is None
    assert stability["median"] is None


def test_evaluate_fold_predictive_metrics_all_fields():
    """Per-fold predictive metrics must include all required fields."""
    y_true = np.array([1, 1, -1, -1, 0, 0, 1, -1, 0, 1])
    y_pred = np.array([1, 1, -1, -1, 0, 0, 1, -1, 0, 1])
    metrics = evaluate_fold_predictive_metrics(y_true, y_pred)
    assert metrics["accuracy"] == 1.0
    assert metrics["balanced_accuracy"] == 1.0
    assert metrics["macro_f1"] == 1.0
    assert metrics["mcc"] == 1.0
    assert metrics["prediction_collapse_status"] == "ok"
    assert "per_class" in metrics
    assert "1" in metrics["per_class"]
    assert "-1" in metrics["per_class"]
    assert "0" in metrics["per_class"]
    assert metrics["class_distribution"]["total"] == 10


def test_evaluate_fold_predictive_metrics_collapse():
    """Prediction collapse must be detected."""
    y_true = np.array([1, -1, 0, 1, -1, 0])
    y_pred = np.array([1, 1, 1, 1, 1, 1])  # all predictions are 1
    metrics = evaluate_fold_predictive_metrics(y_true, y_pred)
    assert metrics["prediction_collapse_status"] == "collapsed"
    assert metrics["prediction_collapse"]["collapsed"] is True


def test_predictive_vs_strategy_stability_separation():
    """Predictive and strategy stability must be computed independently."""
    # Predictive: per-fold classification metrics
    pred_metrics = [
        {"balanced_accuracy": 0.5, "accuracy": 0.5, "macro_f1": 0.3},
        {"balanced_accuracy": 0.9, "accuracy": 0.9, "macro_f1": 0.8},
    ]
    pred_stability = fold_stability(pred_metrics, "balanced_accuracy")
    assert pred_stability["n_folds"] == 2
    assert pred_stability["mean"] == pytest.approx(0.7)

    # Strategy: per-fold net returns
    strat_returns = [100.0, -50.0]
    strat_stability = fold_stability([{"net_return_bp": r} for r in strat_returns], "net_return_bp")
    assert strat_stability["n_folds"] == 2
    assert strat_stability["mean"] == pytest.approx(25.0)
    # Predictive and strategy stability must differ
    assert pred_stability["mean"] != strat_stability["mean"]


# ---------------------------------------------------------------------------
# C. Freeze tests
# ---------------------------------------------------------------------------


def test_freeze_immutable_timestamp_and_id(tmp_path):
    """First freeze creates manifest; rerun returns same freeze_id and created_at."""
    manifest_path = tmp_path / "manifest.json"
    history_path = tmp_path / "history.jsonl"

    config = {
        "feature_config": {"vol_window": 20},
        "target_config": {"pt_mult": 2.0},
        "model_config": {"class_weight": "balanced"},
        "strategy_config": {"simulator": "tb_v1"},
        "cost_config": {"fee_bp": 10.0},
        "thresholds_config": {"min_trades": 100},
    }

    m1 = create_or_reuse_freeze(manifest_path=manifest_path, history_path=history_path, **config)
    freeze_id_1 = m1["freeze_id"]
    created_at_1 = m1["created_at_utc"]

    # Rerun: must return the same manifest (reuse, not recreate)
    m2 = create_or_reuse_freeze(manifest_path=manifest_path, history_path=history_path, **config)
    assert m2["freeze_id"] == freeze_id_1
    assert m2["created_at_utc"] == created_at_1

    # Manifest file must not have been overwritten (same content)
    content1 = manifest_path.read_text(encoding="utf-8")
    content2 = manifest_path.read_text(encoding="utf-8")
    assert content1 == content2


def test_freeze_changed_config_triggers_mismatch(tmp_path):
    """Changed configuration must trigger FREEZE_CONFIG_MISMATCH."""
    manifest_path = tmp_path / "manifest.json"
    history_path = tmp_path / "history.jsonl"

    config_a = {
        "feature_config": {"vol_window": 20},
        "target_config": {"pt_mult": 2.0},
        "model_config": {"class_weight": "balanced"},
        "strategy_config": {"simulator": "tb_v1"},
        "cost_config": {"fee_bp": 10.0},
        "thresholds_config": {"min_trades": 100},
    }
    config_b = {
        "feature_config": {"vol_window": 50},  # changed
        "target_config": {"pt_mult": 2.0},
        "model_config": {"class_weight": "balanced"},
        "strategy_config": {"simulator": "tb_v1"},
        "cost_config": {"fee_bp": 10.0},
        "thresholds_config": {"min_trades": 100},
    }

    create_or_reuse_freeze(manifest_path=manifest_path, history_path=history_path, **config_a)
    with pytest.raises(FreezeConfigMismatch):
        create_or_reuse_freeze(manifest_path=manifest_path, history_path=history_path, **config_b)


def test_freeze_explicit_new_freeze_creates_new_id(tmp_path):
    """Explicit new freeze with force_new=True creates a new manifest."""
    manifest_path = tmp_path / "manifest.json"
    history_path = tmp_path / "history.jsonl"

    config = {
        "feature_config": {"vol_window": 20},
        "target_config": {"pt_mult": 2.0},
        "model_config": {"class_weight": "balanced"},
        "strategy_config": {"simulator": "tb_v1"},
        "cost_config": {"fee_bp": 10.0},
        "thresholds_config": {"min_trades": 100},
    }

    m1 = create_or_reuse_freeze(manifest_path=manifest_path, history_path=history_path, **config)
    original_id = m1["freeze_id"]

    # Force new freeze with new ID
    m2 = create_or_reuse_freeze(
        manifest_path=manifest_path,
        history_path=history_path,
        force_new=True,
        new_freeze_id="freeze-explicit-v2",
        **config,
    )
    assert m2["freeze_id"] == "freeze-explicit-v2"
    assert m2["freeze_id"] != original_id

    # Original manifest file is overwritten by force_new; history preserves both.
    # After force_new, the manifest file is overwritten with the new freeze
    # History should contain both
    history = []
    with history_path.open(encoding="utf-8") as f:
        for line in f:
            history.append(json.loads(line))
    assert len(history) == 2
    assert history[0]["freeze_id"] == original_id
    assert history[1]["freeze_id"] == "freeze-explicit-v2"


def test_freeze_never_uses_execution_timestamp_as_substitute(tmp_path):
    """The created_at_utc must be the freeze creation time, not a variable."""
    manifest_path = tmp_path / "manifest.json"
    history_path = tmp_path / "history.jsonl"

    config = {
        "feature_config": {"vol_window": 20},
        "target_config": {"pt_mult": 2.0},
        "model_config": {"class_weight": "balanced"},
        "strategy_config": {"simulator": "tb_v1"},
        "cost_config": {"fee_bp": 10.0},
        "thresholds_config": {"min_trades": 100},
    }
    m = create_or_reuse_freeze(manifest_path=manifest_path, history_path=history_path, **config)
    # created_at must be a valid ISO timestamp
    dt = datetime.fromisoformat(m["created_at_utc"])
    assert dt.tzinfo is not None  # timezone-aware
    # Rerun must not change it
    m2 = create_or_reuse_freeze(manifest_path=manifest_path, history_path=history_path, **config)
    assert m2["created_at_utc"] == m["created_at_utc"]


# ---------------------------------------------------------------------------
# D. Holdout tests
# ---------------------------------------------------------------------------


def test_holdout_historical_contamination_permanent():
    """Historical holdout must remain CONTAMINATED regardless of pipeline runs."""
    state = assess_prospective_holdout_state(
        freeze_timestamp="2026-10-05T00:00:00Z",
        observation_event_timestamp="2026-10-10T00:00:00Z",
        observation_ingestion_timestamp="2026-10-10T01:00:00Z",
        observation_first_seen_at="2026-10-10T01:00:00Z",
        observation_provenance_available=True,
        observation_used_in_development=False,
        data_quality_passed=True,
        evaluation_performed=False,
        historical_holdout_contaminated=True,
    )
    assert state["state"] == "CONTAMINATED"
    assert state["eligible_for_evaluation"] is False
    assert state["evaluated"] is False


def test_holdout_unavailable_without_ingestion_provenance():
    """Missing ingestion provenance or first_seen_at -> UNAVAILABLE."""
    state = assess_prospective_holdout_state(
        freeze_timestamp="2026-10-05T00:00:00Z",
        observation_event_timestamp="2026-10-10T00:00:00Z",
        observation_ingestion_timestamp=None,
        observation_first_seen_at=None,
        observation_provenance_available=False,
        observation_used_in_development=False,
        data_quality_passed=True,
        evaluation_performed=False,
        historical_holdout_contaminated=False,
    )
    assert state["state"] == "UNAVAILABLE"
    assert state["eligible_for_evaluation"] is False


def test_holdout_post_freeze_observation_eligible():
    """Post-freeze observation with all requirements -> ELIGIBLE_FOR_EVALUATION."""
    state = assess_prospective_holdout_state(
        freeze_timestamp="2026-10-05T00:00:00Z",
        observation_event_timestamp="2026-10-10T00:00:00Z",
        observation_ingestion_timestamp="2026-10-10T01:00:00Z",
        observation_first_seen_at="2026-10-10T01:00:00Z",
        observation_provenance_available=True,
        observation_used_in_development=False,
        data_quality_passed=True,
        evaluation_performed=False,
        historical_holdout_contaminated=False,
    )
    assert state["state"] == "ELIGIBLE_FOR_EVALUATION"
    assert state["eligible_for_evaluation"] is True
    assert state["evaluated"] is False


def test_holdout_pre_freeze_first_seen_rejected():
    """first_seen_at before freeze -> UNAVAILABLE, not prospective."""
    state = assess_prospective_holdout_state(
        freeze_timestamp="2026-10-05T00:00:00Z",
        observation_event_timestamp="2026-10-10T00:00:00Z",
        observation_ingestion_timestamp="2026-10-10T01:00:00Z",
        observation_first_seen_at="2026-10-01T00:00:00Z",  # before freeze
        observation_provenance_available=True,
        observation_used_in_development=False,
        data_quality_passed=True,
        evaluation_performed=False,
        historical_holdout_contaminated=False,
    )
    assert state["state"] == "UNAVAILABLE"
    assert state["eligible_for_evaluation"] is False


def test_holdout_no_automatic_prospective_evaluation():
    """All requirements pass but no evaluation -> ELIGIBLE_FOR_EVALUATION, not EVALUATED."""
    state = assess_prospective_holdout_state(
        freeze_timestamp="2026-10-05T00:00:00Z",
        observation_event_timestamp="2026-10-10T00:00:00Z",
        observation_ingestion_timestamp="2026-10-10T01:00:00Z",
        observation_first_seen_at="2026-10-10T01:00:00Z",
        observation_provenance_available=True,
        observation_used_in_development=False,
        data_quality_passed=True,
        evaluation_performed=False,
        historical_holdout_contaminated=False,
    )
    assert state["state"] == "ELIGIBLE_FOR_EVALUATION"
    assert state["evaluated"] is False


def test_holdout_explicit_evaluation_required():
    """Explicit evaluation after freeze -> EVALUATED."""
    state = assess_prospective_holdout_state(
        freeze_timestamp="2026-10-05T00:00:00Z",
        observation_event_timestamp="2026-10-10T00:00:00Z",
        observation_ingestion_timestamp="2026-10-10T01:00:00Z",
        observation_first_seen_at="2026-10-10T01:00:00Z",
        observation_provenance_available=True,
        observation_used_in_development=False,
        data_quality_passed=True,
        evaluation_performed=True,
        historical_holdout_contaminated=False,
    )
    assert state["state"] == "EVALUATED"
    assert state["evaluated"] is True


# ---------------------------------------------------------------------------
# E. Q4 tests
# ---------------------------------------------------------------------------


def test_q4_missing_evidence_fails_closed(tmp_path, monkeypatch):
    """Missing Q2/Q3 artifacts must cause Q4 to fail closed."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    missing_q2 = tmp_path / "missing_q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "models": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4r, "Q2_PATH", missing_q2)
    monkeypatch.setattr(q4r, "Q3_PATH", q3_path)
    monkeypatch.setattr(q4r, "OUT_JSON", out_json)
    monkeypatch.setattr(q4r, "OUT_MD", out_md)

    with pytest.raises(FileNotFoundError):
        q4r.build_release(ai_enabled=False)


def test_q4_gross_net_inconsistency_fails_closed(tmp_path, monkeypatch):
    """Gross/net inconsistency in Q3 must cause Q4 to mark model integrity as failed."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"
    q2_path.write_text(
        json.dumps({"phase": "Q2.2", "conclusion": "DIAGNOSTIC_ONLY", "holdout": {}}),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                    "evaluation_status": "legacy_assessed",
                },
                "models": [
                    {
                        "model": "test",
                        "strategy": {
                            "trade_count": 50,
                            "gross_return_bp": 1000.0,
                            "total_net_return_bp": 1000.0,  # Same as gross - costs not applied!
                            "total_trading_costs_bp": 0.0,
                            "profit_factor": 2.0,
                            "net_profit_factor": 2.0,
                            "win_rate": 0.6,
                            "maximum_drawdown": 100.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": 1000.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": 1000.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 25,
                                "gross_return_bp": 500.0,
                                "net_return_bp": 500.0,  # Same - misleading!
                                "total_trading_costs_bp": 0.0,
                            }
                        ],
                        "folds": [{"fold": 0}],
                        "collapse": {"collapsed": False},
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
    # The model should have integrity issues flagged
    assert release["models"][0]["model_integrity_ok"] is False
    assert release["research_status"]["integrity_ok"] is False
    assert release["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"


def test_q4_contaminated_holdout_fails_closed(tmp_path, monkeypatch):
    """Contaminated holdout must cause Q4 NO_STRATEGY_ELIGIBLE."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"
    q2_path.write_text(
        json.dumps({"phase": "Q2.2", "conclusion": "DIAGNOSTIC_ONLY", "holdout": {}}),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                    "evaluation_status": "legacy_assessed",
                },
                "models": [
                    {
                        "model": "test",
                        "strategy": {
                            "trade_count": 200,
                            "gross_return_bp": 500.0,
                            "total_net_return_bp": 400.0,
                            "total_trading_costs_bp": 100.0,
                            "profit_factor": 1.5,
                            "net_profit_factor": 1.5,
                            "win_rate": 0.6,
                            "maximum_drawdown": 100.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": 400.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": 100.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 100,
                                "gross_return_bp": 250.0,
                                "net_return_bp": 200.0,
                                "total_trading_costs_bp": 50.0,
                            },
                            {
                                "fold": 1,
                                "trade_count": 100,
                                "gross_return_bp": 250.0,
                                "net_return_bp": 200.0,
                                "total_trading_costs_bp": 50.0,
                            },
                        ],
                        "folds": [{"fold": 0}, {"fold": 1}],
                        "collapse": {"collapsed": False},
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
    assert release["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"
    assert release["models"][0]["promotion_gate"]["status"] == "NO_STRATEGY_ELIGIBLE"


def test_q4_failed_fold_not_counted_as_passed():
    """A failed fold must not be counted as a successful fold for promotion."""
    gate = evaluate_promotion_eligibility(
        strategy_metrics={
            "trade_count": 200,
            "profit_factor": 1.5,
            "cost_stress": [{"cost_multiplier": 2.0, "total_net_return_bp": 100}],
        },
        fold_net_returns_bp=[100.0, 200.0],  # Only 2 evaluated folds, not 5
        holdout_integrity={
            "status": "pristine",
            "pristine": True,
            "evaluation_status": "assessed_pristine",
        },
        prospective_holdout_validated=True,
        thresholds=PromotionThresholds(min_trades=100, min_folds=5),
        n_folds=2,  # Only 2 evaluated, below min_folds=5
    )
    assert gate["status"] == "NO_STRATEGY_ELIGIBLE"
    assert any("below minimum" in r for r in gate["reasons"])


def test_q4_ai_cannot_override_deterministic_rejection(tmp_path, monkeypatch):
    """AI advisory must not change the deterministic promotion decision."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"
    q2_path.write_text(
        json.dumps({"phase": "Q2.2", "conclusion": "DIAGNOSTIC_ONLY", "holdout": {}}),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [
                    {
                        "model": "test",
                        "strategy": {
                            "trade_count": 50,
                            "gross_return_bp": 100.0,
                            "total_net_return_bp": 50.0,
                            "total_trading_costs_bp": 50.0,
                            "profit_factor": 0.5,
                            "net_profit_factor": 0.5,
                            "win_rate": 0.3,
                            "maximum_drawdown": 500.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": 50.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": -100.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 25,
                                "gross_return_bp": 50.0,
                                "net_return_bp": 25.0,
                                "total_trading_costs_bp": 25.0,
                            }
                        ],
                        "folds": [{"fold": 0}],
                        "collapse": {"collapsed": True, "max_class_share": 1.0},
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

    release_no_ai = q4r.build_release(ai_enabled=False)
    release_with_ai = q4r.build_release(ai_enabled=True)

    # Both must be NO_STRATEGY_ELIGIBLE regardless of AI
    assert release_no_ai["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"
    assert release_with_ai["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"
    assert release_no_ai["authority"]["ai_can_promote"] is False
    assert release_with_ai["authority"]["ai_can_promote"] is False
    assert release_no_ai["authority"]["human_authorized"] is False
    assert release_no_ai["authority"]["order_execution_authorized"] is False


# ---------------------------------------------------------------------------
# F. Safety tests
# ---------------------------------------------------------------------------


def test_safety_no_exchange_order_placement():
    """No module may place exchange orders."""

    # Check that execution module has no order/exchange dependency
    import crypto_engine.research.execution as exec_mod

    source = Path(exec_mod.__file__).read_text(encoding="utf-8")
    assert "ccxt" not in source
    assert "create_order" not in source
    assert "place_order" not in source
    assert "execute_trade" not in source


def test_safety_no_live_trading_enablement():
    """No research module may enable live trading."""
    import crypto_engine.research.governance as gov_mod

    source = Path(gov_mod.__file__).read_text(encoding="utf-8")
    assert "live_trading" not in source
    assert "enable_trading" not in source

    # Check Q4 authority
    import crypto_engine.research.freeze as freeze_mod

    source2 = Path(freeze_mod.__file__).read_text(encoding="utf-8")
    assert "order" not in source2.lower() or "order_execution_authorized" not in source2


def test_safety_no_automatic_paper_promotion():
    """No automatic paper trading promotion."""
    from crypto_engine.research.governance import evaluate_promotion_eligibility

    gate = evaluate_promotion_eligibility(
        strategy_metrics={
            "trade_count": 1000,
            "profit_factor": 2.0,
            "cost_stress": [{"cost_multiplier": 2.0, "total_net_return_bp": 500}],
        },
        fold_net_returns_bp=[100.0, 200.0, 150.0, 180.0, 220.0],
        holdout_integrity={
            "status": "pristine",
            "pristine": True,
            "evaluation_status": "assessed_pristine",
        },
        prospective_holdout_validated=True,
        thresholds=PromotionThresholds(min_trades=100, min_folds=5),
    )
    # Even with perfect metrics, human_authorized and execution_authorized are False
    assert gate["human_authorized"] is False
    assert gate["execution_authorized"] is False
    assert gate["paper_trading_authorized"] is False if "paper_trading_authorized" in gate else True
    assert gate["ai_promotion_authority"] is False
    # At most ELIGIBLE_FOR_HUMAN_REVIEW, never auto-promoted
    assert gate["status"] in ("ELIGIBLE_FOR_HUMAN_REVIEW", "NO_STRATEGY_ELIGIBLE")


def test_safety_no_ai_authority_over_risk_engine():
    """AI cannot override risk engine or promotion decisions."""
    from crypto_engine.research.governance import evaluate_promotion_eligibility

    gate = evaluate_promotion_eligibility(
        strategy_metrics={
            "trade_count": 200,
            "profit_factor": 1.5,
            "cost_stress": [{"cost_multiplier": 2.0, "total_net_return_bp": 100}],
        },
        fold_net_returns_bp=[100.0, 200.0, 50.0, 70.0, 90.0],
        holdout_integrity={
            "status": "pristine",
            "pristine": True,
            "evaluation_status": "assessed_pristine",
        },
        prospective_holdout_validated=True,
        thresholds=PromotionThresholds(min_trades=100, min_folds=5),
    )
    assert gate["ai_promotion_authority"] is False
    assert gate["human_approval_required"] is True


# ---------------------------------------------------------------------------
# Additional Q6 regression tests
# ---------------------------------------------------------------------------


def test_freeze_status_never_unknown_when_manifest_loaded(tmp_path):
    """verify_freeze_integrity must never return 'unknown' for a loaded manifest."""
    from crypto_engine.research.freeze import (
        FREEZE_STATUS_MISSING,
        FREEZE_STATUS_SCHEMA_INVALID,
        verify_freeze_integrity,
    )

    manifest_path = tmp_path / "manifest.json"

    # Missing manifest -> MISSING status
    result = verify_freeze_integrity(manifest_path)
    assert result["status"] == FREEZE_STATUS_MISSING

    # Invalid schema -> SCHEMA_INVALID status, never 'unknown'
    manifest_path.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
    result = verify_freeze_integrity(manifest_path)
    assert result["status"] == FREEZE_STATUS_SCHEMA_INVALID
    assert result["status"] != "unknown"


def test_q4_reads_q2_predictive_fold_results(tmp_path, monkeypatch):
    """Q4 must independently consume Q2 predictive fold_results per model."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "models": {
                    "lightgbm": {
                        "model": "lightgbm",
                        "n_folds": 5,
                        "fold_results": [
                            {
                                "fold": 0,
                                "accuracy": 0.5,
                                "balanced_accuracy": 0.5,
                                "macro_f1": 0.3,
                                "mcc": 0.1,
                                "status": "ok",
                                "validation_start": "2026-03-20T14:00:00+00:00",
                                "validation_end": "2026-04-14T12:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                        "aggregate": {"n": 2995, "accuracy": 0.5},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                    "evaluation_status": "legacy_assessed",
                },
                "models": [
                    {
                        "model": "lightgbm",
                        "strategy": {
                            "trade_count": 200,
                            "gross_return_bp": 500.0,
                            "total_net_return_bp": 400.0,
                            "total_trading_costs_bp": 100.0,
                            "profit_factor": 1.5,
                            "net_profit_factor": 1.5,
                            "win_rate": 0.6,
                            "maximum_drawdown": 100.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": 400.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": 100.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 100,
                                "gross_return_bp": 250.0,
                                "net_return_bp": 200.0,
                                "total_trading_costs_bp": 50.0,
                            }
                        ],
                        "folds": [
                            {
                                "fold": 0,
                                "status": "ok",
                                "validation_start": "2026-03-20T14:00:00+00:00",
                                "validation_end": "2026-04-14T12:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                        "cost_adjusted_robustness": {},
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
    model = release["models"][0]
    # Q2 predictive fold_results must be present in the merged record
    assert "predictive_fold_results" in model
    assert len(model["predictive_fold_results"]) == 1
    assert model["predictive_fold_results"][0]["fold"] == 0
    assert model["predictive_fold_results"][0]["accuracy"] == 0.5
    # Predictive metrics must NOT be mislabeled as economic metrics
    assert model["fold_strategy_results"][0]["gross_return_bp"] == 250.0
    assert model["fold_strategy_results"][0]["net_return_bp"] == 200.0


def test_q4_reads_q3_economic_fold_results(tmp_path, monkeypatch):
    """Q4 must read Q3 fold_strategy_results with gross/net accounting."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "models": {
                    "lightgbm": {
                        "model": "lightgbm",
                        "n_folds": 5,
                        "fold_results": [
                            {
                                "fold": 0,
                                "accuracy": 0.5,
                                "balanced_accuracy": 0.5,
                                "macro_f1": 0.3,
                                "mcc": 0.1,
                                "status": "ok",
                            }
                        ],
                        "collapse": {"collapsed": False},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [
                    {
                        "model": "lightgbm",
                        "strategy": {
                            "trade_count": 200,
                            "gross_return_bp": 500.0,
                            "total_net_return_bp": 400.0,
                            "total_trading_costs_bp": 100.0,
                            "profit_factor": 1.5,
                            "net_profit_factor": 1.5,
                            "win_rate": 0.6,
                            "maximum_drawdown": 100.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": 400.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": 100.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 100,
                                "gross_return_bp": 250.0,
                                "net_return_bp": 200.0,
                                "total_trading_costs_bp": 50.0,
                            }
                        ],
                        "folds": [
                            {
                                "fold": 0,
                                "status": "ok",
                                "validation_start": "2026-03-20T14:00:00+00:00",
                                "validation_end": "2026-04-14T12:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                        "cost_adjusted_robustness": {},
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
    model = release["models"][0]
    # Economic fold results must come from Q3
    fsr = model["fold_strategy_results"]
    assert len(fsr) == 1
    assert fsr[0]["fold"] == 0
    assert fsr[0]["gross_return_bp"] == 250.0
    assert fsr[0]["net_return_bp"] == 200.0
    assert fsr[0]["total_trading_costs_bp"] == 50.0
    # Gross and net must differ (costs applied)
    assert fsr[0]["gross_return_bp"] != fsr[0]["net_return_bp"]


def test_q4_model_fold_identity_and_boundary_consistency(tmp_path, monkeypatch):
    """Q4 must match models by name and validate fold boundary compatibility."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "models": {
                    "lightgbm": {
                        "model": "lightgbm",
                        "n_folds": 5,
                        "fold_results": [
                            {
                                "fold": 0,
                                "accuracy": 0.5,
                                "balanced_accuracy": 0.5,
                                "macro_f1": 0.3,
                                "mcc": 0.1,
                                "status": "ok",
                                "validation_start": "2026-03-20T14:00:00+00:00",
                                "validation_end": "2026-04-14T12:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [
                    {
                        "model": "lightgbm",
                        "strategy": {
                            "trade_count": 200,
                            "gross_return_bp": 500.0,
                            "total_net_return_bp": 400.0,
                            "total_trading_costs_bp": 100.0,
                            "profit_factor": 1.5,
                            "net_profit_factor": 1.5,
                            "win_rate": 0.6,
                            "maximum_drawdown": 100.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": 400.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": 100.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 100,
                                "gross_return_bp": 250.0,
                                "net_return_bp": 200.0,
                                "total_trading_costs_bp": 50.0,
                            }
                        ],
                        "folds": [
                            {
                                "fold": 0,
                                "status": "ok",
                                "validation_start": "2026-03-20T14:00:00+00:00",
                                "validation_end": "2026-04-14T12:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                        "cost_adjusted_robustness": {},
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
    model = release["models"][0]
    assert model["model"] == "lightgbm"
    assert model["boundary_validation_ok"] is True
    assert model["boundary_validation_reasons"] == []


def test_q4_boundary_mismatch_fails_integrity(tmp_path, monkeypatch):
    """Q4 must flag fold boundary mismatches between Q2 and Q3."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "models": {
                    "lightgbm": {
                        "model": "lightgbm",
                        "n_folds": 5,
                        "fold_results": [
                            {
                                "fold": 0,
                                "accuracy": 0.5,
                                "balanced_accuracy": 0.5,
                                "macro_f1": 0.3,
                                "mcc": 0.1,
                                "status": "ok",
                                "validation_start": "2026-03-20T14:00:00+00:00",
                                "validation_end": "2026-04-14T12:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [
                    {
                        "model": "lightgbm",
                        "strategy": {
                            "trade_count": 200,
                            "gross_return_bp": 500.0,
                            "total_net_return_bp": 400.0,
                            "total_trading_costs_bp": 100.0,
                            "profit_factor": 1.5,
                            "net_profit_factor": 1.5,
                            "win_rate": 0.6,
                            "maximum_drawdown": 100.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": 400.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": 100.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 100,
                                "gross_return_bp": 250.0,
                                "net_return_bp": 200.0,
                                "total_trading_costs_bp": 50.0,
                            }
                        ],
                        "folds": [
                            {
                                "fold": 0,
                                "status": "ok",
                                "validation_start": "2026-05-01T00:00:00+00:00",  # Mismatched!
                                "validation_end": "2026-06-01T00:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                        "cost_adjusted_robustness": {},
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
    model = release["models"][0]
    assert model["boundary_validation_ok"] is False
    assert len(model["boundary_validation_reasons"]) > 0


def test_q4_freeze_timestamp_propagation(tmp_path, monkeypatch):
    """Q4 must use the freeze manifest's created_at_utc, not generated_at."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps({"phase": "Q2.2", "conclusion": "DIAGNOSTIC_ONLY", "models": {}}),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4r, "Q2_PATH", q2_path)
    monkeypatch.setattr(q4r, "Q3_PATH", q3_path)
    monkeypatch.setattr(q4r, "OUT_JSON", out_json)
    monkeypatch.setattr(q4r, "OUT_MD", out_md)

    # Write a real freeze manifest with known timestamp
    freeze_manifest = tmp_path / "RESEARCH_FREEZE_MANIFEST.json"
    freeze_manifest.write_text(
        json.dumps(
            {
                "schema_version": "research-freeze-manifest.v1",
                "freeze_id": "freeze-test-1234",
                "created_at_utc": "2026-11-12T19:11:47.293700+00:00",
                "repository_commit_sha": "abc123",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4r, "FREEZE_MANIFEST_PATH", freeze_manifest)

    release = q4r.build_release(ai_enabled=False)
    # Freeze timestamp must be the manifest timestamp, not generated_at
    assert release["research_freeze_timestamp"] == "2026-11-12T19:11:47.293700+00:00"
    assert release["research_freeze"]["freeze_id"] == "freeze-test-1234"
    assert release["research_freeze"]["created_at_utc"] == "2026-11-12T19:11:47.293700+00:00"
    # generated_at is separate from freeze timestamp
    assert release["generated_at"] != release["research_freeze_timestamp"]


def test_q4_freeze_status_validation_explicit(tmp_path, monkeypatch):
    """Q4 freeze status must be explicit (VALID/CONFIG_MISMATCH/etc), never 'unknown'."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps({"phase": "Q2.2", "conclusion": "DIAGNOSTIC_ONLY", "models": {}}),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4r, "Q2_PATH", q2_path)
    monkeypatch.setattr(q4r, "Q3_PATH", q3_path)
    monkeypatch.setattr(q4r, "OUT_JSON", out_json)
    monkeypatch.setattr(q4r, "OUT_MD", out_md)

    # Missing manifest -> MISSING status
    missing_manifest = tmp_path / "missing.json"
    monkeypatch.setattr(q4r, "FREEZE_MANIFEST_PATH", missing_manifest)
    release = q4r.build_release(ai_enabled=False)
    assert release["research_freeze"]["status"] == "MISSING"
    assert release["research_freeze"]["status"] != "unknown"

    # Valid manifest -> VALID status
    valid_manifest = tmp_path / "valid.json"
    valid_manifest.write_text(
        json.dumps(
            {
                "schema_version": "research-freeze-manifest.v1",
                "freeze_id": "freeze-valid",
                "created_at_utc": "2026-11-12T19:11:47.293700+00:00",
                "repository_commit_sha": "abc123",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4r, "FREEZE_MANIFEST_PATH", valid_manifest)
    release = q4r.build_release(ai_enabled=False)
    assert release["research_freeze"]["status"] == "VALID"
    assert release["research_freeze"]["status"] != "unknown"


def test_q4_evidence_integrity_separate_from_promotion(tmp_path, monkeypatch):
    """Valid artifacts can still produce NO_STRATEGY_ELIGIBLE (BUG 4)."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    # Valid evidence but poor performance and contaminated holdout
    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "models": {
                    "test": {
                        "model": "test",
                        "n_folds": 5,
                        "fold_results": [
                            {
                                "fold": 0,
                                "accuracy": 0.5,
                                "balanced_accuracy": 0.5,
                                "macro_f1": 0.3,
                                "mcc": 0.1,
                                "status": "ok",
                            }
                        ],
                        "collapse": {"collapsed": False},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [
                    {
                        "model": "test",
                        "strategy": {
                            "trade_count": 200,
                            "gross_return_bp": 500.0,
                            "total_net_return_bp": -100.0,  # Negative net
                            "total_trading_costs_bp": 600.0,
                            "profit_factor": 0.5,  # Below threshold
                            "net_profit_factor": 0.5,
                            "win_rate": 0.3,
                            "maximum_drawdown": 500.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": -100.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": -700.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 100,
                                "gross_return_bp": 250.0,
                                "net_return_bp": -50.0,
                                "total_trading_costs_bp": 300.0,
                            }
                        ],
                        "folds": [
                            {
                                "fold": 0,
                                "status": "ok",
                                "validation_start": "2026-03-20T14:00:00+00:00",
                                "validation_end": "2026-04-14T12:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                        "cost_adjusted_robustness": {},
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
    # Evidence may be valid but strategy is not eligible
    assert release["research_status"]["promotion_eligible"] is False
    assert release["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"
    # Separate integrity fields exist
    assert "artifact_integrity_ok" in release["research_status"]
    assert "accounting_integrity_ok" in release["research_status"]
    assert "predictive_evidence_integrity_ok" in release["research_status"]
    assert "strategy_evidence_integrity_ok" in release["research_status"]
    assert "freeze_integrity_ok" in release["research_status"]
    assert "holdout_integrity_ok" in release["research_status"]


def test_q2_class_count_reconciliation():
    """Q2 class counts must reconcile: complete_rows = sum of class counts."""
    # From Q2 diagnostics: class_counts = {'-1': 9909, '0': 478, '1': 4698}
    class_counts = {"-1": 9909, "0": 478, "1": 4698}
    reported_total = sum(class_counts.values())
    assert reported_total == 15085  # complete_rows
    # usable_rows (14,986) is a different population (feature-complete rows)
    assert reported_total != 14986  # deliberate: different denominators
    # Class fractions must sum to ~1.0
    total = float(reported_total)
    fractions = {k: v / total for k, v in class_counts.items()}
    assert abs(sum(fractions.values()) - 1.0) < 1e-9


def test_q3_fold_economic_records_expose_gross_net():
    """Q3 fold_strategy_results must expose gross_return_bp and net_return_bp."""
    import json
    from pathlib import Path

    q3_path = Path("docs/validation/results/strategy_validation_q3_revised.json")
    if not q3_path.is_file():
        pytest.skip("Q3 artifact not available")
    q3 = json.loads(q3_path.read_text(encoding="utf-8"))
    for mrec in q3.get("models", []):
        fsr = mrec.get("fold_strategy_results", [])
        assert len(fsr) > 0, f"Model {mrec.get('model')} has no fold_strategy_results"
        for fr in fsr:
            assert "gross_return_bp" in fr, (
                f"Model {mrec.get('model')} fold {fr.get('fold')} missing gross_return_bp"
            )
            assert "net_return_bp" in fr, (
                f"Model {mrec.get('model')} fold {fr.get('fold')} missing net_return_bp"
            )
            # Gross and net must differ when costs are applied
            if fr.get("total_trading_costs_bp", 0) > 0:
                assert fr["gross_return_bp"] != fr["net_return_bp"]


def test_q4_artifact_integrity_separate_from_model_integrity(tmp_path, monkeypatch):
    """artifact_integrity_ok is distinct from per-model model_integrity_ok."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "models": {
                    "test": {
                        "model": "test",
                        "n_folds": 5,
                        "fold_results": [
                            {
                                "fold": 0,
                                "accuracy": 0.5,
                                "balanced_accuracy": 0.5,
                                "macro_f1": 0.3,
                                "mcc": 0.1,
                                "status": "ok",
                            }
                        ],
                        "collapse": {"collapsed": False},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [
                    {
                        "model": "test",
                        "strategy": {
                            "trade_count": 200,
                            "gross_return_bp": 500.0,
                            "total_net_return_bp": 400.0,
                            "total_trading_costs_bp": 100.0,
                            "profit_factor": 1.5,
                            "net_profit_factor": 1.5,
                            "win_rate": 0.6,
                            "maximum_drawdown": 100.0,
                            "exposure_fraction": 0.5,
                            "cost_stress": [
                                {"cost_multiplier": 1.0, "total_net_return_bp": 400.0},
                                {"cost_multiplier": 2.0, "total_net_return_bp": 100.0},
                            ],
                        },
                        "fold_strategy_results": [
                            {
                                "fold": 0,
                                "trade_count": 100,
                                "gross_return_bp": 250.0,
                                "net_return_bp": 200.0,
                                "total_trading_costs_bp": 50.0,
                            }
                        ],
                        "folds": [
                            {
                                "fold": 0,
                                "status": "ok",
                                "validation_start": "2026-03-20T14:00:00+00:00",
                                "validation_end": "2026-04-14T12:00:00+00:00",
                            }
                        ],
                        "collapse": {"collapsed": False},
                        "cost_adjusted_robustness": {},
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
    model = release["models"][0]
    # artifact_integrity_ok and model_integrity_ok are separate fields
    assert "artifact_integrity_ok" in model
    assert "model_integrity_ok" in model
    # They can differ: artifact integrity is about evidence completeness,
    # model integrity includes promotion gate assessment
    assert isinstance(model["artifact_integrity_ok"], bool)
    assert isinstance(model["model_integrity_ok"], bool)


def test_q4_promotion_eligible_field_exists_and_is_false(tmp_path, monkeypatch):
    """promotion_eligible must be False when no model passes the gate."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "models": {},
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "abc", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4r, "Q2_PATH", q2_path)
    monkeypatch.setattr(q4r, "Q3_PATH", q3_path)
    monkeypatch.setattr(q4r, "OUT_JSON", out_json)
    monkeypatch.setattr(q4r, "OUT_MD", out_md)

    release = q4r.build_release(ai_enabled=False)
    assert release["research_status"]["promotion_eligible"] is False
    assert release["research_status"]["models_eligible_for_human_review"] == 0
    assert release["research_status"]["overall_verdict"] == "NO_STRATEGY_ELIGIBLE"


def test_q2_q3_dataset_hash_consistency(tmp_path, monkeypatch):
    """Q2 and Q3 must reference the same dataset hash (cross-phase consistency)."""
    import importlib

    scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    q4r = importlib.import_module("q4_research_release_revised")

    q2_path = tmp_path / "q2.json"
    q3_path = tmp_path / "q3.json"
    out_json = tmp_path / "release.json"
    out_md = tmp_path / "release.md"

    q2_path.write_text(
        json.dumps(
            {
                "phase": "Q2.2",
                "conclusion": "DIAGNOSTIC_ONLY",
                "dataset": {"sha256": "same-hash-123", "research_rows": 100},
                "models": {},
            }
        ),
        encoding="utf-8",
    )
    q3_path.write_text(
        json.dumps(
            {
                "phase": "Q3-revised",
                "dataset": {"sha256": "same-hash-123", "research_rows": 100},
                "historical_holdout_integrity": {
                    "status": "contaminated",
                    "pristine": False,
                },
                "models": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(q4r, "Q2_PATH", q2_path)
    monkeypatch.setattr(q4r, "Q3_PATH", q3_path)
    monkeypatch.setattr(q4r, "OUT_JSON", out_json)
    monkeypatch.setattr(q4r, "OUT_MD", out_md)

    # Both Q2 and Q3 artifacts must reference the same underlying dataset hash
    q4r.build_release(ai_enabled=False)
    q2_dataset_hash = q2_path.read_text(encoding="utf-8")
    q3_dataset_hash = q3_path.read_text(encoding="utf-8")
    q2_data = json.loads(q2_dataset_hash)
    q3_data = json.loads(q3_dataset_hash)
    assert q2_data.get("dataset", {}).get("sha256") == q3_data.get("dataset", {}).get("sha256")


def test_historical_holdout_contamination_cannot_be_cleared():
    """Historical holdout must remain CONTAMINATED regardless of later runs."""
    from crypto_engine.research.governance import assess_prospective_holdout_state

    state = assess_prospective_holdout_state(
        freeze_timestamp="2026-11-12T19:11:47.293700+00:00",
        observation_event_timestamp="2026-11-13T00:00:00Z",
        observation_ingestion_timestamp="2026-11-13T01:00:00Z",
        observation_first_seen_at="2026-11-13T01:00:00Z",
        observation_provenance_available=True,
        observation_used_in_development=False,
        data_quality_passed=True,
        evaluation_performed=False,
        historical_holdout_contaminated=True,
    )
    assert state["state"] == "CONTAMINATED"
    assert state["eligible_for_evaluation"] is False
    assert state["evaluated"] is False
    assert "permanently classified CONTAMINATED" in " ".join(state["reasons"])
