"""Canonical signal schema (Phase 5 Track A).

Label semantics (triple-barrier convention, see labels/triple_barrier.py):
``+1`` means the profit barrier was hit for long geometry (up move) and
``-1`` means the stop barrier was hit; short geometry mirrors this
(profit on downward moves). ``0`` covers timeout, ambiguous same-bar
touches, and incomplete horizons. Policy maps model labels to
direction via ``label_convention="long_up"``: ``+1`` is favorable-up
(LONG), ``-1`` is favorable-down (SHORT), ``0`` is neutral.

Units: all cost/edge quantities are basis points (bp); field names
carry the ``_bp`` suffix. Never mix decimals (e.g. 0.0015) into
``_bp`` fields — values >= 10000 bp are rejected as likely
decimal-mixup. Probability (``[0, 1]``) is NOT profitability:
calibrated ``P(y == positive_class)`` says nothing about net P&L
after costs; edge (expected bp net of costs) is a separate,
explicitly-provided estimate, never derived from confidence.

4 GB laptop: single-process, one symbol/timeframe, small fixtures,
no network, sklearn/pandas/numpy only.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Direction = Literal["LONG", "SHORT", "NEUTRAL", "NO_TRADE"]
CalibStatus = Literal["calibrated", "uncalibrated", "unavailable", "mismatch"]
DecisionStatus = Literal["approved", "abstained", "rejected"]
CostSource = Literal["configured", "walkforward-proxy", "exchange"]
AiStatus = Literal["disabled", "ok", "invalid", "over_budget", "unavailable"]
DataQuality = Literal["validated", "stale", "incomplete", "invalid"]

SCHEMA_VERSION = "1.0"

REASON_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]+$")

# Documented reason-code vocabulary (Track B appends RISK_GATE_* codes).
OK = "OK"
MODEL_UNFITTED = "MODEL_UNFITTED"
ARTIFACT_MISSING = "ARTIFACT_MISSING"
ARTIFACT_CORRUPT = "ARTIFACT_CORRUPT"
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
INVALID_OUTPUT = "INVALID_OUTPUT"
NON_FINITE = "NON_FINITE"
STALE_DATA = "STALE_DATA"
INCOMPLETE_DATA = "INCOMPLETE_DATA"
INVALID_TIMESTAMPS = "INVALID_TIMESTAMPS"
CALIBRATION_MISMATCH = "CALIBRATION_MISMATCH"
UNCALIBRATED = "UNCALIBRATED"
RULE_BASED_NO_PROBABILITY = "RULE_BASED_NO_PROBABILITY"
THRESHOLD_NOT_MET = "THRESHOLD_NOT_MET"
INSUFFICIENT_EDGE = "INSUFFICIENT_EDGE"
EDGE_UNAVAILABLE = "EDGE_UNAVAILABLE"
COST_INPUTS_MISSING = "COST_INPUTS_MISSING"
DIRECTION_DISABLED = "DIRECTION_DISABLED"
REGIME_BLOCKED = "REGIME_BLOCKED"
SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
HORIZON_INVALID = "HORIZON_INVALID"
AI_REQUIRED_UNAVAILABLE = "AI_REQUIRED_UNAVAILABLE"

REASON_CODES: tuple[str, ...] = (
    OK,
    MODEL_UNFITTED,
    ARTIFACT_MISSING,
    ARTIFACT_CORRUPT,
    SCHEMA_MISMATCH,
    INVALID_OUTPUT,
    NON_FINITE,
    STALE_DATA,
    INCOMPLETE_DATA,
    INVALID_TIMESTAMPS,
    CALIBRATION_MISMATCH,
    UNCALIBRATED,
    RULE_BASED_NO_PROBABILITY,
    THRESHOLD_NOT_MET,
    INSUFFICIENT_EDGE,
    EDGE_UNAVAILABLE,
    COST_INPUTS_MISSING,
    DIRECTION_DISABLED,
    REGIME_BLOCKED,
    SIGNAL_EXPIRED,
    HORIZON_INVALID,
    AI_REQUIRED_UNAVAILABLE,
)

RULE_BASED_FAMILIES: tuple[str, ...] = ("trend", "majority")

_BP_CAP = 10000.0


def _require_finite_unit(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    v = float(value)
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {v!r}.")
    return v


def _require_bp(value: float, name: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite, got {value!r}.")
    v = float(value)
    if v < 0:
        raise ValueError(f"{name} must be >= 0, got {v!r}.")
    if v >= _BP_CAP:
        raise ValueError(
            f"{name}={v} bp >= 10000 bp; likely decimal-mixup "
            "(1 bp = 0.01%; pass basis points, not decimals)."
        )
    return v


class PredictionInfo(BaseModel):
    """Model output for one decision row (probability != profitability)."""

    model_config = ConfigDict(extra="forbid")

    raw_score: float | None = None
    calibrated_probability: float | None = None
    calibration_status: CalibStatus = "unavailable"
    positive_class: int | None = None
    model_family: str = ""
    threshold_version: str = "v1"

    @field_validator("raw_score", "calibrated_probability", mode="before")
    @classmethod
    def _check_unit(cls, v: object) -> object:
        if v is None:
            return None
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"probability must be numeric, got {v!r}.") from exc
        if not math.isfinite(f):
            raise ValueError(f"probability must be finite, got {v!r}.")
        if not 0.0 <= f <= 1.0:
            raise ValueError(f"probability must be in [0, 1], got {f!r}.")
        return f

    @model_validator(mode="after")
    def _check_calibrated_consistency(self) -> PredictionInfo:
        if self.calibration_status != "calibrated" and self.calibrated_probability is not None:
            raise ValueError(
                "calibrated_probability must be None unless "
                f"calibration_status == 'calibrated' (got {self.calibration_status!r})."
            )
        return self


class CostEstimate(BaseModel):
    """Round-trip cost estimate in basis points (estimate, not fills)."""

    model_config = ConfigDict(extra="forbid")

    fee_bp: float = 0.0
    spread_bp: float = 0.0
    slippage_bp: float = 0.0
    funding_bp: float = 0.0
    cost_bp_total: float = 0.0
    source: CostSource = "configured"
    expected_edge_bp: float | None = None
    edge_margin_bp: float = 0.0

    @field_validator(
        "fee_bp", "spread_bp", "slippage_bp", "funding_bp", "edge_margin_bp", mode="before"
    )
    @classmethod
    def _check_bp(cls, v: object) -> object:
        if v is None:
            return 0.0
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bp value must be numeric, got {v!r}.") from exc
        return _require_bp(f, "bp field")

    @field_validator("expected_edge_bp", mode="before")
    @classmethod
    def _check_edge(cls, v: object) -> object:
        if v is None:
            return None
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected_edge_bp must be numeric, got {v!r}.") from exc
        if not math.isfinite(f):
            raise ValueError(f"expected_edge_bp must be finite, got {v!r}.")
        if abs(f) >= _BP_CAP:
            raise ValueError(
                f"expected_edge_bp={f} bp magnitude >= 10000 bp; likely decimal-mixup."
            )
        return f

    @model_validator(mode="after")
    def _compute_total(self) -> CostEstimate:
        total = float(self.fee_bp) + float(self.spread_bp)
        total += float(self.slippage_bp) + float(self.funding_bp)
        object.__setattr__(self, "cost_bp_total", total)
        return self


def check_direction_geometry(
    direction: str, entry: float | None, stop: float | None, take: float | None
) -> None:
    """Validate level geometry for a direction; raises ValueError on violation."""
    if direction in ("NEUTRAL", "NO_TRADE"):
        if entry is not None or stop is not None or take is not None:
            raise ValueError(
                f"{direction} must have all levels None "
                f"(got entry={entry!r}, stop={stop!r}, take={take!r})."
            )
        return
    for name, val in (("entry_ref", entry), ("stop_loss", stop), ("take_profit", take)):
        if val is not None and (not math.isfinite(float(val)) or float(val) <= 0):
            raise ValueError(f"{name} must be > 0 and finite, got {val!r}.")
    if direction == "LONG":
        if stop is not None and entry is not None and not stop < entry:
            raise ValueError(f"LONG requires stop_loss < entry_ref ({stop!r} vs {entry!r}).")
        if take is not None and entry is not None and not take > entry:
            raise ValueError(f"LONG requires take_profit > entry_ref ({take!r} vs {entry!r}).")
    elif direction == "SHORT":
        if stop is not None and entry is not None and not stop > entry:
            raise ValueError(f"SHORT requires stop_loss > entry_ref ({stop!r} vs {entry!r}).")
        if take is not None and entry is not None and not take < entry:
            raise ValueError(f"SHORT requires take_profit < entry_ref ({take!r} vs {entry!r}).")


class SignalLevels(BaseModel):
    """Entry/stop/take bundle with direction geometry (estimate-only)."""

    model_config = ConfigDict(extra="forbid")

    direction: Direction = "NO_TRADE"
    entry_ref: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None

    @model_validator(mode="after")
    def _check_geometry(self) -> SignalLevels:
        check_direction_geometry(self.direction, self.entry_ref, self.stop_loss, self.take_profit)
        if self.risk_reward is not None and (
            not math.isfinite(float(self.risk_reward)) or float(self.risk_reward) <= 0
        ):
            raise ValueError(f"risk_reward must be > 0, got {self.risk_reward!r}.")
        return self


class AiContextSummary(BaseModel):
    """Advisory-only AI context (recorded, never overrides direction/risk)."""

    model_config = ConfigDict(extra="forbid")

    status: AiStatus = "disabled"
    provider_id: str = ""
    model_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    generated_at: str | None = None
    expiry: str | None = None
    note: str = ""
    summary: str = ""

    @field_validator("note")
    @classmethod
    def _check_note(cls, v: str) -> str:
        if len(v) > 500:
            raise ValueError(f"note must be <= 500 chars (got {len(v)}).")
        lowered = v.lower()
        for banned in ("prompt", "secret", "api_key", "apikey", "bearer"):
            if banned in lowered:
                raise ValueError(f"note must not contain prompts/secrets (found {banned!r}).")
        return v

    @field_validator("summary")
    @classmethod
    def _check_summary(cls, v: str) -> str:
        if len(v) > 2000:
            raise ValueError(f"summary must be <= 2000 chars (got {len(v)}).")
        lowered = v.lower()
        for banned in ("prompt", "secret", "api_key", "apikey", "bearer"):
            if banned in lowered:
                raise ValueError(f"summary must not contain prompts/secrets (found {banned!r}).")
        return v


class DecisionInfo(BaseModel):
    """Policy/risk decision with non-empty reason codes and deciding gate."""

    model_config = ConfigDict(extra="forbid")

    status: DecisionStatus = "abstained"
    reason_codes: list[str] = Field(default_factory=list)
    gate: str = "policy"

    @field_validator("reason_codes")
    @classmethod
    def _check_reasons(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("reason_codes must be non-empty.")
        for code in v:
            if not isinstance(code, str) or not REASON_PATTERN.match(code):
                raise ValueError(f"reason code must match ^[A-Z][A-Z0-9_]+$, got {code!r}.")
        return list(v)


class SignalRecord(BaseModel):
    """Canonical validated trading signal (NO_TRADE is first-class)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    signal_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    symbol: str = ""
    exchange: str = ""
    timeframe: str = ""
    generated_at: datetime
    valid_until: datetime
    direction: Direction = "NO_TRADE"
    prediction_horizon_bars: int = Field(ge=1)
    model_id: str = ""
    model_version: str = ""
    model_artifact_hash: str = ""
    dataset_snapshot_id: str = ""
    feature_snapshot_hash: str = ""
    prediction: PredictionInfo = Field(default_factory=PredictionInfo)
    market_regime: str = ""
    expected_edge_bp: float | None = None
    cost_estimate: CostEstimate | None = None
    entry_ref: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    risk_reward: float | None = None
    data_quality: DataQuality = "validated"
    ai_context: AiContextSummary = Field(default_factory=AiContextSummary)
    decision: DecisionInfo = Field(default_factory=DecisionInfo)
    software_version: str = ""

    @field_validator("generated_at", "valid_until", mode="before")
    @classmethod
    def _parse_ts(cls, v: object) -> object:
        if isinstance(v, datetime):
            dt = v
        elif isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"invalid ISO timestamp {v!r}: {exc}") from exc
        else:
            raise ValueError(f"timestamp must be ISO string, got {v!r}.")
        if dt.tzinfo is None:
            raise ValueError(f"timestamp must be timezone-aware (UTC), got {v!r}.")
        return dt

    @field_validator("expected_edge_bp", mode="before")
    @classmethod
    def _check_edge_record(cls, v: object) -> object:
        if v is None:
            return None
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"expected_edge_bp must be numeric, got {v!r}.") from exc
        if not math.isfinite(f):
            raise ValueError(f"expected_edge_bp must be finite, got {v!r}.")
        if abs(f) >= _BP_CAP:
            raise ValueError("expected_edge_bp magnitude >= 10000 bp; likely decimal-mixup.")
        return f

    @model_validator(mode="after")
    def _check_record(self) -> SignalRecord:
        if self.valid_until <= self.generated_at:
            raise ValueError("valid_until must be > generated_at.")
        check_direction_geometry(self.direction, self.entry_ref, self.stop_loss, self.take_profit)
        if self.direction == "NO_TRADE" and self.decision.status == "approved":
            raise ValueError("NO_TRADE direction cannot have decision status 'approved'.")
        return self


__all__ = [
    "AI_REQUIRED_UNAVAILABLE",
    "ARTIFACT_CORRUPT",
    "ARTIFACT_MISSING",
    "CALIBRATION_MISMATCH",
    "COST_INPUTS_MISSING",
    "DIRECTION_DISABLED",
    "EDGE_UNAVAILABLE",
    "HORIZON_INVALID",
    "INCOMPLETE_DATA",
    "INSUFFICIENT_EDGE",
    "INVALID_OUTPUT",
    "INVALID_TIMESTAMPS",
    "MODEL_UNFITTED",
    "NON_FINITE",
    "OK",
    "REASON_CODES",
    "REASON_PATTERN",
    "REGIME_BLOCKED",
    "RULE_BASED_FAMILIES",
    "RULE_BASED_NO_PROBABILITY",
    "SCHEMA_MISMATCH",
    "SCHEMA_VERSION",
    "SIGNAL_EXPIRED",
    "STALE_DATA",
    "THRESHOLD_NOT_MET",
    "UNCALIBRATED",
    "AiContextSummary",
    "AiStatus",
    "CalibStatus",
    "CostEstimate",
    "CostSource",
    "DataQuality",
    "DecisionInfo",
    "DecisionStatus",
    "Direction",
    "PredictionInfo",
    "SignalLevels",
    "SignalRecord",
    "check_direction_geometry",
]
