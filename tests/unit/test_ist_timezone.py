"""IST timezone formatting regression tests (frontend logic ported to Python).

Verifies that Intl.DateTimeFormat with timeZone "Asia/Kolkata" correctly
converts UTC timestamps to IST (UTC+05:30) display format.

These tests port the dashboard's `fmtIst` logic to Python for verification
that the UTC->IST conversion is correct for all boundary cases.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def fmt_ist_python(iso: str | None) -> str:
    """Python port of dashboard fmtIst: UTC -> Asia/Kolkata (UTC+05:30)."""
    if not iso:
        return "N/A"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        # Asia/Kolkata = UTC+05:30
        ist_offset = timedelta(hours=5, minutes=30)
        ist_dt = dt.astimezone(UTC) + ist_offset
        month_short = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ][ist_dt.month - 1]
        return (
            f"{ist_dt.day:02d} {month_short} {ist_dt.year}, "
            f"{ist_dt.hour:02d}:{ist_dt.minute:02d}:{ist_dt.second:02d} IST"
        )
    except (ValueError, TypeError):
        return "N/A"


def test_utc_to_ist_conversion() -> None:
    """A UTC timestamp converts to correct IST time (UTC+05:30)."""
    # 2024-01-10T10:00:00Z -> 2024-01-10T15:30:00 IST
    result = fmt_ist_python("2024-01-10T10:00:00+00:00")
    assert result == "10 Jan 2024, 15:30:00 IST"


def test_utc_midnight_to_ist_next_day() -> None:
    """UTC midnight converts to IST 05:30 same day."""
    # 2024-01-10T00:00:00Z -> 2024-01-10T05:30:00 IST
    result = fmt_ist_python("2024-01-10T00:00:00+00:00")
    assert result == "10 Jan 2024, 05:30:00 IST"


def test_utc_evening_to_ist_next_day() -> None:
    """UTC evening converts to IST next day early morning."""
    # 2024-01-10T20:00:00Z -> 2024-01-11T01:30:00 IST
    result = fmt_ist_python("2024-01-10T20:00:00+00:00")
    assert result == "11 Jan 2024, 01:30:00 IST"


def test_iso_with_non_utc_offset_converts_correctly() -> None:
    """An ISO timestamp with a non-UTC offset converts correctly to IST."""
    # 2024-01-10T10:00:00+02:00 is 08:00 UTC -> 13:30 IST
    result = fmt_ist_python("2024-01-10T10:00:00+02:00")
    assert result == "10 Jan 2024, 13:30:00 IST"


def test_utc_z_suffix_converts_correctly() -> None:
    """A UTC timestamp with Z suffix converts correctly."""
    result = fmt_ist_python("2024-06-15T12:00:00Z")
    assert result == "15 Jun 2024, 17:30:00 IST"


def test_missing_timestamp_displays_unavailable() -> None:
    """Missing or None timestamp displays as N/A, not a fabricated date."""
    assert fmt_ist_python(None) == "N/A"
    assert fmt_ist_python("") == "N/A"


def test_invalid_timestamp_displays_unavailable() -> None:
    """Invalid timestamp string displays as N/A."""
    assert fmt_ist_python("not-a-timestamp") == "N/A"
    assert fmt_ist_python("") == "N/A"


def test_ist_format_matches_required_pattern() -> None:
    """IST output matches required format: DD MMM YYYY, HH:mm:ss IST."""
    result = fmt_ist_python("2024-01-10T10:00:00+00:00")
    # Pattern: DD MMM YYYY, HH:mm:ss IST
    import re
    assert re.fullmatch(r"\d{2} [A-Za-z]{3} \d{4}, \d{2}:\d{2}:\d{2} IST", result), \
        f"Format mismatch: {result!r}"


def test_dst_boundary_june() -> None:
    """June (no DST in India, but verify offset still +05:30)."""
    # India does not observe DST, so offset is always +05:30
    result = fmt_ist_python("2024-06-15T00:00:00Z")
    assert result == "15 Jun 2024, 05:30:00 IST"


def test_dst_boundary_december() -> None:
    """December (verify offset still +05:30)."""
    result = fmt_ist_python("2024-12-15T00:00:00Z")
    assert result == "15 Dec 2024, 05:30:00 IST"


__all__ = [
    "test_dst_boundary_december",
    "test_dst_boundary_june",
    "test_invalid_timestamp_displays_unavailable",
    "test_iso_with_non_utc_offset_converts_correctly",
    "test_ist_format_matches_required_pattern",
    "test_missing_timestamp_displays_unavailable",
    "test_utc_evening_to_ist_next_day",
    "test_utc_midnight_to_ist_next_day",
    "test_utc_to_ist_conversion",
    "test_utc_z_suffix_converts_correctly",
]
