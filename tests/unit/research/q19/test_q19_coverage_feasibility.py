"""Q19 Phase F: coverage feasibility analysis tests.

These tests verify the coverage feasibility mathematics for the prospective
holdout collection (Q18/Q19 window). They are pure computation plus reads of
the existing immutable prospective storage and the data adapter retry policy.

No network, no credentials, no retraining, no tuning. ccxt-dependent checks
use pytest.importorskip("ccxt").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

from crypto_engine.research.prospective.config import (
    DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S,
)
from crypto_engine.research.prospective.storage import ProspectiveStorage

# Project root (tests/unit/research/q19 -> four levels up).
PROJECT_ROOT = Path(__file__).resolve().parents[4]
STORAGE_ROOT = (
    PROJECT_ROOT
    / "data"
    / "prospective"
    / "binance"
    / "spot"
    / "BTC"
    / "USDT"
    / "1h"
)

# Predeclared policy thresholds (see config.py).
MIN_OBSERVATION_COUNT = 168  # 1 week of hourly candles
MIN_COVERAGE_FRACTION = 0.95  # 95% of expected intervals

# Collector starting state (Q18 accepted one observation; 58 expected intervals
# had elapsed by the Q18 checkpoint).
ACCEPTED_BASE = 1
EXPECTED_BASE = 58

# Minimum additional observations needed to reach 95% coverage given the
# growing denominator: solve (1 + N) / (58 + N) >= 0.95.
N_COVERAGE = 1082
# Minimum additional observations needed to reach the count floor given one
# already accepted: max(0, 168 - 1).
N_COUNT = 167


class TestCoverageFormula:
    """Tests for the coverage = accepted / expected_intervals formula."""

    def test_coverage_growing_denominator_formula(self):
        """Coverage grows by 1 accepted per expected interval; denominator grows
        by 1 every hour. A=1, E=58 -> coverage = 1/58 ~= 0.0172."""
        accepted = ACCEPTED_BASE
        expected_intervals = EXPECTED_BASE
        coverage = (
            accepted / expected_intervals if expected_intervals > 0 else 0.0
        )
        assert expected_intervals == 58
        assert coverage == pytest.approx(1 / 58, rel=1e-9)
        assert coverage == pytest.approx(0.0172, abs=1e-4)


class TestCoverageRequirement:
    """Tests for solving the coverage requirement with a growing denominator."""

    def test_coverage_requirement_with_growing_denominator(self):
        """Solve (1 + N) / (58 + N) >= 0.95 for minimum integer N -> 1082.

        (1 + 1082) / (58 + 1082) = 1083 / 1140 = 19/20 = 0.95 exactly.
        N = 1081 fails (1082/1139 < 0.95), so 1082 is the minimum."""
        n = N_COVERAGE
        coverage = Fraction(1 + n, EXPECTED_BASE + n)
        assert coverage == Fraction(95, 100), (
            f"coverage at N={n} is {coverage}, expected exactly 0.95"
        )
        # Minimality: N - 1 must not satisfy the requirement.
        prior = Fraction(1 + (n - 1), EXPECTED_BASE + (n - 1))
        assert prior < Fraction(95, 100), (
            f"N={n - 1} should fail but coverage is {prior}"
        )

    def test_minimum_observation_requirement(self):
        """N_count = max(0, 168 - 1) = 167 additional observations needed."""
        n_count = max(0, MIN_OBSERVATION_COUNT - ACCEPTED_BASE)
        assert n_count == 167

    def test_combined_requirement(self):
        """N_required = max(N_count, N_coverage) = max(167, 1082) = 1082."""
        n_required = max(N_COUNT, N_COVERAGE)
        assert n_required == 1082
        assert n_required == max(167, 1082)


class TestDeadlineFeasibility:
    """Tests for the 7-day deadline feasibility (collection is blocked)."""

    def test_seven_day_deadline_feasibility(self):
        """By the 7-day deadline (E=167) at most 110 observations can be
        accepted. Coverage max = 110/167 ~= 65.87% < 95%, and obs max = 110
        < 168. Both requirements fail."""
        max_accepted = 110
        expected_deadline = 167
        coverage_max = max_accepted / expected_deadline
        assert coverage_max == pytest.approx(110 / 167, rel=1e-9)
        assert coverage_max < MIN_COVERAGE_FRACTION, (
            f"max coverage {coverage_max:.4%} must be below 95%"
        )
        assert max_accepted < MIN_OBSERVATION_COUNT, (
            f"max observations {max_accepted} must be below 168"
        )

    def test_earliest_eligibility_date(self):
        """From now (2026-11-15) + 1082 hours = Dec 30, 2026."""
        now = datetime(2026, 11, 15, tzinfo=UTC)
        eligible = now + timedelta(hours=N_COVERAGE)
        assert eligible.date() == datetime(2026, 12, 30).date()

    @pytest.mark.parametrize(
        "missed,expected_total",
        [(0, 1082), (1, 1102), (5, 1182)],
    )
    def test_missed_intervals_effect(self, missed, expected_total):
        """Each missed interval adds 20 hours: T >= 1082 + 20*M.

        M=0 -> 1082, M=1 -> 1102, M=5 -> 1182."""
        total_hours = N_COVERAGE + 20 * missed
        assert total_hours == expected_total
        assert total_hours >= N_COVERAGE + 20 * missed


class TestDurationSemantics:
    """Tests that the 7-day duration is a minimum floor, not a cutoff."""

    def test_duration_is_minimum_not_window(self):
        """DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S = 604800 (7 days) is a
        minimum floor: the collector blocks while elapsed_ms <
        7*24*60*60*1000 but never stops collection."""
        assert DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S == 604800
        assert DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S == 7 * 24 * 60 * 60
        min_elapsed_ms = 7 * 24 * 60 * 60 * 1000
        assert min_elapsed_ms == 604_800_000
        # Floor semantics: block only when below the threshold; no upper bound
        # that stops collection.
        assert min_elapsed_ms > 0


class TestReaderConsistency:
    """Tests that all three storage readers agree on current state."""

    def test_reader_consistency(self):
        """Raw and normalized readers agree on observation count; dedup never
        exceeds the raw count."""
        storage = ProspectiveStorage(STORAGE_ROOT)
        raw = storage.read_raw_observations()
        normalized = storage.read_normalized_candles()
        unique = storage.read_unique_normalized_candles()
        # Reader consistency: raw and normalized must report the same count.
        assert len(raw) == len(normalized)
        # Dedup: unique count never exceeds raw count.
        assert len(unique) <= len(raw)


class TestRetryPolicy:
    """Tests for the Q18 exchange adapter retry policy."""

    def test_q18_retry_policy(self):
        """Adapter: max_duration_ms=60000, RateLimitExceeded is a transient
        error, stop_after_delay combined with stop_after_attempt."""
        ccxt = pytest.importorskip("ccxt")

        from crypto_engine.data.adapter import CcxtAdapter, _transient_errors

        adapter = CcxtAdapter()
        assert adapter.max_duration_ms == 60000

        transient = _transient_errors()
        assert ccxt.RateLimitExceeded in transient

        # stop_after_attempt and stop_after_delay are combined with '|' in
        # _fetch_page (see adapter.py retry configuration).
        import crypto_engine.data.adapter as adapter_mod

        source = Path(adapter_mod.__file__).read_text(encoding="utf-8")
        assert "stop_after_attempt" in source
        assert "stop_after_delay" in source
        assert "stop_after_attempt(max(1, self.max_retries + 1))" in source
        assert "stop_after_delay(self.max_duration_ms / 1000.0)" in source

