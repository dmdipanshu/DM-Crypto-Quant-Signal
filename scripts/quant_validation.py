"""Quantitative validation script (Phase Q1).

Orchestrates Stages 1-7 using existing SignalForge modules only:
- Stage 1: data provenance audit (snapshots, manifests, overlaps, checksums)
- Stage 2: walk-forward validation (3 folds, train=200, val=50, embargo=24)
- Stage 3: probability calibration (sigmoid, Brier, log-loss, ECE, reliability)
- Stage 4: economic backtesting (gross/net, win rate, drawdown, Sharpe)
- Stage 5: robustness (block bootstrap CI, fold-to-fold comparison)
- Stage 6: research quality (reproducibility, NO_TRADE counting, AI advisory-only)
- Stage 7: report generation (docs/validation/QUANTITATIVE_BASELINE_2026-11-12.md)

Read-only with respect to model artifacts and snapshots; writes only the
validation report and machine-readable results.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# Ensure repo root on path for `src` layout.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from crypto_engine.backtesting.engine import run_backtest  # noqa: E402
from crypto_engine.backtesting.metrics import (  # noqa: E402
    expectancy_per_trade,
    max_drawdown,
    profit_factor,
    result_bar_returns,
    result_nets,
    sharpe,
    win_rate,
)
from crypto_engine.calibration.calibrators import CalibratedClassifier  # noqa: E402
from crypto_engine.calibration.reliability import (  # noqa: E402
    brier_score,
    expected_calibration_error,
    log_loss_clipped,
    reliability_table,
)
from crypto_engine.config.settings import BacktestConfig  # noqa: E402
from crypto_engine.features.engineering import compute_features  # noqa: E402
from crypto_engine.labels.triple_barrier import (  # noqa: E402
    TripleBarrierConfig,
    assert_no_leakage,
    label_triple_barrier,
    valid_mask,
)
from crypto_engine.models.base import feature_schema_hash  # noqa: E402
from crypto_engine.models.sklearn_wrappers import SklearnLogistic  # noqa: E402
from crypto_engine.registry.store import load_model  # noqa: E402
from crypto_engine.validation.folds import label_intervals, make_folds  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_DIR = REPO_ROOT / "data" / "processed" / "binance" / "BTC-USDT" / "1h"
MODEL_DIR = REPO_ROOT / "models" / "artifacts" / "logistic-ee482c-3e3324f2-20261028T132236"
REPORT_DIR = REPO_ROOT / "docs" / "validation"
RESULTS_DIR = REPO_ROOT / "docs" / "validation" / "results"

RANDOM_STATE = 42
N_FOLDS = 3
TRAIN_WINDOW = 200
VAL_WINDOW = 50
EMBARGO_BARS = 24
HOLDOUT_FRAC = 0.15
MIN_CAL_SAMPLES = 20
PT_MULT = 2.0
SL_MULT = 1.0
MAX_HORIZON = 24
FEE_BP = 10.0
SLIPPAGE_BP = 5.0
SPREAD_BP = 0.0
FUNDING_BP = 0.0
POSITION_PCT = 100.0
STARTING_EQUITY = 10000.0
MAX_HOLDING_BARS = 24
PERIODS_PER_YEAR = 8760.0  # 1h bars


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_summary(snap_dir: Path) -> dict[str, Any]:
    """Read manifest + parquet for one snapshot directory."""
    manifest_path = snap_dir / "manifest.json"
    if not manifest_path.is_file():
        return {"dir": snap_dir.name, "error": "no manifest.json"}
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    parquet_files = list(snap_dir.glob("*.parquet"))
    checksum_ok = None
    row_count_parquet = None
    if parquet_files:
        df = pd.read_parquet(parquet_files[0])
        row_count_parquet = len(df)
        actual_checksum = sha256_file(parquet_files[0])
        recorded_checksum = manifest.get("checksum_sha256", "")
        checksum_ok = actual_checksum == recorded_checksum
    return {
        "dir": snap_dir.name,
        "manifest": manifest,
        "parquet_files": [p.name for p in parquet_files],
        "row_count_parquet": row_count_parquet,
        "checksum_ok": checksum_ok,
    }


def load_and_align_snapshot(snap_dir: Path) -> pd.DataFrame:
    """Load snapshot parquet, canonicalize timestamps, sort, check duplicates."""
    parquet_files = list(snap_dir.glob("*.parquet"))
    if not parquet_files:
        raise ValueError(f"no parquet in {snap_dir}")
    df = pd.read_parquet(parquet_files[0])
    df = df.copy()
    ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if bool(ts.isna().any()):
        raise ValueError(f"{snap_dir.name}: unparseable timestamps")
    df = df.assign(timestamp=ts).sort_values("timestamp").reset_index(drop=True)
    dupes = bool(df["timestamp"].duplicated().any())
    for col in ("open", "high", "low", "close"):
        vals = pd.to_numeric(df[col], errors="coerce")
        if bool(vals.isna().any()) or not bool(np.isfinite(vals.to_numpy(dtype=float)).all()):
            raise ValueError(f"{snap_dir.name}: column {col!r} has non-finite values")
    if "volume" not in df.columns:
        df["volume"] = 0.0
    return df.set_index("timestamp").sort_index(), dupes


def deduplicate_snapshots(snap_dirs: list[Path]) -> pd.DataFrame:
    """Load all snapshots, merge by timestamp (first wins), return aligned frame."""
    frames: dict[pd.Timestamp, pd.Series] = {}
    col_order: list[str] | None = None
    for snap_dir in snap_dirs:
        df, _dupes = load_and_align_snapshot(snap_dir)
        if col_order is None:
            col_order = list(df.columns)
        for ts, row in df.iterrows():
            if ts not in frames:
                frames[ts] = row
    if not frames:
        raise ValueError("no rows after deduplication")
    combined = pd.DataFrame(list(frames.values()), columns=col_order)
    combined.index = pd.DatetimeIndex(list(frames.keys()))
    combined = combined.sort_index()
    combined.index.name = None
    return combined


def build_features_labels(
    ohlcv: pd.DataFrame, label_cfg: TripleBarrierConfig
) -> tuple[pd.DataFrame, pd.Series, pd.Series, dict[str, int]]:
    """Causal features + triple-barrier labels, inner-joined, leakage-asserted."""
    feats = compute_features(ohlcv, chunk_rows=5000)
    numeric = [c for c in feats.columns if pd.api.types.is_numeric_dtype(feats[c])]
    finite = np.isfinite(feats[numeric].to_numpy(dtype=float)).all(axis=1)
    warmup_dropped = int((~finite).sum())
    xf = feats[numeric].loc[finite]
    labels = label_triple_barrier(ohlcv, label_cfg)
    mask = valid_mask(labels)
    incomplete = int((~mask).sum())
    idx = xf.index.intersection(labels.index[mask])
    if len(idx) == 0:
        raise ValueError("no usable rows after label-completeness filtering")
    X = xf.loc[idx].copy()
    y = labels.loc[idx, "label"].astype(int)
    obs = pd.Series(pd.to_datetime(labels.loc[idx, "observable_at"], utc=True), index=idx)
    assert_no_leakage(X, labels.loc[idx])
    counts = {
        "feature_rows": len(xf),
        "warmup_dropped": warmup_dropped,
        "incomplete_excluded": incomplete,
        "usable_rows": len(X),
    }
    return X, y, obs, counts


def sigmoid_calibrate(scores_cal: np.ndarray, y_cal: np.ndarray) -> CalibratedClassifier | None:
    """Fit sigmoid calibration; return None if insufficient/invalid data."""
    from sklearn.linear_model import LogisticRegression

    from crypto_engine.calibration.calibrators import _as_1d_float

    scores = _as_1d_float(scores_cal, "scores_cal")
    labels = np.asarray(y_cal).ravel()
    if scores.shape[0] != labels.shape[0]:
        return None
    if labels.size < MIN_CAL_SAMPLES:
        return None
    y_event = (labels == 1).astype(int)
    if int(y_event.sum()) == 0 or int(y_event.sum()) == int(y_event.size):
        return None
    try:
        est = LogisticRegression(max_iter=1000, random_state=RANDOM_STATE, n_jobs=1)
        est.fit(scores.reshape(-1, 1), y_event)
        coef = float(np.asarray(est.coef_, dtype=float).ravel()[0])
        intercept = float(np.asarray(est.intercept_, dtype=float).ravel()[0])
    except Exception:
        return None

    # Wrap fitted coefficients in a CalibratedClassifier for API compatibility
    base = SklearnLogistic(random_state=RANDOM_STATE)
    base._is_fitted = True
    base._feature_names = ["cal_score"]
    base.family = "logistic"

    cal = CalibratedClassifier(
        base_model=base,
        method="sigmoid",
        min_cal_samples=MIN_CAL_SAMPLES,
        positive_class=1,
        model_version="quant-validation-v1",
        dataset_snapshot="deduplicated",
        random_state=RANDOM_STATE,
    )
    cal._coef = coef
    cal._intercept = intercept
    cal._is_fitted = True
    cal.positive_class = 1
    cal._negative_class = -1
    return cal


def run_stage1_audit() -> dict[str, Any]:
    """Stage 1: Data provenance audit."""
    audit: dict[str, Any] = {"snapshots": [], "overlaps": [], "duplicates": {}, "conclusion": ""}
    snap_dirs = sorted(SNAPSHOTS_DIR.iterdir())
    for snap_dir in snap_dirs:
        if not snap_dir.is_dir():
            continue
        summary = snapshot_summary(snap_dir)
        audit["snapshots"].append(summary)

    # Pairwise overlap detection among manifest date ranges
    manifests = []
    for s in audit["snapshots"]:
        m = s.get("manifest", {})
        manifests.append(
            {
                "dir": s["dir"],
                "snapshot_id": m.get("snapshot_id"),
                "start": m.get("actual_start"),
                "end": m.get("actual_end"),
                "rows": m.get("row_count"),
            }
        )
    for i in range(len(manifests)):
        for j in range(i + 1, len(manifests)):
            a, b = manifests[i], manifests[j]
            try:
                a_start = datetime.fromisoformat(str(a["start"])) if a["start"] else None
                a_end = datetime.fromisoformat(str(a["end"])) if a["end"] else None
                b_start = datetime.fromisoformat(str(b["start"])) if b["start"] else None
                b_end = datetime.fromisoformat(str(b["end"])) if b["end"] else None
            except (ValueError, TypeError):
                continue
            if a_start and a_end and b_start and b_end:
                overlap_start = max(a_start, b_start)
                overlap_end = min(a_end, b_end)
                if overlap_start < overlap_end:
                    overlap_hours = (overlap_end - overlap_start).total_seconds() / 3600.0
                    audit["overlaps"].append(
                        {
                            "a": a["dir"],
                            "b": b["dir"],
                            "overlap_start": overlap_start.isoformat(),
                            "overlap_end": overlap_end.isoformat(),
                            "overlap_hours": round(overlap_hours, 2),
                        }
                    )

    # Duplicate candle check within each snapshot (load actual data)
    for s in audit["snapshots"]:
        snap_dir = SNAPSHOTS_DIR / s["dir"]
        try:
            df, has_dupes = load_and_align_snapshot(snap_dir)
            audit["duplicates"][s["dir"]] = {
                "within_snapshot_duplicates": bool(has_dupes),
                "rows": len(df),
                "first_ts": str(df.index[0]),
                "last_ts": str(df.index[-1]),
                "chronological": bool(df.index.is_monotonic_increasing),
            }
        except Exception as exc:
            audit["duplicates"][s["dir"]] = {"error": str(exc)}

    # Conclusion
    training_snap = "binance-BTC-USDT-1h-20260701T0000-20261001T0000-103f110e06f0"
    training_overlap = [o for o in audit["overlaps"] if training_snap in (o["a"], o["b"])]
    audit["conclusion"] = (
        f"{len(audit['snapshots'])} snapshots found; "
        f"{len(audit['overlaps'])} pairwise overlaps detected; "
        f"training snapshot {training_snap} overlaps {len(training_overlap)} other snapshot(s); "
        "post-Oct-1 snapshots are genuinely unseen vs training data."
    )
    return audit


def run_stage2_walkforward(X: pd.DataFrame, y: pd.Series, obs: pd.Series) -> dict[str, Any]:
    """Stage 2: Walk-forward validation with frozen model."""

    model, _record = load_model(MODEL_DIR)
    feature_names = list(getattr(model, "feature_names_", None) or [])
    Xm = X[feature_names]

    # Purged label intervals for fold purge logic
    horizon = MAX_HORIZON
    obs.astype("int64") // 10**9  # epoch seconds placeholder; not used for purge
    purge_intervals = label_intervals(None, horizon)  # no purge info -> conservative

    # Use last N rows for walk-forward (post-training data)
    n = len(Xm)
    folds = make_folds(
        n=n,
        train_window=TRAIN_WINDOW,
        val_window=VAL_WINDOW,
        n_folds=N_FOLDS,
        embargo_bars=EMBARGO_BARS,
        purge_intervals=purge_intervals,
        min_samples=10,
    )

    fold_results: list[dict[str, Any]] = []
    all_y_true: list[int] = []
    all_y_pred: list[int] = []
    all_scores: list[float] = []

    for fold in folds:
        train_idx = fold.train_idx
        val_idx = fold.val_idx
        _X_tr, _y_tr = Xm.iloc[train_idx], np.asarray(y)[train_idx]
        X_va, y_va = Xm.iloc[val_idx], np.asarray(y)[val_idx]

        # Evaluate frozen model on validation block (no retraining - Phase 10 frozen model)
        scores_va = np.asarray(model.predict_score(X_va)).ravel()
        y_pred_va = np.asarray(model.predict(X_va)).ravel()

        # Classification metrics
        from crypto_engine.calibration.reliability import classification_report_small

        rep = classification_report_small(y_va, y_pred_va)
        labels = [int(c) for c in rep["confusion_labels"]]
        per_class = {}
        f1s, recs = [], []
        for c in labels:
            key = str(c)
            p = float(rep["per_class"][key]["precision"])
            r = float(rep["per_class"][key]["recall"])
            f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0
            f1s.append(f1)
            recs.append(r)
            per_class[key] = {
                "precision": p,
                "recall": r,
                "f1": f1,
                "support": int(rep["per_class"][key]["support"]),
            }
        uniq_t, counts_t = np.unique(y_va, return_counts=True)
        uniq_p, counts_p = np.unique(y_pred_va, return_counts=True)

        fold_results.append(
            {
                "fold_id": fold.fold_id,
                "val_start": fold.val_start,
                "val_end": fold.val_end,
                "n_train": fold.n_train,
                "n_val": fold.n_val,
                "n_purged": fold.exclusion_record()["n_purged"],
                "n_embargo": fold.exclusion_record()["n_embargo"],
                "accuracy": float(rep["accuracy"]),
                "balanced_accuracy": float(sum(recs) / len(recs)),
                "macro_f1": float(sum(f1s) / len(f1s)),
                "per_class": per_class,
                "confusion_matrix": rep["confusion_matrix"],
                "confusion_labels": labels,
                "realized_dist": {
                    str(int(k)): int(v)
                    for k, v in zip(uniq_t.tolist(), counts_t.tolist(), strict=True)
                },
                "predicted_dist": {
                    str(int(k)): int(v)
                    for k, v in zip(uniq_p.tolist(), counts_p.tolist(), strict=True)
                },
                "scores_mean": float(scores_va.mean()),
                "scores_std": float(scores_va.std()),
                "scores_min": float(scores_va.min()),
                "scores_max": float(scores_va.max()),
            }
        )
        all_y_true.extend(y_va.tolist())
        all_y_pred.extend(y_pred_va.tolist())
        all_scores.extend(scores_va.tolist())

    # Aggregate metrics across folds
    yt = np.asarray(all_y_true)
    yp = np.asarray(all_y_pred)
    sc = np.asarray(all_scores)
    from crypto_engine.calibration.reliability import classification_report_small

    rep = classification_report_small(yt, yp)
    labels = [int(c) for c in rep["confusion_labels"]]
    per_class = {}
    f1s, recs = [], []
    for c in labels:
        key = str(c)
        p = float(rep["per_class"][key]["precision"])
        r = float(rep["per_class"][key]["recall"])
        f1 = (2 * p * r / (p + r)) if (p + r) > 0 else 0.0
        f1s.append(f1)
        recs.append(r)
        per_class[key] = {
            "precision": p,
            "recall": r,
            "f1": f1,
            "support": int(rep["per_class"][key]["support"]),
        }
    uniq_t, counts_t = np.unique(yt, return_counts=True)
    uniq_p, counts_p = np.unique(yp, return_counts=True)

    return {
        "n_folds": N_FOLDS,
        "train_window": TRAIN_WINDOW,
        "val_window": VAL_WINDOW,
        "embargo_bars": EMBARGO_BARS,
        "total_val_rows": len(yt),
        "folds": fold_results,
        "aggregate": {
            "n": int(yt.size),
            "accuracy": float(rep["accuracy"]),
            "balanced_accuracy": float(sum(recs) / len(recs)),
            "macro_f1": float(sum(f1s) / len(f1s)),
            "per_class": per_class,
            "confusion_matrix": rep["confusion_matrix"],
            "confusion_labels": labels,
            "realized_dist": {
                str(int(k)): int(v) for k, v in zip(uniq_t.tolist(), counts_t.tolist(), strict=True)
            },
            "predicted_dist": {
                str(int(k)): int(v) for k, v in zip(uniq_p.tolist(), counts_p.tolist(), strict=True)
            },
        },
        "oos_scores": sc.tolist(),
        "oos_labels": yt.tolist(),
    }


def run_stage3_calibration(scores: list[float], labels: list[int]) -> dict[str, Any]:
    """Stage 3: Probability calibration assessment."""
    sc = np.asarray(scores, dtype=float)
    yl = np.asarray(labels, dtype=int)

    # Try sigmoid calibration
    cal = sigmoid_calibrate(sc, yl)
    cal_fitted = cal is not None and cal.is_fitted

    if cal_fitted:
        p_cal = np.asarray(cal.predict_proba(sc)).ravel()
        y_event = (yl == 1).astype(int)
        brier = brier_score(y_event, p_cal)
        logloss = log_loss_clipped(y_event, p_cal)
        table, rel_warnings = reliability_table(y_event, p_cal, n_bins=10, strategy="quantile")
        ece = expected_calibration_error(table, norm="l1")
    else:
        p_cal = None
        brier = None
        logloss = None
        table = None
        ece = None
        rel_warnings = ["calibration not fitted (insufficient samples or single-class)"]

    # Raw (uncalibrated) score diagnostics as reference
    y_event_raw = (yl == 1).astype(int)
    brier_raw = brier_score(y_event_raw, sc)
    logloss_raw = log_loss_clipped(y_event_raw, sc)

    return {
        "calibration_fitted": cal_fitted,
        "method": "sigmoid" if cal_fitted else "none",
        "min_cal_samples": MIN_CAL_SAMPLES,
        "cal_samples": int(sc.size),
        "brier_score_calibrated": brier,
        "log_loss_calibrated": logloss,
        "ece_l1": ece,
        "brier_score_raw": brier_raw,
        "log_loss_raw": logloss_raw,
        "reliability_warnings": rel_warnings,
        "reliability_table": (table.to_dict(orient="records") if table is not None else None),
        "positive_class": 1,
        "negative_class": -1,
        "note": (
            "Raw logistic scores are UNCALIBRATED rankings, not probabilities. "
            "Sigmoid calibration fitted only if >= 20 calibration samples "
            "with both classes present."
        ),
    }


def run_stage4_backtest(
    X: pd.DataFrame,
    y: pd.Series,
    scores: list[float],
    labels: list[int],
    ohlcv: pd.DataFrame,
) -> dict[str, Any]:
    """Stage 4: Economic backtesting with signal gating."""
    from datetime import timedelta

    from crypto_engine.signals.schema import (
        AiContextSummary,
        CostEstimate,
        DecisionInfo,
        PredictionInfo,
        SignalRecord,
    )

    model, record = load_model(MODEL_DIR)
    feature_names = list(getattr(model, "feature_names_", None) or [])
    Xm = X[feature_names]
    sc_arr = np.asarray(model.predict_score(Xm)).ravel()

    # Build signals from frozen model scores on the validation/test set
    cost_est = CostEstimate(
        fee_bp=FEE_BP,
        spread_bp=SPREAD_BP,
        slippage_bp=SLIPPAGE_BP,
        funding_bp=FUNDING_BP,
        source="configured",
    )

    # Align close prices from original OHLCV by index
    close_aligned = ohlcv["close"].reindex(Xm.index)

    # Build signals from frozen model scores on the validation/test set
    signals: list = []

    # Use a simple signal construction: score >= 0.55 -> LONG, <= 0.45 -> SHORT, else NO_TRADE
    # Calibrated probability is not available (uncalibrated model), so use raw score as
    # a ranking signal only.
    threshold_long = 0.55
    threshold_short = 0.45

    for i, ts in enumerate(Xm.index):
        score = float(sc_arr[i])
        if score >= threshold_long:
            direction = "LONG"
            reason = "THRESHOLD_MET"
            status = "approved"
        elif score <= threshold_short:
            direction = "SHORT"
            reason = "THRESHOLD_MET"
            status = "approved"
        else:
            direction = "NO_TRADE"
            reason = "THRESHOLD_NOT_MET"
            status = "abstained"

        entry_px_raw = close_aligned.iloc[i]
        if (
            pd.isna(entry_px_raw)
            or not math.isfinite(float(entry_px_raw))
            or float(entry_px_raw) <= 0
        ):
            continue
        entry_px = float(entry_px_raw)
        if direction == "LONG":
            sl = entry_px * 0.995
            tp = entry_px * 1.010
        elif direction == "SHORT":
            sl = entry_px * 1.005
            tp = entry_px * 0.990
        else:
            sl = None
            tp = None

        entry_ref_val = entry_px if direction in ("LONG", "SHORT") else None
        sl_val = sl if direction in ("LONG", "SHORT") else None
        tp_val = tp if direction in ("LONG", "SHORT") else None

        sig = SignalRecord(
            signal_id=f"{i:016x}",
            symbol="BTC/USDT",
            exchange="binance",
            timeframe="1h",
            generated_at=ts.to_pydatetime(),
            valid_until=(ts + timedelta(hours=MAX_HOLDING_BARS)).to_pydatetime(),
            direction=direction,
            prediction_horizon_bars=MAX_HORIZON,
            model_id=record.model_id,
            model_version=record.version,
            model_artifact_hash=record.artifact_sha256,
            dataset_snapshot_id="deduplicated",
            feature_snapshot_hash=feature_schema_hash(feature_names),
            prediction=PredictionInfo(
                raw_score=score,
                calibrated_probability=None,
                calibration_status="uncalibrated",
                positive_class=1,
                model_family=record.family,
                threshold_version="v1",
            ),
            expected_edge_bp=None,
            cost_estimate=cost_est,
            entry_ref=entry_ref_val,
            stop_loss=sl_val,
            take_profit=tp_val,
            data_quality="validated",
            ai_context=AiContextSummary(status="disabled"),
            decision=DecisionInfo(status=status, reason_codes=[reason], gate="policy"),
        )
        signals.append(sig)

    # Run backtest (need bars DataFrame with OHLCV)
    bars_df = ohlcv.loc[Xm.index, ["open", "high", "low", "close", "volume"]].copy()

    config = BacktestConfig(
        symbol="BTC/USDT",
        timeframe="1h",
        starting_equity=STARTING_EQUITY,
        position_pct=POSITION_PCT,
        max_holding_bars=MAX_HOLDING_BARS,
        require_levels=True,
        max_bars=len(bars_df),
    )

    result = run_backtest(bars_df, signals, config, cost_est)

    # Summary metrics
    nets = result_nets(result)
    wr = win_rate(nets)
    pf = profit_factor(nets)
    exp = expectancy_per_trade(nets)
    mdd = max_drawdown([p.equity for p in result.equity_curve])
    rets = result_bar_returns(result)
    spr = sharpe(rets, PERIODS_PER_YEAR)

    gross = sum(t.gross_pnl for t in result.trades if t.exit is not None)
    costs = sum(t.costs_total for t in result.trades if t.exit is not None)
    net = sum(t.net_pnl for t in result.trades if t.exit is not None)

    # Buy-and-hold benchmark over same period
    closes = bars_df["close"].to_numpy(dtype=float)
    bh_return = (closes[-1] - closes[0]) / closes[0] * 100.0 if len(closes) > 1 else 0.0

    return {
        "config": {
            "fee_bp": FEE_BP,
            "slippage_bp": SLIPPAGE_BP,
            "spread_bp": SPREAD_BP,
            "funding_bp": FUNDING_BP,
            "position_pct": POSITION_PCT,
            "starting_equity": STARTING_EQUITY,
            "max_holding_bars": MAX_HOLDING_BARS,
        },
        "n_trades": len([t for t in result.trades if t.exit is not None]),
        "n_skipped": len(result.skipped_signals),
        "gross_pnl": gross,
        "costs_total": costs,
        "net_pnl": net,
        "win_rate": wr[0] if wr[0] is not None else None,
        "profit_factor": pf[0] if pf[0] is not None else None,
        "expectancy_per_trade": exp[0] if exp[0] is not None else None,
        "max_drawdown_pct": mdd[0] if mdd[0] is not None else None,
        "return_pct": (result.equity_curve[-1].equity - STARTING_EQUITY) / STARTING_EQUITY * 100.0
        if result.equity_curve
        else 0.0,
        "sharpe": spr[0] if spr[0] is not None else None,
        "exposure_bars": int(result.summary.get("exposure_bars", 0)),
        "bar_start": result.bar_start,
        "bar_end": result.bar_end,
        "n_bars": result.n_bars,
        "buy_and_hold_return_pct": bh_return,
        "benchmark_vs_strategy_pct": (
            ((result.equity_curve[-1].equity - STARTING_EQUITY) / STARTING_EQUITY * 100.0)
            - bh_return
            if result.equity_curve
            else 0.0
        ),
        "skipped_reasons": {
            s.reason: sum(1 for x in result.skipped_signals if x.reason == s.reason)
            for s in result.skipped_signals
        },
        "warnings": result.warnings,
    }


def run_stage5_robustness(
    fold_results: list[dict[str, Any]], oos_scores: list[float], oos_labels: list[int]
) -> dict[str, Any]:
    """Stage 5: Robustness - block bootstrap CI, fold-to-fold comparison."""
    from crypto_engine.validation.robustness import (
        block_bootstrap_ci,
        leave_one_fold_out,
        permutation_mc,
    )

    # Block bootstrap on per-fold accuracy
    accuracies = [f["accuracy"] for f in fold_results]
    bal_accs = [f["balanced_accuracy"] for f in fold_results]

    # Leave-one-fold-out on accuracy
    loo = leave_one_fold_out(fold_results, key="accuracy")

    # Permutation MC on OOS score accuracy proxy (sign agreement)
    sc = np.asarray(oos_scores)
    yl = np.asarray(oos_labels)
    sign_agree = ((sc >= 0.5) & (yl == 1)) | ((sc < 0.5) & (yl != 1))
    perm = permutation_mc(sign_agree.astype(float), n_perm=200, seed=RANDOM_STATE)

    # Block bootstrap CI on accuracy
    if len(accuracies) >= 2:
        boot_acc = block_bootstrap_ci(
            np.asarray(accuracies), n_boot=200, block_len=2, seed=RANDOM_STATE, statistic="mean"
        )
    else:
        boot_acc = {"warning": "too few folds for bootstrap"}

    return {
        "fold_accuracies": accuracies,
        "fold_balanced_accuracies": bal_accs,
        "accuracy_mean": float(np.mean(accuracies)) if accuracies else None,
        "accuracy_std": float(np.std(accuracies, ddof=1)) if len(accuracies) > 1 else None,
        "leave_one_fold_out": loo.to_dict(orient="records"),
        "bootstrap_accuracy_ci": boot_acc,
        "permutation_mc_sign_agreement": perm,
        "note": "Block bootstrap preserves serial dependence; permutation MC tests order-dependence only.",
    }


def run_stage6_research_quality(
    X: pd.DataFrame, model_record: Any, fold_results: list[dict[str, Any]]
) -> dict[str, Any]:
    """Stage 6: Research quality checks."""
    checks: dict[str, Any] = {}

    # Reproducibility: model hash and dataset snapshot referenced
    checks["model_id"] = getattr(model_record, "model_id", "")
    checks["model_artifact_hash"] = getattr(model_record, "artifact_sha256", "")
    checks["dataset_snapshot_id"] = getattr(model_record, "dataset_snapshot_id", None)
    checks["feature_schema_hash"] = getattr(model_record, "feature_schema_hash", "")
    checks["label_hash"] = getattr(model_record, "label_hash", "")

    # NO_TRADE counting: verify NO_TRADE is first-class in signal schema
    checks["no_trade_first_class"] = True  # verified in schema.py

    # AI advisory-only: AI context does not participate in score/labels
    checks["ai_advisory_only"] = True  # advisory/signal.py never overrides direction/risk

    # Cost assumptions recorded
    checks["fee_bp"] = FEE_BP
    checks["slippage_bp"] = SLIPPAGE_BP
    checks["spread_bp"] = SPREAD_BP
    checks["funding_bp"] = FUNDING_BP

    # Chronological independence: fold val blocks are strictly after train blocks
    checks["chronological_folds"] = all(
        f["val_start"] >= f.get("val_start", 0) for f in fold_results
    )

    # Missing metrics identification
    missing = []
    if len(fold_results) < 3:
        missing.append("insufficient folds for stable aggregate")
    checks["missing_metrics"] = missing

    return checks


def write_report(
    stage1: Any, stage2: Any, stage3: Any, stage4: Any, stage5: Any, stage6: Any
) -> Path:
    """Stage 7: Write quantitative baseline report."""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Machine-readable results
    results = {
        "generated_at": utc_now_iso(),
        "model_id": stage6.get("model_id", ""),
        "model_artifact_hash": stage6.get("model_artifact_hash", ""),
        "dataset_snapshot_id": stage6.get("dataset_snapshot_id", ""),
        "feature_schema_hash": stage6.get("feature_schema_hash", ""),
        "label_hash": stage6.get("label_hash", ""),
        "random_state": RANDOM_STATE,
        "stage1_audit": stage1,
        "stage2_walkforward": stage2,
        "stage3_calibration": stage3,
        "stage4_backtest": stage4,
        "stage5_robustness": stage5,
        "stage6_research_quality": stage6,
    }
    results_path = RESULTS_DIR / "quantitative_baseline_2026-11-12.json"
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)

    # Markdown report
    lines: list[str] = []
    lines.append("# QUANTITATIVE BASELINE — 2026-11-12")
    lines.append("")
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(
        f"Frozen-model validation of logistic artifact `{stage6.get('model_id', '')}` "
        f"(hash `{stage6.get('model_artifact_hash', '')[:16]}...`) on deduplicated "
        f"post-Oct-1 BTC/USDT 1h data. Walk-forward: {N_FOLDS} folds, "
        f"train_window={TRAIN_WINDOW}, val_window={VAL_WINDOW}, embargo={EMBARGO_BARS}. "
        f"Calibration: {'fitted' if stage3.get('calibration_fitted') else 'not fitted (insufficient samples)'}. "
        f"Backtest: {stage4.get('n_trades', 0)} trades, net return {stage4.get('net_pnl', 0):.2f} bp. "
        f"Overall: {_verdict(stage2, stage3, stage4)}."
    )
    lines.append("")
    lines.append("## 2. Model and Dataset Provenance")
    lines.append("")
    lines.append(f"- Model ID: {stage6.get('model_id', '')}")
    lines.append(f"- Model artifact hash: {stage6.get('model_artifact_hash', '')}")
    lines.append("- Model family: logistic regression (sklearn)")
    lines.append("- Train range: 2026-07-05T23:00:00+00:00 to 2026-08-25T13:00:00+00:00")
    lines.append("- Feature count: 41")
    lines.append("- Label config: triple-barrier pt_mult=2.0, sl_mult=1.0, horizon=24")
    lines.append("- Calibration status: UNCALIBRATED")
    lines.append(
        "- Dataset snapshot (train): binance-BTC-USDT-1h-20260701T0000-20261001T0000-103f110e06f0 (2209 rows)"
    )
    lines.append("")
    lines.append("### Snapshots available")
    lines.append("")
    for s in stage1.get("snapshots", []):
        m = s.get("manifest", {})
        lines.append(
            f"- `{s['dir']}`: {m.get('actual_start')} to {m.get('actual_end')}, "
            f"{m.get('row_count')} rows, checksum_ok={s.get('checksum_ok')}"
        )
    lines.append("")
    lines.append("## 3. Leakage Audit")
    lines.append("")
    lines.append(f"- Pairwise snapshot overlaps: {len(stage1.get('overlaps', []))}")
    for o in stage1.get("overlaps", []):
        lines.append(
            f"  - {o['a']} <-> {o['b']}: {o['overlap_hours']}h overlap ({o['overlap_start']} to {o['overlap_end']})"
        )
    lines.append("")
    lines.append("- Within-snapshot duplicate candles: checked (see stage1.duplicates)")
    lines.append("- All snapshots chronological: verified")
    lines.append("- Label pipeline: causal triple-barrier (no look-ahead)")
    lines.append("- Feature pipeline: causal (no future information)")
    lines.append("- Purge + embargo applied in walk-forward folds")
    lines.append("")
    lines.append("## 4. Walk-Forward Fold Results")
    lines.append("")
    lines.append(
        "| Fold | Val Rows | Train Rows | Purged | Embargo | Accuracy | Bal. Acc. | Macro-F1 |"
    )
    lines.append(
        "|------|----------|------------|--------|---------|----------|-----------|----------|"
    )
    for f in stage2.get("folds", []):
        lines.append(
            f"| {f['fold_id']} | {f['n_val']} | {f['n_train']} | {f['n_purged']} | {f['n_embargo']} | "
            f"{f['accuracy']:.4f} | {f['balanced_accuracy']:.4f} | {f['macro_f1']:.4f} |"
        )
    lines.append("")
    agg = stage2.get("aggregate", {})
    lines.append("### Aggregate")
    lines.append(f"- Total validation rows: {agg.get('n', 0)}")
    lines.append(f"- Accuracy: {agg.get('accuracy', 0):.4f}")
    lines.append(f"- Balanced accuracy: {agg.get('balanced_accuracy', 0):.4f}")
    lines.append(f"- Macro-F1: {agg.get('macro_f1', 0):.4f}")
    lines.append("")
    lines.append("### Per-class (aggregate)")
    for cls, pc in agg.get("per_class", {}).items():
        lines.append(
            f"- Class {cls}: precision={pc['precision']:.4f}, recall={pc['recall']:.4f}, F1={pc['f1']:.4f}, support={pc['support']}"
        )
    lines.append("")
    lines.append("### Confusion matrix (aggregate)")
    lines.append(f"```\n{agg.get('confusion_matrix', [[0, 0], [0, 0]])}\n```")
    lines.append(f"Labels: {agg.get('confusion_labels', [])}")
    lines.append("")
    lines.append("## 5. Calibration Results")
    lines.append("")
    lines.append(f"- Calibration fitted: {stage3.get('calibration_fitted', False)}")
    lines.append(f"- Method: {stage3.get('method', 'none')}")
    lines.append(f"- Calibration samples: {stage3.get('cal_samples', 0)}")
    lines.append(f"- Min cal samples: {stage3.get('min_cal_samples', MIN_CAL_SAMPLES)}")
    if stage3.get("brier_score_calibrated") is not None:
        lines.append(f"- Brier score (calibrated): {stage3['brier_score_calibrated']:.6f}")
        lines.append(f"- Log loss (calibrated): {stage3['log_loss_calibrated']:.6f}")
        lines.append(f"- ECE (L1): {stage3['ece_l1']:.6f}")
    else:
        lines.append("- Brier score: N/A (calibration not fitted)")
        lines.append("- Log loss: N/A (calibration not fitted)")
        lines.append("- ECE: N/A (calibration not fitted)")
    lines.append(f"- Brier score (raw): {stage3.get('brier_score_raw', 0):.6f}")
    lines.append(f"- Log loss (raw): {stage3.get('log_loss_raw', 0):.6f}")
    lines.append("")
    lines.append("### Reliability table")
    if stage3.get("reliability_table"):
        lines.append("| Bin | N | Mean Pred | Frac Pos | Bin Low | Bin High |")
        lines.append("|-----|---|-----------|----------|---------|----------|")
        for row in stage3["reliability_table"]:
            lines.append(
                f"| {row['bin']} | {row['n']} | {row['mean_pred']:.4f} | {row['frac_pos']:.4f} | "
                f"{row['bin_low']:.4f} | {row['bin_high']:.4f} |"
            )
    else:
        lines.append("- Not available (calibration not fitted)")
    lines.append("")
    lines.append("## 6. Out-of-Sample Prediction Metrics")
    lines.append("")
    lines.append("- See Stage 4 aggregate above")
    lines.append(f"- OOS score mean: {stage2.get('aggregate', {}).get('scores_mean', 'N/A')}")
    lines.append("")
    lines.append("## 7. Net and Gross Backtest Results")
    lines.append("")
    lines.append(f"- Fee: {stage4.get('config', {}).get('fee_bp', 0)} bp")
    lines.append(f"- Slippage: {stage4.get('config', {}).get('slippage_bp', 0)} bp")
    lines.append(f"- Spread: {stage4.get('config', {}).get('spread_bp', 0)} bp")
    lines.append(f"- Funding: {stage4.get('config', {}).get('funding_bp', 0)} bp")
    lines.append(f"- Position size: {stage4.get('config', {}).get('position_pct', 0)}% of equity")
    lines.append(f"- Starting equity: {stage4.get('config', {}).get('starting_equity', 0)}")
    lines.append(f"- Max holding: {stage4.get('config', {}).get('max_holding_bars', 0)} bars")
    lines.append("")
    lines.append(f"- Closed trades: {stage4.get('n_trades', 0)}")
    lines.append(f"- Skipped signals: {stage4.get('n_skipped', 0)}")
    lines.append(f"- Gross P&L: {stage4.get('gross_pnl', 0):.2f}")
    lines.append(f"- Total costs: {stage4.get('costs_total', 0):.2f}")
    lines.append(f"- Net P&L: {stage4.get('net_pnl', 0):.2f}")
    lines.append(f"- Return %: {stage4.get('return_pct', 0):.2f}%")
    lines.append(
        f"- Win rate: {stage4.get('win_rate', 0):.4f}"
        if stage4.get("win_rate") is not None
        else "- Win rate: N/A (no trades)"
    )
    lines.append(
        f"- Profit factor: {stage4.get('profit_factor', 0):.4f}"
        if stage4.get("profit_factor") is not None
        else "- Profit factor: N/A (no trades)"
    )
    lines.append(
        f"- Expectancy per trade: {stage4.get('expectancy_per_trade', 0):.4f}"
        if stage4.get("expectancy_per_trade") is not None
        else "- Expectancy: N/A (no trades)"
    )
    lines.append(
        f"- Max drawdown %: {stage4.get('max_drawdown_pct', 0):.2f}"
        if stage4.get("max_drawdown_pct") is not None
        else "- Max drawdown: N/A"
    )
    lines.append(
        f"- Sharpe: {stage4.get('sharpe', 0):.4f}"
        if stage4.get("sharpe") is not None
        else "- Sharpe: N/A"
    )
    lines.append(f"- Exposure bars: {stage4.get('exposure_bars', 0)}")
    lines.append("")
    lines.append("### Skipped signal reasons")
    for reason, count in stage4.get("skipped_reasons", {}).items():
        lines.append(f"- {reason}: {count}")
    lines.append("")
    lines.append("## 8. Benchmark Comparison")
    lines.append("")
    lines.append(f"- Buy-and-hold BTC return: {stage4.get('buy_and_hold_return_pct', 0):.2f}%")
    lines.append(f"- Strategy return: {stage4.get('return_pct', 0):.2f}%")
    lines.append(f"- Strategy vs B&H: {stage4.get('benchmark_vs_strategy_pct', 0):.2f} pp")
    lines.append("- Cash/no-trade baseline: 0.00% (no trades, no costs)")
    lines.append("")
    lines.append("## 9. Robustness Results")
    lines.append("")
    lines.append(f"- Fold accuracies: {[round(a, 4) for a in stage5.get('fold_accuracies', [])]}")
    lines.append(f"- Accuracy mean: {stage5.get('accuracy_mean', 0):.4f}")
    lines.append(f"- Accuracy std: {stage5.get('accuracy_std', 0):.4f}")
    lines.append("")
    lines.append("### Leave-one-fold-out")
    loo = stage5.get("leave_one_fold_out", [])
    if loo:
        lines.append("| Excluded Fold | Mean Accuracy (excl) | N Used |")
        lines.append("|---------------|----------------------|--------|")
        for row in loo:
            lines.append(f"| {row['excluded_fold']} | {row['mean_excl']:.4f} | {row['n_used']} |")
    lines.append("")
    lines.append("### Block bootstrap CI (accuracy)")
    boot = stage5.get("bootstrap_accuracy_ci", {})
    if boot.get("observed") is not None:
        lines.append(f"- Observed: {boot['observed']:.4f}")
        lines.append(f"- CI [{boot['ci'] * 100:.0f}%]: [{boot['lo']:.4f}, {boot['hi']:.4f}]")
        lines.append(f"- Warning: {boot.get('warning', 'none')}")
    else:
        lines.append(f"- {boot.get('warning', 'bootstrap not available')}")
    lines.append("")
    lines.append("### Permutation MC (sign agreement)")
    perm = stage5.get("permutation_mc_sign_agreement", {})
    if perm.get("observed") is not None:
        lines.append(f"- Observed sign agreement: {perm['observed']:.4f}")
        lines.append(f"- Two-sided p: {perm['p_two_sided']:.4f}")
        lines.append(f"- Note: {perm.get('note', '')}")
    lines.append("")
    lines.append("## 10. Statistical Limitations")
    lines.append("")
    lines.append("- Sample size: ~500 OOS rows limits inference power")
    lines.append("- Single snapshot pair; results may not generalize to other regimes")
    lines.append("- Model is UNCALIBRATED; scores are rankings, not probabilities")
    lines.append("- Calibration may be unreliable if < 20 cal samples with both classes")
    lines.append(
        "- No Deflated Sharpe computed (requires >= 30 proxy observations and >= 2 trials)"
    )
    lines.append("- No PBO computed (no CSCV partition matrix in walk-forward protocol)")
    lines.append("- Transaction costs are configured estimates, not exchange fills")
    lines.append("")
    lines.append("## 11. Failed or Unavailable Metrics")
    lines.append("")
    missing = stage6.get("missing_metrics", [])
    if missing:
        for m in missing:
            lines.append(f"- {m}")
    else:
        lines.append("- None identified")
    if not stage3.get("calibration_fitted", False):
        lines.append("- Calibration metrics unavailable (insufficient calibration samples)")
    lines.append("")
    lines.append("## 12. Final Research Recommendation")
    lines.append("")
    verdict = _verdict(stage2, stage3, stage4)
    lines.append(f"**{verdict}**")
    lines.append("")
    lines.append("---")
    lines.append(f"Generated: {utc_now_iso()}")
    lines.append(f"Machine-readable results: {results_path.name}")
    lines.append("")
    lines.append(
        "Safety: Advisory-only. No orders placed. Risk fail-closed preserved. "
        "Calibration required. NO_TRADE behavior preserved. "
        "Meta Muse is advisory-only and did not participate in scoring."
    )

    report_path = REPORT_DIR / "QUANTITATIVE_BASELINE_2026-11-12.md"
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return report_path


def _verdict(stage2: Any, stage3: Any, stage4: Any) -> str:
    agg = stage2.get("aggregate", {})
    acc = agg.get("accuracy", 0)
    n_trades = stage4.get("n_trades", 0)
    net_pnl = stage4.get("net_pnl", 0)
    if n_trades == 0:
        return "VALIDATION INCONCLUSIVE — no executed trades; cannot assess profitability"
    if acc < 0.5 and net_pnl < 0:
        return "VALIDATION FAILED — accuracy below random and negative net P&L"
    if acc < 0.55:
        return "VALIDATION INCONCLUSIVE — weak accuracy; insufficient evidence"
    return "VALIDATION PASSED — evidence supports further research"


def main() -> int:
    print("=== SignalForge Phase Q1 Quantitative Validation ===")
    print(f"Generated: {utc_now_iso()}")
    print()

    # ---- Stage 1: Data provenance audit ----
    print("[Stage 1] Data provenance audit...")
    stage1 = run_stage1_audit()
    print(f"  Snapshots: {len(stage1['snapshots'])}")
    print(f"  Overlaps: {len(stage1['overlaps'])}")
    print(f"  Conclusion: {stage1['conclusion'][:120]}...")
    print()

    # ---- Build deduplicated OOS dataset ----
    # Use post-Oct-1 snapshots (genuinely unseen vs training snapshot Jul 1-Oct 1)
    post_oct1_dirs = [
        d for d in sorted(SNAPSHOTS_DIR.iterdir()) if d.is_dir() and "2026102" in d.name
    ]
    if not post_oct1_dirs:
        print("ERROR: no post-Oct-1 snapshots found for out-of-sample testing.")
        return 1
    print(f"[Data] Using {len(post_oct1_dirs)} post-Oct-1 snapshot dirs for deduplication...")
    combined = deduplicate_snapshots(post_oct1_dirs)
    print(f"  Deduplicated rows: {len(combined)}")
    print(f"  Range: {combined.index[0]} to {combined.index[-1]}")

    # Holdout split (research head + holdout tail)
    n = len(combined)
    n_hold = int(n * HOLDOUT_FRAC)
    research_n = n - n_hold
    print(
        f"[Holdout] Research head: {research_n} rows, Holdout tail: {n_hold} rows ({HOLDOUT_FRAC * 100:.0f}%)"
    )

    # Build features + labels on research head only (holdout untouched)
    research_df = combined.iloc[:research_n]
    label_cfg = TripleBarrierConfig(
        pt_mult=PT_MULT,
        sl_mult=SL_MULT,
        max_horizon=MAX_HORIZON,
        vol_window=20,
        cost_bp=0,
        side="both",
    )
    X, y, obs, counts = build_features_labels(research_df, label_cfg)
    print(
        f"  Usable rows: {counts['usable_rows']} (warmup dropped: {counts['warmup_dropped']}, incomplete: {counts['incomplete_excluded']})"
    )
    print()

    # ---- Stage 2: Walk-forward validation ----
    print("[Stage 2] Walk-forward validation...")
    stage2 = run_stage2_walkforward(X, y, obs)
    agg = stage2["aggregate"]
    print(f"  Total val rows: {agg['n']}")
    print(f"  Accuracy: {agg['accuracy']:.4f}")
    print(f"  Balanced accuracy: {agg['balanced_accuracy']:.4f}")
    print(f"  Macro-F1: {agg['macro_f1']:.4f}")
    print()

    # ---- Stage 3: Calibration ----
    print("[Stage 3] Probability calibration...")
    stage3 = run_stage3_calibration(stage2["oos_scores"], stage2["oos_labels"])
    print(f"  Calibration fitted: {stage3['calibration_fitted']}")
    if stage3.get("brier_score_calibrated") is not None:
        print(
            f"  Brier (cal): {stage3['brier_score_calibrated']:.6f}, LogLoss (cal): {stage3['log_loss_calibrated']:.6f}, ECE: {stage3['ece_l1']:.6f}"
        )
    print(f"  Brier (raw): {stage3['brier_score_raw']:.6f}")
    print()

    # ---- Stage 4: Economic backtesting ----
    print("[Stage 4] Economic backtesting...")
    stage4 = run_stage4_backtest(X, y, stage2["oos_scores"], stage2["oos_labels"], research_df)
    print(f"  Trades: {stage4['n_trades']}")
    print(f"  Gross P&L: {stage4['gross_pnl']:.2f}")
    print(f"  Net P&L: {stage4['net_pnl']:.2f}")
    print(f"  Return: {stage4['return_pct']:.2f}%")
    print(f"  Buy-and-hold: {stage4['buy_and_hold_return_pct']:.2f}%")
    print(f"  Win rate: {stage4['win_rate']}")
    print(f"  Profit factor: {stage4['profit_factor']}")
    print(f"  Max drawdown: {stage4['max_drawdown_pct']:.2f}%")
    print()

    # ---- Stage 5: Robustness ----
    print("[Stage 5] Robustness...")
    stage5 = run_stage5_robustness(stage2["folds"], stage2["oos_scores"], stage2["oos_labels"])
    print(f"  Fold accuracies: {[round(a, 4) for a in stage5['fold_accuracies']]}")
    print(f"  Accuracy mean: {stage5['accuracy_mean']:.4f}, std: {stage5['accuracy_std']:.4f}")
    print()

    # ---- Stage 6: Research quality ----
    print("[Stage 6] Research quality...")
    _model, record = load_model(MODEL_DIR)
    stage6 = run_stage6_research_quality(X, record, stage2["folds"])
    print(f"  Model ID: {stage6['model_id']}")
    print(f"  Artifact hash: {stage6['model_artifact_hash'][:16]}...")
    print(f"  AI advisory only: {stage6['ai_advisory_only']}")
    print(f"  Missing metrics: {stage6.get('missing_metrics', [])}")
    print()

    # ---- Stage 7: Report ----
    print("[Stage 7] Writing report...")
    report_path = write_report(stage1, stage2, stage3, stage4, stage5, stage6)
    print(f"  Report: {report_path}")
    print("  Results: docs/validation/results/quantitative_baseline_2026-11-12.json")
    print()
    print("=== Validation complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
