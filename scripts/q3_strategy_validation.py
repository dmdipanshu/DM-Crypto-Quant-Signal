"""Q3 — walk-forward, next-open and cost-stressed strategy validation.

All model predictions are made out-of-fold on the research head. A decision
formed at close t executes at open t+1 and exits at open after the fixed
holding horizon. The historical Q2 holdout is excluded completely.

Run:
    uv run python scripts/q3_strategy_validation.py
Optional cost overrides are explicit basis-point inputs, for example:
    uv run python scripts/q3_strategy_validation.py --fee-bp 10 --spread-bp 5 --slippage-bp 5
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
from crypto_engine.labels.triple_barrier import (  # noqa: E402
    TripleBarrierConfig,
    assert_no_leakage,
    label_triple_barrier,
    valid_mask,
)
from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline  # noqa: E402
from crypto_engine.models.sklearn_wrappers import SklearnLogistic  # noqa: E402
from crypto_engine.research.execution import evaluate_fixed_horizon_strategy  # noqa: E402
from crypto_engine.research.governance import (  # noqa: E402
    PromotionThresholds,
    assess_holdout_integrity,
    evaluate_promotion_eligibility,
)
from crypto_engine.validation.folds import label_intervals, make_folds  # noqa: E402
from crypto_engine.validation.holdout import split_holdout  # noqa: E402

DATA_PATH = ROOT / "data/canonical/binance/BTC-USDT/1h/candles.parquet"
OUT_JSON = ROOT / "docs/validation/results/strategy_validation_q3.json"
OUT_MD = ROOT / "docs/validation/STRATEGY_VALIDATION_Q3.md"
SEED = 42
HORIZON = 24
HOLDOUT_FRACTION = 0.15
FOLD_COUNT = 5
TRAIN_WINDOW_CAP = 6_000
EMBARGO_BARS = 24


def _read_frame() -> pd.DataFrame:
    if not DATA_PATH.is_file():
        raise FileNotFoundError(f"Canonical dataset not found: {DATA_PATH}")
    frame = pd.read_parquet(DATA_PATH)
    for candidate in ("timestamp", "index"):
        if candidate in frame.columns:
            frame = frame.set_index(candidate)
            break
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.DatetimeIndex(frame.index)
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    frame = frame.sort_index()
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("Canonical dataset timestamps must be unique and increasing.")
    required = {"open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Canonical dataset missing OHLCV columns: {missing}")
    return frame


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_factories() -> tuple[dict[str, Any], list[str]]:
    from crypto_engine.models.sklearn_wrappers import LightGBMModel

    factories: dict[str, Any] = {
        "majority": lambda: MajorityBaseline(random_state=SEED),
        "trend": lambda: TrendBaseline(random_state=SEED, ratio_col="trend_ratio_20_50"),
        "logistic": lambda: SklearnLogistic(random_state=SEED, C=1.0, max_iter=300),
    }
    skipped: list[str] = []
    try:
        import lightgbm  # noqa: F401

        factories["lightgbm"] = lambda: LightGBMModel(
            random_state=SEED,
            n_estimators=75,
            num_leaves=15,
            min_child_samples=30,
        )
    except ImportError:
        skipped.append("lightgbm: optional dependency not installed")
    return factories, skipped


def _fold_plan(n_rows: int):
    val_window = max(250, min(800, n_rows // 25))
    val_window = min(val_window, max(1, (n_rows - 100) // FOLD_COUNT))
    train_window = min(TRAIN_WINDOW_CAP, n_rows - FOLD_COUNT * val_window - HORIZON)
    if train_window <= HORIZON + 100:
        raise ValueError(f"Insufficient research rows for Q3 (n={n_rows}).")
    return make_folds(
        n=n_rows,
        train_window=train_window,
        val_window=val_window,
        n_folds=FOLD_COUNT,
        embargo_bars=max(EMBARGO_BARS, HORIZON),
        purge_intervals=label_intervals(None, HORIZON),
        min_samples=100,
    )


def _evaluate_model(
    name: str,
    factory: Any,
    X: pd.DataFrame,
    y: pd.Series,
    frame_research: pd.DataFrame,
    folds: list[Any],
    costs: dict[str, float],
) -> dict[str, Any]:
    prediction_by_timestamp = np.zeros(len(frame_research), dtype=np.int8)
    decision_mask = np.zeros(len(frame_research), dtype=bool)
    fold_by_timestamp = np.full(len(frame_research), -1, dtype=np.int16)
    fold_records: list[dict[str, Any]] = []
    y_values = y.to_numpy(dtype=int)

    for fold in folds:
        train_idx = np.asarray(fold.train_idx, dtype=int)
        val_idx = np.asarray(fold.val_idx, dtype=int)
        try:
            model = factory()
            model.fit(X.iloc[train_idx], y_values[train_idx])
            prediction = np.asarray(model.predict(X.iloc[val_idx]), dtype=int).reshape(-1)
            if prediction.size != val_idx.size:
                raise ValueError("prediction length differs from validation window")
            raw_times = X.index[val_idx]
            raw_idx = frame_research.index.get_indexer(raw_times)
            if bool((raw_idx < 0).any()):
                raise ValueError("OOS prediction timestamp is absent from raw OHLCV bars")
            prediction_by_timestamp[raw_idx] = prediction.astype(np.int8)
            decision_mask[raw_idx] = True
            fold_by_timestamp[raw_idx] = int(fold.fold_id)
            fold_records.append(
                {
                    "fold": int(fold.fold_id),
                    "status": "ok",
                    "train_rows": int(train_idx.size),
                    "validation_rows": int(val_idx.size),
                    "train_start": str(X.index[train_idx[0]]),
                    "train_end": str(X.index[train_idx[-1]]),
                    "validation_start": str(X.index[val_idx[0]]),
                    "validation_end": str(X.index[val_idx[-1]]),
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

    simulation = evaluate_fixed_horizon_strategy(
        opens=frame_research["open"].to_numpy(dtype=float),
        predictions=prediction_by_timestamp,
        decision_mask=decision_mask,
        timestamps=frame_research.index,
        horizon_bars=HORIZON,
        bar_seconds=3_600,
        fee_bp=costs["fee_bp"],
        spread_bp=costs["spread_bp"],
        slippage_bp=costs["slippage_bp"],
        funding_bp_per_bar=costs["funding_bp_per_bar"],
        stress_multipliers=(1.0, 1.5, 2.0),
    )

    fold_net: dict[int, float] = {int(f.fold_id): 0.0 for f in folds}
    fold_counts: dict[int, int] = {int(f.fold_id): 0 for f in folds}
    for trade in simulation["trades"]:
        decision_ts = pd.Timestamp(trade["decision_bar_open_at"])
        idx = int(frame_research.index.get_indexer([decision_ts])[0])
        fold_id = int(fold_by_timestamp[idx])
        if fold_id >= 0:
            fold_net[fold_id] += float(trade["net_return_bp"])
            fold_counts[fold_id] += 1
    fold_returns = [float(fold_net[k]) for k in sorted(fold_net)]
    fold_metrics = [
        {
            "fold": int(k),
            "trade_count": int(fold_counts[k]),
            "net_return_bp": float(fold_net[k]),
        }
        for k in sorted(fold_net)
    ]

    # A positive profit factor is undefined when the simulation had no losses;
    # preserve None rather than claiming infinite certainty.
    promotion = evaluate_promotion_eligibility(
        strategy_metrics=simulation,
        fold_net_returns_bp=fold_returns,
        holdout_integrity={"status": "unknown", "pristine": False},
        prospective_holdout_validated=False,
        thresholds=PromotionThresholds(),
    )
    return {
        "model": name,
        "oos_decisions": int(decision_mask.sum()),
        "oos_prediction_distribution": {
            str(v): int((prediction_by_timestamp[decision_mask] == v).sum())
            for v in (-1, 0, 1)
        },
        "folds": fold_records,
        "fold_strategy_results": fold_metrics,
        "strategy": simulation,
        "promotion_gate": promotion,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    frame = _read_frame()
    # Reserve the final raw bars before feature or label computation. This is
    # stronger than calculating over all rows and masking holdout predictions.
    research_raw_idx, holdout_raw_idx = split_holdout(len(frame), HOLDOUT_FRACTION)
    research_frame = frame.iloc[research_raw_idx].copy()
    features_all = compute_features(research_frame, chunk_rows=5_000)
    numeric_cols = [
        col for col in features_all.columns if pd.api.types.is_numeric_dtype(features_all[col])
    ]
    finite_mask = np.isfinite(features_all[numeric_cols].to_numpy(dtype=float)).all(axis=1)
    features = features_all.loc[finite_mask, numeric_cols]
    research_feature_index = features.index

    label_config = TripleBarrierConfig(
        pt_mult=2.0,
        sl_mult=1.0,
        max_horizon_bars=HORIZON,
        vol_window=20,
        cost_bp=0,
        side="both",
    )
    labels = label_triple_barrier(research_frame, label_config)
    complete = valid_mask(labels)
    aligned = features.index.intersection(labels.index[complete])
    aligned = aligned.intersection(research_feature_index)
    X = features.loc[aligned]
    y = labels.loc[aligned, "label"].astype(int)
    assert_no_leakage(X, labels.loc[aligned])
    if len(X) < 1_500:
        raise ValueError(f"Q3 requires at least 1500 usable research rows; got {len(X)}")

    folds = _fold_plan(len(X))
    research_end = research_feature_index[-1]
    frame_research = research_frame.loc[research_frame.index <= research_end].copy()
    factories, skipped = _model_factories()
    costs = {
        "fee_bp": float(args.fee_bp),
        "spread_bp": float(args.spread_bp),
        "slippage_bp": float(args.slippage_bp),
        "funding_bp_per_bar": float(args.funding_bp_per_bar),
    }
    model_results = [
        _evaluate_model(name, factory, X, y, frame_research, folds, costs)
        for name, factory in factories.items()
    ]

    holdout_start = str(frame.index[holdout_raw_idx[0]]) if len(holdout_raw_idx) else None
    known_holdout_integrity = assess_holdout_integrity(
        holdout_start="2026-07-24T06:00:00+00:00",
        training_end="2026-10-01T00:00:00+00:00",
        access_count=1,
        model_frozen=True,
        parameters_frozen=True,
    )
    reserved_tail_integrity = assess_holdout_integrity(
        holdout_start=holdout_start,
        training_end="2026-10-01T00:00:00+00:00",
        access_count=1,
        model_frozen=True,
        parameters_frozen=True,
    )
    result = {
        "schema": "signalforge.strategy-validation.q3.v1",
        "phase": "Q3",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "path": "data/canonical/binance/BTC-USDT/1h/candles.parquet",
            "sha256": _hash(DATA_PATH),
            "rows": len(frame),
            "start": str(frame.index[0]),
            "end": str(frame.index[-1]),
            "research_rows": len(X),
            "research_start": str(X.index[0]),
            "research_end": str(X.index[-1]),
            "historical_holdout_reserved_rows": len(holdout_raw_idx),
            "historical_holdout_start": holdout_start,
            "historical_holdout_evaluated_by_q3": False,
            "reserved_tail_integrity": reserved_tail_integrity,
        },
        "protocol": {
            "fold_count": len(folds),
            "train_window": len(folds[0].train_idx),
            "validation_window": len(folds[0].val_idx),
            "embargo_bars": max(EMBARGO_BARS, HORIZON),
            "purge_horizon_bars": HORIZON,
            "decision_timing": "bar close",
            "entry_timing": "next bar open",
            "exit_timing": "open after fixed horizon",
            "overlapping_positions": False,
            "selection": "no parameter or threshold search; all candidates reported",
        },
        "cost_assumptions": costs,
        "models_skipped": skipped,
        "models": model_results,
        "historical_holdout_integrity": known_holdout_integrity,
        "limitations": [
            "The previously reported 2026-07-24 to 2026-11-11 holdout overlaps production training through 2026-10-01 and is already consumed.",
            "Q3 applies its chronological raw-bar split before feature/label generation; the reserved tail receives no features, labels, predictions, or strategy simulation.",
            f"The reserved tail integrity is {reserved_tail_integrity['status']}; it overlaps/accesses prior Q2 holdout history and is not a prospective pristine holdout.",
            "Fixed-horizon diagnostic uses next-open prices; it does not model stop-loss, take-profit, funding settlements, liquidation, borrow or exchange microstructure.",
            "Fee, spread, slippage and funding inputs are declared scenario assumptions, not fetched live venue quotes.",
            "A candidate cannot pass the promotion gate without a new untouched prospective holdout and independent human review.",
        ],
        "order_execution_enabled": False,
        "paper_trading_enabled": False,
        "production_promotion_enabled": False,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")

    lines = [
        "# Strategy Validation — Q3",
        "",
        f"Generated: {result['generated_at']}",
        "",
        "## Protocol",
        "",
        f"- Research period: {X.index[0]} to {X.index[-1]}",
        f"- Purged/embargoed folds: {len(folds)}",
        f"- Horizon: {HORIZON} bars",
        "- Decision at close; entry at next open; exit at open after horizon",
        "- Non-overlapping positions; no threshold search",
        f"- Costs (bp): `{costs}`",
        "- Historical holdout excluded from this phase",
        "",
    ]
    for row in model_results:
        metrics = row["strategy"]
        lines.extend(
            [
                f"## Model: {row['model']}",
                "",
                f"- OOS decisions: {row['oos_decisions']}",
                f"- Trades: {metrics['trade_count']}",
                f"- Win rate: {metrics['win_rate']}",
                f"- Expectancy: {metrics['expectancy_bp']} bp",
                f"- Profit factor: {metrics['profit_factor']}",
                f"- Net total: {metrics['total_net_return_bp']:.2f} bp",
                f"- Max drawdown: {metrics['max_drawdown_bp']:.2f} bp",
                "",
                "| Cost multiplier | Trades | Net total (bp) | Profit factor |",
                "|---:|---:|---:|---:|",
            ]
        )
        for stress in metrics["cost_stress"]:
            lines.append(
                f"| {stress['cost_multiplier']:.1f}x | {stress['trade_count']} | "
                f"{stress['total_net_return_bp']:.2f} | {stress['profit_factor']} |"
            )
        lines.extend(
            [
                "",
                f"- Promotion gate: **{row['promotion_gate']['status']}**",
                "- Gate reasons: " + "; ".join(row["promotion_gate"]["reasons"]),
                "",
            ]
        )
    lines.extend(
        [
            "## Overall decision",
            "",
            "No strategy is production-eligible from this research run. A positive "
            "walk-forward result is not sufficient: a new prospective holdout, "
            "stress-cost survival, robustness and independent human review remain required.",
            "",
            "No order placement or paper-state mutation is enabled.",
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
    print(f"Q3 complete: {len(result['models'])} model candidates; no execution enabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
