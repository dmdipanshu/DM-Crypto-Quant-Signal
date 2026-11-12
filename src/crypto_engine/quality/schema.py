"""Schema validation — validate a payload against a declared schema dict.

Pure stdlib. Supports type checks (str, int, float, bool, list, dict),
required fields, and min/max bounds for numbers and lengths.
"""

from __future__ import annotations

from typing import Any


def validate_schema(schema: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Validate ``payload`` against ``schema``.

    Schema entries are dicts with optional keys:
    - ``type``: one of "str", "int", "float", "bool", "list", "dict"
    - ``required``: bool (default False)
    - ``min``/``max``: numeric bounds (int/float types)
    - ``min_length``/``max_length``: length bounds (str/list/dict types)

    Returns a dict with ``valid`` (bool) and ``reasons`` (list of str).
    """
    reasons: list[str] = []

    if not isinstance(schema, dict):
        return {"valid": False, "reasons": ["schema must be a JSON object"]}
    if not isinstance(payload, dict):
        return {"valid": False, "reasons": ["payload must be a JSON object"]}

    for field, rules in schema.items():
        if not isinstance(rules, dict):
            reasons.append(f"schema entry {field!r} must be an object")
            continue

        required = bool(rules.get("required", False))
        expected_type = rules.get("type")
        value = payload.get(field)

        if field not in payload:
            if required:
                reasons.append(f"missing required field: {field}")
            continue

        if value is None:
            if required:
                reasons.append(f"field {field!r} is null but required")
            continue

        if expected_type:
            type_ok = _check_type(value, expected_type)
            if not type_ok:
                reasons.append(
                    f"field {field!r}: expected {expected_type}, got {type(value).__name__}"
                )
                continue

            v_num = _as_number(value)
            if v_num is not None:
                if "min" in rules and v_num < rules["min"]:
                    reasons.append(f"field {field!r}: value {v_num} below minimum {rules['min']}")
                if "max" in rules and v_num > rules["max"]:
                    reasons.append(f"field {field!r}: value {v_num} above maximum {rules['max']}")

            v_len = _as_length(value)
            if v_len is not None:
                if "min_length" in rules and v_len < rules["min_length"]:
                    reasons.append(
                        f"field {field!r}: length {v_len} below minimum {rules['min_length']}"
                    )
                if "max_length" in rules and v_len > rules["max_length"]:
                    reasons.append(
                        f"field {field!r}: length {v_len} above maximum {rules['max_length']}"
                    )

    return {"valid": len(reasons) == 0, "reasons": reasons}


def _check_type(value: Any, expected_type: str) -> bool:
    type_map = {
        "str": str,
        "int": int,
        "float": (int, float),
        "bool": bool,
        "list": list,
        "dict": dict,
    }
    expected = type_map.get(expected_type)
    if expected is None:
        return True  # unknown type: skip check
    # bool is a subclass of int in Python; exclude it from int/float checks
    if expected_type in ("int", "float") and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_length(value: Any) -> int | None:
    if isinstance(value, (str, list, dict)):
        return len(value)
    return None


__all__ = ["validate_schema"]
