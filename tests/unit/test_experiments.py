"""Experiment registry + multiple-testing tests (Track B). Offline, deterministic."""

from __future__ import annotations

import numpy as np
import pytest

from crypto_engine.models.base import InformativeError
from crypto_engine.validation.experiments import (
    ExperimentRegistry,
    bonferroni_threshold,
    deflated_sharpe,
    experiment_id,
    holm_thresholds,
    pbo_note,
    selection_warning,
    validate_experiment,
)


def _entry(**overrides):
    base = {
        "dataset_snapshot": "snap-001",
        "dataset_checksum": "abc123",
        "model_version": "logistic-v1",
        "feature_schema": ["ret_1", "rsi_14"],
        "label_def": {"pt_mult": 2.0, "sl_mult": 1.0},
        "protocol": "walkforward-3x50-embargo24",
        "periods": ["2024-01", "2024-02"],
        "calibration": {"method": "sigmoid"},
        "hyperparams": {"C": 1.0},
        "thresholds": [0.5],
        "n_trials": 4,
        "metrics": {"accuracy": 0.55},
        "counts": {"n_train": 200, "n_val": 150},
        "software_versions": {"sklearn": "1.3.0", "pkg": "0.1.0"},
        "seeds": {"split": 42, "model": 42},
        "holdout_status": {"used": False, "log": []},
        "status": "success",
        "limitations": "proxy-level only",
        "config": {"C": 1.0},
        "code_version": "pkg-0.1.0",
    }
    base.update(overrides)
    if "experiment_id" not in base:
        base["experiment_id"] = experiment_id(base["config"], base["code_version"])
    return base


def test_record_get_list_roundtrip(tmp_path) -> None:
    """Recorded entries reload identically; ids are deterministic."""
    reg = ExperimentRegistry(tmp_path / "experiments.jsonl")
    exp_id = reg.record(_entry())
    assert exp_id == experiment_id({"C": 1.0}, "pkg-0.1.0")
    assert reg.record(_entry(config={"C": 2.0})) != exp_id
    fetched = reg.get(exp_id)
    assert fetched["metrics"] == {"accuracy": 0.55}
    assert len(reg.list()) == 2
    assert len(reg.list(status="success")) == 2
    with pytest.raises(FileExistsError):
        reg.record(_entry())  # no overwrite of an identical id
    with pytest.raises(KeyError):
        reg.get("missing-id")


def test_failed_status_kept_not_counted(tmp_path) -> None:
    """Failed/incomplete runs persist but never count as successes."""
    reg = ExperimentRegistry(tmp_path / "exp.jsonl")
    reg.record(_entry(status="success"))
    reg.record(_entry(status="failed", config={"C": 9.0}, limitations="diverged"))
    reg.record(_entry(status="incomplete", config={"C": 10.0}))
    counts = reg.summary_counts()
    assert counts == {"success": 1, "failed": 1, "incomplete": 1, "total": 3}
    assert len(reg.list(status="failed")) == 1
    assert reg.list(status="failed")[0]["limitations"] == "diverged"


def test_invalid_entries_rejected(tmp_path) -> None:
    """Missing provenance or bad status/n_trials raise with reasons."""
    reg = ExperimentRegistry(tmp_path / "exp.jsonl")
    bad = _entry()
    del bad["dataset_checksum"]
    with pytest.raises(InformativeError):
        reg.record(bad)
    with pytest.raises(InformativeError):
        reg.record(_entry(status="maybe", config={"C": 3.0}))
    with pytest.raises(InformativeError):
        reg.record(_entry(n_trials=0, config={"C": 4.0}))
    assert validate_experiment({"status": "success"})  # missing fields listed
    assert validate_experiment(_entry()) == []


def test_mark_holdout_used(tmp_path) -> None:
    """Holdout consumption is flagged with an auditable purpose log."""
    reg = ExperimentRegistry(tmp_path / "exp.jsonl")
    exp_id = reg.record(_entry())
    assert reg.get(exp_id)["holdout_status"]["used"] is False
    updated = reg.mark_holdout_used(exp_id, "final unbiased read")
    assert updated["holdout_status"]["used"] is True
    assert reg.get(exp_id)["holdout_status"]["log"][0]["purpose"] == "final unbiased read"
    with pytest.raises(KeyError):
        reg.mark_holdout_used("nope", "x")


def test_deflated_sharpe_sufficient() -> None:
    """Enough data + trials -> float in [0, 1] with documented assumptions."""
    rng = np.random.default_rng(0)
    returns = rng.normal(0.002, 0.01, size=250)
    dsr, info = deflated_sharpe(returns, n_trials=10)
    assert dsr is not None
    assert 0.0 <= dsr <= 1.0
    assert info["T"] == 250
    assert "V_source" in info and info["assumptions"]
    # Strong positive edge should deflate less than a flat series.
    flat_dsr, _ = deflated_sharpe(rng.normal(0.0, 0.01, size=250), n_trials=10)
    assert flat_dsr is not None and dsr > flat_dsr


def test_deflated_sharpe_insufficient() -> None:
    """Small T, single trial, or degenerate variance -> None + reason."""
    rng = np.random.default_rng(1)
    dsr, info = deflated_sharpe(rng.normal(size=20), n_trials=5)
    assert dsr is None and "T=20" in info["reason"]
    dsr, info = deflated_sharpe(rng.normal(size=100), n_trials=1)
    assert dsr is None and ("K=1" in info["reason"] or "single trial" in info["reason"])
    dsr, info = deflated_sharpe(np.zeros(100), n_trials=5)
    assert dsr is None and "dispersion" in info["reason"]
    dsr, info = deflated_sharpe(
        rng.normal(size=100),
        n_trials=5,
        trial_srs=[0.5],  # single trial SR
    )
    assert dsr is None and "trial_srs" in info["reason"]


def test_multiple_comparison_thresholds() -> None:
    """Bonferroni = alpha/K; Holm sequence starts there and rises."""
    assert bonferroni_threshold(0.05, 10) == pytest.approx(0.005)
    holm = holm_thresholds(0.05, 4)
    assert holm[0] == pytest.approx(0.05 / 4)
    assert holm == sorted(holm)
    assert holm[-1] == pytest.approx(0.05)
    warn = selection_warning(10)
    assert "10" in warn and "Bonferroni" in warn
    assert "no selection bias" in selection_warning(1)


def test_pbo_not_fabricated() -> None:
    """pbo_note documents inapplicability and refuses a number."""
    note = pbo_note()
    assert "NOT COMPUTED" in note
    assert "CSCV" in note
    assert "fabrication" in note.lower()
