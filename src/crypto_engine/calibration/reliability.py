"""Reliability diagnostics for calibrated probabilities (Phase 3 Track A).

Pure, lightweight helpers (pandas/numpy only). Scope: calibration quality
(reliability tables, Brier/log-loss, ECE, coverage, small classification
report). Research metrics such as Sharpe/profit/drawdown belong to the
Track B validation layer, NOT here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_engine.models.base import InformativeError

_RELIABILITY_COLUMNS = ["bin", "n", "mean_pred", "frac_pos", "bin_low", "bin_high"]
_COVERAGE_COLUMNS = ["threshold", "n_retained", "frac_retained"]


def _as_binary_labels(y_true: object, name: str) -> np.ndarray:
    arr = np.asarray(y_true).ravel()
    if arr.size == 0:
        raise InformativeError(f"reliability: {name} is empty.")
    try:
        vals = arr.astype(float)
    except (TypeError, ValueError) as exc:
        raise InformativeError(f"reliability: {name} must be numeric 0/1.") from exc
    if bool(np.isnan(vals).any()):
        raise InformativeError(f"reliability: {name} contains NaN; reject.")
    if not bool(np.isin(vals, [0.0, 1.0]).all()):
        raise InformativeError(f"reliability: {name} must be binary 0/1 for the event.")
    return vals.astype(int)


def _as_probs(p_pred: object, name: str = "p_pred") -> np.ndarray:
    arr = np.asarray(p_pred, dtype=float).ravel()
    if arr.size == 0:
        raise InformativeError(f"reliability: {name} is empty.")
    if bool(np.isnan(arr).any()):
        raise InformativeError(f"reliability: {name} contains NaN; reject.")
    if bool(((arr < 0.0) | (arr > 1.0)).any()):
        raise InformativeError(f"reliability: {name} must lie in [0, 1].")
    return arr


def reliability_table(
    y_true_binary: np.ndarray,
    p_pred: np.ndarray,
    n_bins: int = 10,
    strategy: str = "quantile",
) -> tuple[pd.DataFrame, list[str]]:
    """Bin predicted probabilities and compare against empirical frequencies.

    Binning strategies (documented for reproducibility):

    * ``"uniform"``: equal-width edges ``linspace(0, 1, n_bins + 1)``.
    * ``"quantile"``: edges at ``quantile(p_pred, linspace(0, 1, n_bins+1))``
      with duplicate edges dropped (constant scores collapse to one bin);
      first/last edges are pinned to ``0.0``/``1.0``.

    Returns ``(table, warnings)`` where ``table`` has columns ``bin, n,
    mean_pred, frac_pos, bin_low, bin_high`` and ``warnings`` holds one
    entry per bin with ``n < 10`` (small bins are noisy; reported, never
    silently dropped).
    """
    y = _as_binary_labels(y_true_binary, "y_true_binary")
    p = _as_probs(p_pred)
    if y.shape[0] != p.shape[0]:
        raise InformativeError(
            f"reliability: y_true (n={y.shape[0]}) and p_pred (n={p.shape[0]}) differ."
        )
    if int(n_bins) < 1:
        raise InformativeError(f"reliability: n_bins must be >= 1 (got {n_bins!r}).")
    n_bins = int(n_bins)
    if strategy == "uniform":
        edges = np.linspace(0.0, 1.0, n_bins + 1)
    elif strategy == "quantile":
        edges = np.quantile(p, np.linspace(0.0, 1.0, n_bins + 1))
        edges = np.unique(edges)
        if edges.size < 2:
            edges = np.asarray([0.0, 1.0])
        else:
            edges[0] = 0.0
            edges[-1] = 1.0
            edges = np.unique(edges)
    else:
        raise InformativeError(
            f"reliability: unknown strategy {strategy!r}; use 'quantile' or 'uniform'."
        )

    idx = np.searchsorted(edges, p, side="right") - 1
    idx = np.clip(idx, 0, edges.size - 2)
    rows: list[dict[str, float]] = []
    warnings: list[str] = []
    for b in range(edges.size - 1):
        mask = idx == b
        n = int(mask.sum())
        if n == 0:
            rows.append(
                {
                    "bin": b,
                    "n": 0,
                    "mean_pred": float("nan"),
                    "frac_pos": float("nan"),
                    "bin_low": float(edges[b]),
                    "bin_high": float(edges[b + 1]),
                }
            )
            continue
        mean_pred = float(p[mask].mean())
        frac_pos = float(y[mask].mean())
        rows.append(
            {
                "bin": b,
                "n": n,
                "mean_pred": mean_pred,
                "frac_pos": frac_pos,
                "bin_low": float(edges[b]),
                "bin_high": float(edges[b + 1]),
            }
        )
        if n < 10:
            warnings.append(
                f"reliability: bin {b} [{edges[b]:.4f}, {edges[b + 1]:.4f}] "
                f"has n={n} < 10; fraction estimate is noisy."
            )
    table = pd.DataFrame(rows, columns=_RELIABILITY_COLUMNS)
    return table, warnings


def brier_score(y_true_binary: np.ndarray, p_pred: np.ndarray) -> float:
    """Mean squared error between binary outcomes and predicted probabilities."""
    y = _as_binary_labels(y_true_binary, "y_true_binary")
    p = _as_probs(p_pred)
    if y.shape[0] != p.shape[0]:
        raise InformativeError("reliability: y_true and p_pred lengths differ.")
    return float(np.mean((p - y.astype(float)) ** 2))


def log_loss_clipped(y_true_binary: np.ndarray, p_pred: np.ndarray, eps: float = 1e-12) -> float:
    """Bernoulli log-loss after clipping probabilities into ``[eps, 1 - eps]``."""
    y = _as_binary_labels(y_true_binary, "y_true_binary")
    p = _as_probs(p_pred)
    if y.shape[0] != p.shape[0]:
        raise InformativeError("reliability: y_true and p_pred lengths differ.")
    if not 0.0 < float(eps) < 0.5:
        raise InformativeError(f"reliability: eps must be in (0, 0.5) (got {eps!r}).")
    pc = np.clip(p, float(eps), 1.0 - float(eps))
    yf = y.astype(float)
    return float(-np.mean(yf * np.log(pc) + (1.0 - yf) * np.log(1.0 - pc)))


def expected_calibration_error(table: pd.DataFrame, norm: str = "l1") -> float:
    """Weighted calibration error from a :func:`reliability_table`.

    ``l1``: ``sum_b (n_b / n) * |frac_pos_b - mean_pred_b|``. ``l2`` uses
    squared gaps. ECE depends on the binning (strategy and ``n_bins``) and
    on sample size; compare ECE values only under identical binning.
    """
    if norm not in ("l1", "l2"):
        raise InformativeError(f"reliability: norm must be 'l1' or 'l2' (got {norm!r}).")
    for col in ("n", "mean_pred", "frac_pos"):
        if col not in table.columns:
            raise InformativeError(f"reliability: table missing column {col!r}.")
    sub = table.dropna(subset=["mean_pred", "frac_pos"])
    total = float(sub["n"].sum())
    if total <= 0:
        raise InformativeError("reliability: table holds no samples; ECE undefined.")
    gaps = sub["frac_pos"].to_numpy(dtype=float) - sub["mean_pred"].to_numpy(dtype=float)
    weights = sub["n"].to_numpy(dtype=float) / total
    if norm == "l1":
        return float(np.sum(weights * np.abs(gaps)))
    return float(np.sum(weights * gaps * gaps))


def coverage_at_thresholds(
    p_pred: np.ndarray, thresholds: list[float] | tuple[float, ...] = (0.5, 0.6, 0.7, 0.8, 0.9)
) -> pd.DataFrame:
    """Retention counts/fractions at each probability threshold (``p >= t``)."""
    p = _as_probs(p_pred)
    ts = [float(t) for t in thresholds]
    if not ts:
        raise InformativeError("reliability: thresholds must be non-empty.")
    if any(not 0.0 <= t <= 1.0 for t in ts):
        raise InformativeError("reliability: thresholds must lie in [0, 1].")
    n = p.shape[0]
    rows = [
        {
            "threshold": t,
            "n_retained": int((p >= t).sum()),
            "frac_retained": float((p >= t).mean()),
        }
        for t in ts
    ]
    _ = n
    return pd.DataFrame(rows, columns=_COVERAGE_COLUMNS)


def classification_report_small(
    y_true_labels: np.ndarray, y_pred_labels: np.ndarray
) -> dict[str, object]:
    """Accuracy, per-class precision/recall, confusion matrix, class counts.

    Pure label-agreement summary; NO trading metrics (no Sharpe, profit, or
    drawdown — those belong to the Track B research layer).
    """
    yt = np.asarray(y_true_labels).ravel()
    yp = np.asarray(y_pred_labels).ravel()
    if yt.shape[0] != yp.shape[0]:
        raise InformativeError("reliability: y_true and y_pred lengths differ.")
    if yt.size == 0:
        raise InformativeError("reliability: empty label arrays.")
    labels = sorted(int(c) for c in np.unique(np.concatenate([yt, yp])))
    index = {c: i for i, c in enumerate(labels)}
    cm = np.zeros((len(labels), len(labels)), dtype=int)
    for a, b in zip(yt.tolist(), yp.tolist(), strict=True):
        cm[index[int(a)], index[int(b)]] += 1
    n = int(yt.size)
    accuracy = float(np.trace(cm) / n) if n else 0.0
    per_class: dict[str, dict[str, float]] = {}
    for c in labels:
        i = index[c]
        tp = int(cm[i, i])
        fp = int(cm[:, i].sum() - tp)
        fn = int(cm[i, :].sum() - tp)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        per_class[str(c)] = {
            "precision": float(precision),
            "recall": float(recall),
            "support": float(int(cm[i, :].sum())),
        }
    counts = {str(c): int((yt == c).sum()) for c in labels}
    return {
        "accuracy": accuracy,
        "n": n,
        "labels": labels,
        "per_class": per_class,
        "confusion_matrix": cm.tolist(),
        "confusion_labels": labels,
        "class_counts": counts,
    }


__all__ = [
    "brier_score",
    "classification_report_small",
    "coverage_at_thresholds",
    "expected_calibration_error",
    "log_loss_clipped",
    "reliability_table",
]
