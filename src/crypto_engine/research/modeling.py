"""Q2.2 — modeling harness for the corrected research pipeline.

Provides label-distribution diagnostics, per-class classification metrics,
MCC, multiclass ROC-AUC / PR-AUC (one-vs-rest where mathematically valid),
prediction-collapse detection, and fold-by-fold stability summaries. All
preprocessing/scaling/fitting happens inside training folds by construction
in the callers; this module only scores predictions.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    matthews_corrcoef,
    precision_recall_fscore_support,
    roc_auc_score,
)

LABEL_CLASSES: tuple[int, ...] = (-1, 0, 1)

#: A model "collapses" when a single class accounts for this share or more
#: of its predictions.
COLLAPSE_SHARE_THRESHOLD: float = 0.90


def class_distribution(labels: np.ndarray | pd.Series) -> dict[str, Any]:
    """Class counts, fractions, majority class and imbalance ratio."""
    y = np.asarray(labels).ravel()
    counts = np.bincount(y.astype(np.int64) + 1, minlength=3).astype(int)
    total = int(counts.sum())
    fractions = {
        str(int(c)): (float(counts[i] / total) if total else None)
        for i, c in enumerate(LABEL_CLASSES)
    }
    return {
        "total": total,
        "counts": {str(int(c)): int(counts[i]) for i, c in enumerate(LABEL_CLASSES)},
        "fractions": fractions,
        "majority_class": str(int(LABEL_CLASSES[int(counts.argmax())])) if total else None,
        "majority_fraction": (float(counts.max() / total) if total else None),
        "minority_fraction": (float(counts.min() / total) if total and counts.min() else None),
        "imbalance_ratio": (
            float(counts.max() / counts.min()) if total and counts.min() > 0 else None
        ),
        "class_count": int((counts > 0).sum()),
    }


def detect_prediction_collapse(
    predictions: np.ndarray | pd.Series,
    y_true: np.ndarray | pd.Series | None = None,
) -> dict[str, Any]:
    """Detect prediction collapse: a single class dominating predictions.

    Returns ``collapsed: bool`` plus per-class prediction shares and the
    maximum share. Collapse means the model offers almost no directional
    discrimination (e.g. logistic predicting ``-1`` on ~99.9% of rows).
    """
    preds = np.asarray(predictions).ravel()
    total = int(preds.size)
    counts = np.bincount(preds.astype(np.int64) + 1, minlength=3).astype(int)
    shares = {
        str(int(c)): (float(counts[i] / total) if total else None)
        for i, c in enumerate(LABEL_CLASSES)
    }
    max_share = float(counts.max() / total) if total else 0.0
    collapsed = max_share >= COLLAPSE_SHARE_THRESHOLD
    result: dict[str, Any] = {
        "total_predictions": total,
        "prediction_counts": {str(int(c)): int(counts[i]) for i, c in enumerate(LABEL_CLASSES)},
        "prediction_shares": shares,
        "max_class_share": max_share,
        "collapsed": collapsed,
        "collapse_threshold": COLLAPSE_SHARE_THRESHOLD,
        "collapsing_class": (str(int(LABEL_CLASSES[int(counts.argmax())])) if collapsed else None),
    }
    if y_true is not None:
        result["prediction_accuracy"] = float((preds == np.asarray(y_true).ravel()).mean())
    return result


def evaluate_classifier(
    y_true: np.ndarray | pd.Series,
    y_pred: np.ndarray | pd.Series,
    y_score: np.ndarray | pd.Series | None = None,
) -> dict[str, Any]:
    """Per-class and aggregate classification metrics.

    ``y_score`` is optional ranking output (uncalibrated scores or
    probabilities). Multiclass ROC-AUC / PR-AUC use one-vs-rest and are
    only reported when both classes are present in ``y_true``.
    """
    yt = np.asarray(y_true).ravel().astype(np.int64)
    yp = np.asarray(y_pred).ravel().astype(np.int64)
    if yt.shape != yp.shape:
        raise ValueError("y_true and y_pred must have the same shape")
    total = int(yt.size)
    labels = list(LABEL_CLASSES)

    prec, rec, f1, support = precision_recall_fscore_support(
        yt, yp, labels=labels, average=None, zero_division=0
    )
    per_class: dict[str, Any] = {}
    for i, c in enumerate(labels):
        per_class[str(int(c))] = {
            "precision": float(prec[i]),
            "recall": float(rec[i]),
            "f1": float(f1[i]),
            "support": int(support[i]),
        }

    cm = confusion_matrix(yt, yp, labels=labels)
    try:
        mcc = float(matthews_corrcoef(yt, yp))
    except ValueError:
        mcc = None
    bal_acc = float(balanced_accuracy_score(yt, yp))
    macro_f1 = float(f1_score(yt, yp, labels=labels, average="macro", zero_division=0))
    accuracy = float((yt == yp).mean())

    result: dict[str, Any] = {
        "total": total,
        "accuracy": accuracy,
        "balanced_accuracy": bal_acc,
        "macro_f1": macro_f1,
        "mcc": mcc,
        "per_class": per_class,
        "confusion_matrix": cm.tolist(),
        "confusion_labels": labels,
        "class_distribution": class_distribution(yt),
    }

    if y_score is not None:
        ys = np.asarray(y_score).ravel().astype(float)
        if ys.shape != yt.shape:
            raise ValueError("y_score must have the same shape as y_true")
        # Multiclass one-vs-rest ROC-AUC / PR-AUC; report None when a class
        # is absent from y_true (mathematically undefined).
        roc_auc: dict[str, Any] = {}
        pr_auc: dict[str, Any] = {}
        for c in labels:
            binary = (yt == c).astype(int)
            if binary.sum() == 0 or binary.sum() == total:
                roc_auc[str(int(c))] = None
                pr_auc[str(int(c))] = None
                continue
            try:
                roc_auc[str(int(c))] = float(roc_auc_score(binary, ys))
            except ValueError:
                roc_auc[str(int(c))] = None
            try:
                pr_auc[str(int(c))] = float(average_precision_score(binary, ys))
            except ValueError:
                pr_auc[str(int(c))] = None
        result["roc_auc_ovr"] = roc_auc
        result["pr_auc_ovr"] = pr_auc
        result["y_score_mean"] = float(ys.mean())
        result["y_score_std"] = float(ys.std())
    return result


def fold_stability(
    fold_metrics: list[dict[str, Any]], key: str = "balanced_accuracy"
) -> dict[str, Any]:
    """Summarise fold-by-fold stability for a metric key.

    Returns count, mean, sample std, min, max, median, range and coefficient
    of variation. Only successfully evaluated folds with finite values are
    counted; missing, failed or skipped folds are never counted as successful.
    Returns ``None``-valued fields when fewer than two valid folds exist.
    """
    values: list[float] = []
    for m in fold_metrics:
        if not isinstance(m, dict):
            continue
        try:
            v = float(m.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            values.append(v)

    n_valid = len(values)
    if n_valid < 2:
        return {
            "key": key,
            "n_folds": n_valid,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "median": None,
            "range": None,
            "coefficient_of_variation": None,
        }
    arr = np.asarray(values, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    median = float(np.median(arr))
    return {
        "key": key,
        "n_folds": n_valid,
        "mean": mean,
        "std": std,
        "min": float(arr.min()),
        "max": float(arr.max()),
        "median": median,
        "range": float(arr.max() - arr.min()),
        "coefficient_of_variation": (std / abs(mean)) if mean else None,
    }


def evaluate_fold_predictive_metrics(
    y_true: np.ndarray | pd.Series,
    y_pred: np.ndarray | pd.Series,
    y_score: np.ndarray | pd.Series | None = None,
) -> dict[str, Any]:
    """Compute per-fold predictive metrics from out-of-fold predictions.

    Returns accuracy, balanced_accuracy, macro_precision, macro_recall,
    macro_f1, MCC, per-class precision/recall/F1/support, multiclass
    ROC-AUC/PR-AUC (one-vs-rest, None when undefined), and
    prediction_collapse_status.
    """
    yt = np.asarray(y_true).ravel().astype(np.int64)
    yp = np.asarray(y_pred).ravel().astype(np.int64)
    if yt.shape != yp.shape:
        raise ValueError("y_true and y_pred must have the same shape")
    total = int(yt.size)
    labels = list(LABEL_CLASSES)

    prec, rec, f1, support = precision_recall_fscore_support(
        yt, yp, labels=labels, average=None, zero_division=0
    )
    per_class: dict[str, Any] = {}
    for i, c in enumerate(labels):
        per_class[str(int(c))] = {
            "precision": float(prec[i]),
            "recall": float(rec[i]),
            "f1": float(f1[i]),
            "support": int(support[i]),
        }

    macro_precision = float(np.mean(prec))
    macro_recall = float(np.mean(rec))
    macro_f1 = float(f1_score(yt, yp, labels=labels, average="macro", zero_division=0))
    accuracy = float((yt == yp).mean())

    try:
        mcc = float(matthews_corrcoef(yt, yp))
    except ValueError:
        mcc = None
    bal_acc = float(balanced_accuracy_score(yt, yp))

    collapse = detect_prediction_collapse(yp, yt)
    collapse_status = "collapsed" if collapse["collapsed"] else "ok"

    result: dict[str, Any] = {
        "total": total,
        "accuracy": accuracy,
        "balanced_accuracy": bal_acc,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "mcc": mcc,
        "per_class": per_class,
        "prediction_collapse_status": collapse_status,
        "prediction_collapse": collapse,
        "confusion_labels": labels,
        "class_distribution": class_distribution(yt),
    }

    if y_score is not None:
        ys = np.asarray(y_score).ravel().astype(float)
        if ys.shape != yt.shape:
            raise ValueError("y_score must have the same shape as y_true")
        roc_auc: dict[str, Any] = {}
        pr_auc: dict[str, Any] = {}
        for c in labels:
            binary = (yt == c).astype(int)
            if binary.sum() == 0 or binary.sum() == total:
                roc_auc[str(int(c))] = None
                pr_auc[str(int(c))] = None
                continue
            try:
                roc_auc[str(int(c))] = float(roc_auc_score(binary, ys))
            except ValueError:
                roc_auc[str(int(c))] = None
            try:
                pr_auc[str(int(c))] = float(average_precision_score(binary, ys))
            except ValueError:
                pr_auc[str(int(c))] = None
        result["roc_auc_ovr"] = roc_auc
        result["pr_auc_ovr"] = pr_auc

    return result


__all__ = [
    "COLLAPSE_SHARE_THRESHOLD",
    "LABEL_CLASSES",
    "class_distribution",
    "detect_prediction_collapse",
    "evaluate_classifier",
    "evaluate_fold_predictive_metrics",
    "fold_stability",
]
