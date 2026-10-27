"""Experiment registry, multiple-testing discipline, and PBO applicability.

Registry
--------
:class:`ExperimentRegistry` appends one JSON object per line (JSONL) with
atomic read-modify-write (tmp file + ``os.replace``; small files, no lock
held). ``experiment_id`` is deterministic: ``sha256`` over canonical JSON
of ``(config, code_version)``. Failed/incomplete runs are recorded with
their ``status`` and ``limitations`` — they are stored, auditable, and
never counted as successes (see :meth:`summary_counts`).

Multiple testing
----------------
* :func:`deflated_sharpe` implements the Deflated Sharpe Ratio (Bailey &
  Lopez de Prado, 2014): ``DSR = Phi((SRhat - SR0) * sqrt(T-1) /
  sqrt(1 - skew*SRhat + (kurt-1)/4*SRhat^2))`` with
  ``SR0 = sqrt(V) * ((1-gamma) * PhiInv(1-1/K) + gamma * PhiInv(1-1/(K*e)))``,
  ``gamma`` the Euler-Mascheroni constant (~0.5772). Assumptions: per-period
  returns IID enough for sample skew/kurtosis to describe the Sharpe
  estimator variance; trial Sharpe estimates jointly Gaussian with
  variance ``V``; ``K`` is the number of configurations actually tried.
  ``V`` should come from the observed trial Sharpe distribution
  (``trial_srs``); without it the null sampling variance ``1/(T-1)`` is
  used as a documented approximation. Requires ``K >= 2`` trials and
  ``T >= 30`` observations -- otherwise ``(None, info)`` with a reason.
* :func:`holm_thresholds` / :func:`bonferroni_threshold` give
  family-wise-adjusted selection bars for ``K`` trials.

PBO
---
:func:`pbo_note` documents why no Probability-of-Backtest-Overfitting
number is produced: PBO needs the combinatorial symmetric cross-validation
(CSCV) partition matrix, which the walk-forward protocol does not emit.
Manufacturing a PBO value from walk-forward folds would be fabrication.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from crypto_engine.models.base import InformativeError

#: Registry entry schema version.
EXPERIMENT_SCHEMA = "experiment-v1"

#: Trial statuses; only ``"success"`` counts as a success.
STATUSES = ("success", "failed", "incomplete")

#: Provenance fields every entry must carry (``metrics`` may be ``None``
#: for failed runs, but the key must exist).
REQUIRED_FIELDS = (
    "experiment_id",
    "dataset_snapshot",
    "dataset_checksum",
    "model_version",
    "feature_schema",
    "label_def",
    "protocol",
    "periods",
    "calibration",
    "hyperparams",
    "thresholds",
    "n_trials",
    "metrics",
    "counts",
    "software_versions",
    "seeds",
    "holdout_status",
    "status",
    "limitations",
)

#: Minimum observations for a Deflated Sharpe read.
MIN_DSR_OBS = 30


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def experiment_id(config: dict[str, Any], code_version: str) -> str:
    """Deterministic id: ``sha256`` over canonical ``(config, code_version)`` JSON."""
    if not isinstance(config, dict):
        raise InformativeError("experiments: config must be a dict.")
    if not str(code_version).strip():
        raise InformativeError("experiments: code_version must be non-empty.")
    payload = json.dumps(
        {"config": config, "code_version": str(code_version)},
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_experiment(exp: dict[str, Any]) -> list[str]:
    """Return human-readable reasons an entry is not registry-ready (empty = ok)."""
    reasons: list[str] = []
    if not isinstance(exp, dict):
        return ["entry must be a dict."]
    for field_name in REQUIRED_FIELDS:
        if field_name not in exp:
            reasons.append(f"missing required field: {field_name!r}.")
    status = exp.get("status")
    if "status" in exp and status not in STATUSES:
        reasons.append(f"status must be one of {STATUSES} (got {status!r}).")
    n_trials = exp.get("n_trials")
    if "n_trials" in exp and (
        not isinstance(n_trials, int) or isinstance(n_trials, bool) or n_trials < 1
    ):
        reasons.append(f"n_trials must be an integer >= 1 (got {n_trials!r}).")
    holdout = exp.get("holdout_status")
    if "holdout_status" in exp and not isinstance(holdout, dict):
        reasons.append("holdout_status must be a dict with at least a 'used' flag.")
    return reasons


class ExperimentRegistry:
    """JSONL experiment/trial registry with deterministic ids.

    Failed/incomplete runs are first-class entries (recorded with
    ``status`` + ``limitations``), but :meth:`summary_counts` only credits
    ``status == "success"``.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError as exc:
                raise InformativeError(
                    f"experiments: corrupt registry line {lineno} in {self.path}: {exc}"
                ) from exc
            if not isinstance(entry, dict):
                raise InformativeError(f"experiments: registry line {lineno} is not a JSON object.")
            entries.append(entry)
        return entries

    def _write_all(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(e, sort_keys=True, default=str) + "\n" for e in entries)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise

    def record(self, exp: dict[str, Any]) -> str:
        """Validate and append ``exp``; returns its ``experiment_id``.

        Computes the deterministic id from ``exp["config"]`` +
        ``exp["code_version"]`` when ``experiment_id`` is absent.
        Duplicate ids raise ``FileExistsError`` (no overwrite — reruns
        must differ in config, code version, or seed).
        """
        entry = dict(exp)
        if not entry.get("experiment_id"):
            if "config" not in entry or "code_version" not in entry:
                raise InformativeError(
                    "experiments: need 'experiment_id' or ('config' + 'code_version')."
                )
            entry["experiment_id"] = experiment_id(entry["config"], entry["code_version"])
        reasons = validate_experiment(entry)
        if reasons:
            raise InformativeError("experiments: entry not registry-ready: " + "; ".join(reasons))
        entries = self._read_all()
        if any(e.get("experiment_id") == entry["experiment_id"] for e in entries):
            raise FileExistsError(
                f"experiments: id {entry['experiment_id']} already recorded (no overwrite)."
            )
        entry.setdefault("schema", EXPERIMENT_SCHEMA)
        entry.setdefault("recorded_at", _utc_now_iso())
        entries.append(entry)
        self._write_all(entries)
        return str(entry["experiment_id"])

    def get(self, experiment_id_: str) -> dict[str, Any]:
        """Return the entry with ``experiment_id_`` (``KeyError`` if absent)."""
        for entry in self._read_all():
            if entry.get("experiment_id") == experiment_id_:
                return entry
        raise KeyError(f"experiments: unknown experiment_id {experiment_id_!r}.")

    def list(self, status: str | None = None) -> list[dict[str, Any]]:
        """All entries, optionally filtered by trial ``status``."""
        entries = self._read_all()
        if status is None:
            return entries
        if status not in STATUSES:
            raise InformativeError(
                f"experiments: status filter must be one of {STATUSES} (got {status!r})."
            )
        return [e for e in entries if e.get("status") == status]

    def mark_holdout_used(self, experiment_id_: str, purpose: str) -> dict[str, Any]:
        """Flag an entry's holdout as consumed, with a purpose log.

        Sets ``holdout_status["used"] = True`` and appends
        ``{"purpose", "at"}`` to ``holdout_status["log"]``. Repeat
        accesses keep appending (re-access is itself auditable).
        """
        if not str(purpose).strip():
            raise InformativeError("experiments: purpose must be a non-empty string.")
        entries = self._read_all()
        for entry in entries:
            if entry.get("experiment_id") == experiment_id_:
                holdout = dict(entry.get("holdout_status") or {})
                holdout["used"] = True
                log = list(holdout.get("log") or [])
                log.append({"purpose": str(purpose), "at": _utc_now_iso()})
                holdout["log"] = log
                entry["holdout_status"] = holdout
                self._write_all(entries)
                return entry
        raise KeyError(f"experiments: unknown experiment_id {experiment_id_!r}.")

    def summary_counts(self) -> dict[str, int]:
        """Counts by status; only ``success`` counts as a success."""
        entries = self._read_all()
        counts = {name: 0 for name in STATUSES}
        for entry in entries:
            status = entry.get("status")
            if status in counts:
                counts[status] += 1
        counts["total"] = len(entries)
        return counts


def deflated_sharpe(
    returns_proxy: np.ndarray,
    n_trials: int,
    *,
    trial_srs: Sequence[float] | np.ndarray | None = None,
    periods_per_year: float = 1.0,
) -> tuple[float | None, dict[str, Any]]:
    """Deflated Sharpe Ratio for the proxy return series.

    Args:
        returns_proxy: 1D per-period proxy returns (see
            :func:`metrics.trade_proxy_returns`).
        n_trials: ``K`` — number of configurations actually tried (>= 2).
        trial_srs: Observed Sharpe ratios of the ``K`` trials (same
            annualization as implied by ``periods_per_year``). When
            omitted, ``V`` falls back to the null sampling variance
            ``1/(T-1)`` — a documented approximation, flagged in ``info``.
        periods_per_year: Annualization factor for the reported Sharpe
            (``1.0`` = per-period units).

    Returns:
        ``(dsr, info)`` where ``dsr`` is ``None`` when requirements fail
        (``info["reason"]`` explains) and otherwise lies in ``[0, 1]``
        (probability the true Sharpe exceeds the multiple-testing bar).
    """
    info: dict[str, Any] = {"n_trials": int(n_trials)}
    arr = np.asarray(returns_proxy, dtype=float).ravel()
    n_trials = int(n_trials)
    if arr.size < MIN_DSR_OBS:
        info["reason"] = (
            f"insufficient data: T={arr.size} < {MIN_DSR_OBS}; DSR needs "
            "enough observations for stable skew/kurtosis."
        )
        return None, info
    if n_trials < 2:
        info["reason"] = (
            f"insufficient trials: K={n_trials} < 2; with a single trial there is "
            "no selection — report the Sharpe directly, DSR is undefined."
        )
        return None, info
    if not bool(np.isfinite(arr).all()):
        info["reason"] = "returns_proxy must be finite (no NaN/inf)."
        return None, info
    periods = float(periods_per_year)
    if periods <= 0:
        info["reason"] = f"periods_per_year must be > 0 (got {periods_per_year!r})."
        return None, info

    from scipy.stats import kurtosis, norm, skew

    mean = float(arr.mean())
    std = float(arr.std(ddof=1))
    if not np.isfinite(std) or std <= 0:
        info["reason"] = "zero/non-finite dispersion: Sharpe (hence DSR) is undefined."
        return None, info
    sr = float(mean / std * np.sqrt(periods))
    skew_v = float(skew(arr))
    kurt_v = float(kurtosis(arr, fisher=False))  # Pearson (normal == 3)
    if not (np.isfinite(skew_v) and np.isfinite(kurt_v)):
        info["reason"] = "non-finite skew/kurtosis: DSR denominator undefined."
        return None, info

    if trial_srs is None:
        var_v = 1.0 / (arr.shape[0] - 1)
        v_source = (
            "null-sampling-variance approximation V=1/(T-1): assumes tried "
            "strategies are null with independent estimation error; pass "
            "trial_srs for the exact cross-trial variance."
        )
    else:
        trial_arr = np.asarray(list(trial_srs), dtype=float).ravel()
        if trial_arr.shape[0] < 2 or not bool(np.isfinite(trial_arr).all()):
            info["reason"] = "trial_srs needs >= 2 finite values to estimate V."
            return None, info
        var_v = float(trial_arr.var(ddof=1))
        if not np.isfinite(var_v) or var_v <= 0:
            info["reason"] = "cross-trial variance V <= 0: DSR undefined."
            return None, info
        v_source = "cross-trial sample variance of provided trial_srs."
    gamma = float(np.euler_gamma)
    k = float(n_trials)
    sr0 = float(
        np.sqrt(var_v)
        * ((1.0 - gamma) * norm.ppf(1.0 - 1.0 / k) + gamma * norm.ppf(1.0 - 1.0 / (k * np.e)))
    )
    denom = 1.0 - skew_v * sr + (kurt_v - 1.0) / 4.0 * sr * sr
    if not np.isfinite(denom) or denom <= 0:
        info["reason"] = "non-positive Sharpe-variance denominator (extreme skew/kurtosis)."
        return None, info
    z = float((sr - sr0) * np.sqrt(arr.shape[0] - 1) / np.sqrt(denom))
    dsr = float(norm.cdf(z))
    info.update(
        {
            "sharpe": sr,
            "sharpe_null_bar": sr0,
            "T": int(arr.shape[0]),
            "V": float(var_v),
            "V_source": v_source,
            "skew": skew_v,
            "kurtosis_pearson": kurt_v,
            "periods_per_year": periods,
            "assumptions": [
                "Per-period returns IID enough for sample skew/kurtosis to "
                "describe Sharpe estimator variance.",
                "Trial Sharpe estimates jointly Gaussian with variance V.",
                "K equals the number of configurations actually tried "
                "(unreported trials invalidate the bar).",
                "Proxy-level inputs: DSR inherits all trade-proxy limits.",
            ],
        }
    )
    return dsr, info


def bonferroni_threshold(alpha: float, n_trials: int) -> float:
    """Single adjusted bar ``alpha / K`` for selection among ``K`` trials."""
    alpha = float(alpha)
    n_trials = int(n_trials)
    if not 0.0 < alpha < 1.0:
        raise InformativeError(f"experiments: alpha must lie in (0, 1) (got {alpha}).")
    if n_trials < 1:
        raise InformativeError(f"experiments: n_trials must be >= 1 (got {n_trials}).")
    return alpha / n_trials


def holm_thresholds(alpha: float, n_trials: int) -> list[float]:
    """Holm step-down bars ``alpha/(K-i)`` for ordered p-values (rank 1..K)."""
    alpha = float(alpha)
    n_trials = int(n_trials)
    if not 0.0 < alpha < 1.0:
        raise InformativeError(f"experiments: alpha must lie in (0, 1) (got {alpha}).")
    if n_trials < 1:
        raise InformativeError(f"experiments: n_trials must be >= 1 (got {n_trials}).")
    return [alpha / (n_trials - i) for i in range(n_trials)]


def selection_warning(n_trials: int, alpha: float = 0.05) -> str:
    """Citable warning for reporting the best of ``n_trials`` configurations."""
    n_trials = int(n_trials)
    if n_trials < 1:
        raise InformativeError(f"experiments: n_trials must be >= 1 (got {n_trials}).")
    if n_trials == 1:
        return (
            "Single pre-registered trial: no selection bias from multiple testing "
            f"(alpha={alpha}). Untested variations must still be disclosed."
        )
    return (
        f"Best of {n_trials} trials selected: naive p-values/SR are optimistic. "
        f"Use Bonferroni bar {bonferroni_threshold(alpha, n_trials):.6f} "
        f"(alpha={alpha}) or the Holm sequence, and report the Deflated Sharpe "
        "with K equal to every configuration tried — including discarded ones."
    )


def pbo_note() -> str:
    """Why no Probability-of-Backtest-Overfitting number is produced.

    PBO (Bailey et al., 2014) is defined over the combinatorial symmetric
    cross-validation (CSCV) partition matrix: S submatrices, each split
    into J in-sample / K-J out-of-sample trial blocks, with logit shortly
    after. The Phase 3 walk-forward protocol emits sequential purged folds,
    not a CSCV partition matrix, so the PBO statistic has no valid input
    here. Computing "PBO" from walk-forward folds would silently redefine
    the estimator -- that number would be fabrication, so none is returned.
    Produce PBO only after implementing CSCV partition generation (out of
    scope for Phase 3); until then, rely on the Deflated Sharpe +
    Holm/Bonferroni discipline above.
    """
    return (
        "PBO applicability: NOT COMPUTED — no CSCV partition matrix exists in "
        "the Phase 3 walk-forward protocol. PBO (Bailey et al., 2014) requires "
        "S CSCV submatrices, each split into in-sample/out-of-sample trial "
        "blocks; walk-forward folds are sequential, not combinatorial, and "
        "cannot substitute. Manufacturing a PBO value from walk-forward folds "
        "would redefine the estimator — that number would be fabrication, "
        "so none is returned. Use "
        "deflated_sharpe + holm_thresholds/bonferroni_threshold for "
        "multiple-testing discipline; implement CSCV generation before any "
        "PBO claim (out of scope for Phase 3)."
    )


__all__ = [
    "ExperimentRegistry",
    "bonferroni_threshold",
    "deflated_sharpe",
    "experiment_id",
    "holm_thresholds",
    "pbo_note",
    "selection_warning",
    "validate_experiment",
]
