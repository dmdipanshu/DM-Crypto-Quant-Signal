"""Closed analyst schemas (Phase 4 Track B).

Every model forbids unknown fields (``extra="forbid"``). A
:class:`ContextScore` is a bounded interpretive lean in ``[-1, 1]`` — it is
NOT a return forecast, NOT a probability, and NOT an edge estimate. It must
never feed a trading rule directly; the ablation ``contextual_veto`` arm
evaluates it only as an abstention policy on folds.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Forbid = ConfigDict(extra="forbid")

AnalystId = Literal["technical", "regime", "news", "macro", "bull", "bear", "judge", "explainer"]
ANALYST_IDS: tuple[str, ...] = (
    "technical",
    "regime",
    "news",
    "macro",
    "bull",
    "bear",
    "judge",
    "explainer",
)

AnalystStatus = Literal["ok", "insufficient_evidence", "disabled", "invalid"]

EvidenceKind = Literal["feature_summary", "regime", "news", "macro", "model_output"]
EvidenceTrust = Literal["quant", "external", "model"]

Verdict = Literal["bull_lean", "bear_lean", "insufficient_evidence"]

CONTEXT_SCORE_DOC = (
    "ContextScore.value is a bounded interpretive lean in [-1, 1]. "
    "It is NOT a return forecast, NOT a probability, and NOT an edge estimate."
)


def _parse_utc(value: str, field: str) -> datetime:
    """Parse an ISO-8601 timestamp; require timezone-aware UTC."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be ISO-8601 UTC (got {value!r}).") from exc
    if dt.tzinfo is None:
        raise ValueError(f"{field} must carry a timezone (got {value!r}).")
    return dt.astimezone(UTC)


def _require_finite_number(value: Any, field: str) -> float:
    """Coerce to float; reject NaN/Inf/non-numeric."""
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric (got {value!r}).") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite (got {value!r}).")
    return number


def _scan_finite(payload: Any, where: str) -> None:
    """Recursively reject NaN/Inf floats anywhere in ``payload``."""
    if isinstance(payload, float):
        if not math.isfinite(payload):
            raise ValueError(f"{where} contains non-finite float {payload!r}; reject.")
    elif isinstance(payload, dict):
        for key, item in payload.items():
            _scan_finite(item, f"{where}[{key!r}]")
    elif isinstance(payload, (list, tuple)):
        for i, item in enumerate(payload):
            _scan_finite(item, f"{where}[{i}]")


class EvidenceItem(BaseModel):
    """One timestamped, provenance-tagged observation."""

    model_config = Forbid

    id: str = Field(min_length=1, max_length=128)
    kind: EvidenceKind
    source: str = Field(min_length=1, max_length=256)
    observed_at: str = Field(min_length=1)
    valid_until: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    trust: EvidenceTrust

    @field_validator("payload")
    @classmethod
    def _payload_bounded_finite(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > 64:
            raise ValueError("payload has > 64 keys; narrow the evidence selection.")
        for key, value in v.items():
            if isinstance(value, str) and len(value) > 8000:
                raise ValueError(f"payload[{key!r}] exceeds 8000 chars; truncate.")
        _scan_finite(v, "payload")
        return v

    @model_validator(mode="after")
    def _timestamps_ordered(self) -> EvidenceItem:
        start = _parse_utc(self.observed_at, "observed_at")
        if self.valid_until is not None:
            end = _parse_utc(self.valid_until, "valid_until")
            if end < start:
                raise ValueError("valid_until precedes observed_at.")
        return self

    def observed_dt(self) -> datetime:
        """``observed_at`` as an aware UTC datetime."""
        return _parse_utc(self.observed_at, "observed_at")


class ContextScore(BaseModel):
    """Bounded interpretive lean — NOT a forecast/probability/edge."""

    model_config = Forbid

    value: float
    evidence_ids: list[str] = Field(default_factory=list)
    source_ts: str = Field(min_length=1)
    analysis_ts: str = Field(min_length=1)
    expires_at: str | None = None
    missing_evidence: bool = False
    validation: Literal["schema_ok", "insufficient", "disabled"] = "schema_ok"
    explanation: str = Field(min_length=1, max_length=1000)
    provider: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)

    @field_validator("value")
    @classmethod
    def _score_bounded(cls, v: float) -> float:
        number = _require_finite_number(v, "value")
        if not -1.0 <= number <= 1.0:
            raise ValueError(f"value must lie in [-1, 1] (got {v!r}).")
        return number

    @model_validator(mode="after")
    def _score_timestamps(self) -> ContextScore:
        _parse_utc(self.source_ts, "source_ts")
        _parse_utc(self.analysis_ts, "analysis_ts")
        if self.expires_at is not None:
            _parse_utc(self.expires_at, "expires_at")
        return self


class AnalystOutput(BaseModel):
    """Validated result of one analyst over supplied evidence."""

    model_config = Forbid

    analyst_id: AnalystId
    status: AnalystStatus
    summary: str = Field(min_length=1, max_length=2000)
    score: ContextScore | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provider: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)
    correlation_id: str = Field(default="", max_length=128)


class JudgeOutput(BaseModel):
    """Bull-vs-bear comparison. Verdict is a lean only — never a trade direction."""

    model_config = Forbid

    verdict: Verdict
    agreement: bool
    disagreement_points: list[str] = Field(default_factory=list)
    model_disagreement_notes: str = Field(default="", max_length=1000)


__all__ = [
    "ANALYST_IDS",
    "CONTEXT_SCORE_DOC",
    "AnalystId",
    "AnalystOutput",
    "AnalystStatus",
    "ContextScore",
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceTrust",
    "JudgeOutput",
    "Verdict",
]
