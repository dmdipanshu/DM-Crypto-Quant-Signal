"""AI temporal-leakage tests: future context must never move earlier folds."""

from __future__ import annotations

from crypto_engine.agents.schemas import EvidenceItem
from crypto_engine.validation.ablation import AblationStudy, context_features_for_row
from crypto_engine.validation.experiments import ExperimentRegistry


def _ctx_items(ts: str, sentiment: float):
    return [
        EvidenceItem(
            id="c0",
            kind="news",
            source="wire",
            observed_at=ts,
            payload={"headline": "h", "sentiment": sentiment},
            trust="external",
        )
    ]


def test_future_news_never_moves_earlier_folds(ablation_frame_120, tmp_path):
    """Perturbing future rows' context leaves earlier fold metrics unchanged."""
    frame = ablation_frame_120
    folds = frame["folds"]
    first_val_end = folds[0].val_end
    ts = list(frame["ts"])
    base_ts = "2024-01-01T00:00:00+00:00"  # <= every train cutoff: context used

    def build(future_sentiment: float):
        evidence = []
        for i, _t in enumerate(ts):
            sentiment = future_sentiment if i >= first_val_end else 0.0
            evidence.append(_ctx_items(base_ts, sentiment))
        study = AblationStudy(ExperimentRegistry(tmp_path / f"r-{future_sentiment}.jsonl"), seed=4)
        return study.run(frame["y"], frame["p"], evidence, ts, folds)

    calm = build(0.0)
    shock = build(1.0)
    assert calm["n_context_used"] > 0, "test vacuous unless context is used"
    # Fold 0 validates rows before the perturbed region: identical metrics.
    assert calm["per_fold"][0] == shock["per_fold"][0]
    # Later folds see the shock (context actually flows through).
    assert calm["per_fold"][-1] != shock["per_fold"][-1]


def test_evidence_after_prediction_excluded_and_counted(ablation_frame_120):
    """Items dated after the prediction time are excluded with a count."""
    from datetime import datetime

    frame = ablation_frame_120
    cutoff = datetime.fromisoformat(frame["ts"][10])
    items = _ctx_items(frame["ts"][0], 0.3) + _ctx_items("2025-01-01T00:00:00+00:00", 1.0)
    feats, excluded = context_features_for_row(items, cutoff)
    assert excluded == 1
    assert feats[0] == 0.3


def test_calibration_before_holdout_preserved(ablation_frame_120, tmp_path):
    """Ablation defaults to folds-only and never marks a holdout ledger."""
    from crypto_engine.validation.holdout import HoldoutLedger

    frame = ablation_frame_120
    HoldoutLedger.create(tmp_path / "hold.json", 90, 120, 0.25)
    evidence = [_ctx_items(t, 0.0) for t in frame["ts"]]
    AblationStudy(ExperimentRegistry(tmp_path / "reg.jsonl"), seed=2).run(
        frame["y"],
        frame["p"],
        evidence,
        frame["ts"],
        frame["folds"],
        holdout_ledger_path=tmp_path / "hold.json",
    )
    reloaded = HoldoutLedger.load(tmp_path / "hold.json")
    assert reloaded.is_pristine
    assert reloaded.access_count == 0
