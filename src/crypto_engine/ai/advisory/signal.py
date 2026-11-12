"""Bounded AI explanation for deterministic signal records (Stage 3).

Flow::

    SignalRecord -> bounded evidence -> AI gateway -> AIExplanation
                                                      |
                                            apply_ai_context()
                                                      |
                                      SignalRecord.ai_context only

Authority boundary: the AI explanation is frozen, advisory-only data. It has no
direction, score, risk, position-size, approval, execution or ledger field, and
:func:`apply_ai_context` updates **only** ``SignalRecord.ai_context``. If AI is
disabled, unavailable or returns junk, the deterministic signal is returned
unchanged and the explanation simply becomes unavailable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from crypto_engine.ai.contracts import (
    DEFAULT_MAX_REQUEST_CHARS,
    MAX_EVIDENCE_KEYS,
    MAX_EVIDENCE_VALUE_CHARS,
    AIRequest,
    AITask,
)
from crypto_engine.ai.errors import AIError, AIStatus, classify_exception, redact_secrets
from crypto_engine.ai.gateway import AIGateway
from crypto_engine.signals.schema import AiContextSummary, SignalRecord

SIGNAL_EXPLANATION_SCHEMA = "signal_explanation.v1"

#: Constant, application-owned instruction. Never built from record content.
SIGNAL_INSTRUCTION = (
    "Explain the supplied deterministic signal record in plain language. "
    "Cover exactly these points: "
    "(1) the symbol and timeframe (for example BTC/USDT on 1h); "
    "(2) the raw prediction score and whether calibration is available or unavailable; "
    "(3) the signal direction (for example NO_TRADE); "
    "(4) the decision reason codes (for example UNCALIBRATED, RISK_GATE_EXPOSURE); "
    "(5) what the calibration and risk restrictions mean, without inventing information; "
    "(6) an advisory-only disclaimer stating this is not a trading decision. "
    "State only what the evidence shows. Name supporting and caution factors, "
    "and describe uncertainty. Do not recommend trades, do not invent metrics, "
    "and never claim trading, risk or execution authority. "
    "End with a complete sentence; do not stop mid-sentence."
)

MAX_SUMMARY_CHARS = 2_000
MAX_FACTOR_CHARS = 300
MAX_FACTORS = 8
MAX_UNCERTAINTY_CHARS = 500
MAX_NOTE_CHARS = 500
MAX_EVIDENCE_FIELD_CHARS = min(MAX_EVIDENCE_VALUE_CHARS, 1_200)
DEFAULT_EVIDENCE_BUDGET = DEFAULT_MAX_REQUEST_CHARS - len(SIGNAL_INSTRUCTION) - 256

#: Top-level response keys that would assert authority. Their presence makes
#: the whole advisory response invalid; it is never partially accepted.
AUTHORITY_KEYS = frozenset(
    {
        "approved",
        "approval",
        "candidate_for_paper_testing",
        "direction",
        "execute",
        "execution",
        "order",
        "position_size",
        "promote",
        "promotion",
        "quantity",
        "risk",
        "risk_decision",
        "score",
        "signal_direction",
        "signal_score",
        "trade",
    }
)

_SECRET_WORDS = ("prompt", "secret", "api_key", "apikey", "bearer")


@dataclass(frozen=True, slots=True)
class AIExplanation:
    """Bounded, advisory-only explanation. No trading authority fields exist."""

    status: AIStatus = AIStatus.DISABLED
    summary: str = ""
    supporting_factors: tuple[str, ...] = ()
    caution_factors: tuple[str, ...] = ()
    uncertainty: str = ""
    provider_id: str | None = None
    model_id: str | None = None
    generated_at: str = ""
    note: str = ""
    advisory_only: bool = True

    def __post_init__(self) -> None:
        if not self.advisory_only:
            raise ValueError("AI explanations are advisory only and cannot be authoritative")
        object.__setattr__(self, "status", AIStatus(self.status))
        object.__setattr__(self, "summary", _clean_text(self.summary, MAX_SUMMARY_CHARS))
        object.__setattr__(self, "supporting_factors", _clean_factors(self.supporting_factors))
        object.__setattr__(self, "caution_factors", _clean_factors(self.caution_factors))
        object.__setattr__(
            self, "uncertainty", _clean_text(self.uncertainty, MAX_UNCERTAINTY_CHARS)
        )
        object.__setattr__(self, "note", _clean_text(self.note, MAX_NOTE_CHARS))
        if not self.generated_at:
            object.__setattr__(
                self,
                "generated_at",
                datetime.now(UTC).isoformat(timespec="seconds"),
            )

    @property
    def ok(self) -> bool:
        return self.status is AIStatus.OK

    def to_dict(self) -> dict[str, Any]:
        """Deterministic, secret-free view for CLI/API/report output."""
        return {
            "status": self.status.value,
            "summary": self.summary,
            "supporting_factors": list(self.supporting_factors),
            "caution_factors": list(self.caution_factors),
            "uncertainty": self.uncertainty,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "generated_at": self.generated_at,
            "note": self.note,
            "advisory_only": self.advisory_only,
        }


def context_status(status: AIStatus | str) -> str:
    """Map the closed AI vocabulary onto ``AiContextSummary.status``."""
    resolved = AIStatus(status)
    if resolved is AIStatus.OK:
        return "ok"
    if resolved in {AIStatus.DISABLED, AIStatus.NOT_CONFIGURED}:
        return "disabled"
    if resolved is AIStatus.BUDGET_EXHAUSTED:
        return "over_budget"
    if resolved in {
        AIStatus.INVALID_REQUEST,
        AIStatus.INVALID_CONFIGURATION,
        AIStatus.INVALID_RESPONSE,
        AIStatus.UNSUPPORTED_CAPABILITY,
    }:
        return "invalid"
    return "unavailable"


def build_signal_evidence(
    record: SignalRecord,
    *,
    max_total_chars: int | None = None,
) -> dict[str, str]:
    """Build a bounded, deterministic, secret-free evidence snapshot.

    Values are priority ordered; the lowest-priority fields are dropped first
    when the character budget is exceeded. Truncation is explicit (marker keys),
    never silent. The result always respects ``MAX_EVIDENCE_KEYS`` and
    ``MAX_EVIDENCE_VALUE_CHARS``.
    """
    if not isinstance(record, SignalRecord):
        raise TypeError("build_signal_evidence() requires a SignalRecord")
    budget = DEFAULT_EVIDENCE_BUDGET if max_total_chars is None else int(max_total_chars)
    if budget < 128:
        raise ValueError("max_total_chars must be at least 128")

    items: list[tuple[str, str]] = []
    for key, raw in _record_fields(record):
        value = _clean_text(raw, MAX_EVIDENCE_FIELD_CHARS)
        if value:
            items.append((key, value))

    items = items[:MAX_EVIDENCE_KEYS]
    reserve = 64
    fit_budget = max(128, budget - reserve)
    dropped = 0
    while items and _items_size(items) > fit_budget:
        if len(items) > 1:
            items.pop()
            dropped += 1
        else:
            key, value = items[0]
            room = max(0, fit_budget - len(key))
            items[0] = (key, value[:room])
            break
    if dropped:
        items.extend([("evidence_truncated", "true"), ("evidence_keys_dropped", str(dropped))])
    return dict(items)


def explain_signal(
    record: SignalRecord,
    gateway: AIGateway | None = None,
    *,
    task: AITask | str = AITask.SIGNAL_EXPLANATION,
    generated_at: str | None = None,
    max_total_chars: int | None = None,
) -> AIExplanation:
    """Explain one signal; **never** raises provider/transport failures.

    ``gateway=None`` means AI is not wired: the result is ``DISABLED`` and the
    deterministic signal is untouched.
    """
    if not isinstance(record, SignalRecord):
        raise TypeError("explain_signal() requires a SignalRecord")
    resolved_task = AITask(task)
    stamp = generated_at or datetime.now(UTC).isoformat(timespec="seconds")
    evidence = build_signal_evidence(record, max_total_chars=max_total_chars)

    if gateway is None:
        return AIExplanation(
            status=AIStatus.DISABLED,
            generated_at=stamp,
            note="AI gateway is not configured; deterministic signal is unchanged.",
        )

    request = AIRequest(
        task=resolved_task,
        task_text=SIGNAL_INSTRUCTION,
        evidence=evidence,
        output_schema_id=SIGNAL_EXPLANATION_SCHEMA,
        related_ids={"signal_id": record.signal_id},
    )
    try:
        response = gateway.complete(request)
    except Exception as exc:  # gateway contract says it returns; belt and braces
        error = classify_exception(exc)
        return AIExplanation(
            status=error.status,
            generated_at=stamp,
            note=error.message,
        )

    if not response.ok:
        message = response.error.message if response.error is not None else "AI call failed"
        return AIExplanation(
            status=response.status,
            provider_id=response.provider_id,
            model_id=response.model_id,
            generated_at=stamp,
            note=message,
        )
    try:
        return _explanation_from_response(response, stamp)
    except AIError as error:
        return AIExplanation(
            status=error.status,
            provider_id=response.provider_id,
            model_id=response.model_id,
            generated_at=stamp,
            note=error.message,
        )


def apply_ai_context(
    record: SignalRecord,
    explanation: AIExplanation,
    *,
    evidence_ids: Sequence[str] | None = None,
    expiry: str | None = None,
) -> SignalRecord:
    """Return a copy with **only** ``ai_context`` replaced.

    Direction, prediction score, expected edge, risk levels and the policy/risk
    decision are copied byte-for-byte; the function asserts that invariant.
    """
    if not isinstance(record, SignalRecord):
        raise TypeError("apply_ai_context() requires a SignalRecord")
    if not isinstance(explanation, AIExplanation):
        raise TypeError("apply_ai_context() requires an AIExplanation")
    ids = list(evidence_ids) if evidence_ids is not None else [record.signal_id]
    note = explanation.note or explanation.uncertainty
    summary = AiContextSummary(
        status=context_status(explanation.status),
        provider_id=explanation.provider_id or "",
        model_id=explanation.model_id or "",
        evidence_ids=ids[:8],
        generated_at=explanation.generated_at or None,
        expiry=expiry,
        note=_safe_note(note),
        summary=_safe_note(explanation.summary or ""),
    )
    updated = record.model_copy(update={"ai_context": summary})
    if (
        updated.direction != record.direction
        or updated.expected_edge_bp != record.expected_edge_bp
        or updated.decision != record.decision
        or updated.prediction != record.prediction
    ):
        raise RuntimeError("advisory AI context must not change deterministic signal fields")
    return updated


def _text_appears_complete(text: str) -> bool:
    """Return True if the explanation text appears complete, not truncated.

    A truncated response ends mid-sentence with a trailing comma or a dangling
    mid-word token. We never fabricate missing text, so an obviously incomplete
    explanation is treated as invalid rather than presented as ok. Short
    responses are exempt from the mid-word check.
    """
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith(","):
        return False
    # Long responses ending on an alphanumeric character with no closing
    # punctuation were likely cut off mid-word. Short responses are exempt.
    return not (len(stripped) > 40 and stripped[-1].isalnum())


def _explanation_from_response(response: Any, stamp: str) -> AIExplanation:
    content = dict(response.content or {})
    _reject_authority_keys(content)
    summary = response.text or ""
    if not summary and isinstance(content.get("summary"), str):
        summary = content["summary"]
    summary = _clean_text(summary, MAX_SUMMARY_CHARS)
    if not summary:
        raise AIError(
            AIStatus.INVALID_RESPONSE,
            "AI response carried no bounded explanation text",
            provider_id=response.provider_id,
            model_id=response.model_id,
        )
    if not _text_appears_complete(summary):
        raise AIError(
            AIStatus.INVALID_RESPONSE,
            "AI explanation appears incomplete or truncated",
            provider_id=response.provider_id,
            model_id=response.model_id,
        )
    return AIExplanation(
        status=AIStatus.OK,
        summary=summary,
        supporting_factors=_coerce_factors(content.get("supporting_factors")),
        caution_factors=_coerce_factors(content.get("caution_factors")),
        uncertainty=_coerce_text(content.get("uncertainty")),
        provider_id=response.provider_id,
        model_id=response.model_id,
        generated_at=stamp,
    )


def _reject_authority_keys(content: Mapping[str, Any]) -> None:
    for key in content:
        normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in AUTHORITY_KEYS:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "AI explanation attempted to assert trading authority "
                f"(field '{normalized}'); deterministic output is unchanged",
            )


def _coerce_factors(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))[:MAX_FACTORS]


def _coerce_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _clean_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(redact_secrets(value).split())
    if len(text) > limit:
        keep = max(1, limit - len(" [truncated]"))
        text = text[:keep].rstrip() + " [truncated]"
    return text


def _clean_factors(values: Sequence[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values[:MAX_FACTORS]:
        text = _clean_text(value, MAX_FACTOR_CHARS)
        if text:
            cleaned.append(text)
    return tuple(cleaned)


def _safe_note(text: str) -> str:
    cleaned = redact_secrets(text or "")
    lowered = cleaned.lower()
    for banned in _SECRET_WORDS:
        if banned in lowered:
            cleaned = re.sub(re.escape(banned), "redacted", cleaned, flags=re.IGNORECASE)
            lowered = cleaned.lower()
    cleaned = " ".join(cleaned.split())
    return cleaned[:MAX_NOTE_CHARS]


def _items_size(items: Sequence[tuple[str, str]]) -> int:
    return sum(len(key) + len(value) for key, value in items)


def _json_scalar(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)


def _record_fields(record: SignalRecord) -> list[tuple[str, str]]:
    prediction = record.prediction
    cost = record.cost_estimate
    decision = record.decision
    candidates: list[tuple[str, str | None]] = [
        ("signal_id", record.signal_id),
        ("symbol", record.symbol),
        ("timeframe", record.timeframe),
        ("generated_at", record.generated_at.isoformat()),
        ("direction", record.direction),
        ("decision_status", decision.status),
        ("decision_gate", decision.gate),
        ("reason_codes", ", ".join(decision.reason_codes)),
        ("prediction_horizon_bars", _json_scalar(record.prediction_horizon_bars)),
        ("raw_score", _json_scalar(prediction.raw_score)),
        ("calibrated_probability", _json_scalar(prediction.calibrated_probability)),
        ("calibration_status", prediction.calibration_status),
        ("model_family", prediction.model_family),
        ("expected_edge_bp", _json_scalar(record.expected_edge_bp)),
        ("cost_bp_total", _json_scalar(cost.cost_bp_total) if cost else None),
        ("cost_source", cost.source if cost else None),
        ("market_regime", record.market_regime),
        ("model_id", record.model_id),
        ("model_version", record.model_version),
        ("entry_ref", _json_scalar(record.entry_ref)),
        ("stop_loss", _json_scalar(record.stop_loss)),
        ("take_profit", _json_scalar(record.take_profit)),
        ("risk_reward", _json_scalar(record.risk_reward)),
        ("data_quality", record.data_quality),
        ("exchange", record.exchange),
        ("valid_until", record.valid_until.isoformat()),
        ("dataset_snapshot_id", record.dataset_snapshot_id),
        ("model_artifact_hash", record.model_artifact_hash),
        ("feature_snapshot_hash", record.feature_snapshot_hash),
        ("threshold_version", prediction.threshold_version),
    ]
    fields: list[tuple[str, str]] = []
    for key, value in candidates:
        if value is None:
            continue
        text = str(value)
        if text:
            fields.append((key, text))
    return fields
