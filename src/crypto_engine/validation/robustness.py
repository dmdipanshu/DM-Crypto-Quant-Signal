"""Robustness diagnostics for the cost-aware trade proxy.

Contents
--------
* :func:`fee_slippage_grid`: expectancy/total/win-rate over a (fee,
  slippage) multiplier grid — costs only move one way, so worse cells must
  not beat the base cell (monotonicity is asserted in tests).
* :func:`block_bootstrap_ci`: circular block bootstrap confidence interval
  for the proxy mean/total. Blocks preserve local serial dependence, which
  an IID bootstrap would destroy; the CI is still indicative-only on small
  samples (warned, never hidden).
* :func:`permutation_mc`: Monte-Carlo reshuffle null for temporal
  structure — a structure-robustness check, NOT a profitability
  significance claim.
* :func:`leave_one_fold_out`: per-fold sensitivity table (aggregate metric
  recomputed excluding each fold in turn).

All resampling is seeded (deterministic reruns) and capped
(``n_boot <= max_bootstrap``; 4 GB laptop bound).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from crypto_engine.models.base import InformativeError
from crypto_engine.validation.metrics import proxy_summary, trade_proxy_returns


def fee_slippage_grid(
    labels: np.ndarray,
    *,
    pt_ret: float | None = None,
    sl_ret: float | None = None,
    vol_at_entry: np.ndarray | None = None,
    pt_mult: float | None = None,
    sl_mult: float | None = None,
    fee_bp: float = 10.0,
    slippage_bp: float = 5.0,
    multipliers: Sequence[float] = (1.0, 2.0),
) -> pd.DataFrame:
    """Cost-sensitivity grid over fee/slippage multipliers.

    Each ``(fee_mult, slip_mult)`` cell recomputes
    :func:`trade_proxy_returns` with scaled costs and summarizes with
    :func:`proxy_summary`. Higher costs cannot improve expectancy — the
    grid makes that monotonicity auditable.
    """
    mults = [float(m) for m in multipliers]
    if not mults or any(m < 0 for m in mults):
        raise InformativeError("robustness: multipliers must be non-empty and >= 0.")
    rows: list[dict[str, Any]] = []
    for fee_mult in mults:
        for slip_mult in mults:
            returns = trade_proxy_returns(
                labels,
                pt_ret=pt_ret,
                sl_ret=sl_ret,
                vol_at_entry=vol_at_entry,
                pt_mult=pt_mult,
                sl_mult=sl_mult,
                fee_bp=float(fee_bp) * fee_mult,
                slippage_bp=float(slippage_bp) * slip_mult,
            )
            summary = proxy_summary(returns)
            rows.append(
                {
                    "fee_mult": fee_mult,
                    "slip_mult": slip_mult,
                    "fee_bp": float(fee_bp) * fee_mult,
                    "slippage_bp": float(slippage_bp) * slip_mult,
                    "expectancy": summary["expectancy"],
                    "total": summary["total"],
                    "win_rate_nonzero": summary["win_rate_nonzero"],
                    "n": summary["n"],
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "fee_mult",
            "slip_mult",
            "fee_bp",
            "slippage_bp",
            "expectancy",
            "total",
            "win_rate_nonzero",
            "n",
        ],
    )


def block_bootstrap_ci(
    values: np.ndarray,
    *,
    n_boot: int = 200,
    block_len: int = 20,
    ci: float = 0.95,
    seed: int = 42,
    statistic: str = "mean",
    max_bootstrap: int = 200,
) -> dict[str, Any]:
    """Circular block-bootstrap percentile CI for the proxy mean/total.

    Rationale: proxy returns are serially dependent (overlapping label
    horizons, volatility clustering); resampling contiguous circular
    blocks preserves local dependence where an IID bootstrap would break
    it. Circular wrap keeps every row eligible as a block start.

    Args:
        values: 1D finite sample (proxy returns or accuracies).
        n_boot: Replicates; must satisfy ``1 <= n_boot <= max_bootstrap``.
        block_len: Block length (``>= n`` degenerates to resampling the
            whole series; allowed with a warning, not an error).
        ci: Confidence level in ``(0, 1)``.
        statistic: ``"mean"`` or ``"total"``.

    Returns:
        Dict with the observed statistic, percentile bounds, inputs, and
        a ``warning`` when ``n`` is small (indicative-only CI).
    """
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size == 0:
        raise InformativeError("robustness: values is empty.")
    if not bool(np.isfinite(arr).all()):
        raise InformativeError("robustness: values must be finite (no NaN/inf).")
    n_boot = int(n_boot)
    max_bootstrap = int(max_bootstrap)
    if not 1 <= n_boot <= max_bootstrap:
        raise InformativeError(
            f"robustness: n_boot must lie in [1, {max_bootstrap}] (got {n_boot})."
        )
    block_len = int(block_len)
    if block_len < 1:
        raise InformativeError(f"robustness: block_len must be >= 1 (got {block_len}).")
    ci = float(ci)
    if not 0.0 < ci < 1.0:
        raise InformativeError(f"robustness: ci must lie in (0, 1) (got {ci}).")
    if statistic not in ("mean", "total"):
        raise InformativeError(
            f"robustness: statistic must be 'mean' or 'total' (got {statistic!r})."
        )
    n = int(arr.shape[0])
    rng = np.random.default_rng(int(seed))
    n_blocks = max(1, int(np.ceil(n / block_len)))
    stats = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        starts = rng.integers(0, n, size=n_blocks)
        pieces = [(starts[:, None] + np.arange(block_len)) % n]
        sample = np.concatenate([arr[idx] for idx in pieces], axis=1).ravel()[:n]
        stats[b] = float(sample.mean()) if statistic == "mean" else float(sample.sum())
    observed = float(arr.mean()) if statistic == "mean" else float(arr.sum())
    alpha = 1.0 - ci
    lo = float(np.percentile(stats, 100.0 * alpha / 2.0))
    hi = float(np.percentile(stats, 100.0 * (1.0 - alpha / 2.0)))
    warning = ""
    if n < 100:
        warning = (
            f"robustness: n={n} < 100; block-bootstrap CI is indicative only "
            "(serial dependence + small sample)."
        )
    if block_len >= n:
        warning += (
            (" " if warning else "") + f"robustness: block_len={block_len} >= n={n}; replicates "
            "resample the whole series (degenerate blocks)."
        )
    return {
        "statistic": statistic,
        "observed": observed,
        "lo": lo,
        "hi": hi,
        "ci": ci,
        "n": n,
        "n_boot": n_boot,
        "block_len": block_len,
        "seed": int(seed),
        "warning": warning,
    }


def permutation_mc(values: np.ndarray, *, n_perm: int = 200, seed: int = 42) -> dict[str, Any]:
    """Monte-Carlo reshuffle null for temporal structure.

    Full permutations of ``values`` destroy order; the two-sided ``p``
    (fraction of ``|perm_mean| >= |observed|``) tests whether the observed
    mean needs temporal structure to arise. A small ``p`` says structure
    matters — it is NOT a significance claim for profitability.
    """
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size == 0:
        raise InformativeError("robustness: values is empty.")
    if not bool(np.isfinite(arr).all()):
        raise InformativeError("robustness: values must be finite (no NaN/inf).")
    n_perm = int(n_perm)
    if not 1 <= n_perm <= 1000:
        raise InformativeError(f"robustness: n_perm must lie in [1, 1000] (got {n_perm}).")
    rng = np.random.default_rng(int(seed))
    observed = float(arr.mean())
    perm_means = np.empty(n_perm, dtype=float)
    for k in range(n_perm):
        perm_means[k] = float(arr[rng.permutation(arr.shape[0])].mean())
    p_value = float((np.abs(perm_means) >= abs(observed)).mean())
    return {
        "observed": observed,
        "perm_mean": float(perm_means.mean()),
        "perm_std": float(perm_means.std(ddof=1)) if n_perm > 1 else 0.0,
        "p_two_sided": p_value,
        "n": int(arr.shape[0]),
        "n_perm": n_perm,
        "seed": int(seed),
        "note": (
            "Structure-robustness check only: small p means the mean depends on "
            "order, not that the strategy is profitable."
        ),
    }


def leave_one_fold_out(per_fold: Sequence[Mapping[str, Any]], key: str) -> pd.DataFrame:
    """Aggregate ``key`` recomputed excluding each fold in turn.

    Flags evaluation-period sensitivity: a conclusion that collapses when
    any single fold is dropped is not robust. Non-numeric entries are
    ignored; folds contributing no number are still listed with ``NaN``.
    """
    rows_in = list(per_fold)
    if not rows_in:
        raise InformativeError("robustness: per_fold is empty.")
    records: list[dict[str, Any]] = []
    for excluded in range(len(rows_in)):
        vals = [
            float(row[key])
            for j, row in enumerate(rows_in)
            if j != excluded and isinstance(row.get(key), (int, float))
        ]
        vals = [v for v in vals if np.isfinite(v)]
        records.append(
            {
                "excluded_fold": excluded,
                "mean_excl": float(np.mean(vals)) if vals else float("nan"),
                "n_used": len(vals),
            }
        )
    return pd.DataFrame(records, columns=["excluded_fold", "mean_excl", "n_used"])


__all__ = [
    "block_bootstrap_ci",
    "fee_slippage_grid",
    "leave_one_fold_out",
    "permutation_mc",
]
