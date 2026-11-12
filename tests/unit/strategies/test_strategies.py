"""Strategies unit tests (Stage 6).

Offline tests for StrategyMetadata, StrategyVersion, StrategyParameter,
and compose_signal().
"""

from __future__ import annotations

import pytest

from crypto_engine.domain.direction import Direction
from crypto_engine.domain.reason_codes import ReasonCode
from crypto_engine.strategies.composition import compose_signal
from crypto_engine.strategies.metadata import (
    StrategyMetadata,
    StrategyParameter,
    StrategyVersion,
)

# ---------------------------------------------------------------------------
# StrategyVersion
# ---------------------------------------------------------------------------


def test_strategy_version_creation() -> None:
    v = StrategyVersion(1, 2, 3)
    assert v.major == 1
    assert v.minor == 2
    assert v.patch == 3
    assert v.tag == ""
    assert str(v) == "1.2.3"


def test_strategy_version_with_tag() -> None:
    v = StrategyVersion(1, 0, 0, tag="rc1")
    assert str(v) == "1.0.0-rc1"


def test_strategy_version_negative_rejected() -> None:
    with pytest.raises(ValueError):
        StrategyVersion(-1, 0, 0)
    with pytest.raises(ValueError):
        StrategyVersion(0, -1, 0)
    with pytest.raises(ValueError):
        StrategyVersion(0, 0, -1)


def test_strategy_version_to_tuple() -> None:
    v = StrategyVersion(2, 5, 1, tag="beta")
    assert v.to_tuple() == (2, 5, 1, "beta")


# ---------------------------------------------------------------------------
# StrategyParameter
# ---------------------------------------------------------------------------


def test_strategy_parameter_int_validation() -> None:
    p = StrategyParameter("window", "int", 10, min_value=1, max_value=100)
    assert p.validate(50) == 50
    assert p.validate(1) == 1
    assert p.validate(100) == 100


def test_strategy_parameter_int_bounds_rejected() -> None:
    p = StrategyParameter("window", "int", 10, min_value=1, max_value=100)
    with pytest.raises(ValueError):
        p.validate(0)
    with pytest.raises(ValueError):
        p.validate(101)
    with pytest.raises(ValueError):
        p.validate(True)  # bool is not int


def test_strategy_parameter_float_validation() -> None:
    p = StrategyParameter("threshold", "float", 0.5, min_value=0.0, max_value=1.0)
    assert p.validate(0.75) == 0.75
    assert p.validate(0) == 0.0
    assert p.validate(1) == 1.0


def test_strategy_parameter_str_validation() -> None:
    p = StrategyParameter("mode", "str", "strict")
    assert p.validate("lenient") == "lenient"
    with pytest.raises(ValueError):
        p.validate(123)


def test_strategy_parameter_bool_validation() -> None:
    p = StrategyParameter("enabled", "bool", True)
    assert p.validate(False) is False
    assert p.validate(True) is True
    with pytest.raises(ValueError):
        p.validate(1)


def test_strategy_parameter_default_used() -> None:
    p = StrategyParameter("window", "int", 42)
    # validate() rejects None (not an int); defaults are applied by metadata.
    with pytest.raises(ValueError):
        p.validate(None)


# ---------------------------------------------------------------------------
# StrategyMetadata
# ---------------------------------------------------------------------------


def test_strategy_metadata_strategy_id() -> None:
    meta = StrategyMetadata(
        name="ema_cross",
        version=StrategyVersion(1, 0, 0),
        parameters=(
            StrategyParameter("fast", "int", 10, min_value=1, max_value=100),
            StrategyParameter("slow", "int", 30, min_value=1, max_value=200),
        ),
    )
    sid = meta.strategy_id()
    assert sid.startswith("strat-")
    assert len(sid) == 22  # "strat-" + 16 hex chars


def test_strategy_metadata_deterministic_id() -> None:
    params = (
        StrategyParameter("a", "int", 1),
        StrategyParameter("b", "int", 2),
    )
    meta1 = StrategyMetadata("s", StrategyVersion(1, 0, 0), params)
    meta2 = StrategyMetadata("s", StrategyVersion(1, 0, 0), params)
    assert meta1.strategy_id() == meta2.strategy_id()


def test_strategy_metadata_different_params_different_id() -> None:
    meta1 = StrategyMetadata("s", StrategyVersion(1, 0, 0), (StrategyParameter("a", "int", 1),))
    meta2 = StrategyMetadata("s", StrategyVersion(1, 0, 0), (StrategyParameter("a", "int", 2),))
    assert meta1.strategy_id() != meta2.strategy_id()


def test_strategy_metadata_validate_parameters() -> None:
    meta = StrategyMetadata(
        "s",
        StrategyVersion(1, 0, 0),
        (StrategyParameter("window", "int", 10, min_value=1, max_value=100),),
    )
    validated = meta.validate_parameters({"window": 50})
    assert validated == {"window": 50}


def test_strategy_metadata_validate_parameters_uses_default() -> None:
    meta = StrategyMetadata(
        "s",
        StrategyVersion(1, 0, 0),
        (StrategyParameter("window", "int", 42, min_value=1, max_value=100),),
    )
    validated = meta.validate_parameters({})
    assert validated == {"window": 42}


def test_strategy_metadata_validate_parameters_rejects_bad_value() -> None:
    meta = StrategyMetadata(
        "s",
        StrategyVersion(1, 0, 0),
        (StrategyParameter("window", "int", 10, min_value=1, max_value=100),),
    )
    with pytest.raises(ValueError):
        meta.validate_parameters({"window": 999})


# ---------------------------------------------------------------------------
# compose_signal
# ---------------------------------------------------------------------------


def test_compose_signal_no_outputs() -> None:
    result = compose_signal([])
    assert result.direction == Direction.NO_TRADE
    assert ReasonCode.INSUFFICIENT_DATA in result.reason_codes
    assert result.contributing_strategies == ()


def test_compose_signal_all_below_confidence() -> None:
    outputs = [
        _output("s1", Direction.LONG, 0.1),
        _output("s2", Direction.SHORT, 0.05),
    ]
    result = compose_signal(outputs, min_confidence=0.5)
    assert result.direction == Direction.NO_TRADE
    assert ReasonCode.INSUFFICIENT_EDGE in result.reason_codes


def test_compose_signal_single_strategy() -> None:
    outputs = [_output("s1", Direction.LONG, 0.8)]
    result = compose_signal(outputs)
    assert result.direction == Direction.LONG
    # Single strategy: confidence normalized to 1.0 (0.8 / 0.8)
    assert result.confidence == 1.0
    assert result.contributing_strategies == ("s1",)


def test_compose_signal_weighted_direction() -> None:
    outputs = [
        _output("s1", Direction.LONG, 0.9),
        _output("s2", Direction.SHORT, 0.3),
    ]
    result = compose_signal(outputs)
    assert result.direction == Direction.LONG
    # LONG weight: 0.9, SHORT weight: 0.3; confidence = 0.9 / 1.2 = 0.75
    assert result.confidence == pytest.approx(0.75, rel=1e-2)


def test_compose_signal_require_unanimous_agree() -> None:
    outputs = [
        _output("s1", Direction.LONG, 0.8),
        _output("s2", Direction.LONG, 0.7),
    ]
    result = compose_signal(outputs, require_unanimous=True)
    assert result.direction == Direction.LONG


def test_compose_signal_require_unanimous_disagree() -> None:
    outputs = [
        _output("s1", Direction.LONG, 0.8),
        _output("s2", Direction.SHORT, 0.7),
    ]
    result = compose_signal(outputs, require_unanimous=True)
    assert result.direction == Direction.NO_TRADE
    assert ReasonCode.DISAGREEMENT in result.reason_codes


def test_compose_signal_reason_codes_deduplicated() -> None:
    outputs = [
        _output("s1", Direction.LONG, 0.8, reason_codes=("QUALITY_FAIL", "STALE_DATA")),
        _output("s2", Direction.LONG, 0.7, reason_codes=("QUALITY_FAIL", "INSUFFICIENT_DATA")),
    ]
    result = compose_signal(outputs)
    assert result.reason_codes == ("QUALITY_FAIL", "STALE_DATA", "INSUFFICIENT_DATA")


def _output(
    strategy_id: str, direction: Direction, confidence: float, reason_codes: tuple[str, ...] = ()
) -> object:
    from crypto_engine.strategies.composition import StrategyOutput

    return StrategyOutput(
        strategy_id=strategy_id,
        direction=direction,
        confidence=confidence,
        reason_codes=reason_codes,
    )


__all__ = [
    "test_compose_signal_all_below_confidence",
    "test_compose_signal_no_outputs",
    "test_compose_signal_reason_codes_deduplicated",
    "test_compose_signal_require_unanimous_agree",
    "test_compose_signal_require_unanimous_disagree",
    "test_compose_signal_single_strategy",
    "test_compose_signal_weighted_direction",
    "test_strategy_metadata_deterministic_id",
    "test_strategy_metadata_different_params_different_id",
    "test_strategy_metadata_strategy_id",
    "test_strategy_metadata_validate_parameters",
    "test_strategy_metadata_validate_parameters_rejects_bad_value",
    "test_strategy_metadata_validate_parameters_uses_default",
    "test_strategy_parameter_bool_validation",
    "test_strategy_parameter_default_used",
    "test_strategy_parameter_float_validation",
    "test_strategy_parameter_int_bounds_rejected",
    "test_strategy_parameter_int_validation",
    "test_strategy_parameter_str_validation",
    "test_strategy_version_creation",
    "test_strategy_version_negative_rejected",
    "test_strategy_version_to_tuple",
    "test_strategy_version_with_tag",
]
