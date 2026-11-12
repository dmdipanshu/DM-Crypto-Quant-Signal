"""Analytics unit tests (Stage 5).

Offline tests for research_proxies() and validate_holdout().
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crypto_engine.analytics.holdout import validate_holdout
from crypto_engine.analytics.proxies import research_proxies

# ---------------------------------------------------------------------------
# research_proxies
# ---------------------------------------------------------------------------


def test_research_proxies_empty() -> None:
    result = research_proxies([])
    assert result["experiment_count"] == 0
    assert result["by_status"] == {}
    assert result["avg_trials"] is None
    assert result["directional_read"] == "neutral"


def test_research_proxies_positive() -> None:
    experiments = [
        {"status": "success", "n_trials": 10},
        {"status": "success", "n_trials": 20},
        {"status": "failed", "n_trials": 5},
    ]
    result = research_proxies(experiments)
    assert result["experiment_count"] == 3
    assert result["by_status"] == {"success": 2, "failed": 1}
    assert result["directional_read"] == "positive"
    assert result["avg_trials"] == pytest.approx(11.667, rel=1e-2)


def test_research_proxies_negative() -> None:
    experiments = [
        {"status": "failed", "n_trials": 10},
        {"status": "failed", "n_trials": 20},
        {"status": "success", "n_trials": 5},
    ]
    result = research_proxies(experiments)
    assert result["directional_read"] == "negative"


def test_research_proxies_neutral() -> None:
    experiments = [
        {"status": "success", "n_trials": 10},
        {"status": "failed", "n_trials": 10},
    ]
    result = research_proxies(experiments)
    assert result["directional_read"] == "neutral"


def test_research_proxies_incomplete() -> None:
    experiments = [
        {"status": "incomplete", "n_trials": 0},
        {"status": "incomplete", "n_trials": 5},
    ]
    result = research_proxies(experiments)
    assert result["directional_read"] == "neutral"
    assert result["avg_trials"] == 5.0  # trials > 0 averaged across all entries


def test_research_proxies_avg_trials() -> None:
    experiments = [
        {"status": "success", "n_trials": 10},
        {"status": "success", "n_trials": 20},
        {"status": "failed", "n_trials": 0},
    ]
    result = research_proxies(experiments)
    assert result["avg_trials"] == 15.0


def test_research_proxies_non_dict_entries_skipped() -> None:
    experiments = ["not a dict", {"status": "success", "n_trials": 5}]
    result = research_proxies(experiments)
    assert result["experiment_count"] == 2
    assert result["by_status"] == {"success": 1}


def test_research_proxies_limitation_present() -> None:
    result = research_proxies([])
    assert "limitation" in result
    assert "profitability" in result["limitation"].lower()


# ---------------------------------------------------------------------------
# validate_holdout
# ---------------------------------------------------------------------------


def test_validate_holdout_missing(tmp_path: Path) -> None:
    result = validate_holdout(tmp_path / "nonexistent.json")
    assert result["available"] is False
    assert result["pristine"] is None
    assert result["adequacy"] is None


def test_validate_holdout_pristine(tmp_path: Path) -> None:
    ledger_path = tmp_path / "holdout.json"
    from crypto_engine.validation.holdout import HoldoutLedger

    HoldoutLedger.create(ledger_path, holdout_start=100, n_total=500, holdout_frac=0.2)
    result = validate_holdout(ledger_path)
    assert result["available"] is True
    assert result["pristine"] is True
    assert result["access_count"] == 0
    assert result["adequacy"]["verdict"] == "sufficient"


def test_validate_holdout_used(tmp_path: Path) -> None:
    ledger_path = tmp_path / "holdout.json"
    from crypto_engine.validation.holdout import HoldoutLedger

    ledger = HoldoutLedger.create(ledger_path, holdout_start=100, n_total=500, holdout_frac=0.2)
    ledger.mark_accessed("final accuracy read")
    result = validate_holdout(ledger_path)
    assert result["available"] is True
    assert result["pristine"] is False
    assert result["access_count"] == 1


def test_validate_holdout_insufficient(tmp_path: Path) -> None:
    ledger_path = tmp_path / "holdout.json"
    from crypto_engine.validation.holdout import HoldoutLedger

    HoldoutLedger.create(ledger_path, holdout_start=100, n_total=10, holdout_frac=0.2)
    result = validate_holdout(ledger_path, min_n=100)
    assert result["available"] is True
    assert result["adequacy"]["verdict"] == "insufficient"


def test_validate_holdout_limitation_present(tmp_path: Path) -> None:
    ledger_path = tmp_path / "holdout.json"
    from crypto_engine.validation.holdout import HoldoutLedger

    HoldoutLedger.create(ledger_path, holdout_start=100, n_total=500, holdout_frac=0.2)
    result = validate_holdout(ledger_path)
    assert "limitation" in result
    assert "holdout" in result["limitation"].lower()


__all__ = [
    "test_research_proxies_avg_trials",
    "test_research_proxies_empty",
    "test_research_proxies_incomplete",
    "test_research_proxies_limitation_present",
    "test_research_proxies_negative",
    "test_research_proxies_neutral",
    "test_research_proxies_non_dict_entries_skipped",
    "test_research_proxies_positive",
    "test_validate_holdout_insufficient",
    "test_validate_holdout_limitation_present",
    "test_validate_holdout_missing",
    "test_validate_holdout_pristine",
    "test_validate_holdout_used",
]
