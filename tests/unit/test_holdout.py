"""Holdout split + ledger + adequacy tests (Track B). Offline, deterministic."""

from __future__ import annotations

import pytest

from crypto_engine.models.base import InformativeError
from crypto_engine.validation.holdout import (
    HoldoutLedger,
    adequacy_report,
    split_holdout,
)


def test_tail_split_sizes() -> None:
    """Holdout is the chronological tail; research is the head."""
    research, holdout = split_holdout(400, 0.15)
    assert holdout.tolist() == list(range(340, 400))
    assert research.tolist() == list(range(340))
    assert len(set(research.tolist()) & set(holdout.tolist())) == 0


def test_zero_frac_empty_holdout() -> None:
    """holdout_frac=0 keeps everything in research (explicit, not an error)."""
    research, holdout = split_holdout(100, 0.0)
    assert holdout.size == 0
    assert research.size == 100


def test_frac_bounds_rejected() -> None:
    """Fractions outside [0, 0.4] are rejected (research must keep a majority)."""
    with pytest.raises(InformativeError):
        split_holdout(100, 0.5)
    with pytest.raises(InformativeError):
        split_holdout(100, -0.1)
    with pytest.raises(InformativeError):
        split_holdout(0, 0.1)


def test_ledger_pristine_to_used_transition(tmp_path) -> None:
    """Ledger starts pristine; one access flips it permanently."""
    path = tmp_path / "holdout_ledger.json"
    ledger = HoldoutLedger.create(path, holdout_start=340, n_total=400, holdout_frac=0.15)
    assert ledger.is_pristine is True
    assert ledger.access_count == 0

    reloaded = HoldoutLedger.load(path)
    assert reloaded.is_pristine is True

    reloaded.mark_accessed("final unbiased accuracy read, exp deadbeef")
    assert reloaded.is_pristine is False
    assert reloaded.access_count == 1

    after = HoldoutLedger.load(path)
    assert after.is_pristine is False
    assert after.access_count == 1
    assert after.log[0]["purpose"] == "final unbiased accuracy read, exp deadbeef"
    assert "at" in after.log[0]

    # Second access keeps appending (re-access is auditable, pristineness never returns).
    after.mark_accessed("threshold confirmation")
    assert HoldoutLedger.load(path).access_count == 2


def test_ledger_no_overwrite_and_purpose_required(tmp_path) -> None:
    """Existing ledgers are never overwritten; empty purposes are rejected."""
    path = tmp_path / "ledger.json"
    HoldoutLedger.create(path, holdout_start=10, n_total=100, holdout_frac=0.1)
    with pytest.raises(FileExistsError):
        HoldoutLedger.create(path, holdout_start=10, n_total=100, holdout_frac=0.1)
    with pytest.raises(InformativeError):
        HoldoutLedger.load(path).mark_accessed("   ")


def test_adequacy_verdicts() -> None:
    """sufficient / marginal / insufficient verdicts with reasons."""
    assert adequacy_report(150, min_n=100)["verdict"] == "sufficient"
    marginal = adequacy_report(60, min_n=100)
    assert marginal["verdict"] == "marginal"
    assert marginal["reasons"]
    insufficient = adequacy_report(10, min_n=100)
    assert insufficient["verdict"] == "insufficient"
    assert any("definitive" in r for r in insufficient["reasons"])
    assert "never" in insufficient["limitation"].lower() or "only if" in insufficient["limitation"]
    # Absolute floor: tiny holdouts are insufficient regardless of min_n.
    assert adequacy_report(20, min_n=30)["verdict"] == "insufficient"
    with pytest.raises(InformativeError):
        adequacy_report(-1)
