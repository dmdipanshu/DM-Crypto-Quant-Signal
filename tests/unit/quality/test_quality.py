"""Quality engine unit tests (Stage 4).

Offline tests for audit(), validate_schema(), and detect_staleness().
Pure stdlib only.
"""

from __future__ import annotations

from datetime import UTC, datetime

from crypto_engine.quality.audit import audit
from crypto_engine.quality.schema import validate_schema
from crypto_engine.quality.staleness import detect_staleness

# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def test_audit_valid_state() -> None:
    state = {
        "schema_version": 1,
        "records": {"a": {"x": 1}, "b": {"y": 2}},
        "checksums": {"a": "abc", "b": "def"},
    }
    result = audit(state)
    assert result["ok"] is True
    assert result["errors"] == []
    assert result["checked"] == 2
    assert "checked_at" in result


def test_audit_missing_required_keys() -> None:
    result = audit({"records": {}})
    assert result["ok"] is False
    assert "missing required key: schema_version" in result["errors"]
    assert "missing required key: checksums" in result["errors"]


def test_audit_record_without_checksum() -> None:
    state = {
        "schema_version": 1,
        "records": {"a": {"x": 1}},
        "checksums": {},
    }
    result = audit(state)
    assert result["ok"] is False
    assert "record 'a' has no checksum" in result["errors"]


def test_audit_checksum_without_record() -> None:
    state = {
        "schema_version": 1,
        "records": {},
        "checksums": {"a": "abc"},
    }
    result = audit(state)
    assert result["ok"] is False
    assert "checksum 'a' has no record" in result["errors"]


def test_audit_empty_record() -> None:
    state = {
        "schema_version": 1,
        "records": {"a": None},
        "checksums": {"a": "abc"},
    }
    result = audit(state)
    assert result["ok"] is False
    assert "record 'a' has empty payload" in result["errors"]


def test_audit_not_a_dict() -> None:
    result = audit("not a dict")  # type: ignore[arg-type]
    assert result["ok"] is False
    assert "state must be a JSON object" in result["errors"]


def test_audit_never_mutates() -> None:
    state = {
        "schema_version": 1,
        "records": {"a": {"x": 1}},
        "checksums": {"a": "abc"},
    }
    before = dict(state)
    audit(state)
    assert state == before


# ---------------------------------------------------------------------------
# validate_schema
# ---------------------------------------------------------------------------


def test_validate_schema_valid() -> None:
    schema = {
        "name": {"type": "str", "required": True},
        "age": {"type": "int", "required": True, "min": 0, "max": 150},
        "active": {"type": "bool", "required": False},
    }
    payload = {"name": "alice", "age": 30, "active": True}
    result = validate_schema(schema, payload)
    assert result["valid"] is True
    assert result["reasons"] == []


def test_validate_schema_missing_required() -> None:
    schema = {"name": {"type": "str", "required": True}}
    result = validate_schema(schema, {})
    assert result["valid"] is False
    assert "missing required field: name" in result["reasons"]


def test_validate_schema_type_mismatch() -> None:
    schema = {"age": {"type": "int", "required": True}}
    result = validate_schema(schema, {"age": "thirty"})
    assert result["valid"] is False
    assert "expected int" in result["reasons"][0]


def test_validate_schema_bool_is_not_int() -> None:
    schema = {"flag": {"type": "int", "required": True}}
    result = validate_schema(schema, {"flag": True})
    assert result["valid"] is False


def test_validate_schema_numeric_bounds() -> None:
    schema = {"score": {"type": "float", "required": True, "min": 0.0, "max": 1.0}}
    result = validate_schema(schema, {"score": 1.5})
    assert result["valid"] is False
    assert "above maximum" in result["reasons"][0]

    result = validate_schema(schema, {"score": -0.1})
    assert result["valid"] is False
    assert "below minimum" in result["reasons"][0]


def test_validate_schema_length_bounds() -> None:
    schema = {
        "code": {"type": "str", "required": True, "min_length": 3, "max_length": 10},
        "tags": {"type": "list", "required": True, "max_length": 5},
    }
    result = validate_schema(schema, {"code": "ab", "tags": [1, 2, 3, 4, 5, 6]})
    assert result["valid"] is False
    assert any("below minimum" in r for r in result["reasons"])
    assert any("above maximum" in r for r in result["reasons"])


def test_validate_schema_optional_field_omitted() -> None:
    schema = {"name": {"type": "str", "required": True}, "nick": {"type": "str"}}
    result = validate_schema(schema, {"name": "alice"})
    assert result["valid"] is True


def test_validate_schema_null_optional_field() -> None:
    schema = {"name": {"type": "str", "required": True}, "nick": {"type": "str"}}
    result = validate_schema(schema, {"name": "alice", "nick": None})
    assert result["valid"] is True


# ---------------------------------------------------------------------------
# detect_staleness
# ---------------------------------------------------------------------------


def test_detect_staleness_current() -> None:
    now = datetime.now(UTC)
    result = detect_staleness(now, max_age_seconds=3600)
    assert result["stale"] is False
    assert result["age_seconds"] is not None
    assert result["age_seconds"] < 3600
    assert result["reason"] == "data is current"


def test_detect_staleness_stale() -> None:
    old = datetime(2020, 1, 1, tzinfo=UTC)
    result = detect_staleness(old, max_age_seconds=3600)
    assert result["stale"] is True
    assert result["age_seconds"] is not None
    assert result["age_seconds"] > 3600
    assert "exceeding" in result["reason"]


def test_detect_staleness_none() -> None:
    result = detect_staleness(None, max_age_seconds=3600)
    assert result["stale"] is True
    assert result["age_seconds"] is None
    assert result["reason"] == "no timestamp provided"


def test_detect_staleness_unparseable() -> None:
    result = detect_staleness("not-a-date", max_age_seconds=3600)
    assert result["stale"] is True
    assert result["reason"] == "unparseable timestamp"


def test_detect_staleness_naive_datetime_rejected() -> None:
    naive = datetime(2026, 1, 1, 0, 0)  # no tzinfo
    result = detect_staleness(naive, max_age_seconds=3600)
    assert result["stale"] is True
    assert result["reason"] == "unparseable timestamp"


def test_detect_staleness_iso_string() -> None:
    result = detect_staleness("2026-10-01T00:00:00+00:00", max_age_seconds=3600)
    assert result["stale"] is True
    assert "2026-10-01T00:00:00+00:00" in result["timestamp"]


def test_detect_staleness_exact_boundary() -> None:
    # Age exactly equals max_age_seconds -> not stale (strictly greater)
    old = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    result = detect_staleness(old, max_age_seconds=999999999)
    assert result["stale"] is False
    assert result["age_seconds"] is not None
    assert result["age_seconds"] < 999999999


__all__ = [
    "test_audit_checksum_without_record",
    "test_audit_empty_record",
    "test_audit_missing_required_keys",
    "test_audit_never_mutates",
    "test_audit_not_a_dict",
    "test_audit_record_without_checksum",
    "test_audit_valid_state",
    "test_detect_staleness_current",
    "test_detect_staleness_exact_boundary",
    "test_detect_staleness_iso_string",
    "test_detect_staleness_naive_datetime_rejected",
    "test_detect_staleness_none",
    "test_detect_staleness_stale",
    "test_detect_staleness_unparseable",
    "test_validate_schema_bool_is_not_int",
    "test_validate_schema_length_bounds",
    "test_validate_schema_missing_required",
    "test_validate_schema_null_optional_field",
    "test_validate_schema_numeric_bounds",
    "test_validate_schema_optional_field_omitted",
    "test_validate_schema_type_mismatch",
    "test_validate_schema_valid",
]
