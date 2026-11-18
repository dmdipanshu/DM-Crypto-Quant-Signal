"""Q3 revised — barrier-aligned walk-forward strategy validation.

Predictions from Q2.2's execution-aligned target are evaluated using the
*same* triple-barrier geometry: entry at the next-bar open, profit-target
and stop-loss barriers from causal volatility, conservative same-bar
collision (stop first), and max holding-period exit at the last bar's close.

This resolves the Q2.1/Q3 methodology gap where models were trained on
triple-barrier labels but evaluated with a fixed-horizon next-open exit.

Run:
    uv run python scripts/q3_strategy_validation_revised.py
    uv run python scripts/q3_strategy_validation_revised.py --fee-bp 10 --spread-bp 5 --slippage-bp 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.features.engineering import compute_features  # noqa: E402
from crypto_engine.labels.triple_barrier import assert_no_leakage, valid_mask  # noqa: E402
from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline  # noqa: E402
from crypto_engine.models.sklearn_wrappers import LightGBMModel, SklearnLogistic  # noqa: E402
from crypto_engine.research.execution import evaluate_triple_barrier_strategy  # noqa: E402
from crypto_engine.research.governance import (  # noqa: E402
    PromotionThresholds,
    assess_holdout_integrity,
    evaluate_promotion_eligibility,
)
from crypto_engine.research.modeling import detect_prediction_collapse  # noqa: E402
from crypto_engine.research.targets import TargetDefinition, label_execution_aligned  # noqa: E402
from crypto_engine.validation.folds import label_intervals, make_folds  # noqa: E402
from crypto_engine.validation.holdout import split_holdout  # noqa: E402

DATA_PATH = ROOT / "data/canonical/binance/BTC-USDT/1h/candles.parquet"
OUT_JSON = ROOT / "docs/validation/results/strategy_validation_q3_revised.json"
OUT_MD = ROOT / "docs/validation/STRATEGY_VALIDATION_Q3_REVISED.md"
SEED = 42
HORIZON = 24
PT_MULT = 2.0
SL_MULT = 1.0
HOLDOUT_FRACTION = 0.15
FOLD_COUNT = 5
TRAIN_WINDOW_CAP = 6_000
EMBARGO_BARS = 24


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_frame() -> pd.DataFrame:
    if not DATA_PATH.is_file():
        raise FileNotFoundError(f"Canonical dataset not found: {DATA_PATH}")
    frame = pd.read_parquet(DATA_PATH)
    for c in ("timestamp", "index"):
        if c in frame.columns:
            frame = frame.set_index(c)
            break
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.DatetimeIndex(frame.index)
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    return frame.sort_index()


def _factories() -> tuple[dict[str, Any], list[str]]:
    facs: dict[str, Any] = {
        "majority": lambda: MajorityBaseline(random_state=SEED),
        "trend": lambda: TrendBaseline(random_state=SEED, ratio_col="trend_ratio_20_50"),
        "logistic": lambda: SklearnLogistic(
            random_state=SEED, C=1.0, max_iter=300, class_weight="balanced"
        ),
    }
    skipped: list[str] = []
    try:
        import lightgbm  # noqa: F401

        facs["lightgbm"] = lambda: LightGBMModel(
            random_state=SEED,
            n_estimators=75,
            num_leaves=15,
            min_child_samples=30,
            class_weight="balanced",
        )
    except ImportError:
        skipped.append("lightgbm: not installed")
    return facs, skipped


def _fold_plan(n: int, obs_positions: np.ndarray):
    val_w = max(250, min(800, n // 25))
    val_w = min(val_w, max(1, (n - 100) // FOLD_COUNT))
    train_w = min(TRAIN_WINDOW_CAP, n - FOLD_COUNT * val_w - HORIZON)
    if train_w <= HORIZON + 100:
        raise ValueError(f"Insufficient research rows for Q3 revised (n={n}).")
    return make_folds(
        n=n,
        train_window=train_w,
        val_window=val_w,
        n_folds=FOLD_COUNT,
        embargo_bars=max(EMBARGO_BARS, HORIZON),
        purge_intervals=label_intervals(obs_positions, HORIZON),
        min_samples=100,
    )


def _evaluate_model(
    name: str,
    factory: Any,
    X: pd.DataFrame,
    y: pd.Series,
    research_frame: pd.DataFrame,
    labels_full: pd.DataFrame,
    folds: list[Any],
    costs: dict[str, float],
) -> dict[str, Any]:
    """Train per fold, collect OOS predictions, run barrier-aligned simulator."""
    n_bars = len(research_frame)
    predictions = np.zeros(n_bars, dtype=np.int8)
    decision_mask = np.zeros(n_bars, dtype=bool)
    fold_by_bar = np.full(n_bars, -1, dtype=np.int16)
    fold_records: list[dict[str, Any]] = []
    y_values = y.to_numpy(dtype=int)

    for fold in folds:
        ti = np.asarray(fold.train_idx, dtype=int)
        vi = np.asarray(fold.val_idx, dtype=int)
        try:
            model = factory()
            model.fit(X.iloc[ti], y_values[ti])
            preds = np.asarray(model.predict(X.iloc[vi]), dtype=int).reshape(-1)
            if preds.size != vi.size:
                raise ValueError("prediction length differs from val window")
            val_times = X.index[vi]
            bar_idx = research_frame.index.get_indexer(val_times)
            if bool((bar_idx < 0).any()):
                raise ValueError("OOS prediction timestamp absent from research bars")
            predictions[bar_idx] = preds.astype(np.int8)
            decision_mask[bar_idx] = True
            fold_by_bar[bar_idx] = int(fold.fold_id)
            fold_records.append(
                {
                    "fold": int(fold.fold_id),
                    "status": "ok",
                    "train_rows": int(ti.size),
                    "validation_rows": int(vi.size),
                    "train_start": str(X.index[ti[0]]),
                    "train_end": str(X.index[ti[-1]]),
                    "validation_start": str(X.index[vi[0]]),
                    "validation_end": str(X.index[vi[-1]]),
                    "purged_rows": int(fold.exclusion_record()["n_purged"]),
                    "embargo_rows": int(fold.exclusion_record()["n_embargo"]),
                }
            )
        except Exception as exc:
            fold_records.append(
                {
                    "fold": int(fold.fold_id),
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                }
            )

    # Align vol_at_entry from labels to research_frame
    vol_array = np.full(n_bars, np.nan)
    common = research_frame.index.intersection(labels_full.index)
    common_bar_idx = research_frame.index.get_indexer(common)
    valid_mask_arr = common_bar_idx >= 0
    vol_array[common_bar_idx[valid_mask_arr]] = labels_full.loc[
        common[valid_mask_arr], "vol_at_entry"
    ].to_numpy(dtype=float)

    simulation = evaluate_triple_barrier_strategy(
        opens=research_frame["open"].to_numpy(dtype=float),
        highs=research_frame["high"].to_numpy(dtype=float),
        lows=research_frame["low"].to_numpy(dtype=float),
        closes=research_frame["close"].to_numpy(dtype=float),
        predictions=predictions,
        decision_mask=decision_mask,
        timestamps=research_frame.index,
        vol_at_entry=vol_array,
        pt_mult=PT_MULT,
        sl_mult=SL_MULT,
        max_horizon_bars=HORIZON,
        fee_bp=costs["fee_bp"],
        spread_bp=costs["spread_bp"],
        slippage_bp=costs["slippage_bp"],
        funding_bp_per_bar=costs["funding_bp_per_bar"],
        stress_multipliers=(1.0, 1.5, 2.0),
        position_pct=1.0,
        starting_equity=10_000.0,
    )

    # Fold-level gross/net accounting (costs applied exactly once per trade).
    fold_net: dict[int, float] = {int(f.fold_id): 0.0 for f in folds}
    fold_gross: dict[int, float] = {int(f.fold_id): 0.0 for f in folds}
    fold_costs: dict[int, float] = {int(f.fold_id): 0.0 for f in folds}
    fold_trade_counts: dict[int, int] = {int(f.fold_id): 0 for f in folds}
    fold_trade_returns: dict[int, list[float]] = {int(f.fold_id): [] for f in folds}
    for trade in simulation["trades"]:
        ts = pd.Timestamp(trade["decision_bar_open_at"])
        idx = int(research_frame.index.get_indexer([ts])[0])
        fid = int(fold_by_bar[idx])
        if fid >= 0:
            g = float(trade.get("gross_return_bp", 0))
            n = float(trade.get("net_return_bp", 0))
            fold_gross[fid] += g
            fold_net[fid] += n
            fold_costs[fid] += g - n
            fold_trade_counts[fid] += 1
            fold_trade_returns[fid].append(n)
    fold_returns = [float(fold_net[k]) for k in sorted(fold_net)]
    fold_gross_returns = [float(fold_gross[k]) for k in sorted(fold_net)]

    # Collapse detection
    oos_preds = predictions[decision_mask]
    collapse = detect_prediction_collapse(oos_preds)

    # Holdout integrity record (contaminated - known)
    holdout_integrity = assess_holdout_integrity(
        holdout_start="2026-07-24T06:00:00+00:00",
        training_end="2026-10-01T00:00:00+00:00",
        access_count=1,
        model_frozen=True,
        parameters_frozen=True,
        previously_inspected=True,
        influenced_feature_or_threshold_selection=True,
    )

    gate = evaluate_promotion_eligibility(
        strategy_metrics=simulation,
        fold_net_returns_bp=fold_returns,
        holdout_integrity=holdout_integrity,
        prospective_holdout_validated=False,
        thresholds=PromotionThresholds(),
        prediction_collapse=collapse,
        n_folds=FOLD_COUNT,
    )

    # Cost-adjusted robustness metrics per model.
    evaluated_folds = [k for k in sorted(fold_net) if fold_trade_counts[k] > 0]
    gross_positive = sum(1 for k in evaluated_folds if fold_gross[k] > 0)
    net_positive = sum(1 for k in evaluated_folds if fold_net[k] > 0)
    n_eval = len(evaluated_folds)
    gross_pos_frac = gross_positive / n_eval if n_eval else 0.0
    net_pos_frac = net_positive / n_eval if n_eval else 0.0
    gross_avg_fold = float(np.mean(fold_gross_returns)) if fold_gross_returns else None
    net_avg_fold = float(np.mean(fold_returns)) if fold_returns else None
    net_std_fold = float(np.std(fold_returns, ddof=1)) if len(fold_returns) > 1 else None
    worst_net_fold = float(min(fold_returns)) if fold_returns else None
    best_net_fold = float(max(fold_returns)) if fold_returns else None
    stress_1x = simulation["cost_stress"][0] if simulation["cost_stress"] else {}
    stress_2x = next(
        (r for r in simulation["cost_stress"] if r.get("cost_multiplier") == 2.0),
        None,
    )

    return {
        "model": name,
        "oos_decisions": int(decision_mask.sum()),
        "oos_prediction_distribution": {str(v): int((oos_preds == v).sum()) for v in (-1, 0, 1)},
        "collapse": collapse,
        "folds": fold_records,
        "evaluated_folds": n_eval,
        "fold_strategy_results": [
            {
                "fold": int(k),
                "trade_count": int(fold_trade_counts[k]),
                "gross_return_bp": float(fold_gross[k]),
                "net_return_bp": float(fold_net[k]),
                "total_trading_costs_bp": float(fold_costs[k]),
            }
            for k in sorted(fold_net)
        ],
        "cost_adjusted_robustness": {
            "evaluated_folds": n_eval,
            "gross_positive_fold_count": gross_positive,
            "net_positive_fold_count": net_positive,
            "gross_positive_fold_fraction": gross_pos_frac,
            "net_positive_fold_fraction": net_pos_frac,
            "gross_average_fold_return_bp": gross_avg_fold,
            "net_average_fold_return_bp": net_avg_fold,
            "net_return_std_bp": net_std_fold,
            "worst_net_fold_bp": worst_net_fold,
            "best_net_fold_bp": best_net_fold,
            "performance_1x_cost": stress_1x.get("total_net_return_bp"),
            "performance_2x_cost": stress_2x.get("total_net_return_bp") if stress_2x else None,
        },
        "strategy": simulation,
        "promotion_gate": gate,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    frame = _load_frame()
    research_raw_idx, holdout_raw_idx = split_holdout(len(frame), HOLDOUT_FRACTION)
    research_frame = frame.iloc[research_raw_idx].copy()

    raw_features = compute_features(research_frame, chunk_rows=5_000)
    numeric_cols = [
        c for c in raw_features.columns if pd.api.types.is_numeric_dtype(raw_features[c])
    ]
    finite_mask = np.isfinite(raw_features[numeric_cols].to_numpy(dtype=float)).all(axis=1)
    features = raw_features.loc[finite_mask, numeric_cols]

    definition = TargetDefinition(
        pt_mult=PT_MULT, sl_mult=SL_MULT, max_horizon_bars=HORIZON, vol_window=20
    )
    labels_full = label_execution_aligned(research_frame, definition)
    complete = valid_mask(labels_full)
    aligned = features.index.intersection(labels_full.index[complete])
    X = features.loc[aligned]
    y = labels_full.loc[aligned, "label"].astype(int)
    assert_no_leakage(X, labels_full.loc[aligned])

    if len(X) < 1500:
        raise ValueError(f"Q3 revised requires >= 1500 usable rows; got {len(X)}")

    obs_ts = pd.to_datetime(labels_full.loc[aligned, "observable_at"], utc=True)
    obs_positions = X.index.get_indexer(obs_ts).astype(float)
    obs_positions = np.where(
        labels_full.loc[aligned, "barrier_hit"] == "incomplete", np.nan, obs_positions
    )
    folds = _fold_plan(len(X), obs_positions)

    costs = {
        "fee_bp": float(args.fee_bp),
        "spread_bp": float(args.spread_bp),
        "slippage_bp": float(args.slippage_bp),
        "funding_bp_per_bar": float(args.funding_bp_per_bar),
    }
    factories, skipped = _factories()
    model_results = [
        _evaluate_model(name, factory, X, y, research_frame, labels_full, folds, costs)
        for name, factory in factories.items()
    ]

    holdout_start = str(frame.index[holdout_raw_idx[0]]) if len(holdout_raw_idx) else None
    holdout_integrity = assess_holdout_integrity(
        holdout_start=holdout_start,
        training_end="2026-10-01T00:00:00+00:00",
        access_count=1,
        model_frozen=True,
        parameters_frozen=True,
        previously_inspected=True,
        influenced_feature_or_threshold_selection=True,
    )

    result = {
        "schema": "signalforge.strategy-validation.q3-revised.v1",
        "phase": "Q3-revised",
        "generated_at": _utc_now(),
        "dataset": {
            "path": str(DATA_PATH),
            "sha256": _sha256(DATA_PATH),
            "rows": len(frame),
            "start": str(frame.index[0]),
            "end": str(frame.index[-1]),
            "research_rows": len(X),
            "research_start": str(X.index[0]),
            "research_end": str(X.index[-1]),
        },
        "target": {
            "name": definition.name,
            "entry_convention": definition.entry_convention,
            "pt_mult": PT_MULT,
            "sl_mult": SL_MULT,
            "horizon": HORIZON,
            "definition_hash": definition.definition_hash(),
        },
        "protocol": {
            "simulator": "research_triple_barrier_next_open_v1",
            "fold_count": FOLD_COUNT,
            "train_window_cap": TRAIN_WINDOW_CAP,
            "embargo_bars": max(EMBARGO_BARS, HORIZON),
            "purge_basis": "actual observable bar",
            "class_weight": "balanced (training fold only)",
        },
        "cost_assumptions": costs,
        "models_skipped": skipped,
        "models": model_results,
        "historical_holdout_integrity": holdout_integrity,
        "order_execution_enabled": False,
        "paper_trading_enabled": False,
        "production_promotion_enabled": False,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")

    lines = [
        "# Strategy Validation (Revised) - Q3",
        "",
        f"Generated: {result['generated_at']}",
        "",
        "## Methodology correction",
        "",
        "Q3 revised uses **barrier-aligned execution**: the simulator executes the",
        "**same** triple-barrier geometry that produced the Q2.2 training targets.",
        f"Entry at next-bar open, barriers from causal vol x {PT_MULT}/{SL_MULT},",
        f"horizon {HORIZON} bars, conservative same-bar collision (stop first).",
        "",
        "## Protocol",
        "",
        f"- Research rows: {len(X)}",
        f"- Folds: {FOLD_COUNT} (actual-duration purge + {max(EMBARGO_BARS, HORIZON)}-bar embargo)",
        "- Class weight: balanced (training fold only)",
        f"- Costs (bp): `{costs}`",
        "",
    ]
    for m in model_results:
        s = m["strategy"]
        r = m["cost_adjusted_robustness"]
        lines.extend(
            [
                f"## Model: {m['model']}",
                "",
                f"- OOS decisions: {m['oos_decisions']}",
                f"- Prediction distribution: {m['oos_prediction_distribution']}",
                f"- Collapsed: {m['collapse'].get('collapsed')}",
                f"- Trades: {s['trade_count']}",
                f"- Win rate: {s['win_rate']}",
                f"- Expectancy: {s['expectancy_bp']} bp",
                f"- Gross profit factor: {s['gross_profit_factor']}",
                f"- Net profit factor: {s['net_profit_factor']}",
                f"- Gross return: {s['gross_return_bp']:.2f} bp",
                f"- Net return: {s['total_net_return_bp']:.2f} bp",
                f"- Total trading costs: {s['total_trading_costs_bp']:.2f} bp",
                f"- Account return: {s['account_return_pct']:.2f}%",
                f"- Max drawdown: {s['maximum_drawdown']:.2f} bp",
                f"- Exposure: {s['exposure_fraction']:.2%}",
                f"- Evaluated folds: {r['evaluated_folds']}",
                f"- Gross positive-fold fraction: {r['gross_positive_fold_fraction']:.0%}",
                f"- Net positive-fold fraction: {r['net_positive_fold_fraction']:.0%}",
                f"- Net average fold return: {r['net_average_fold_return_bp']} bp",
                f"- Worst net fold: {r['worst_net_fold_bp']} bp",
                f"- Best net fold: {r['best_net_fold_bp']} bp",
                "",
                "| Cost mult | Trades | Gross (bp) | Net (bp) | PF |",
                "|---:|---:|---:|---:|---:|",
            ]
        )
        for stress in s["cost_stress"]:
            lines.append(
                f"| {stress['cost_multiplier']:.1f}x | {stress['trade_count']} | "
                f"{stress['total_gross_return_bp']:.2f} | "
                f"{stress['total_net_return_bp']:.2f} | {stress['profit_factor']} |"
            )
        lines.extend(
            [
                "",
                f"- Gate: **{m['promotion_gate']['status']}**",
                "- Reasons: " + "; ".join(m["promotion_gate"]["reasons"]),
                "",
            ]
        )
    lines.extend(
        [
            "## Overall decision",
            "",
            "No strategy is production-eligible. A genuinely prospective holdout,",
            "positive net performance under cost stress, and independent human review",
            "remain required.",
            "",
            f"Holdout integrity: **{holdout_integrity.get('status', 'unknown')}**",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fee-bp", type=float, default=10.0)
    parser.add_argument("--spread-bp", type=float, default=5.0)
    parser.add_argument("--slippage-bp", type=float, default=5.0)
    parser.add_argument("--funding-bp-per-bar", type=float, default=0.0)
    args = parser.parse_args()
    result = run(args)
    n_models = len(result.get("models", []))
    print(f"Q3 revised complete: {n_models} models; no execution enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
