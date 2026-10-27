"""Track B ablation tests: registry reuse, temporal cutoff, determinism."""

from __future__ import annotations

import numpy as np
import pytest

from crypto_engine.agents.schemas import EvidenceItem
from crypto_engine.models.base import InformativeError
from crypto_engine.validation.ablation import (
    ARMS,
    AblationStudy,
    context_features_for_row,
    evidence_available_at,
)
from crypto_engine.validation.experiments import ExperimentRegistry
from tests.fixtures.ai_fixtures import make_evidence


def _row_evidence(ts: str, sentiment: float = 0.0, future_ts: str | None = None):
    # NOTE: available-context tests pass an early ``ts`` (<= fold-train end);
    # row-time ``ts`` is excluded by the train-end cutoff by design.
    items = [
        EvidenceItem(
            id="c0",
            kind="news",
            source="wire",
            observed_at=ts,
            payload={"headline": "h", "sentiment": sentiment},
            trust="external",
        )
    ]
    if future_ts is not None:
        items.append(
            EvidenceItem(
                id="future",
                kind="news",
                source="wire",
                observed_at=future_ts,
                payload={"headline": "leak", "sentiment": 1.0},
                trust="external",
            )
        )
    return items


def test_four_arms_recorded_same_folds(ablation_frame_120, tmp_path):
    """All 4 arms recorded in the registry with identical fold periods."""
    frame = ablation_frame_120
    registry = ExperimentRegistry(tmp_path / "reg.jsonl")
    study = AblationStudy(registry, seed=11)
    # Early observed_at (<= every fold-train end) so context is usable.
    evidence = [_row_evidence("2024-01-01T00:00:00+00:00", sentiment=0.3) for _ in frame["ts"]]
    results = study.run(frame["y"], frame["p"], evidence, frame["ts"], frame["folds"])
    assert results["n_context_used"] > 0
    assert set(results["arm_metrics"]) == set(ARMS)
    assert registry.summary_counts() == {
        "success": 4,
        "failed": 0,
        "incomplete": 0,
        "total": 4,
    }
    entries = registry.list()
    periods0 = entries[0]["periods"]
    for entry in entries[1:]:
        assert entry["periods"] == periods0
        assert entry["n_trials"] == 4
        assert entry["holdout_status"] == {"used": False, "log": []}


def test_temporal_cutoff_excludes_future_evidence(tmp_path):
    """Evidence dated after the train cutoff is excluded and counted."""
    from crypto_engine.validation.folds import make_folds

    n = 60
    rng = np.random.default_rng(3)
    y = (rng.uniform(size=n) > 0.5).astype(int)
    p = np.clip(0.5 + 0.1 * (y - 0.5), 0.01, 0.99)
    base = "2024-01-01T00:00:00+00:00"
    ts = [f"2024-01-{1 + i // 24:02d}T{i % 24:02d}:00:00+00:00" for i in range(n)]
    folds = make_folds(n, train_window=20, val_window=10, n_folds=2, min_samples=5)
    # Every row carries one future-dated item (after the whole series).
    evidence = [
        _row_evidence(base, sentiment=0.9, future_ts="2025-06-01T00:00:00+00:00") for _ in range(n)
    ]
    registry = ExperimentRegistry(tmp_path / "reg.jsonl")
    study = AblationStudy(registry, seed=5)
    results = study.run(y, p, evidence, ts, folds)
    assert results["n_excluded_future"] == sum(f["n_excluded_future"] for f in results["per_fold"])
    assert results["n_excluded_future"] > 0
    # Future sentiment 1.0 must NOT leak: base sentiment 0.9 is also excluded?
    # base item observed 2024-01-01 <= cutoff → used; future item excluded.
    assert results["n_context_used"] > 0
    cutoff = folds[0].train_idx.max()
    _ = cutoff


def test_evidence_available_at_boundary():
    """observed_at == cutoff counts as available (observable at cutoff)."""
    item = EvidenceItem(
        id="x",
        kind="news",
        source="s",
        observed_at="2024-01-05T00:00:00+00:00",
        payload={},
        trust="external",
    )
    from datetime import datetime

    assert evidence_available_at(item, datetime.fromisoformat("2024-01-05T00:00:00+00:00"))
    assert not evidence_available_at(item, datetime.fromisoformat("2024-01-04T23:59:59+00:00"))


def test_mocked_context_zero_value_inconclusive(ablation_frame_120, tmp_path):
    """Zero-value mocked context → no uplift → inconclusive, never a false win."""
    frame = ablation_frame_120
    registry = ExperimentRegistry(tmp_path / "reg.jsonl")
    study = AblationStudy(registry, seed=11)
    # Zero-value but temporally-available context: uplift exactly 0.
    evidence = [_row_evidence("2024-01-01T00:00:00+00:00", sentiment=0.0) for _ in frame["ts"]]
    results = study.run(frame["y"], frame["p"], evidence, frame["ts"], frame["folds"])
    assert results["n_context_used"] > 0
    assert results["verdict"]["verdict"] == "inconclusive"
    assert results["accuracy_delta"] == pytest.approx(0.0, abs=1e-12)


def test_no_context_dataset_inconclusive_reason(ablation_frame_120, tmp_path):
    """Rows with no usable context → inconclusive + dataset reason."""
    frame = ablation_frame_120
    registry = ExperimentRegistry(tmp_path / "reg.jsonl")
    study = AblationStudy(registry, seed=11)
    evidence = [
        [
            EvidenceItem(
                id=f"e{i}",
                kind="news",
                source="s",
                observed_at=t,
                payload={"headline": "no score here"},
                trust="external",
            )
        ]
        for i, t in enumerate(frame["ts"])
    ]
    results = study.run(frame["y"], frame["p"], evidence, frame["ts"], frame["folds"])
    assert results["verdict"]["verdict"] == "inconclusive"
    assert "no timestamped contextual dataset" in results["verdict"]["reason"]


def test_reproducibility_same_seed(ablation_frame_120, tmp_path):
    """Two runs, same seed → identical metrics."""
    frame = ablation_frame_120
    evidence = [_row_evidence("2024-01-01T00:00:00+00:00", sentiment=0.2) for _ in frame["ts"]]
    first = AblationStudy(ExperimentRegistry(tmp_path / "a.jsonl"), seed=9).run(
        frame["y"], frame["p"], evidence, frame["ts"], frame["folds"]
    )
    second = AblationStudy(ExperimentRegistry(tmp_path / "b.jsonl"), seed=9).run(
        frame["y"], frame["p"], evidence, frame["ts"], frame["folds"]
    )
    assert first["arm_metrics"] == second["arm_metrics"]
    assert first["verdict"] == second["verdict"]


def test_failed_arm_recorded_not_counted(ablation_frame_120, tmp_path):
    """A fully-vetoed arm records status failed and is never a success."""
    frame = ablation_frame_120
    registry = ExperimentRegistry(tmp_path / "reg.jsonl")
    # veto_threshold huge → every retained check abstains when missing.
    study = AblationStudy(registry, seed=11, veto_threshold=100.0)
    evidence = [
        [
            EvidenceItem(
                id=f"e{i}",
                kind="news",
                source="s",
                observed_at=t,
                payload={"headline": "unscored"},
                trust="external",
            )
        ]
        for i, t in enumerate(frame["ts"])
    ]
    results = study.run(frame["y"], frame["p"], evidence, frame["ts"], frame["folds"])
    veto = next(e for e in registry.list() if e["protocol"]["arm"] == "contextual_veto")
    assert veto["status"] == "failed"
    assert registry.summary_counts()["success"] == 3
    assert results["arm_metrics"]["contextual_veto"]["accuracy"] is None


def test_holdout_refused_when_used(ablation_frame_120, tmp_path):
    """Non-pristine holdout ledger → ablation refuses (folds-only)."""
    from crypto_engine.validation.holdout import HoldoutLedger

    frame = ablation_frame_120
    ledger = HoldoutLedger.create(tmp_path / "hold.json", 90, 120, 0.25)
    ledger.mark_accessed("some prior evaluation")
    study = AblationStudy(ExperimentRegistry(tmp_path / "reg.jsonl"), seed=1)
    evidence = [_row_evidence(t) for t in frame["ts"]]
    with pytest.raises(InformativeError, match="folds-only"):
        study.run(
            frame["y"],
            frame["p"],
            evidence,
            frame["ts"],
            frame["folds"],
            holdout_ledger_path=tmp_path / "hold.json",
        )


def test_pristine_holdout_allowed_but_untouched(ablation_frame_120, tmp_path):
    """Pristine ledger passes the guard; holdout rows are still never read."""
    from crypto_engine.validation.holdout import HoldoutLedger

    frame = ablation_frame_120
    HoldoutLedger.create(tmp_path / "hold.json", 90, 120, 0.25)
    ledger = HoldoutLedger.load(tmp_path / "hold.json")
    assert ledger.is_pristine
    study = AblationStudy(ExperimentRegistry(tmp_path / "reg.jsonl"), seed=1)
    evidence = [_row_evidence(t) for t in frame["ts"]]
    study.run(
        frame["y"],
        frame["p"],
        evidence,
        frame["ts"],
        frame["folds"],
        holdout_ledger_path=tmp_path / "hold.json",
    )
    assert HoldoutLedger.load(tmp_path / "hold.json").is_pristine


def test_context_features_exclusion_count():
    """context_features_for_row uses only cutoff-available items + counts."""
    from datetime import datetime

    cutoff = datetime.fromisoformat("2024-01-10T00:00:00+00:00")
    items = _row_evidence(
        "2024-01-01T00:00:00+00:00",
        sentiment=0.4,
        future_ts="2024-02-01T00:00:00+00:00",
    )
    feats, excluded = context_features_for_row(items, cutoff)
    assert excluded == 1
    assert feats == pytest.approx([0.4, 0.0])
    _ = make_evidence  # fixture builder import sanity
