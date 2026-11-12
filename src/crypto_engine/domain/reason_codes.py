"""ReasonCode — closed vocabulary for signal abstention and rejection reasons.

Every NO_TRADE or rejected signal carries a reason code from this closed
vocabulary. Pure stdlib: no third-party imports.
"""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    """Closed reason-code vocabulary.

    Data tier (A):
    - STALE_DATA: dataset too old at consumption time
    - INSUFFICIENT_DATA: not enough bars for evaluation
    - QUALITY_FAIL: dataset quality verdict is FAIL
    - CONFLICTING_DATA: conflicting duplicate rows detected

    Model tier (B):
    - ARTIFACT_MISSING: no registered model artifact
    - SCHEMA_MISMATCH: feature schema hash mismatch
    - MODEL_OUTPUT_INVALID: model produced unusable output
    - UNCALIBRATED: model score is uncalibrated and calibration is required

    Decision tier (C):
    - DISAGREEMENT: strategies disagree beyond threshold
    - EDGE_UNAVAILABLE: expected edge could not be calculated
    - INSUFFICIENT_EDGE: edge below cost + margin
    - SIGNAL_TOO_OLD: signal age exceeds max_signal_age_min
    - HORIZON_INVALID: prediction horizon invalid
    - REGIME_VETO: current regime vetoes the direction
    - AI_VETO: AI advisory veto (advisory only, never overrides)

    Exposure tier (D):
    - TRADE_RISK_EXCEEDED: per-trade risk above limit
    - EXPOSURE_UNKNOWN: portfolio exposure cannot be determined
    - EXPOSURE_EXCEEDED: position would exceed max_position_pct
    - DRAWDOWN_KILLSWITCH: drawdown above max_drawdown_pct
    - STRATEGY_HALT: strategy is halted
    - CIRCUIT_BREAKER: circuit breaker tripped
    """

    # Data tier
    STALE_DATA = "STALE_DATA"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    QUALITY_FAIL = "QUALITY_FAIL"
    CONFLICTING_DATA = "CONFLICTING_DATA"

    # Model tier
    ARTIFACT_MISSING = "ARTIFACT_MISSING"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    UNCALIBRATED = "UNCALIBRATED"

    # Decision tier
    DISAGREEMENT = "DISAGREEMENT"
    EDGE_UNAVAILABLE = "EDGE_UNAVAILABLE"
    INSUFFICIENT_EDGE = "INSUFFICIENT_EDGE"
    SIGNAL_TOO_OLD = "SIGNAL_TOO_OLD"
    HORIZON_INVALID = "HORIZON_INVALID"
    REGIME_VETO = "REGIME_VETO"
    AI_VETO = "AI_VETO"

    # Exposure tier
    TRADE_RISK_EXCEEDED = "TRADE_RISK_EXCEEDED"
    EXPOSURE_UNKNOWN = "EXPOSURE_UNKNOWN"
    EXPOSURE_EXCEEDED = "EXPOSURE_EXCEEDED"
    DRAWDOWN_KILLSWITCH = "DRAWDOWN_KILLSWITCH"
    STRATEGY_HALT = "STRATEGY_HALT"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"

    @classmethod
    def all_codes(cls) -> list[str]:
        """Return all reason codes in the closed vocabulary."""
        return [code.value for code in cls]


__all__ = ["ReasonCode"]
