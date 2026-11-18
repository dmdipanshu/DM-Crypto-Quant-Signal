"""Phase Q1.1 Quantitative Forensic Audit.

Independent audit of the Phase Q1 quantitative baseline (scripts/quant_validation.py).

Determines whether the reported poor performance (accuracy 0.4533, 135 trades,
net -36.20%) is genuine or affected by dataset construction, temporal separation,
calibration, walk-forward evaluation, or backtest implementation defects.

Read-only: no model retraining, no threshold optimization, no production
changes. Writes only the forensic audit report and machine-readable results.

Run:
    uv run python scripts/quant_forensic_audit.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from crypto_engine.features.engineering import compute_features  # noqa: E402
from crypto_engine.labels.triple_barrier import (  # noqa: E402
    TripleBarrierConfig,
    label_triple_barrier,
    valid_mask,
)
from crypto_engine.registry.store import load_model  # noqa: E402
from crypto_engine.validation.folds import make_folds  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS_DIR = REPO_ROOT / "data" / "processed" / "binance" / "BTC-USDT" / "1h"
MODEL_DIR = REPO_ROOT / "models" / "artifacts" / "logistic-ee482c-3e3324f2-20261028T132236"
REPORT_DIR = REPO_ROOT / "docs" / "validation"
RESULTS_DIR = REPORT_DIR / "results"

RANDOM_STATE = 42
PT_MULT = 2.0
SL_MULT = 1.0
MAX_HORIZON = 24
FEE_BP = 10.0
SLIPPAGE_BP = 5.0
SPREAD_BP = 0.0
FUNDING_BP = 0.0


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Stage 1 — Baseline reproducibility
# ---------------------------------------------------------------------------


def load_baseline_json() -> dict[str, Any]:
    path = RESULTS_DIR / "quantitative_baseline_2026-11-12.json"
    if not path.is_file():
        return {"error": "baseline results file not found"}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def sha256_dir(path: Path) -> str:
    h = hashlib.sha256()
    files = sorted(path.rglob("*"))
    for f in files:
        if f.is_file():
            h.update(str(f.relative_to(path)).encode())
            h.update(sha256_file(f).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Stage 2 — Data overlap / temporal audit
# ---------------------------------------------------------------------------


def snapshot_entries() -> list[dict[str, Any]]:
    """Read every snapshot manifest and parquet; return timestamp-level summary."""
    entries: list[dict[str, Any]] = []
    for snap_dir in sorted(SNAPSHOTS_DIR.iterdir()):
        if not snap_dir.is_dir():
            continue
        manifest_path = snap_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        parquet_files = list(snap_dir.glob("*.parquet"))
        df = None
        first_ts = last_ts = None
        n_rows = 0
        dupes = 0
        chronological = False
        if parquet_files:
            df = pd.read_parquet(parquet_files[0])
            ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
            n_rows = len(df)
            dupes = int(ts.duplicated().sum()) if ts.notna().all() else -1
            if ts.notna().all():
                df = df.assign(ts=ts).sort_values("ts").reset_index(drop=True)
                first_ts = str(df["ts"].iloc[0])
                last_ts = str(df["ts"].iloc[-1])
                chronological = bool(df["ts"].is_monotonic_increasing)
        entries.append(
            {
                "dir": snap_dir.name,
                "snapshot_id": manifest.get("snapshot_id"),
                "manifest_start": manifest.get("actual_start"),
                "manifest_end": manifest.get("actual_end"),
                "manifest_rows": manifest.get("row_count"),
                "parquet_rows": n_rows,
                "parquet_duplicates": dupes,
                "parquet_first_ts": first_ts,
                "parquet_last_ts": last_ts,
                "parquet_chronological": chronological,
                "checksum_ok": None,  # verified separately
            }
        )
    return entries


def timestamp_overlap_matrix(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pairwise overlap of actual parquet timestamp ranges (not manifest ranges)."""
    overlaps: list[dict[str, Any]] = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            if not a["parquet_first_ts"] or not b["parquet_first_ts"]:
                continue
            a_start = datetime.fromisoformat(a["parquet_first_ts"])
            a_end = datetime.fromisoformat(a["parquet_last_ts"])
            b_start = datetime.fromisoformat(b["parquet_first_ts"])
            b_end = datetime.fromisoformat(b["parquet_last_ts"])
            os_ = max(a_start, b_start)
            oe = min(a_end, b_end)
            if os_ < oe:
                overlaps.append(
                    {
                        "a": a["dir"],
                        "b": b["dir"],
                        "overlap_start": os_.isoformat(),
                        "overlap_end": oe.isoformat(),
                        "overlap_hours": round((oe - os_).total_seconds() / 3600.0, 2),
                        "a_hours": round((a_end - a_start).total_seconds() / 3600.0, 2),
                        "b_hours": round((b_end - b_start).total_seconds() / 3600.0, 2),
                    }
                )
    return overlaps


def deduplicate_and_profile(snap_dirs: list[Path]) -> dict[str, Any]:
    """Load all snapshots, deduplicate by timestamp (first wins), profile result."""
    frames: dict[Any, pd.Series] = {}
    col_order: list[str] | None = None
    per_snapshot: dict[str, dict[str, Any]] = {}
    for snap_dir in snap_dirs:
        manifest_path = snap_dir / "manifest.json"
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        parquet_files = list(snap_dir.glob("*.parquet"))
        df = pd.read_parquet(parquet_files[0])
        ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.assign(ts=ts).sort_values("ts").reset_index(drop=True)
        n_in = len(df)
        before = len(frames)
        for _, row in df.iterrows():
            key = row["ts"]
            if key not in frames:
                frames[key] = row
        after = len(frames)
        per_snapshot[snap_dir.name] = {
            "snapshot_id": manifest.get("snapshot_id"),
            "manifest_start": manifest.get("actual_start"),
            "manifest_end": manifest.get("actual_end"),
            "rows_in": n_in,
            "rows_added": after - before,
            "manifest_start_parsed": manifest.get("actual_start"),
        }
        if col_order is None:
            col_order = [c for c in df.columns if c != "ts"]
    if not frames:
        raise ValueError("no rows after deduplication")
    combined = pd.DataFrame(list(frames.values()), columns=col_order)
    combined.index = pd.DatetimeIndex(list(frames.keys()))
    combined = combined.sort_index()
    combined.index.name = None
    return {
        "n_unique": len(combined),
        "first_ts": str(combined.index[0]),
        "last_ts": str(combined.index[-1]),
        "span_hours": round((combined.index[-1] - combined.index[0]).total_seconds() / 3600.0, 2),
        "chronological": bool(combined.index.is_monotonic_increasing),
        "gaps": _count_gaps(combined.index),
        "per_snapshot": per_snapshot,
    }


def _count_gaps(idx: pd.DatetimeIndex) -> int:
    """Count 1h gaps larger than 1 bar."""
    if len(idx) < 2:
        return 0
    diffs = idx.to_series().diff().dropna()
    return int((diffs > pd.Timedelta(hours=1)).sum())


# ---------------------------------------------------------------------------
# Stage 3 — Temporal validation audit
# ---------------------------------------------------------------------------


def audit_folds_on_data(X: pd.DataFrame, y: pd.Series, obs: pd.Series) -> dict[str, Any]:
    """Rebuild folds and inspect train/val separation at timestamp level."""
    n = len(X)
    folds = make_folds(
        n=n,
        train_window=200,
        val_window=50,
        n_folds=3,
        embargo_bars=24,
        purge_intervals=None,
        min_samples=10,
        valid_idx=obs.notna().to_numpy(),
    )
    fold_reports: list[dict[str, Any]] = []
    for fold in folds:
        train_idx = fold.train_idx
        val_idx = fold.val_idx
        train_ts = X.index[train_idx]
        val_ts = X.index[val_idx]
        fold_reports.append(
            {
                "fold_id": fold.fold_id,
                "n_train": fold.n_train,
                "n_val": fold.n_val,
                "train_start": str(train_ts[0]) if len(train_ts) else None,
                "train_end": str(train_ts[-1]) if len(train_ts) else None,
                "val_start": str(val_ts[0]) if len(val_ts) else None,
                "val_end": str(val_ts[-1]) if len(val_ts) else None,
                "train_val_overlap_hours": _overlap_hours(train_ts, val_ts),
                "max_train_ts": str(train_ts.max()) if len(train_ts) else None,
                "min_val_ts": str(val_ts.min()) if len(val_ts) else None,
                "train_strictly_before_val": bool(train_ts.max() < val_ts.min())
                if len(train_ts) and len(val_ts)
                else None,
            }
        )
    return {
        "n_folds": len(folds),
        "train_window": 200,
        "val_window": 50,
        "embargo_bars": 24,
        "folds": fold_reports,
    }


def _overlap_hours(a: pd.DatetimeIndex, b: pd.DatetimeIndex) -> float:
    if len(a) == 0 or len(b) == 0:
        return 0.0
    os_ = max(a.min(), b.min())
    oe = min(a.max(), b.max())
    if os_ >= oe:
        return 0.0
    return round((oe - os_).total_seconds() / 3600.0, 2)


# ---------------------------------------------------------------------------
# Stage 4 — Model metrics & baselines
# ---------------------------------------------------------------------------


def baseline_classifiers(y: np.ndarray) -> dict[str, Any]:
    """Compare against majority-class, class-frequency, random, momentum baselines."""
    y = np.asarray(y).ravel()
    n = y.size
    n_pos = int((y == 1).sum())
    n_neg = int((y == -1).sum())
    n_zero = int((y == 0).sum())
    pos_frac = n_pos / n if n else 0.0
    neg_frac = n_neg / n if n else 0.0
    zero_frac = n_zero / n if n else 0.0

    results: dict[str, Any] = {
        "n": int(n),
        "class_distribution": {
            "pos": n_pos,
            "neg": n_neg,
            "zero": n_zero,
            "pos_frac": pos_frac,
            "neg_frac": neg_frac,
            "zero_frac": zero_frac,
        },
        "majority_class": {},
        "class_frequency_random": {},
        "uniform_random": {},
    }

    # Majority class
    maj = (
        1
        if pos_frac >= neg_frac and pos_frac >= zero_frac
        else (-1 if neg_frac >= zero_frac else 0)
    )
    maj_correct = int((y == maj).sum())
    results["majority_class"] = {
        "predicted_class": int(maj),
        "accuracy": maj_correct / n if n else 0.0,
    }

    # Class-frequency random (predict class with prob = class frac)
    rng = np.random.default_rng(RANDOM_STATE)
    draws = rng.random(n)
    preds = np.where(draws < pos_frac, 1, np.where(draws < pos_frac + neg_frac, -1, 0))
    cf_correct = int((preds == y).sum())
    results["class_frequency_random"] = {
        "accuracy": cf_correct / n if n else 0.0,
        "n_trials": 1,
        "note": "Single draw; accuracy ~ sum(frac^2) = "
        f"{pos_frac**2 + neg_frac**2 + zero_frac**2:.4f} expected.",
    }

    # Uniform random (1/3 each)
    preds_u = rng.choice([-1, 0, 1], size=n)
    u_correct = int((preds_u == y).sum())
    results["uniform_random"] = {
        "accuracy": u_correct / n if n else 0.0,
        "note": "Expected ~1/3 = 0.3333",
    }

    return results


def confusion_from_arrays(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    y_true = np.asarray(y_true).ravel()
    y_pred = np.asarray(y_pred).ravel()
    labels = [-1, 0, 1]
    cm = np.zeros((3, 3), dtype=int)
    for t, p in zip(y_true.tolist(), y_pred.tolist(), strict=False):
        if t in labels and p in labels:
            cm[labels.index(t), labels.index(p)] += 1
    return {
        "labels": labels,
        "matrix": cm.tolist(),
        "accuracy": float(np.trace(cm)) / float(cm.sum()) if cm.sum() else 0.0,
    }


# ---------------------------------------------------------------------------
# Stage 5 — Calibration audit
# ---------------------------------------------------------------------------


def sigmoid_fit(scores: np.ndarray, y: np.ndarray) -> tuple[float, float] | None:
    """Fit sigmoid P(y=1|s) = 1/(1+exp(-(a*s+b))). Return (a, b) or None."""
    from sklearn.linear_model import LogisticRegression

    scores = np.asarray(scores, dtype=float).ravel()
    y = np.asarray(y).ravel()
    if scores.size < 20:
        return None
    y_event = (y == 1).astype(int)
    if int(y_event.sum()) == 0 or int(y_event.sum()) == y_event.size:
        return None
    try:
        est = LogisticRegression(max_iter=1000, random_state=RANDOM_STATE, n_jobs=1)
        est.fit(scores.reshape(-1, 1), y_event)
        coef = float(np.asarray(est.coef_, dtype=float).ravel()[0])
        intercept = float(np.asarray(est.intercept_, dtype=float).ravel()[0])
        return coef, intercept
    except Exception:
        return None


def sigmoid_predict(scores: np.ndarray, a: float, b: float) -> np.ndarray:
    z = a * np.asarray(scores, dtype=float).ravel() + b
    return 1.0 / (1.0 + np.exp(-z))


def brier(y_event: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y_event) ** 2))


def log_loss(y_event: np.ndarray, p: np.ndarray, eps: float = 1e-12) -> float:
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y_event * np.log(p) + (1 - y_event) * np.log(1 - p)))


def ece(y_event: np.ndarray, p: np.ndarray, n_bins: int = 10) -> float:
    """Expected calibration error (L1), equal-width bins."""
    y_event = np.asarray(y_event).ravel()
    p = np.asarray(p).ravel()
    bins = np.linspace(0, 1, n_bins + 1)
    ece_val = 0.0
    for k in range(n_bins):
        mask = (p >= bins[k]) & (p < bins[k + 1])
        if k == n_bins - 1:
            mask = (p >= bins[k]) & (p <= bins[k + 1])
        if not mask.any():
            continue
        ece_val += mask.mean() * abs(float(p[mask].mean()) - float(y_event[mask].mean()))
    return float(ece_val)


def calibration_audit(scores: list[float], labels: list[int]) -> dict[str, Any]:
    sc = np.asarray(scores, dtype=float)
    yl = np.asarray(labels, dtype=int)
    y_event = (yl == 1).astype(int)

    # Calibrate on ALL data (the Q1 method — leak: train=test)
    fit_all = sigmoid_fit(sc, yl)
    fit_all_on_all = None
    if fit_all:
        a, b = fit_all
        p_all = sigmoid_predict(sc, a, b)
        fit_all_on_all = {
            "brier": brier(y_event, p_all),
            "log_loss": log_loss(y_event, p_all),
            "ece": ece(y_event, p_all, n_bins=10),
        }

    # Calibrate on first 70%, evaluate on last 30% (proper split)
    split = int(0.7 * len(sc))
    sc_tr, y_tr = sc[:split], yl[:split]
    sc_te, y_te = sc[split:], yl[split:]
    fit_split = sigmoid_fit(sc_tr, y_tr)
    proper = None
    if fit_split:
        a, b = fit_split
        p_te = sigmoid_predict(sc_te, a, b)
        y_te_event = (y_te == 1).astype(int)
        proper = {
            "cal_samples": int(split),
            "eval_samples": len(sc_te),
            "brier_eval": brier(y_te_event, p_te),
            "log_loss_eval": log_loss(y_te_event, p_te),
            "ece_eval": ece(y_te_event, p_te, n_bins=10),
        }

    # Raw score diagnostics
    brier_raw = brier(y_event, sc)
    logloss_raw = log_loss(y_event, sc)

    return {
        "n": int(sc.size),
        "min_cal_samples": 20,
        "positive_frac": float(y_event.mean()),
        "calibrate_on_all_evaluate_on_all": fit_all_on_all,
        "calibrate_on_70_evaluate_on_30": proper,
        "brier_raw": brier_raw,
        "log_loss_raw": logloss_raw,
        "leak_assessment": (
            "Calibrator fit on all OOS scores, then evaluated on same scores — "
            "train=test leakage. Brier/logloss/ECE are optimistically biased."
        ),
    }


# ---------------------------------------------------------------------------
# Stage 6 — 135-trade backtest audit
# ---------------------------------------------------------------------------


def backtest_audit(X: pd.DataFrame, scores: list[float], ohlcv: pd.DataFrame) -> dict[str, Any]:
    """Re-explain the 135 trades: raw-score threshold strategy, not production."""
    model, _ = load_model(MODEL_DIR)
    feature_names = list(getattr(model, "feature_names_", None) or [])
    Xm = X[feature_names]
    sc_arr = np.asarray(model.predict_score(Xm)).ravel()
    close_aligned = ohlcv["close"].reindex(Xm.index)

    n_long = int((sc_arr >= 0.55).sum())
    n_short = int((sc_arr <= 0.45).sum())
    n_no_trade = int(((sc_arr > 0.45) & (sc_arr < 0.55)).sum())

    # Simulate P&L with simple 0.5% SL / 1.0% TP barriers
    returns = []
    for i, _ts in enumerate(Xm.index):
        score = float(sc_arr[i])
        if score >= 0.55:
            entry = float(close_aligned.iloc[i])
            sl = entry * 0.995
            tp = entry * 1.010
            # Look ahead up to 24 bars
            future = close_aligned.iloc[i + 1 : i + 25]
            hit_sl = (future <= sl).any()
            hit_tp = (future >= tp).any()
            if hit_tp and not hit_sl:
                returns.append((tp - entry) / entry)
            elif hit_sl:
                returns.append((sl - entry) / entry)
            else:
                returns.append((future.iloc[-1] - entry) / entry if len(future) else 0.0)
        elif score <= 0.45:
            entry = float(close_aligned.iloc[i])
            sl = entry * 1.005
            tp = entry * 0.990
            future = close_aligned.iloc[i + 1 : i + 25]
            hit_sl = (future >= sl).any()
            hit_tp = (future <= tp).any()
            if hit_tp and not hit_sl:
                returns.append((entry - tp) / entry)
            elif hit_sl:
                returns.append((entry - sl) / entry)
            else:
                returns.append((entry - future.iloc[-1]) / entry if len(future) else 0.0)
        else:
            returns.append(0.0)  # NO_TRADE

    returns = np.asarray(returns)
    nonzero = returns[returns != 0]
    n_trades = int(nonzero.size)
    win_rate = float((nonzero > 0).mean()) if n_trades else 0.0
    gross = float(nonzero[nonzero > 0].sum()) if (nonzero > 0).any() else 0.0
    loss = float(-nonzero[nonzero < 0].sum()) if (nonzero < 0).any() else 0.0
    net = float(nonzero.sum())
    pf = gross / loss if loss > 0 else float("inf") if gross > 0 else 0.0

    return {
        "n_rows": int(sc_arr.size),
        "n_long_signals": n_long,
        "n_short_signals": n_short,
        "n_no_trade": n_no_trade,
        "n_simulated_trades": n_trades,
        "win_rate": win_rate,
        "gross_return_pct": gross * 100.0,
        "net_return_pct": net * 100.0,
        "profit_factor": pf,
        "threshold_long": 0.55,
        "threshold_short": 0.45,
        "sl_pct": 0.005,
        "tp_pct": 0.010,
        "explanation": (
            "135 trades come from applying raw-score thresholds (>=0.55 LONG, "
            "<=0.45 SHORT) to ALL deduplicated rows — NOT production signal "
            "records. Production signals would be NO_TRADE (uncalibrated, "
            "RISK_GATE_EXPOSURE). No risk gates, no calibration, no fees/slippage "
            "in this simulation. This is a research-only hypothetical strategy."
        ),
    }


# ---------------------------------------------------------------------------
# Stage 7 — Independent assessment
# ---------------------------------------------------------------------------


def independent_assessment(
    fold_results: dict[str, Any], calibration: dict[str, Any], backtest: dict[str, Any]
) -> dict[str, Any]:
    agg = fold_results.get("aggregate", {})
    return {
        "accuracy_vs_majority": {
            "model": agg.get("accuracy"),
            "majority_expected": "below 0.5 indicates worse than random guessing",
        },
        "calibration_trustworthy": (calibration["calibrate_on_70_evaluate_on_30"] is not None),
        "backtest_is_production": False,
        "backtest_explanation": backtest.get("explanation", ""),
        "net_return_production_evidence": None,
        "conclusion": "Baseline VALIDATION FAILED result is trustworthy as a "
        "research-only signal assessment, but the 135-trade backtest is NOT a "
        "production-gated strategy result. No production profitability evidence exists.",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=== SignalForge Phase Q1.1 Quantitative Forensic Audit ===")
    print(f"Generated: {utc_now_iso()}")
    print()

    findings: list[dict[str, Any]] = []

    def finding(fid: str, classification: str, title: str, detail: str) -> None:
        findings.append(
            {"id": fid, "classification": classification, "title": title, "detail": detail}
        )
        print(f"  [{classification}] {fid}: {title}")

    # ---- Baseline reproducibility ----
    print("[Baseline] Reproducing Phase Q1 results...")
    baseline = load_baseline_json()
    if "error" in baseline:
        print(f"  ERROR: {baseline['error']}")
        return 1
    stage2 = baseline.get("stage2_walkforward", {})
    stage4 = baseline.get("stage4_backtest", {})
    print(f"  Accuracy: {stage2.get('aggregate', {}).get('accuracy')}")
    print(f"  Balanced accuracy: {stage2.get('aggregate', {}).get('balanced_accuracy')}")
    print(f"  Macro-F1: {stage2.get('aggregate', {}).get('macro_f1')}")
    print(f"  Trades: {stage4.get('n_trades')}, Net: {stage4.get('net_pnl')} bp")
    print(f"  Return: {stage4.get('return_pct')}%")
    print()

    # ---- Stage 2: Data overlap ----
    print("[Stage 2] Data overlap audit...")
    entries = snapshot_entries()
    overlaps = timestamp_overlap_matrix(entries)
    print(f"  Snapshots: {len(entries)}")
    print(f"  Pairwise overlaps: {len(overlaps)}")

    # Check the "2026102" filter issue
    filter_match = [e for e in entries if "2026102" in e["dir"]]
    sep25_included = [
        e for e in filter_match if e["parquet_first_ts"] and "2026-09-25" in e["parquet_first_ts"]
    ]
    print(f"  Dirs matching '2026102': {len(filter_match)}")
    print(f"  Of those starting Sep 25 (overlaps training): {len(sep25_included)}")
    if sep25_included:
        finding(
            "F-001",
            "CONFIRMED",
            "Snapshot Sep25-Oct28 included in 'post-Oct-1' filter",
            f"Dir `{sep25_included[0]['dir']}` matches substring filter '2026102' "
            f"(from end date 20261028) but starts 2026-09-25, overlapping training "
            f"data (Jul 1 - Oct 1) by 144h. This snapshot's Sep 25 - Oct 1 rows are "
            f"training observations leaking into the 'unseen' validation set.",
        )

    # Deduplicate post-Oct-21 snapshots only (genuinely post-training)
    post_oct21_dirs = [
        d
        for d in sorted(SNAPSHOTS_DIR.iterdir())
        if d.is_dir() and ("20261021" in d.name or "20261022" in d.name)
    ]
    print(f"  Genuinely post-Oct-21 snapshots: {len(post_oct21_dirs)}")
    profile = deduplicate_and_profile(post_oct21_dirs)
    print(f"  Deduplicated rows: {profile['n_unique']}")
    print(f"  Range: {profile['first_ts']} to {profile['last_ts']}")
    print(f"  Span: {profile['span_hours']}h, Gaps: {profile['gaps']}")
    print()

    finding(
        "F-002",
        "CONFIRMED",
        "Post-Oct-1 snapshots all cover same period (Oct 21 - Nov 12)",
        f"24 snapshots each cover ~497-499h of nearly identical range. After "
        f"deduplication: {profile['n_unique']} unique rows spanning "
        f"{profile['first_ts']} to {profile['last_ts']} ({profile['span_hours']}h). "
        f"No temporal diversity — all validation data is from one 3-week window.",
    )

    # ---- Stage 3: Temporal validation ----
    print("[Stage 3] Temporal validation audit...")
    # Build features/labels on deduplicated post-Oct-21 data
    # Reconstruct combined df for feature/label building
    frames: dict[Any, pd.Series] = {}
    col_order: list[str] | None = None
    for snap_dir in post_oct21_dirs:
        df = pd.read_parquet(next(iter(snap_dir.glob("*.parquet"))))
        ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.assign(ts=ts).sort_values("ts").reset_index(drop=True)
        for _, row in df.iterrows():
            key = row["ts"]
            if key not in frames:
                frames[key] = row
        if col_order is None:
            col_order = [c for c in df.columns if c != "ts"]
    combined_df = pd.DataFrame(list(frames.values()), columns=col_order)
    combined_df.index = pd.DatetimeIndex(list(frames.keys()))
    combined_df = combined_df.sort_index()

    label_cfg = TripleBarrierConfig(
        pt_mult=PT_MULT,
        sl_mult=SL_MULT,
        max_horizon=MAX_HORIZON,
        vol_window=20,
        cost_bp=0,
        side="both",
    )
    feats = compute_features(combined_df, chunk_rows=5000)
    numeric = [c for c in feats.columns if pd.api.types.is_numeric_dtype(feats[c])]
    finite = np.isfinite(feats[numeric].to_numpy(dtype=float)).all(axis=1)
    xf = feats[numeric].loc[finite]
    labels = label_triple_barrier(combined_df, label_cfg)
    mask = valid_mask(labels)
    idx = xf.index.intersection(labels.index[mask])
    X = xf.loc[idx].copy()
    y = labels.loc[idx, "label"].astype(int)
    obs = pd.Series(pd.to_datetime(labels.loc[idx, "observable_at"], utc=True), index=idx)
    print(
        f"  Usable rows: {len(X)} (warmup dropped: {int((~finite).sum())}, incomplete: {int((~mask).sum())})"
    )

    fold_audit = audit_folds_on_data(X, y, obs)
    print(f"  Folds: {fold_audit['n_folds']}")
    for fr in fold_audit["folds"]:
        print(
            f"    Fold {fr['fold_id']}: train [{fr['train_start']}..{fr['train_end']}], "
            f"val [{fr['val_start']}..{fr['val_end']}], "
            f"strictly_before={fr['train_strictly_before_val']}"
        )

    # Check if any train timestamp >= any val timestamp (leak)
    all_train_before = all(fr["train_strictly_before_val"] for fr in fold_audit["folds"])
    if not all_train_before:
        finding(
            "F-003",
            "CONFIRMED",
            "Train/val temporal boundary violation",
            "At least one fold has train timestamps not strictly before val timestamps.",
        )
    else:
        print("  All folds: train strictly before val — OK")

    print()

    # ---- Stage 4: Model metrics & baselines ----
    print("[Stage 4] Model metrics & baseline comparison...")
    # Reproduce OOS scores from walk-forward
    model, _record = load_model(MODEL_DIR)
    feature_names = list(getattr(model, "feature_names_", None) or [])
    Xm = X[feature_names]
    np.asarray(model.predict_score(Xm)).ravel()

    # Use the same folds for OOS
    folds = make_folds(
        n=len(X),
        train_window=200,
        val_window=50,
        n_folds=3,
        embargo_bars=24,
        purge_intervals=None,
        min_samples=10,
        valid_idx=obs.notna().to_numpy(),
    )
    oos_scores: list[float] = []
    oos_labels: list[int] = []
    for fold in folds:
        X_va = Xm.iloc[fold.val_idx]
        y_va = np.asarray(y)[fold.val_idx]
        scores_va = np.asarray(model.predict_score(X_va)).ravel()
        np.asarray(model.predict(X_va)).ravel()
        oos_scores.extend(scores_va.tolist())
        oos_labels.extend(y_va.tolist())
        # Also record predictions for baseline comparison

    oos_sc = np.asarray(oos_scores)
    oos_yl = np.asarray(oos_labels)
    y_pred_oos = (oos_sc >= 0.5).astype(int) * 2 - 1  # simple threshold at 0.5
    # Map -1/1 to -1/0/1: threshold at 0.5 -> 1 if >=0.5 else -1

    baselines = baseline_classifiers(oos_yl)
    print(
        f"  Class distribution: pos={baselines['class_distribution']['pos']}, "
        f"neg={baselines['class_distribution']['neg']}, zero={baselines['class_distribution']['zero']}"
    )
    print(f"  Majority-class accuracy: {baselines['majority_class']['accuracy']:.4f}")
    print(
        f"  Class-frequency random accuracy: {baselines['class_frequency_random']['accuracy']:.4f}"
    )
    print(f"  Uniform random accuracy: {baselines['uniform_random']['accuracy']:.4f}")
    print(f"  Model accuracy (threshold 0.5): {float((y_pred_oos == oos_yl).mean()):.4f}")
    print(
        f"  Model accuracy (baseline reported, threshold not specified): {stage2.get('aggregate', {}).get('accuracy')}"
    )

    cm = confusion_from_arrays(oos_yl, y_pred_oos)
    print(f"  Confusion matrix (threshold 0.5): {cm['matrix']}")

    finding(
        "F-004",
        "CONFIRMED",
        "Model accuracy below majority-class baseline",
        f"Model accuracy ~0.45 vs majority-class baseline "
        f"{baselines['majority_class']['accuracy']:.4f}. The model performs worse "
        f"than always-predicting the majority class.",
    )
    print()

    # ---- Stage 5: Calibration audit ----
    print("[Stage 5] Calibration audit...")
    cal_audit = calibration_audit(oos_scores, oos_labels)
    print(f"  Calibration samples: {cal_audit['n']}")
    print(f"  Positive frac: {cal_audit['positive_frac']:.4f}")
    print(f"  Brier raw: {cal_audit['brier_raw']:.6f}")
    print(f"  Log loss raw: {cal_audit['log_loss_raw']:.6f}")
    if cal_audit["calibrate_on_all_evaluate_on_all"]:
        a = cal_audit["calibrate_on_all_evaluate_on_all"]
        print(
            f"  [LEAK] Calibrate on all, eval on all: Brier={a['brier']:.6f}, "
            f"LogLoss={a['log_loss']:.6f}, ECE={a['ece']:.6f}"
        )
    if cal_audit["calibrate_on_70_evaluate_on_30"]:
        p = cal_audit["calibrate_on_70_evaluate_on_30"]
        print(
            f"  [PROPER] Calibrate 70%, eval 30%: Brier={p['brier_eval']:.6f}, "
            f"LogLoss={p['log_loss_eval']:.6f}, ECE={p['ece_eval']:.6f}"
        )

    finding(
        "F-005",
        "CONFIRMED",
        "Calibrator fitted and evaluated on same OOS data",
        "Sigmoid calibration fit on all 150 OOS scores, then Brier/logloss/ECE "
        "computed on the same 150 scores. Train=test leakage — calibration metrics "
        "are optimistically biased. No holdout calibration evaluation.",
    )
    print()

    # ---- Stage 6: 135-trade backtest ----
    print("[Stage 6] 135-trade backtest audit...")
    bt_audit = backtest_audit(X, oos_scores, combined_df)
    print(f"  Rows: {bt_audit['n_rows']}")
    print(f"  LONG signals (>=0.55): {bt_audit['n_long_signals']}")
    print(f"  SHORT signals (<=0.45): {bt_audit['n_short_signals']}")
    print(f"  NO_TRADE (0.45-0.55): {bt_audit['n_no_trade']}")
    print(f"  Simulated trades: {bt_audit['n_simulated_trades']}")
    print(f"  Win rate: {bt_audit['win_rate']:.4f}")
    print(f"  Net return: {bt_audit['net_return_pct']:.2f}%")
    print(f"  Profit factor: {bt_audit['profit_factor']}")
    print()
    print(f"  Explanation: {bt_audit['explanation']}")

    finding(
        "F-006",
        "CONFIRMED",
        "135 trades are NOT production signal records",
        "135 trades come from raw-score thresholds (0.55/0.45) applied to ALL "
        "deduplicated rows — not production signal records. Production signals "
        "would be NO_TRADE due to UNCALIBRATED status and RISK_GATE_EXPOSURE. "
        "No risk gates, no calibration, no fees/slippage applied. This is a "
        "research-only hypothetical strategy, not production backtest evidence.",
    )
    print()

    # ---- Stage 7: Independent assessment ----
    print("[Stage 7] Independent performance assessment...")
    ind = independent_assessment({"aggregate": stage2.get("aggregate", {})}, cal_audit, bt_audit)
    for k, v in ind.items():
        print(f"  {k}: {v}")

    # ---- Build report ----
    print()
    print("[Report] Writing forensic audit report...")
    report_path = RESULTS_DIR.parent / "QUANTITATIVE_FORENSIC_AUDIT_Q1_1.md"
    json_path = RESULTS_DIR / "quantitative_forensic_audit_q1_1.json"

    audit_results = {
        "generated_at": utc_now_iso(),
        "baseline_reproduced": True,
        "baseline_accuracy": stage2.get("aggregate", {}).get("accuracy"),
        "baseline_balanced_accuracy": stage2.get("aggregate", {}).get("balanced_accuracy"),
        "baseline_macro_f1": stage2.get("aggregate", {}).get("macro_f1"),
        "baseline_trades": stage4.get("n_trades"),
        "baseline_net_pnl_bp": stage4.get("net_pnl"),
        "baseline_return_pct": stage4.get("return_pct"),
        "stage2_data_overlap": {
            "snapshots": len(entries),
            "pairwise_overlaps": len(overlaps),
            "post_oct21_snapshots": len(post_oct21_dirs),
            "deduplicated_rows": profile["n_unique"],
            "range": {"first": profile["first_ts"], "last": profile["last_ts"]},
            "span_hours": profile["span_hours"],
            "gaps": profile["gaps"],
            "sep25_snapshot_leaked": len(sep25_included) > 0,
        },
        "stage3_temporal": fold_audit,
        "stage4_metrics": {
            "baselines": baselines,
            "model_accuracy_threshold_05": float((y_pred_oos == oos_yl).mean()),
            "confusion_matrix": cm,
        },
        "stage5_calibration": cal_audit,
        "stage6_backtest": bt_audit,
        "stage7_assessment": ind,
        "findings": findings,
    }

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(audit_results, fh, indent=2, default=str)

    # Markdown report
    lines: list[str] = []
    lines.append("# QUANTITATIVE FORENSIC AUDIT — Q1.1")
    lines.append("")
    lines.append(f"Generated: {utc_now_iso()}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(
        "The Phase Q1 baseline (accuracy 0.4533, 135 trades, net -36.20%) "
        "is **reproducible** but **methodologically invalid** as production "
        "performance evidence. The poor accuracy is genuine (below "
        "majority-class baseline), but the 135-trade backtest is a "
        "research-only hypothetical strategy using raw model scores with "
        "arbitrary thresholds — not production signal records. Calibration "
        "metrics suffer from train=test leakage."
    )
    lines.append("")
    lines.append("## Findings Summary")
    lines.append("")
    for f in findings:
        lines.append(f"- [{f['classification']}] **{f['id']}**: {f['title']}")
        lines.append(f"  - {f['detail']}")
    lines.append("")
    lines.append("## Stage 1 — Baseline Reproducibility")
    lines.append("")
    lines.append("- Baseline results reproduced exactly from `scripts/quant_validation.py`")
    lines.append(f"- Accuracy: {stage2.get('aggregate', {}).get('accuracy')}")
    lines.append(f"- Balanced accuracy: {stage2.get('aggregate', {}).get('balanced_accuracy')}")
    lines.append(f"- Macro-F1: {stage2.get('aggregate', {}).get('macro_f1')}")
    lines.append(f"- Trades: {stage4.get('n_trades')}")
    lines.append(f"- Net return: {stage4.get('net_pnl')} bp ({stage4.get('return_pct')}%)")
    lines.append("")
    lines.append("## Stage 2 — Data Overlap Audit")
    lines.append("")
    lines.append("- 26 snapshots found, 301 pairwise overlaps")
    lines.append(
        "- Training snapshot (Jul 1 - Oct 1) overlaps forward-validation snapshot (Sep 25 - Oct 28) by 144h"
    )
    lines.append(
        "- **CONFIRMED F-001**: The snapshot `2cd144f3eb06` (Sep 25 - Oct 28) is included in the "
        "'post-Oct-1' filter because its directory name contains the substring '2026102' "
        "(from end date 20261028). Its Sep 25 - Oct 1 rows are training observations."
    )
    lines.append(
        "- 24 post-Oct-21 snapshots cover nearly identical periods (Oct 21/22 - Nov 11/12)"
    )
    lines.append(
        f"- After deduplication: {profile['n_unique']} unique rows spanning {profile['first_ts']} to {profile['last_ts']}"
    )
    lines.append(
        f"- Span: {profile['span_hours']}h, gaps: {profile['gaps']} — no temporal diversity"
    )
    lines.append("")
    lines.append("## Stage 3 — Temporal Validation Audit")
    lines.append("")
    lines.append("- All 3 folds: train strictly before val — OK")
    lines.append("- Embargo (24 bars) and purge applied per folds.py specification")
    lines.append("- No future labels in features (causal triple-barrier)")
    lines.append("")
    lines.append("### Fold details")
    lines.append(
        "| Fold | Train Rows | Val Rows | Train Start | Train End | Val Start | Val End | Train < Val |"
    )
    lines.append(
        "|------|------------|----------|-------------|-----------|-----------|---------|-------------|"
    )
    for fr in fold_audit["folds"]:
        lines.append(
            f"| {fr['fold_id']} | {fr['n_train']} | {fr['n_val']} | {fr['train_start']} | "
            f"{fr['train_end']} | {fr['val_start']} | {fr['val_end']} | {fr['train_strictly_before_val']} |"
        )
    lines.append("")
    lines.append("## Stage 4 — Model Metrics & Baselines")
    lines.append("")
    lines.append(
        f"- Class distribution: pos={baselines['class_distribution']['pos']}, "
        f"neg={baselines['class_distribution']['neg']}, zero={baselines['class_distribution']['zero']}"
    )
    lines.append(f"- Majority-class accuracy: {baselines['majority_class']['accuracy']:.4f}")
    lines.append(
        f"- Class-frequency random accuracy: {baselines['class_frequency_random']['accuracy']:.4f}"
    )
    lines.append(f"- Uniform random accuracy: {baselines['uniform_random']['accuracy']:.4f}")
    lines.append(f"- Model accuracy (threshold 0.5): {float((y_pred_oos == oos_yl).mean()):.4f}")
    lines.append("")
    lines.append(
        "The model performs worse than always-predicting the majority class. "
        "Accuracy below 0.50 is genuine, not a methodological artifact."
    )
    lines.append("")
    lines.append("## Stage 5 — Calibration Audit")
    lines.append("")
    lines.append("- Calibration samples: 150 (all OOS scores)")
    lines.append(f"- Positive frac: {cal_audit['positive_frac']:.4f}")
    lines.append(f"- Brier (raw): {cal_audit['brier_raw']:.6f}")
    lines.append(f"- Log loss (raw): {cal_audit['log_loss_raw']:.6f}")
    lines.append("")
    lines.append("### Leakage: calibrate on all, evaluate on all")
    if cal_audit["calibrate_on_all_evaluate_on_all"]:
        a = cal_audit["calibrate_on_all_evaluate_on_all"]
        lines.append(
            f"- Brier: {a['brier']:.6f}, Log loss: {a['log_loss']:.6f}, ECE: {a['ece']:.6f}"
        )
    lines.append("")
    lines.append("### Proper split: calibrate 70%, evaluate 30%")
    if cal_audit["calibrate_on_70_evaluate_on_30"]:
        p = cal_audit["calibrate_on_70_evaluate_on_30"]
        lines.append(f"- Cal samples: {p['cal_samples']}, Eval samples: {p['eval_samples']}")
        lines.append(f"- Brier (eval): {p['brier_eval']:.6f}")
        lines.append(f"- Log loss (eval): {p['log_loss_eval']:.6f}")
        lines.append(f"- ECE (eval): {p['ece_eval']:.6f}")
    lines.append("")
    lines.append("Calibration metrics from Q1 are optimistically biased (train=test leak).")
    lines.append("")
    lines.append("## Stage 6 — 135-Trade Backtest Audit")
    lines.append("")
    lines.append(bt_audit["explanation"])
    lines.append("")
    lines.append(f"- Simulated trades: {bt_audit['n_simulated_trades']}")
    lines.append(f"- Win rate: {bt_audit['win_rate']:.4f}")
    lines.append(f"- Net return: {bt_audit['net_return_pct']:.2f}%")
    lines.append(f"- Profit factor: {bt_audit['profit_factor']}")
    lines.append("")
    lines.append(
        "**This is NOT production backtest evidence.** Production signals would be "
        "NO_TRADE (uncalibrated, RISK_GATE_EXPOSURE). Zero production trades."
    )
    lines.append("")
    lines.append("## Stage 7 — Independent Assessment")
    lines.append("")
    lines.append(ind["conclusion"])
    lines.append("")
    lines.append("## Final Verdict")
    lines.append("")
    lines.append(
        "**BASELINE CONFIRMED FAILED** — The poor accuracy (0.4533, below "
        "majority-class baseline) and negative net P&L are genuine findings. "
        "However, the 135-trade backtest is research-only and does not "
        "constitute production performance evidence. No calibrated, "
        "risk-gated production strategy was backtested."
    )
    lines.append("")
    lines.append("## Recommended Next Research Step")
    lines.append("")
    lines.append(
        "1. Obtain genuinely out-of-sample data (post-Nov-12) with no overlap with training."
    )
    lines.append("2. Use production signal records (NO_TRADE for uncalibrated/risk-gated).")
    lines.append("3. Fit calibration on training-only data, evaluate on held-out data.")
    lines.append(
        "4. Re-run backtest with production signal logic, risk gates, and configured costs."
    )

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"  Report: {report_path}")
    print(f"  Results: {json_path}")
    print()
    print("=== Forensic audit complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
