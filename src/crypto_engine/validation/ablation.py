"""Context-ablation framework (Phase 4 Track B).

Four arms evaluated on the SAME purged walk-forward folds, datasets, and
horizons, each recorded in the Phase 3 :class:`ExperimentRegistry`:

* ``quant_only`` — calibrated quant probabilities alone (baseline).
* ``quant_plus_context`` — quant probabilities shifted by a deterministic
  function of the :class:`ContextScore` (score value + missing flag as extra
  columns), using ONLY evidence with ``observed_at <= fold-train end``.
* ``explanation_only`` — same predictions as ``quant_only`` (explanations
  never change predictions); recorded to keep the comparison honest.
* ``contextual_veto`` — abstention policy (NOT a trade rule): abstain when
  the judge-equivalent signal is insufficient (missing context) AND
  ``|score| < threshold``; reports retained-subset accuracy + coverage.

Context features are a deterministic function of the score — no model, no
LLM, no network. Future evidence (``observed_at`` after the fold-train
cutoff) is excluded and counted, never silently used.

Never touches the final holdout: folds only by default; when a
``holdout_ledger_path`` is supplied the ledger must be pristine or the run
is refused (holdout rows are never read in either case).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from crypto_engine.agents.schemas import EvidenceItem
from crypto_engine.models.base import InformativeError
from crypto_engine.validation.experiments import ExperimentRegistry

ARMS: tuple[str, ...] = (
    "quant_only",
    "quant_plus_context",
    "explanation_only",
    "contextual_veto",
)

CODE_VERSION = "ablation-v1"


def evidence_available_at(item: EvidenceItem, cutoff: datetime) -> bool:
    """``True`` when ``item.observed_at <= cutoff`` (temporally available)."""
    observed = item.observed_dt()
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=observed.tzinfo)
    return observed <= cutoff


def _parse_cutoff(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InformativeError(f"ablation: bad timestamp {value!r}.") from exc
    return dt


def _score_from_items(items: list[EvidenceItem]) -> tuple[float, bool]:
    """Deterministic context score: mean of numeric sentiment/score fields.

    Returns ``(value, missing)``; ``missing=True`` when no usable numeric
    field exists. Bounded to ``[-1, 1]``; no model involved.
    """
    values: list[float] = []
    for item in items:
        for key in ("sentiment", "score"):
            raw = item.payload.get(key)
            if raw is None:
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                values.append(max(-1.0, min(1.0, number)))
    if not values:
        return 0.0, True
    return float(sum(values) / len(values)), False


def context_features_for_row(
    items: list[EvidenceItem], cutoff: datetime
) -> tuple[list[float], int]:
    """Deterministic ``[score_value, missing_flag]`` + excluded-future count.

    Only items with ``observed_at <= cutoff`` contribute; later-dated items
    are excluded and counted (never silently dropped from the audit).
    """
    usable = [item for item in items if evidence_available_at(item, cutoff)]
    excluded = len(items) - len(usable)
    value, missing = _score_from_items(usable)
    return [value, 1.0 if missing else 0.0], excluded


def _accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true).ravel()
    y_pred = np.asarray(y_pred).ravel()
    if y_true.size == 0:
        raise InformativeError("ablation: empty prediction set.")
    return float((y_true == y_pred).mean())


def _brier(y_true_binary: np.ndarray, p_pred: np.ndarray) -> float:
    from crypto_engine.calibration.reliability import brier_score

    return float(brier_score(np.asarray(y_true_binary), np.asarray(p_pred)))


def _bootstrap_ci(values: np.ndarray, *, seed: int, n_boot: int = 200) -> dict[str, Any]:
    """Block-bootstrap CI, falling back to a local percentile CI on cycles."""
    try:
        from crypto_engine.validation.robustness import block_bootstrap_ci

        return dict(
            block_bootstrap_ci(
                np.asarray(values, dtype=float),
                n_boot=n_boot,
                block_len=5,
                seed=int(seed),
            )
        )
    except Exception:  # pragma: no cover - import-cycle safety net
        rng = np.random.default_rng(int(seed))
        arr = np.asarray(values, dtype=float).ravel()
        stats = np.array([float(rng.choice(arr, size=arr.size).mean()) for _ in range(n_boot)])
        return {
            "statistic": "mean",
            "observed": float(arr.mean()),
            "lo": float(np.percentile(stats, 2.5)),
            "hi": float(np.percentile(stats, 97.5)),
            "seed": int(seed),
            "warning": "local percentile CI fallback (robustness import unavailable).",
        }


@dataclass
class AblationStudy:
    """Four-arm context ablation reusing :class:`ExperimentRegistry` + folds."""

    registry: ExperimentRegistry
    seed: int = 42
    acc_margin: float = 0.02
    coverage_tol: float = 0.0
    veto_threshold: float = 0.2
    context_alpha: float = 0.1
    run_tag: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    results: dict[str, Any] = field(default_factory=dict)

    def run(
        self,
        y_true: np.ndarray,
        p_quant: np.ndarray,
        evidence_by_row: list[list[EvidenceItem]],
        row_ts: list[str],
        folds: list[Any],
        dataset_meta: dict[str, Any] | None = None,
        holdout_ledger_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Evaluate all four arms on ``folds``; record each arm in the registry.

        Raises:
            InformativeError: Non-binary labels, bad shapes, empty folds, or
                a non-pristine holdout ledger when ``holdout_ledger_path`` is
                given (holdout rows are never read).
        """
        if holdout_ledger_path is not None:
            from crypto_engine.validation.holdout import HoldoutLedger

            ledger = HoldoutLedger.load(holdout_ledger_path)
            if not ledger.is_pristine:
                raise InformativeError(
                    "ablation: holdout ledger already used; ablation is folds-only "
                    "and refuses holdout access."
                )
        y_true = np.asarray(y_true).ravel()
        p_quant = np.asarray(p_quant, dtype=float).ravel()
        if y_true.shape[0] != p_quant.shape[0]:
            raise InformativeError("ablation: y_true and p_quant lengths differ.")
        if y_true.size == 0:
            raise InformativeError("ablation: empty inputs.")
        if not bool(np.isin(y_true, [0, 1]).all()):
            raise InformativeError("ablation: y_true must be binary 0/1.")
        if bool(np.isnan(p_quant).any()) or bool(((p_quant < 0) | (p_quant > 1)).any()):
            raise InformativeError("ablation: p_quant must lie in [0, 1] (no NaN).")
        if not list(folds):
            raise InformativeError("ablation: folds must be non-empty.")
        n = int(y_true.shape[0])
        if len(evidence_by_row) != n or len(row_ts) != n:
            raise InformativeError("ablation: evidence/timestamps must match n rows.")
        cutoffs = [_parse_cutoff(t) for t in row_ts]
        meta = dict(dataset_meta or {})

        pooled: dict[str, dict[str, list]] = {
            arm: {"correct": [], "probs": [], "labels": [], "retained": []} for arm in ARMS
        }
        per_fold: list[dict[str, Any]] = []
        total_excluded_future = 0
        total_context_used = 0
        for fold in folds:
            train_idx = np.asarray(fold.train_idx).ravel()
            val_idx = np.asarray(fold.val_idx).ravel()
            if train_idx.size == 0 or val_idx.size == 0:
                raise InformativeError("ablation: fold has empty train/val block.")
            cutoff = max(cutoffs[int(i)] for i in train_idx.tolist())
            fold_scores: list[float] = []
            fold_missing: list[bool] = []
            for i in val_idx.tolist():
                feats, excluded = context_features_for_row(evidence_by_row[int(i)], cutoff)
                total_excluded_future += excluded
                fold_scores.append(feats[0])
                fold_missing.append(feats[1] > 0.5)
                if feats[1] < 0.5:
                    total_context_used += 1
            scores = np.asarray(fold_scores, dtype=float)
            missing = np.asarray(fold_missing, dtype=bool)
            yv = y_true[val_idx]
            pv = p_quant[val_idx]
            pred_q = (pv >= 0.5).astype(int)
            pv_adj = np.clip(pv + self.context_alpha * scores, 0.0, 1.0)
            pred_adj = (pv_adj >= 0.5).astype(int)
            veto_abstain = missing & (np.abs(scores) < float(self.veto_threshold))
            retained = ~veto_abstain
            fold_rec: dict[str, Any] = {
                "fold_id": int(getattr(fold, "fold_id", -1)),
                "n_val": int(val_idx.size),
                "n_excluded_future": int(
                    sum(
                        context_features_for_row(evidence_by_row[int(i)], cutoff)[1]
                        for i in val_idx.tolist()
                    )
                ),
                "n_context_used": int((~missing).sum()),
                "n_retained_veto": int(retained.sum()),
            }
            arm_probs = {
                "quant_only": pv,
                "quant_plus_context": pv_adj,
                "explanation_only": pv,
                "contextual_veto": pv,
            }
            arm_preds = {
                "quant_only": pred_q,
                "quant_plus_context": pred_adj,
                "explanation_only": pred_q,
                "contextual_veto": pred_q,
            }
            for arm in ARMS:
                if arm == "contextual_veto" and not bool(retained.any()):
                    fold_rec[f"{arm}_accuracy"] = None
                    fold_rec[f"{arm}_brier"] = None
                    fold_rec[f"{arm}_coverage"] = 0.0
                    continue
                mask = retained if arm == "contextual_veto" else np.ones_like(yv, bool)
                fold_rec[f"{arm}_accuracy"] = _accuracy(yv[mask], arm_preds[arm][mask])
                fold_rec[f"{arm}_brier"] = _brier(yv[mask], arm_probs[arm][mask])
                fold_rec[f"{arm}_coverage"] = float(mask.mean())
                pooled[arm]["correct"].extend(
                    (yv[mask] == arm_preds[arm][mask]).astype(float).tolist()
                )
                pooled[arm]["probs"].extend(np.asarray(arm_probs[arm][mask]).tolist())
                pooled[arm]["labels"].extend(yv[mask].tolist())
                pooled[arm]["retained"].extend(mask.astype(float).tolist())
            per_fold.append(fold_rec)

        arm_metrics: dict[str, dict[str, Any]] = {}
        for arm in ARMS:
            correct = np.asarray(pooled[arm]["correct"], dtype=float)
            probs = np.asarray(pooled[arm]["probs"], dtype=float)
            labels = np.asarray(pooled[arm]["labels"], dtype=float)
            if correct.size == 0:
                arm_metrics[arm] = {
                    "accuracy": None,
                    "brier": None,
                    "coverage": 0.0,
                    "n": 0,
                }
                continue
            arm_metrics[arm] = {
                "accuracy": float(correct.mean()),
                "brier": _brier(labels, probs),
                "coverage": float(np.asarray(pooled[arm]["retained"]).mean()),
                "n": int(correct.size),
            }
        delta = np.asarray(pooled["quant_plus_context"]["correct"]) - np.asarray(
            pooled["quant_only"]["correct"]
        )
        ci = _bootstrap_ci(delta, seed=self.seed) if delta.size else {"lo": 0.0, "hi": 0.0}
        self.results = {
            "arms": list(ARMS),
            "arm_metrics": arm_metrics,
            "per_fold": per_fold,
            "accuracy_delta": float(delta.mean()) if delta.size else 0.0,
            "accuracy_delta_ci": {"lo": float(ci.get("lo")), "hi": float(ci.get("hi"))},
            "n_excluded_future": int(total_excluded_future),
            "n_context_used": int(total_context_used),
            "n_folds": len(list(folds)),
            "seed": int(self.seed),
        }
        self.results["verdict"] = self.verdict()
        self._record_arms(meta, folds)
        return dict(self.results)

    def verdict(self) -> dict[str, Any]:
        """Return superiority verdict (``inconclusive`` unless margin is met)."""
        if not self.results:
            raise InformativeError("ablation: run() before verdict().")
        metrics = self.results["arm_metrics"]
        base = metrics["quant_only"]["accuracy"] or 0.0
        plus = metrics["quant_plus_context"]["accuracy"] or 0.0
        delta_acc = float(plus - base)
        cov_q = float(metrics["quant_only"]["coverage"])
        cov_p = float(metrics["quant_plus_context"]["coverage"])
        coverage_ok = bool(cov_p >= cov_q - float(self.coverage_tol))
        if int(self.results.get("n_context_used", 0)) == 0:
            return {
                "verdict": "inconclusive",
                "reason": "no timestamped contextual dataset: no fold row had "
                "temporally-available context evidence.",
                "accuracy_delta": delta_acc,
                "coverage_ok": coverage_ok,
            }
        if delta_acc >= float(self.acc_margin) and coverage_ok:
            return {
                "verdict": "superior",
                "reason": f"accuracy uplift {delta_acc:+.4f} >= margin "
                f"{self.acc_margin} with coverage retained.",
                "accuracy_delta": delta_acc,
                "coverage_ok": coverage_ok,
            }
        return {
            "verdict": "inconclusive",
            "reason": "prespecified superiority margin not met on unseen folds "
            f"(delta {delta_acc:+.4f} vs margin {self.acc_margin}).",
            "accuracy_delta": delta_acc,
            "coverage_ok": coverage_ok,
        }

    def _record_arms(self, meta: dict[str, Any], folds: list[Any]) -> list[str]:
        ids: list[str] = []
        metrics = self.results["arm_metrics"]
        periods = [
            {
                "fold_id": int(getattr(f, "fold_id", k)),
                "val_start": int(getattr(f, "val_start", -1)),
                "val_end": int(getattr(f, "val_end", -1)),
                "n_train": int(np.asarray(f.train_idx).size),
                "n_val": int(np.asarray(f.val_idx).size),
            }
            for k, f in enumerate(folds)
        ]
        for arm in ARMS:
            arm_m = metrics[arm]
            entry = {
                "experiment_id": (f"ablation-{self.run_tag}-{arm}-seed{self.seed}"),
                "dataset_snapshot": str(meta.get("dataset_snapshot", "ablation-fixture")),
                "dataset_checksum": str(meta.get("dataset_checksum", "fixture")),
                "model_version": f"{CODE_VERSION}+quant",
                "feature_schema": meta.get("feature_schema", ["quant", "context"]),
                "label_def": meta.get("label_def", {"horizon": "fixture"}),
                "protocol": {
                    "name": "ablation-walk-forward",
                    "n_folds": len(list(folds)),
                    "arm": arm,
                    "arms": list(ARMS),
                    "n_trials": 4,
                    "veto_threshold": float(self.veto_threshold),
                    "context_alpha": float(self.context_alpha),
                },
                "periods": periods,
                "calibration": meta.get("calibration", {"method": "fixture", "params": {}}),
                "hyperparams": {
                    "seed": int(self.seed),
                    "acc_margin": float(self.acc_margin),
                    "coverage_tol": float(self.coverage_tol),
                },
                "thresholds": {"predict": 0.5, "veto_abs": float(self.veto_threshold)},
                "n_trials": 4,
                "metrics": {
                    "accuracy": arm_m["accuracy"],
                    "brier": arm_m["brier"],
                    "coverage": arm_m["coverage"],
                    "n": arm_m["n"],
                    "accuracy_delta_vs_quant_only": (
                        float(arm_m["accuracy"] - (metrics["quant_only"]["accuracy"] or 0.0))
                        if arm_m["accuracy"] is not None
                        else None
                    ),
                },
                "counts": {
                    "n_folds": len(list(folds)),
                    "n_excluded_future": int(self.results["n_excluded_future"]),
                    "n_context_used": int(self.results["n_context_used"]),
                },
                "software_versions": meta.get("software_versions", {"ablation": CODE_VERSION}),
                "seeds": [int(self.seed)],
                "holdout_status": {"used": False, "log": []},
                "status": "success" if arm_m["accuracy"] is not None else "failed",
                "limitations": [
                    "Mocked/no-real-news context: verdict inconclusive unless a "
                    "timestamped contextual dataset shows the margin on folds.",
                    "Folds-only; final holdout never touched.",
                ],
            }
            ids.append(self.registry.record(entry))
        return ids


__all__ = [
    "ARMS",
    "CODE_VERSION",
    "AblationStudy",
    "context_features_for_row",
    "evidence_available_at",
]
