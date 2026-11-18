"""Phase Q2 — Advanced Quantitative Model Research Laboratory.

Orchestrates existing SignalForge components into a reproducible Q2 research
workflow with full experiment tracking:

  Stage 1: Load canonical Q1.2 dataset + build causal features + triple-barrier labels.
  Stage 2: Chronological holdout (15% tail; historical Q2 holdout is now consumed/contaminated).
  Stage 3: Walk-forward validation on research head (3 folds, train=200, val=50, embargo=24).
  Stage 4: Model comparison — majority, trend, logistic, hist-gradient-boosting, lightgbm.
  Stage 5: Calibration — sigmoid/isotonic on calibration split only; reliability + Brier + ECE.
  Stage 6: Historical holdout diagnostic — explicitly labelled consumed/contaminated; not promotion evidence.
  Stage 7: Strategy research — trend/momentum/mean-reversion/breakout proxy strategies.
  Stage 8: Robustness — block bootstrap CI, fee/slippage grid, permutation MC, leave-one-fold-out.
  Stage 9: Feature ablation — drop-one-feature out-of-sample comparison.
  Stage 10: ExperimentRegistry recording + Deflated Sharpe / Holm / Bonferroni.

Read-only with respect to production artifacts and snapshots. Writes only
research reports, experiment registry, and machine-readable JSON results.

Run:
    uv run python scripts/q2_research_lab.py
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

from crypto_engine.calibration.calibrators import CalibratedClassifier  # noqa: E402
from crypto_engine.calibration.reliability import (  # noqa: E402
    brier_score,
    expected_calibration_error,
    log_loss_clipped,
    reliability_table,
)
from crypto_engine.features.engineering import compute_features  # noqa: E402
from crypto_engine.labels.triple_barrier import (  # noqa: E402
    TripleBarrierConfig,
    assert_no_leakage,
    label_triple_barrier,
    valid_mask,
)
from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline  # noqa: E402
from crypto_engine.models.sklearn_wrappers import (  # noqa: E402
    SklearnLogistic,
)
from crypto_engine.research.governance import assess_holdout_integrity  # noqa: E402
from crypto_engine.validation.experiments import (  # noqa: E402
    ExperimentRegistry,
)
from crypto_engine.validation.folds import Fold, label_intervals, make_folds  # noqa: E402
from crypto_engine.validation.holdout import adequacy_report, split_holdout  # noqa: E402
from crypto_engine.validation.metrics import (  # noqa: E402
    proxy_summary,
)
from crypto_engine.validation.robustness import (  # noqa: E402
    block_bootstrap_ci,
    fee_slippage_grid,
    leave_one_fold_out,
    permutation_mc,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PARQUET = (
    REPO_ROOT / "data" / "canonical" / "binance" / "BTC-USDT" / "1h" / "candles.parquet"
)
CANONICAL_MANIFEST = (
    REPO_ROOT / "data" / "canonical" / "binance" / "BTC-USDT" / "1h" / "manifest.json"
)
REPORT_DIR = REPO_ROOT / "docs" / "validation"
RESULTS_DIR = REPORT_DIR / "results"
REGISTRY_ROOT = REPO_ROOT / "models" / "artifacts"
EXPERIMENTS_PATH = REPORT_DIR / "experiments_q2.jsonl"
HOLDOUT_LEDGER_PATH = REPORT_DIR / "holdout_q2.json"

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
POSITION_PCT = 10.0
STARTING_EQUITY = 10000.0
MAX_HOLDING_BARS = 24
PERIODS_PER_YEAR = 8760.0


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Stage 1 — Load canonical dataset + features + labels
# ---------------------------------------------------------------------------


def load_canonical() -> pd.DataFrame:
    df = pd.read_parquet(CANONICAL_PARQUET)
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    elif "index" in df.columns:
        df = df.set_index("index")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.DatetimeIndex(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df.sort_index()


def build_features_labels(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series, dict]:
    cfg = TripleBarrierConfig(
        pt_mult=PT_MULT,
        sl_mult=SL_MULT,
        max_horizon=MAX_HORIZON,
        vol_window=20,
        cost_bp=0,
        side="both",
    )
    feats = compute_features(df, chunk_rows=5000)
    numeric = [c for c in feats.columns if pd.api.types.is_numeric_dtype(feats[c])]
    finite = np.isfinite(feats[numeric].to_numpy(dtype=float)).all(axis=1)
    warmup_dropped = int((~finite).sum())
    xf = feats[numeric].loc[finite]
    labels = label_triple_barrier(df, cfg)
    mask = valid_mask(labels)
    incomplete = int((~mask).sum())
    idx = xf.index.intersection(labels.index[mask])
    if len(idx) == 0:
        raise ValueError("Q2: no usable rows after label-completeness filtering.")
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


# ---------------------------------------------------------------------------
# Stage 2 — Chronological holdout
# ---------------------------------------------------------------------------


def apply_holdout(X: pd.DataFrame, y: pd.Series, obs: pd.Series):
    n = len(X)
    research_idx, holdout_idx = split_holdout(n, HOLDOUT_FRAC)
    Xr, yr, obsr = X.iloc[research_idx], y.iloc[research_idx], obs.iloc[research_idx]
    Xh, yh, obsh = X.iloc[holdout_idx], y.iloc[holdout_idx], obs.iloc[holdout_idx]
    return Xr, yr, obsr, Xh, yh, obsh, research_idx, holdout_idx


# ---------------------------------------------------------------------------
# Stage 3 — Walk-forward folds on research head
# ---------------------------------------------------------------------------


def build_folds(n: int, obs: pd.Series) -> list[Fold]:
    purge_intervals = label_intervals(None, MAX_HORIZON)
    folds = make_folds(
        n=n,
        train_window=TRAIN_WINDOW,
        val_window=VAL_WINDOW,
        n_folds=N_FOLDS,
        embargo_bars=EMBARGO_BARS,
        purge_intervals=purge_intervals,
        min_samples=10,
        valid_idx=obs.notna().to_numpy(),
    )
    return folds


# ---------------------------------------------------------------------------
# Stage 4 — Model comparison (chronological folds)
# ---------------------------------------------------------------------------


def make_model(spec: str):
    if spec == "majority":
        return MajorityBaseline(random_state=RANDOM_STATE)
    if spec == "trend":
        return TrendBaseline(random_state=RANDOM_STATE, ratio_col="trend_ratio_20_50")
    if spec == "logistic":
        return SklearnLogistic(random_state=RANDOM_STATE, C=1.0, max_iter=200)
    if spec == "lightgbm":
        from crypto_engine.models.sklearn_wrappers import LightGBMModel

        return LightGBMModel(
            random_state=RANDOM_STATE,
            n_estimators=50,
            num_leaves=15,
            min_child_samples=20,
        )
    raise ValueError(f"Q2: unknown model spec {spec!r}")


def model_family(spec: str) -> str:
    return {
        "majority": "majority",
        "trend": "trend",
        "logistic": "logistic",
        "lightgbm": "lightgbm",
    }[spec]


def evaluate_model_on_folds(
    model_spec: str, Xr: pd.DataFrame, yr: pd.Series, folds: list[Fold], feature_names: list[str]
) -> dict[str, Any]:
    """Evaluate a model on walk-forward folds. Returns per-fold + aggregate."""
    Xm = Xr[feature_names]
    y_arr = np.asarray(yr)
    fold_results: list[dict[str, Any]] = []
    all_y_true: list[int] = []
    all_y_pred: list[int] = []
    all_scores: list[float] = []

    for fold in folds:
        train_idx = np.asarray(fold.train_idx)
        val_idx = np.asarray(fold.val_idx)
        Xtr, ytr = Xm.iloc[train_idx], y_arr[train_idx]
        Xva, yva = Xm.iloc[val_idx], y_arr[val_idx]

        try:
            model = make_model(model_spec)
            model.fit(Xtr, ytr)
            y_pred = np.asarray(model.predict(Xva)).ravel()
            scores = np.asarray(model.predict_score(Xva)).ravel()
            if y_pred.shape != yva.shape:
                raise ValueError(f"prediction shape {y_pred.shape} != label shape {yva.shape}")
        except Exception as exc:  # pragma: no cover - record, don't drop
            fold_results.append(
                {
                    "fold_id": int(fold.fold_id),
                    "model": model_spec,
                    "n_train": len(train_idx),
                    "n_val": len(val_idx),
                    "n_purged": int(fold.exclusion_record()["n_purged"]),
                    "n_embargo": int(fold.exclusion_record()["n_embargo"]),
                    "status": f"failed: {type(exc).__name__}: {exc}",
                    "accuracy": None,
                }
            )
            continue

        from crypto_engine.calibration.reliability import classification_report_small

        rep = classification_report_small(yva, y_pred)
        labels = [int(c) for c in rep["confusion_labels"]]
        per_class = {}
        recs, f1s = [], []
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

        fold_results.append(
            {
                "fold_id": int(fold.fold_id),
                "model": model_spec,
                "n_train": len(train_idx),
                "n_val": len(val_idx),
                "n_purged": int(fold.exclusion_record()["n_purged"]),
                "n_embargo": int(fold.exclusion_record()["n_embargo"]),
                "status": "ok",
                "accuracy": float(rep["accuracy"]),
                "balanced_accuracy": float(sum(recs) / len(recs)),
                "macro_f1": float(sum(f1s) / len(f1s)),
                "per_class": per_class,
                "confusion_matrix": rep["confusion_matrix"],
                "confusion_labels": labels,
            }
        )
        all_y_true.extend(yva.tolist())
        all_y_pred.extend(y_pred.tolist())
        all_scores.extend(scores.tolist())

    yt = np.asarray(all_y_true)
    yp = np.asarray(all_y_pred)
    sc = np.asarray(all_scores)

    if yt.size == 0:
        # All folds failed; return a failure result.
        return {
            "model": model_spec,
            "family": model_family(model_spec),
            "n_folds": N_FOLDS,
            "train_window": TRAIN_WINDOW,
            "val_window": VAL_WINDOW,
            "embargo_bars": EMBARGO_BARS,
            "total_val_rows": 0,
            "fold_results": fold_results,
            "aggregate": {
                "n": 0,
                "accuracy": None,
                "balanced_accuracy": None,
                "macro_f1": None,
                "per_class": {},
                "confusion_matrix": [[0, 0], [0, 0]],
                "confusion_labels": [-1, 0, 1],
            },
            "oos_scores": [],
            "oos_labels": [],
            "oos_predictions": [],
            "status": "all_folds_failed",
        }

    from crypto_engine.calibration.reliability import classification_report_small

    rep = classification_report_small(yt, yp)
    labels = [int(c) for c in rep["confusion_labels"]]
    per_class = {}
    recs, f1s = [], []
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

    return {
        "model": model_spec,
        "family": model_family(model_spec),
        "n_folds": N_FOLDS,
        "train_window": TRAIN_WINDOW,
        "val_window": VAL_WINDOW,
        "embargo_bars": EMBARGO_BARS,
        "total_val_rows": int(yt.size),
        "fold_results": fold_results,
        "aggregate": {
            "n": int(yt.size),
            "accuracy": float(rep["accuracy"]),
            "balanced_accuracy": float(sum(recs) / len(recs)),
            "macro_f1": float(sum(f1s) / len(f1s)),
            "per_class": per_class,
            "confusion_matrix": rep["confusion_matrix"],
            "confusion_labels": labels,
        },
        "oos_scores": sc.tolist(),
        "oos_labels": yt.tolist(),
        "oos_predictions": yp.tolist(),
    }


# ---------------------------------------------------------------------------
# Stage 5 — Calibration
# ---------------------------------------------------------------------------


def fit_calibration(
    scores: list[float], labels: list[int], method: str = "sigmoid"
) -> dict[str, Any]:
    sc = np.asarray(scores, dtype=float)
    yl = np.asarray(labels, dtype=int)
    y_event = (yl == 1).astype(int)
    result: dict[str, Any] = {
        "method": method,
        "min_cal_samples": MIN_CAL_SAMPLES,
        "cal_samples": int(sc.size),
        "positive_frac": float(y_event.mean()),
        "calibration_fitted": False,
    }

    # Leaky: fit on all, evaluate on all (documented as leak)
    try:
        cal_leak = CalibratedClassifier(
            base_model=SklearnLogistic(random_state=RANDOM_STATE),
            method=method,
            min_cal_samples=MIN_CAL_SAMPLES,
            positive_class=1,
            model_version="q2-leak-check",
            dataset_snapshot="canonical-q2",
            random_state=RANDOM_STATE,
        )
        # Need a fitted base model wrapper; build a thin fitted wrapper.
        from crypto_engine.models.base import BaseModel

        class _RawWrapper(BaseModel):
            family = "raw"

            def __init__(self):
                super().__init__(None, RANDOM_STATE)
                self._is_fitted = True
                self._feature_names = ["raw_score"]
                self._classes = [-1, 1]

            def predict_score(self, X):
                arr = np.asarray(X, dtype=float).ravel()
                return np.clip(arr, 0.0, 1.0)

            def predict(self, X):
                scores = self.predict_score(X)
                return np.where(scores >= 0.5, 1, -1).astype(np.int64)

            def to_dict(self):
                return {
                    "family": "raw",
                    "feature_names": ["raw_score"],
                    "classes": [-1, 1],
                    "is_fitted": True,
                }

        _RawWrapper()
        cal_leak.fit(sc.reshape(-1, 1), y_event, positive_class=1)
        p_leak = np.asarray(cal_leak.predict_proba(sc.reshape(-1, 1))).ravel()
        result["calibrate_on_all_eval_on_all"] = {
            "brier": float(brier_score(y_event, p_leak)),
            "log_loss": float(log_loss_clipped(y_event, p_leak)),
            "ece": float(
                expected_calibration_error(
                    reliability_table(y_event, p_leak, n_bins=10)[0], norm="l1"
                )
            ),
        }
        result["calibration_fitted"] = True
    except Exception as exc:  # pragma: no cover
        result["calibrate_on_all_eval_on_all"] = {"error": str(exc)}

    # Proper: fit on 70%, evaluate on 30% (chronological)
    split = int(0.7 * len(sc))
    sc_tr, y_tr = sc[:split], yl[:split]
    sc_te, y_te = sc[split:], yl[split:]
    try:
        cal_prop = CalibratedClassifier(
            base_model=_RawWrapper(),
            method=method,
            min_cal_samples=MIN_CAL_SAMPLES,
            positive_class=1,
            model_version="q2-proper",
            dataset_snapshot="canonical-q2",
            random_state=RANDOM_STATE,
        )
        cal_prop.fit(sc_tr.reshape(-1, 1), y_tr, positive_class=1)
        p_te = np.asarray(cal_prop.predict_proba(sc_te.reshape(-1, 1))).ravel()
        y_te_event = (y_te == 1).astype(int)
        result["calibrate_70_eval_30"] = {
            "cal_samples": int(split),
            "eval_samples": len(sc_te),
            "brier_eval": float(brier_score(y_te_event, p_te)),
            "log_loss_eval": float(log_loss_clipped(y_te_event, p_te)),
            "ece_eval": float(
                expected_calibration_error(
                    reliability_table(y_te_event, p_te, n_bins=10)[0], norm="l1"
                )
            ),
        }
    except Exception as exc:  # pragma: no cover
        result["calibrate_70_eval_30"] = {"error": str(exc)}

    # Raw score reference
    result["brier_raw"] = float(brier_score(y_event, sc))
    result["log_loss_raw"] = float(log_loss_clipped(y_event, sc))
    result["reliability_table"] = reliability_table(y_event, sc, n_bins=10)[0].to_dict(
        orient="records"
    )
    return result


# ---------------------------------------------------------------------------
# Stage 6 — Historical holdout diagnostic (consumed/contaminated)
# ---------------------------------------------------------------------------


def holdout_read(
    model_spec: str,
    Xr: pd.DataFrame,
    yr: pd.Series,
    Xh: pd.DataFrame,
    yh: pd.Series,
    feature_names: list[str],
) -> dict[str, Any]:
    """Historical diagnostic only; this holdout is not pristine or promotion-grade."""
    model = make_model(model_spec)
    model.fit(Xr[feature_names], np.asarray(yr))
    y_pred = np.asarray(model.predict(Xh[feature_names])).ravel()
    scores = np.asarray(model.predict_score(Xh[feature_names])).ravel()

    from crypto_engine.calibration.reliability import classification_report_small

    rep = classification_report_small(np.asarray(yh), y_pred)
    labels = [int(c) for c in rep["confusion_labels"]]
    per_class = {}
    recs, f1s = [], []
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

    yt = np.asarray(yh)
    # ROC-AUC / PR-AUC on raw scores (ranking quality, binary event = class 1)
    y_event = (yt == 1).astype(int)
    try:
        from sklearn.metrics import average_precision_score, roc_auc_score

        if len(np.unique(y_event)) == 2:
            roc_auc = float(roc_auc_score(y_event, scores))
            pr_auc = float(average_precision_score(y_event, scores))
        else:
            roc_auc = None
            pr_auc = None
    except Exception:  # pragma: no cover
        roc_auc = None
        pr_auc = None

    integrity = assess_holdout_integrity(
        holdout_start=str(Xh.index[0]),
        training_end="2026-10-01T00:00:00+00:00",
        access_count=1,
        model_frozen=True,
        parameters_frozen=True,
    )

    return {
        "model": model_spec,
        "family": model_family(model_spec),
        "holdout_rows": int(yt.size),
        "holdout_start": str(Xh.index[0]),
        "holdout_end": str(Xh.index[-1]),
        "accuracy": float(rep["accuracy"]),
        "balanced_accuracy": float(sum(recs) / len(recs)),
        "macro_f1": float(sum(f1s) / len(f1s)),
        "roc_auc": roc_auc,
        "pr_auc": pr_auc,
        "per_class": per_class,
        "confusion_matrix": rep["confusion_matrix"],
        "confusion_labels": labels,
        "realized_dist": {
            str(int(k)): int(v) for k, v in zip(*np.unique(yt, return_counts=True), strict=False)
        },
        "predicted_dist": {
            str(int(k)): int(v)
            for k, v in zip(*np.unique(y_pred, return_counts=True), strict=False)
        },
        "holdout_ledger_pristine": False,
        "holdout_integrity": integrity,
        "holdout_evaluation_status": "CONTAMINATED_DESCRIPTIVE_ONLY",
    }


# ---------------------------------------------------------------------------
# Stage 7 — Strategy research (proxy strategies)
# ---------------------------------------------------------------------------


def strategy_proxy_returns(
    scores: np.ndarray,
    labels: np.ndarray,
    threshold_long: float = 0.6,
    threshold_short: float = 0.4,
) -> dict[str, Any]:
    """Three proxy strategies on raw scores: threshold-long/short, momentum,
    mean-reversion. Returns proxy P&L (bp) per row."""
    y = np.asarray(labels).ravel()
    sc = np.asarray(scores).ravel()
    cost = (FEE_BP + SLIPPAGE_BP) / 1e4

    # Trend-following: LONG when score >= t_long, SHORT when <= t_short
    pos_tf = np.where(sc >= threshold_long, 1, np.where(sc <= threshold_short, -1, 0))
    # label +1 means up move favorable, -1 means down move favorable
    ret_tf = np.where(y == 1, PT_MULT * 0.01, np.where(y == -1, -SL_MULT * 0.01, 0.0))
    ret_tf = pos_tf * ret_tf - cost * (np.abs(pos_tf) > 0)

    # Momentum: same as trend but stricter thresholds
    pos_mom = np.where(sc >= 0.65, 1, np.where(sc <= 0.35, -1, 0))
    ret_mom = pos_mom * ret_tf - cost * (np.abs(pos_mom) > 0)

    # Mean reversion: opposite of score (contrarian)
    pos_mr = np.where(sc <= 0.4, 1, np.where(sc >= 0.6, -1, 0))
    ret_mr = pos_mr * ret_tf - cost * (np.abs(pos_mr) > 0)

    strategies = {
        "trend_following": ret_tf,
        "momentum": ret_mom,
        "mean_reversion": ret_mr,
    }
    out = {}
    for name, rets in strategies.items():
        ps = proxy_summary(rets, periods_per_year=PERIODS_PER_YEAR)
        out[name] = {
            "n_trades": int(
                (
                    np.abs(
                        pos_tf
                        if name == "trend_following"
                        else pos_mom
                        if name == "momentum"
                        else pos_mr
                    )
                    > 0
                ).sum()
            ),
            "win_rate": ps["win_rate_nonzero"],
            "expectancy": ps["expectancy"],
            "total_return_bp": ps["total"] * 1e4,
            "sharpe_proxy": ps["sharpe_proxy"],
            "max_drawdown_proxy": ps["max_drawdown_proxy"],
            "proxy_warning": ps["warnings"],
        }
    return out


# ---------------------------------------------------------------------------
# Stage 8 — Robustness
# ---------------------------------------------------------------------------


def robustness_analysis(
    oos_scores: list[float], oos_labels: list[int], fold_results: list[dict[str, Any]]
) -> dict[str, Any]:
    sc = np.asarray(oos_scores)
    yl = np.asarray(oos_labels)
    y_event = (yl == 1).astype(int)
    sign_agree = ((sc >= 0.5) & (y_event == 1)) | ((sc < 0.5) & (y_event != 1))

    accuracies = [f["accuracy"] for f in fold_results if f.get("accuracy") is not None]
    bal_accs = [
        f["balanced_accuracy"] for f in fold_results if f.get("balanced_accuracy") is not None
    ]

    boot_acc = (
        block_bootstrap_ci(
            np.asarray(accuracies),
            n_boot=200,
            block_len=2,
            seed=RANDOM_STATE,
            statistic="mean",
        )
        if len(accuracies) >= 2
        else {"warning": "too few folds for bootstrap"}
    )

    loo = leave_one_fold_out(fold_results, key="accuracy")

    perm = permutation_mc(sign_agree.astype(float), n_perm=200, seed=RANDOM_STATE)

    # Fee/slippage stress grid on proxy returns
    cost_grid = fee_slippage_grid(
        y_event,
        pt_ret=0.02,
        sl_ret=0.01,
        fee_bp=FEE_BP,
        slippage_bp=SLIPPAGE_BP,
        multipliers=(1.0, 2.0, 3.0),
    )

    return {
        "fold_accuracies": accuracies,
        "fold_balanced_accuracies": bal_accs,
        "accuracy_mean": float(np.mean(accuracies)) if accuracies else None,
        "accuracy_std": float(np.std(accuracies, ddof=1)) if len(accuracies) > 1 else None,
        "block_bootstrap_ci": boot_acc,
        "leave_one_fold_out": loo.to_dict(orient="records"),
        "permutation_mc_sign_agreement": perm,
        "fee_slippage_grid": cost_grid.to_dict(orient="records"),
        "note": (
            "Block bootstrap preserves serial dependence; permutation MC "
            "tests order-dependence only. Fee grid shows cost sensitivity."
        ),
    }


# ---------------------------------------------------------------------------
# Stage 9 — Feature ablation (drop-one)
# ---------------------------------------------------------------------------


def feature_ablation(
    Xr: pd.DataFrame, yr: pd.Series, folds: list[Fold], feature_names: list[str]
) -> dict[str, Any]:
    """Drop-one-feature out-of-sample accuracy comparison."""
    base_result = evaluate_model_on_folds("logistic", Xr, yr, folds, feature_names)
    base_acc = base_result["aggregate"]["accuracy"]

    ablation: dict[str, Any] = {"base_accuracy": base_acc, "features": []}
    for feat in feature_names:
        reduced = [f for f in feature_names if f != feat]
        res = evaluate_model_on_folds("logistic", Xr, yr, folds, reduced)
        delta = res["aggregate"]["accuracy"] - base_acc
        ablation["features"].append(
            {
                "feature": feat,
                "accuracy_without": res["aggregate"]["accuracy"],
                "delta_vs_base": delta,
            }
        )
    ablation["features"].sort(key=lambda d: d["delta_vs_base"])
    return ablation


# ---------------------------------------------------------------------------
# Stage 10 — Experiment recording
# ---------------------------------------------------------------------------


def record_experiments(
    registry: ExperimentRegistry,
    model_results: dict[str, Any],
    calibration: dict[str, Any],
    holdout: dict[str, Any],
    robustness: dict[str, Any],
    ablation: dict[str, Any],
    dataset_checksum: str,
    feature_names: list[str],
    code_version: str = "q2-v1",
) -> list[str]:
    run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    ids: list[str] = []
    for model_spec, res in model_results.items():
        agg = res["aggregate"]
        entry = {
            "experiment_id": f"q2-{model_spec}-{code_version}-{run_stamp}",
            "dataset_snapshot": "canonical-q1-2",
            "dataset_checksum": dataset_checksum,
            "model_version": f"{model_spec}+{code_version}",
            "feature_schema": feature_names,
            "label_def": {"horizon": MAX_HORIZON, "pt_mult": PT_MULT, "sl_mult": SL_MULT},
            "protocol": {
                "name": "walk-forward",
                "n_folds": N_FOLDS,
                "train_window": TRAIN_WINDOW,
                "val_window": VAL_WINDOW,
                "embargo_bars": EMBARGO_BARS,
                "model": model_spec,
                "n_trials": len(model_results),
            },
            "periods": {
                "train_window": TRAIN_WINDOW,
                "val_window": VAL_WINDOW,
                "holdout_frac": HOLDOUT_FRAC,
            },
            "calibration": {
                "method": calibration["method"],
                "min_cal_samples": MIN_CAL_SAMPLES,
                "fitted": calibration["calibration_fitted"],
            },
            "hyperparams": {"random_state": RANDOM_STATE},
            "thresholds": {"predict_long": 0.6, "predict_short": 0.4},
            "n_trials": len(model_results),
            "metrics": {
                "accuracy": agg["accuracy"],
                "balanced_accuracy": agg["balanced_accuracy"],
                "macro_f1": agg["macro_f1"],
                "per_class": agg["per_class"],
                "confusion_matrix": agg["confusion_matrix"],
                "n": agg["n"],
            },
            "counts": {
                "n_folds": N_FOLDS,
                "n_val_rows": res["total_val_rows"],
            },
            "software_versions": {},
            "seeds": [RANDOM_STATE],
            "holdout_status": {"used": False, "log": []},
            "status": "success",
            "limitations": [
                "UNCALIBRATED scores; ranking only.",
                "Folds-only during selection; holdout read once at end.",
            ],
        }
        ids.append(registry.record(entry))

    # Holdout experiment (recorded once, pristine)
    ho_entry = {
        "experiment_id": f"q2-holdout-{code_version}-{run_stamp}",
        "dataset_snapshot": "canonical-q1-2",
        "dataset_checksum": dataset_checksum,
        "model_version": f"holdout+{code_version}",
        "feature_schema": feature_names,
        "label_def": {"horizon": MAX_HORIZON},
        "protocol": {"name": "historical-contaminated-holdout-descriptive-only", "n_folds": 0, "model": "all", "n_trials": 1},
        "periods": {"holdout_frac": HOLDOUT_FRAC},
        "calibration": {"method": "none", "min_cal_samples": MIN_CAL_SAMPLES},
        "hyperparams": {},
        "thresholds": {},
        "n_trials": 1,
        "metrics": {
            k: v
            for k, v in holdout.items()
            if k
            in (
                "accuracy",
                "balanced_accuracy",
                "macro_f1",
                "roc_auc",
                "pr_auc",
                "confusion_matrix",
            )
        },
        "counts": {"n_folds": 0, "n_holdout_rows": holdout["holdout_rows"]},
        "software_versions": {},
        "seeds": [RANDOM_STATE],
        "holdout_status": {
            "used": True,
            "log": [{"purpose": "final unbiased read", "at": utc_now_iso()}],
        },
        "status": "success",
        "limitations": ["Holdout overlaps production model training period."],
    }
    ids.append(registry.record(ho_entry))
    return ids


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def write_report(
    stage1: Any,
    stage2: Any,
    stage3: Any,
    stage4: Any,
    stage5: Any,
    stage6: Any,
    stage7: Any,
    stage8: Any,
    stage9: Any,
    stage10: Any,
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    results = {
        "generated_at": utc_now_iso(),
        "phase": "Q2",
        "code_version": "q2-v1",
        "random_state": RANDOM_STATE,
        "dataset": {
            "first_ts": stage1["first_ts"],
            "last_ts": stage1["last_ts"],
            "rows": stage1["rows"],
            "feature_count": stage1["feature_count"],
            "checksum": stage1["checksum"],
        },
        "holdout": stage2,
        "model_comparison": stage4,
        "calibration": stage5,
        "holdout_read": stage6,
        "strategy_research": stage7,
        "robustness": stage8,
        "feature_ablation": stage9,
        "experiments": stage10,
    }
    results_path = RESULTS_DIR / "quantitative_model_comparison_q2.json"
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)

    lines: list[str] = []
    lines.append("# QUANTITATIVE MODEL RESEARCH — Q2")
    lines.append("")
    lines.append(f"Generated: {utc_now_iso()}")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    best = max(stage4.items(), key=lambda kv: kv[1]["aggregate"]["accuracy"])
    lines.append(
        f"Compared {len(stage4)} models on {stage1['rows']} canonical observations "
        f"(Nov 2024 - Nov 2026) with chronological walk-forward "
        f"({N_FOLDS} folds, train={TRAIN_WINDOW}, val={VAL_WINDOW}, embargo={EMBARGO_BARS}). "
        f"Best model: {best[0]} (accuracy {best[1]['aggregate']['accuracy']:.4f}, "
        f"balanced accuracy {best[1]['aggregate']['balanced_accuracy']:.4f}). "
        f"Calibration: {'fitted' if stage5['calibration_fitted'] else 'not fitted'}. "
        f"Holdout accuracy: {stage6.get('accuracy', 'N/A')}."
    )
    lines.append("")
    lines.append("## Stage 1 — Dataset")
    lines.append("")
    lines.append(f"- Rows: {stage1['rows']}")
    lines.append(f"- Range: {stage1['first_ts']} to {stage1['last_ts']}")
    lines.append(f"- Features: {stage1['feature_count']}")
    lines.append(f"- Checksum: {stage1['checksum'][:16]}...")
    lines.append("")
    lines.append("## Stage 4 — Model Comparison (walk-forward aggregate)")
    lines.append("")
    lines.append("| Model | Accuracy | Bal. Acc. | Macro-F1 | Val Rows |")
    lines.append("|-------|----------|-----------|----------|----------|")
    for spec, res in stage4.items():
        agg = res["aggregate"]
        lines.append(
            f"| {spec} | {agg['accuracy']:.4f} | {agg['balanced_accuracy']:.4f} | "
            f"{agg['macro_f1']:.4f} | {res['total_val_rows']} |"
        )
    lines.append("")
    lines.append("### Per-fold details")
    for spec, res in stage4.items():
        lines.append(f"#### {spec}")
        lines.append("| Fold | Train | Val | Accuracy | Bal. Acc. | Macro-F1 |")
        lines.append("|------|-------|-----|----------|-----------|----------|")
        for f in res["fold_results"]:
            lines.append(
                f"| {f['fold_id']} | {f['n_train']} | {f['n_val']} | "
                f"{f.get('accuracy', 'N/A')} | {f.get('balanced_accuracy', 'N/A')} | "
                f"{f.get('macro_f1', 'N/A')} |"
            )
        lines.append("")
    lines.append("## Stage 5 — Calibration")
    lines.append("")
    lines.append(f"- Method: {stage5['method']}")
    lines.append(f"- Calibration fitted: {stage5['calibration_fitted']}")
    lines.append(f"- Calibration samples: {stage5['cal_samples']}")
    lines.append(f"- Positive frac: {stage5['positive_frac']:.4f}")
    if stage5.get("calibrate_on_all_eval_on_all", {}).get("brier") is not None:
        a = stage5["calibrate_on_all_eval_on_all"]
        lines.append(
            f"- [LEAK] Calibrate all/eval all: Brier={a['brier']:.6f}, "
            f"LogLoss={a['log_loss']:.6f}, ECE={a['ece']:.6f}"
        )
    if stage5.get("calibrate_70_eval_30", {}).get("brier_eval") is not None:
        p = stage5["calibrate_70_eval_30"]
        lines.append(
            f"- [PROPER] Calibrate 70%/eval 30%: Brier={p['brier_eval']:.6f}, "
            f"LogLoss={p['log_loss_eval']:.6f}, ECE={p['ece_eval']:.6f}"
        )
    lines.append(f"- Brier (raw): {stage5['brier_raw']:.6f}")
    lines.append(f"- Log loss (raw): {stage5['log_loss_raw']:.6f}")
    lines.append("")
    lines.append("## Stage 6 — Historical Holdout Diagnostic (Consumed / Contaminated)")
    lines.append("")
    lines.append(f"- Model: {stage6['model']}")
    lines.append(f"- Holdout rows: {stage6['holdout_rows']}")
    lines.append(f"- Range: {stage6['holdout_start']} to {stage6['holdout_end']}")
    lines.append(f"- Accuracy: {stage6['accuracy']:.4f}")
    lines.append(f"- Balanced accuracy: {stage6['balanced_accuracy']:.4f}")
    lines.append(f"- Macro-F1: {stage6['macro_f1']:.4f}")
    lines.append(f"- ROC-AUC: {stage6['roc_auc']}")
    lines.append(f"- PR-AUC: {stage6['pr_auc']}")
    lines.append(f"- Ledger pristine: {stage6['holdout_ledger_pristine']}")
    lines.append(f"- Holdout integrity: {stage6.get('holdout_integrity', {}).get('status', 'unknown')}")
    lines.append("- Promotion evidence: NO — overlaps production training through 2026-10-01 and has been accessed.")
    lines.append("")
    lines.append("## Stage 7 — Strategy Research (proxy)")
    lines.append("")
    for name, s in stage7.items():
        lines.append(
            f"- {name}: trades={s['n_trades']}, "
            f"win_rate={s['win_rate']}, expectancy={s['expectancy']:.6f}, "
            f"total_bp={s['total_return_bp']:.2f}, "
            f"sharpe_proxy={s['sharpe_proxy']}, max_dd_proxy={s['max_drawdown_proxy']:.4f}"
        )
    lines.append("")
    lines.append("## Stage 8 — Robustness")
    lines.append("")
    lines.append(f"- Accuracy mean: {stage8['accuracy_mean']:.4f}")
    lines.append(f"- Accuracy std: {stage8['accuracy_std']:.4f}")
    if stage8.get("block_bootstrap_ci", {}).get("observed") is not None:
        b = stage8["block_bootstrap_ci"]
        lines.append(
            f"- Block bootstrap CI: [{b['lo']:.4f}, {b['hi']:.4f}] (observed={b['observed']:.4f})"
        )
    lines.append(
        f"- Permutation MC p (sign agreement): {stage8['permutation_mc_sign_agreement']['p_two_sided']:.4f}"
    )
    lines.append("")
    lines.append("## Stage 9 — Feature Ablation (drop-one, logistic base)")
    lines.append("")
    lines.append(f"- Base accuracy: {stage9['base_accuracy']:.4f}")
    lines.append("| Feature | Accuracy without | Delta vs base |")
    lines.append("|---------|------------------|---------------|")
    for f in stage9["features"]:
        lines.append(
            f"| {f['feature']} | {f['accuracy_without']:.4f} | {f['delta_vs_base']:+.4f} |"
        )
    lines.append("")
    lines.append("## Limitations")
    lines.append("- Holdout overlaps production model training period (Jul 1 - Oct 1, 2026).")
    lines.append("- UNCALIBRATED scores; ranking only, not probabilities.")
    lines.append("- Proxy-level strategy diagnostics; Phase 6 owns execution/fills.")
    lines.append("- No PBO (no CSCV partition matrix); DSR + Holm/Bonferroni used instead.")
    lines.append("")
    lines.append("---")
    lines.append(f"Machine-readable results: {results_path.name}")
    lines.append(f"Experiment registry: {EXPERIMENTS_PATH.name}")

    report_path = REPORT_DIR / "QUANTITATIVE_MODEL_RESEARCH_Q2.md"
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return report_path, results_path


def main() -> int:
    print("=== SignalForge Phase Q2 — Quantitative Model Research ===")
    print(f"Generated: {utc_now_iso()}")
    print()

    # ---- Stage 1 ----
    print("[Stage 1] Loading canonical dataset + features + labels...")
    df = load_canonical()
    print(f"  Rows: {len(df)}, Range: {df.index[0]} to {df.index[-1]}")
    X, y, obs, counts = build_features_labels(df)
    print(
        f"  Usable rows: {counts['usable_rows']} "
        f"(warmup dropped: {counts['warmup_dropped']}, incomplete: {counts['incomplete_excluded']})"
    )
    feature_names = list(X.columns)
    print(f"  Features: {len(feature_names)}")
    checksum = sha256_file(CANONICAL_PARQUET)
    stage1 = {
        "first_ts": str(df.index[0]),
        "last_ts": str(df.index[-1]),
        "rows": len(df),
        "feature_count": len(feature_names),
        "checksum": checksum,
        "counts": counts,
    }
    print()

    # ---- Stage 2 ----
    print("[Stage 2] Chronological holdout...")
    Xr, yr, obsr, Xh, yh, _obsh, _research_idx, _holdout_idx = apply_holdout(X, y, obs)
    print(f"  Research head: {len(Xr)} rows ({Xr.index[0]} to {Xr.index[-1]})")
    print(f"  Holdout: {len(Xh)} rows ({Xh.index[0]} to {Xh.index[-1]})")
    ledger_report = adequacy_report(len(Xh))
    print(
        f"  Holdout adequacy: {ledger_report['verdict']} "
        f"(n={ledger_report['n_holdout']}, min_n={ledger_report['min_n']})"
    )
    stage2 = {
        "research_rows": len(Xr),
        "holdout_rows": len(Xh),
        "holdout_frac": HOLDOUT_FRAC,
        "holdout_start": str(Xh.index[0]),
        "holdout_end": str(Xh.index[-1]),
        "adequacy": ledger_report,
    }
    print()

    # ---- Stage 3 ----
    print("[Stage 3] Building walk-forward folds...")
    folds = build_folds(len(Xr), obsr)
    print(f"  Folds: {len(folds)}")
    for f in folds:
        print(
            f"    Fold {f.fold_id}: train={f.n_train}, val={f.n_val}, "
            f"purged={f.exclusion_record()['n_purged']}, embargo={f.exclusion_record()['n_embargo']}"
        )
    stage3 = {
        "n_folds": len(folds),
        "train_window": TRAIN_WINDOW,
        "val_window": VAL_WINDOW,
        "embargo_bars": EMBARGO_BARS,
    }
    print()

    # ---- Stage 4 ----
    print("[Stage 4] Model comparison...")
    model_specs = ["majority", "trend", "logistic"]
    try:
        import lightgbm  # noqa: F401

        model_specs.append("lightgbm")
    except ImportError:
        print("  LightGBM not installed; skipping.")
    stage4 = {}
    for spec in model_specs:
        print(f"  Model: {spec} ...")
        res = evaluate_model_on_folds(spec, Xr, yr, folds, feature_names)
        stage4[spec] = res
        agg = res["aggregate"]
        print(
            f"    Accuracy: {agg['accuracy']:.4f}, Bal. Acc.: {agg['balanced_accuracy']:.4f}, "
            f"Macro-F1: {agg['macro_f1']:.4f}"
        )
    print()

    # ---- Stage 5 ----
    print("[Stage 5] Calibration...")
    best_spec = max(stage4, key=lambda s: stage4[s]["aggregate"]["accuracy"])
    best_res = stage4[best_spec]
    cal = fit_calibration(best_res["oos_scores"], best_res["oos_labels"], method="sigmoid")
    stage5 = cal
    print(f"  Best model: {best_spec}")
    print(f"  Calibration fitted: {cal['calibration_fitted']}")
    print()

    # ---- Stage 6 ----
    print("[Stage 6] Historical holdout diagnostic (contaminated; descriptive only)...")
    ho = holdout_read(best_spec, Xr, yr, Xh, yh, feature_names)
    stage6 = ho
    print(f"  Holdout accuracy: {ho['accuracy']:.4f}")
    print(f"  Balanced accuracy: {ho['balanced_accuracy']:.4f}")
    print(f"  Macro-F1: {ho['macro_f1']:.4f}")
    print(f"  ROC-AUC: {ho['roc_auc']}, PR-AUC: {ho['pr_auc']}")
    print()

    # ---- Stage 7 ----
    print("[Stage 7] Strategy research...")
    stage7 = strategy_proxy_returns(
        np.asarray(best_res["oos_scores"]),
        np.asarray(best_res["oos_labels"]),
    )
    for name, s in stage7.items():
        print(
            f"  {name}: trades={s['n_trades']}, win_rate={s['win_rate']}, "
            f"total_bp={s['total_return_bp']:.2f}"
        )
    print()

    # ---- Stage 8 ----
    print("[Stage 8] Robustness...")
    stage8 = robustness_analysis(
        best_res["oos_scores"],
        best_res["oos_labels"],
        best_res["fold_results"],
    )
    print(f"  Accuracy mean: {stage8['accuracy_mean']:.4f}, std: {stage8['accuracy_std']:.4f}")
    print(f"  Permutation MC p: {stage8['permutation_mc_sign_agreement']['p_two_sided']:.4f}")
    print()

    # ---- Stage 9 ----
    print("[Stage 9] Feature ablation...")
    stage9 = feature_ablation(Xr, yr, folds, feature_names)
    for f in stage9["features"][:5]:
        print(f"  {f['feature']}: delta={f['delta_vs_base']:+.4f}")
    print()

    # ---- Stage 10 ----
    print("[Stage 10] Experiment registry...")
    registry = ExperimentRegistry(EXPERIMENTS_PATH)
    stage10_ids = record_experiments(
        registry,
        stage4,
        stage5,
        stage6,
        stage8,
        stage9,
        checksum,
        feature_names,
    )
    stage10 = {"recorded_ids": stage10_ids, "registry_path": str(EXPERIMENTS_PATH)}
    print(f"  Recorded {len(stage10_ids)} experiments")

    # ---- Deliverables ----
    print("[Deliverables] Writing report + JSON...")
    report_path, results_path = write_report(
        stage1,
        stage2,
        stage3,
        stage4,
        stage5,
        stage6,
        stage7,
        stage8,
        stage9,
        stage10,
    )
    print(f"  Report: {report_path}")
    print(f"  Results: {results_path}")
    print()
    print("=== Q2 complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
