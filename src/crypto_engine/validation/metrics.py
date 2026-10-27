"""Research-level validation metrics (NOT a backtester).

Contents
--------
* Classification agreement: accuracy (overall + nonzero-only directional
  read), per-class precision/recall, confusion matrix, class counts
  (delegates to Track A ``reliability.classification_report_small``).
* Score quality: Brier, clipped log-loss, reliability table, ECE, coverage
  at thresholds — always tagged with ``score_kind`` (``"calibrated"`` vs
  ``"raw"``) so readers know what the numbers describe.
* Cost-aware trade proxy: maps realized triple-barrier labels to per-trade
  proxy returns. This is a documented approximation, NOT a fill-level
  backtest: it assumes one position attempt per bar, fixed fractional
  outcomes, and round-trip costs on every row including timeouts. Phase 6
  owns execution, fills, funding, and portfolio accounting.
* Proxy Sharpe / max-drawdown on cumulative proxy returns: reported with an
  explicit proxy-level warning, never as portfolio statistics.
* :func:`baseline_comparison`: same-fold accuracy table for
  trend/majority/logistic baselines.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from crypto_engine.calibration.reliability import (
    brier_score,
    classification_report_small,
    coverage_at_thresholds,
    expected_calibration_error,
    log_loss_clipped,
    reliability_table,
)
from crypto_engine.models.base import InformativeError
from crypto_engine.validation.folds import Fold

#: Allowed ``score_kind`` tags for score-quality outputs.
SCORE_KINDS = ("calibrated", "raw")

#: Always-attached proxy warning (Sharpe/drawdown/cost numbers).
PROXY_WARNING = (
    "Proxy-level diagnostics only: fixed fractional outcomes, no fills, no "
    "funding, no portfolio effects. Not a backtest; Phase 6 owns execution."
)


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, Any]:
    """Label agreement + directional read on nonzero outcomes.

    Returns the Track A report (accuracy, per-class precision/recall,
    confusion matrix, class counts, ``n``) plus ``accuracy_nonzero``
    (accuracy restricted to rows with ``y_true != 0``; ``None`` when no
    nonzero row exists) and ``n_nonzero``.
    """
    yt = np.asarray(y_true).ravel()
    yp = np.asarray(y_pred).ravel()
    report = classification_report_small(yt, yp)
    mask = yt != 0
    n_nonzero = int(mask.sum())
    accuracy_nonzero = float((yt[mask] == yp[mask]).mean()) if n_nonzero else None
    report["accuracy_nonzero"] = accuracy_nonzero
    report["n_nonzero"] = n_nonzero
    return report


def score_metrics(
    y_true_binary: np.ndarray,
    p_pred: np.ndarray,
    score_kind: str,
    n_bins: int = 10,
    thresholds: Sequence[float] = (0.5, 0.6, 0.7, 0.8, 0.9),
) -> dict[str, Any]:
    """Brier/log-loss/ECE/reliability/coverage tagged with ``score_kind``.

    Args:
        y_true_binary: Binary 0/1 outcomes for the modeled event.
        p_pred: Predicted probabilities (or raw scores in ``[0, 1]``).
        score_kind: ``"calibrated"`` or ``"raw"`` — recorded verbatim in
            the output so Brier/ECE numbers are never mistaken for the
            other kind.
    """
    if score_kind not in SCORE_KINDS:
        raise InformativeError(
            f"metrics: score_kind must be one of {SCORE_KINDS} (got {score_kind!r})."
        )
    table, warnings = reliability_table(y_true_binary, p_pred, n_bins=n_bins)
    coverage = coverage_at_thresholds(p_pred, list(thresholds))
    return {
        "score_kind": score_kind,
        "n": int(np.asarray(p_pred).ravel().shape[0]),
        "n_bins": int(n_bins),
        "brier": brier_score(y_true_binary, p_pred),
        "log_loss": log_loss_clipped(y_true_binary, p_pred),
        "ece": expected_calibration_error(table),
        "reliability_table": table.to_dict(orient="records"),
        "reliability_warnings": list(warnings),
        "coverage": coverage.to_dict(orient="records"),
    }


def trade_proxy_returns(
    labels: np.ndarray,
    *,
    pt_ret: float | None = None,
    sl_ret: float | None = None,
    vol_at_entry: np.ndarray | None = None,
    pt_mult: float | None = None,
    sl_mult: float | None = None,
    fee_bp: float = 10.0,
    slippage_bp: float = 5.0,
) -> np.ndarray:
    """Per-trade proxy returns from realized triple-barrier labels.

    Two mutually exclusive outcome modes:

    * fixed: ``pt_ret``/``sl_ret`` fractional moves (e.g. ``0.02``/``0.01``);
    * vol-scaled: per-row ``pt_i = pt_mult * vol_i``,
      ``sl_i = sl_mult * vol_i`` from ``vol_at_entry``.

    Mapping: label ``+1`` → ``+pt``; ``-1`` → ``-sl``; ``0``
    (timeout/ambiguous) → ``0``. Every row then pays round-trip costs
    ``(fee_bp + slippage_bp) / 1e4`` — timeouts still pay because a
    position was attempted. Labels outside ``{-1, 0, +1}`` (or NaN) raise.

    Limits: one attempt per bar, no sizing, no partial fills, no spread
    dynamics, no funding. Stress variants (2x fee/slippage) only scale the
    cost term; see :mod:`robustness` for the sensitivity grid.
    """
    fee = float(fee_bp)
    slip = float(slippage_bp)
    if fee < 0 or slip < 0:
        raise InformativeError(
            f"metrics: fee_bp and slippage_bp must be >= 0 (got {fee_bp!r}, {slippage_bp!r})."
        )
    cost = (fee + slip) / 1e4
    lab = np.asarray(labels).ravel()
    if lab.size == 0:
        raise InformativeError("metrics: labels is empty.")
    try:
        labf = lab.astype(float)
    except (TypeError, ValueError) as exc:
        raise InformativeError("metrics: labels must be numeric -1/0/+1.") from exc
    if bool(np.isnan(labf).any()) or not bool(np.isin(labf, [-1.0, 0.0, 1.0]).all()):
        raise InformativeError("metrics: labels must contain only -1/0/+1 (no NaN).")

    if vol_at_entry is not None:
        if pt_ret is not None or sl_ret is not None:
            raise InformativeError(
                "metrics: pass either fixed (pt_ret/sl_ret) or vol-scaled "
                "(vol_at_entry + pt_mult/sl_mult) outcomes, not both."
            )
        if pt_mult is None or sl_mult is None:
            raise InformativeError("metrics: vol mode needs pt_mult and sl_mult.")
        vol = np.asarray(vol_at_entry, dtype=float).ravel()
        if vol.shape[0] != labf.shape[0]:
            raise InformativeError("metrics: vol_at_entry length differs from labels.")
        if bool(np.isnan(vol).any()) or bool((vol < 0).any()):
            raise InformativeError("metrics: vol_at_entry must be finite and >= 0.")
        pt = float(pt_mult) * vol
        sl = float(sl_mult) * vol
    else:
        if pt_ret is None or sl_ret is None:
            raise InformativeError("metrics: fixed mode needs pt_ret and sl_ret.")
        pt = np.full(labf.shape, float(pt_ret))
        sl = np.full(labf.shape, float(sl_ret))
        if not np.isfinite(pt).all() or not np.isfinite(sl).all():
            raise InformativeError("metrics: pt_ret/sl_ret must be finite.")

    outcome = np.where(labf > 0, pt, np.where(labf < 0, -sl, 0.0))
    return (outcome - cost).astype(float)


def proxy_summary(returns: np.ndarray, *, periods_per_year: float | None = None) -> dict[str, Any]:
    """Expectancy/win-rate/proxy-Sharpe/drawdown for proxy returns.

    * ``expectancy``: mean per-trade proxy return; ``total``: sum.
    * ``win_rate_nonzero``: fraction of nonzero outcomes ``> 0``
      (``None`` + warning when no nonzero outcome exists).
    * ``sharpe_proxy``: ``mean/std`` (``* sqrt(periods_per_year)`` when
      given); ``None`` + warning when ``std == 0`` or ``n < 2``.
    * ``max_drawdown_proxy``: worst peak-to-trough drop of the cumulative
      proxy curve, in return units.

    Every output carries ``warnings`` including :data:`PROXY_WARNING`:
    these are proxy-level, never portfolio-level, statistics.
    """
    r = np.asarray(returns, dtype=float).ravel()
    if r.size == 0:
        raise InformativeError("metrics: returns is empty.")
    if bool(np.isnan(r).any()) or not bool(np.isfinite(r).all()):
        raise InformativeError("metrics: returns must be finite (no NaN/inf).")
    if periods_per_year is not None and float(periods_per_year) <= 0:
        raise InformativeError(f"metrics: periods_per_year must be > 0 (got {periods_per_year!r}).")
    warnings = [PROXY_WARNING]
    n = int(r.shape[0])
    mean = float(r.mean())
    total = float(r.sum())
    std = float(r.std(ddof=1)) if n > 1 else 0.0
    if n < 2 or std <= 0:
        sharpe: float | None = None
        warnings.append("sharpe_proxy undefined: need n >= 2 with nonzero dispersion.")
    else:
        scale = float(np.sqrt(float(periods_per_year))) if periods_per_year else 1.0
        sharpe = float(mean / std * scale)
    nonzero = r[r != 0.0]
    if nonzero.size:
        win_rate = float((nonzero > 0).mean())
    else:
        win_rate = None
        warnings.append("win_rate_nonzero undefined: no nonzero proxy outcome.")
    curve = np.concatenate([[0.0], np.cumsum(r)])
    drawdown = float(np.max(np.maximum.accumulate(curve) - curve))
    return {
        "n": n,
        "n_nonzero": int(nonzero.shape[0]),
        "expectancy": mean,
        "total": total,
        "std": std,
        "win_rate_nonzero": win_rate,
        "sharpe_proxy": sharpe,
        "periods_per_year": periods_per_year,
        "max_drawdown_proxy": drawdown,
        "warnings": warnings,
    }


def aggregate_folds(
    per_fold: Sequence[Mapping[str, Any]], keys: Sequence[str] | None = None
) -> dict[str, dict[str, float]]:
    """Mean/std/min/max/n per numeric key across folds.

    Non-numeric values are skipped; a key present in no fold as a number
    is omitted. ``std`` uses ``ddof=1`` (``0.0`` for a single fold).
    """
    rows = list(per_fold)
    if not rows:
        raise InformativeError("metrics: per_fold is empty; nothing to aggregate.")
    if keys is None:
        seen: list[str] = []
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.append(key)
        keys = seen
    out: dict[str, dict[str, float]] = {}
    for key in keys:
        vals = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
        vals = [v for v in vals if np.isfinite(v)]
        if not vals:
            continue
        arr = np.asarray(vals, dtype=float)
        out[str(key)] = {
            "mean": float(arr.mean()),
            "std": float(arr.std(ddof=1)) if arr.shape[0] > 1 else 0.0,
            "min": float(arr.min()),
            "max": float(arr.max()),
            "n": float(arr.shape[0]),
        }
    return out


def summarize_folds(per_fold: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Bundle per-fold dicts with their :func:`aggregate_folds` summary."""
    rows = [dict(row) for row in per_fold]
    return {
        "per_fold": rows,
        "aggregate": aggregate_folds(rows),
        "n_folds": len(rows),
    }


def baseline_comparison(
    X: pd.DataFrame,
    y: np.ndarray,
    folds: Sequence[Fold],
    *,
    seed: int = 42,
    ratio_col: str = "trend_ratio_20_50",
) -> pd.DataFrame:
    """Same-fold accuracy table for trend / majority / logistic baselines.

    Each model fits on ``fold.train_idx`` only and predicts
    ``fold.val_idx``. Logistic failures (e.g. single-class training folds)
    are recorded with ``status`` ``"failed: ..."`` and ``NaN`` accuracy —
    never silently dropped. ``n_jobs=1`` everywhere (4 GB laptop bound).

    Note: accuracy only. Mapping predictions to trade outcomes needs a
    strategy-specific position rule, which belongs to Phase 6 execution.
    """
    from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline
    from crypto_engine.models.sklearn_wrappers import SklearnLogistic

    if not isinstance(X, pd.DataFrame):
        raise InformativeError("metrics: X must be a DataFrame (schema-checked models).")
    labels = np.asarray(y).ravel()
    if labels.shape[0] != len(X):
        raise InformativeError("metrics: X rows and y length differ.")
    constructors = [
        ("trend", lambda: TrendBaseline(ratio_col=ratio_col)),
        ("majority", lambda: MajorityBaseline()),
        ("logistic", lambda: SklearnLogistic(random_state=int(seed))),
    ]
    records: list[dict[str, Any]] = []
    for fold in folds:
        Xtr, ytr = X.iloc[fold.train_idx], labels[fold.train_idx]
        Xva, yva = X.iloc[fold.val_idx], labels[fold.val_idx]
        for name, make in constructors:
            try:
                model = make().fit(Xtr, ytr)
                pred = model.predict(Xva)
                acc = float((np.asarray(pred).ravel() == yva).mean()) if yva.size else 0.0
                records.append(
                    {
                        "fold_id": int(fold.fold_id),
                        "model": name,
                        "n_train": int(fold.n_train),
                        "n_val": int(fold.n_val),
                        "accuracy": acc,
                        "status": "ok",
                    }
                )
            except Exception as exc:
                records.append(
                    {
                        "fold_id": int(fold.fold_id),
                        "model": name,
                        "n_train": int(fold.n_train),
                        "n_val": int(fold.n_val),
                        "accuracy": float("nan"),
                        "status": f"failed: {type(exc).__name__}: {exc}",
                    }
                )
    return pd.DataFrame(
        records, columns=["fold_id", "model", "n_train", "n_val", "accuracy", "status"]
    )


__all__ = [
    "aggregate_folds",
    "baseline_comparison",
    "classification_metrics",
    "proxy_summary",
    "score_metrics",
    "summarize_folds",
    "trade_proxy_returns",
]
