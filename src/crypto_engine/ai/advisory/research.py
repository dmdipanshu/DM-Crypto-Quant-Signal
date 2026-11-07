"""Bounded AI summary of deterministic research results (Stage 4).

Research flow::

    metrics -> deterministic report -> (optional) AI summary -> advisory block

The AI layer is strictly downstream. It receives allowlisted, classified
evidence, may add INTERPRETATION/UNCERTAINTY text, and can never generate
performance metrics, alter trades/datasets/backtest parameters, or promote a
strategy. Missing metrics are reported as ``NOT_AVAILABLE``; a response that
claims a value for a missing metric, uses an unknown field or asserts promotion
authority is rejected in full.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from crypto_engine.ai.advisory.signal import AUTHORITY_KEYS
from crypto_engine.ai.contracts import AIRequest, AITask
from crypto_engine.ai.errors import AIError, AIStatus, classify_exception, redact_secrets
from crypto_engine.ai.gateway import AIGateway

FACT = "FACT"
CALCULATION = "CALCULATION"
INTERPRETATION = "INTERPRETATION"
UNCERTAINTY = "UNCERTAINTY"
NOT_AVAILABLE = "NOT_AVAILABLE"

METRIC_CLASSIFICATIONS = frozenset({FACT, CALCULATION})

RESEARCH_SUMMARY_SCHEMA = "research_summary.v1"

RESEARCH_INSTRUCTION = (
    "Summarize the supplied deterministic research evidence. Separate what is "
    "measured (FACT), what is derived arithmetically (CALCULATION), your own "
    "interpretation, and remaining uncertainty. Metrics marked NOT_AVAILABLE "
    "must stay unavailable: never invent, estimate or infer them. Do not "
    "recommend promotion, paper testing, trades or execution; you have no "
    "authority over strategy selection."
)

MAX_METRIC_VALUE_CHARS = 500
MAX_SUMMARY_CHARS = 2_000
MAX_FACTOR_CHARS = 300
MAX_FACTORS = 12
MAX_UNCERTAINTY_CHARS = 500
MAX_NOTE_CHARS = 500
DEFAULT_EVIDENCE_BUDGET = 7_000

#: Allowlisted deterministic metrics -> their evidence classification.
ALLOWED_METRICS: dict[str, str] = {
    "dataset_id": FACT,
    "exchange": FACT,
    "symbol": FACT,
    "timeframe": FACT,
    "sample_size": FACT,
    "train_period": FACT,
    "validation_period": FACT,
    "holdout_period": FACT,
    "trade_count": FACT,
    "failure_diagnostics": FACT,
    "total_return_bp": CALCULATION,
    "max_drawdown_bp": CALCULATION,
    "profit_factor": CALCULATION,
    "win_rate": CALCULATION,
    "cost_stress_return_bp": CALCULATION,
    "walk_forward_return_bp": CALCULATION,
    "holdout_return_bp": CALCULATION,
    "robustness_score": CALCULATION,
}

#: Fields that would turn an advisory summary into a promotion decision.
PROMOTION_KEYS = frozenset(
    {
        "approve",
        "approved",
        "candidate_for_paper_testing",
        "promote",
        "promotion",
        "selected_strategy",
        "strategy_selection",
    }
)

#: AI output keys accepted; everything else is a hallucinated field.
ALLOWED_OUTPUT_KEYS = frozenset(
    {
        "calculations",
        "facts",
        "interpretations",
        "metric_claims",
        "metrics",
        "notes",
        "summary",
        "uncertainties",
    }
)

_BANNED_EVIDENCE_KEYS = AUTHORITY_KEYS | PROMOTION_KEYS


@dataclass(frozen=True, slots=True)
class ResearchItem:
    """One classified evidence item; ``NOT_AVAILABLE`` is never fabricated."""

    name: str
    value: str
    classification: str

    @property
    def available(self) -> bool:
        return self.value != NOT_AVAILABLE


@dataclass(frozen=True, slots=True)
class ResearchEvidence:
    """Bounded, classified research evidence snapshot."""

    items: tuple[ResearchItem, ...]
    missing: tuple[str, ...]

    def as_evidence(self) -> dict[str, str]:
        """Provider-neutral ``AIRequest.evidence`` mapping."""
        return {item.name: f"{item.classification}: {item.value}" for item in self.items}

    @property
    def facts(self) -> tuple[str, ...]:
        return tuple(
            f"{item.name}={item.value}"
            for item in self.items
            if item.classification == FACT and item.available
        )

    @property
    def calculations(self) -> tuple[str, ...]:
        return tuple(
            f"{item.name}={item.value}"
            for item in self.items
            if item.classification == CALCULATION and item.available
        )

    def metric_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.items)


@dataclass(frozen=True, slots=True)
class ResearchSummary:
    """Bounded advisory summary. Promotion authority is always denied."""

    status: AIStatus = AIStatus.DISABLED
    summary: str = ""
    facts: tuple[str, ...] = ()
    calculations: tuple[str, ...] = ()
    interpretations: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    provider_id: str | None = None
    model_id: str | None = None
    generated_at: str = ""
    note: str = ""
    advisory_only: bool = True
    promotion_authority: bool = False
    candidate_for_paper_testing: bool = False

    def __post_init__(self) -> None:
        if not self.advisory_only:
            raise ValueError("AI research summaries are advisory only")
        if self.promotion_authority:
            raise ValueError("AI research summaries can never hold promotion authority")
        if self.candidate_for_paper_testing:
            raise ValueError("AI can never mark a strategy candidate for paper testing")
        object.__setattr__(self, "status", AIStatus(self.status))
        object.__setattr__(self, "summary", _clean_text(self.summary, MAX_SUMMARY_CHARS))
        object.__setattr__(self, "facts", _clean_list(self.facts))
        object.__setattr__(self, "calculations", _clean_list(self.calculations))
        object.__setattr__(self, "interpretations", _clean_list(self.interpretations))
        object.__setattr__(self, "uncertainties", _clean_list(self.uncertainties))
        object.__setattr__(self, "missing", tuple(str(item) for item in self.missing))
        object.__setattr__(self, "note", _clean_text(self.note, MAX_NOTE_CHARS))
        if not self.generated_at:
            object.__setattr__(
                self, "generated_at", datetime.now(UTC).isoformat(timespec="seconds")
            )

    @property
    def ok(self) -> bool:
        return self.status is AIStatus.OK

    def to_dict(self) -> dict[str, Any]:
        """Deterministic, secret-free view for reports and CLI output."""
        return {
            "status": self.status.value,
            "summary": self.summary,
            "facts": list(self.facts),
            "calculations": list(self.calculations),
            "interpretations": list(self.interpretations),
            "uncertainties": list(self.uncertainties),
            "missing": list(self.missing),
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "generated_at": self.generated_at,
            "note": self.note,
            "advisory_only": self.advisory_only,
            "promotion_authority": self.promotion_authority,
            "candidate_for_paper_testing": self.candidate_for_paper_testing,
        }


def build_research_evidence(
    metrics: Mapping[str, Any],
    *,
    required: Sequence[str] | None = None,
    max_total_chars: int | None = None,
) -> ResearchEvidence:
    """Classify allowlisted metrics; reject unknown or authority-bearing keys."""
    if not isinstance(metrics, Mapping):
        raise TypeError("build_research_evidence() requires a mapping of metrics")
    budget = DEFAULT_EVIDENCE_BUDGET if max_total_chars is None else int(max_total_chars)
    if budget < 256:
        raise ValueError("max_total_chars must be at least 256")

    for raw_key in metrics:
        key = _normalize_key(raw_key)
        if key in _BANNED_EVIDENCE_KEYS:
            raise AIError(
                AIStatus.INVALID_REQUEST,
                f"research evidence may not carry authority field '{key}'",
            )
        if key not in ALLOWED_METRICS:
            raise AIError(
                AIStatus.INVALID_REQUEST,
                f"research metric '{key}' is not in the allowlist",
            )

    names = tuple(required) if required is not None else tuple(ALLOWED_METRICS)
    for name in names:
        if name not in ALLOWED_METRICS:
            raise AIError(
                AIStatus.INVALID_REQUEST,
                f"research metric '{name}' is not in the allowlist",
            )

    items: list[ResearchItem] = []
    for name in names:
        raw = metrics.get(name)
        value = _coerce_metric(raw)
        items.append(ResearchItem(name=name, value=value, classification=ALLOWED_METRICS[name]))

    items = _fit_budget(items, budget)
    missing = tuple(item.name for item in items if not item.available)
    return ResearchEvidence(items=tuple(items), missing=missing)


def summarize_research(
    metrics: Mapping[str, Any],
    gateway: AIGateway | None = None,
    *,
    task: AITask | str = AITask.RESEARCH_SUMMARY,
    generated_at: str | None = None,
    required: Sequence[str] | None = None,
    max_total_chars: int | None = None,
) -> ResearchSummary:
    """Summarize deterministic research results; never raises provider faults."""
    resolved_task = AITask(task)
    stamp = generated_at or datetime.now(UTC).isoformat(timespec="seconds")
    try:
        evidence = build_research_evidence(
            metrics, required=required, max_total_chars=max_total_chars
        )
    except AIError as error:
        return ResearchSummary(
            status=error.status,
            generated_at=stamp,
            note=error.message,
        )

    deterministic = {
        "facts": evidence.facts,
        "calculations": evidence.calculations,
        "missing": evidence.missing,
    }

    if gateway is None:
        return ResearchSummary(
            status=AIStatus.DISABLED,
            generated_at=stamp,
            note="AI gateway is not configured; deterministic report is unchanged.",
            **deterministic,
        )

    dataset_id = next((item.value for item in evidence.items if item.name == "dataset_id"), None)
    request = AIRequest(
        task=resolved_task,
        task_text=RESEARCH_INSTRUCTION,
        evidence=evidence.as_evidence(),
        output_schema_id=RESEARCH_SUMMARY_SCHEMA,
        related_ids={"dataset_id": dataset_id} if dataset_id else {},
    )
    try:
        response = gateway.complete(request)
    except Exception as exc:  # gateway contract says it returns; belt and braces
        error = classify_exception(exc)
        return ResearchSummary(
            status=error.status,
            generated_at=stamp,
            note=error.message,
            **deterministic,
        )

    if not response.ok:
        message = response.error.message if response.error is not None else "AI call failed"
        return ResearchSummary(
            status=response.status,
            provider_id=response.provider_id,
            model_id=response.model_id,
            generated_at=stamp,
            note=message,
            **deterministic,
        )

    try:
        content = _validated_content(response.content, evidence)
        summary_text = response.text or ""
        if not summary_text and isinstance(content.get("summary"), str):
            summary_text = content["summary"]
        summary_text = _clean_text(summary_text, MAX_SUMMARY_CHARS)
        if not summary_text:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "AI research response carried no bounded summary text",
                provider_id=response.provider_id,
                model_id=response.model_id,
            )
    except AIError as error:
        return ResearchSummary(
            status=error.status,
            provider_id=response.provider_id,
            model_id=response.model_id,
            generated_at=stamp,
            note=error.message,
            **deterministic,
        )

    return ResearchSummary(
        status=AIStatus.OK,
        summary=summary_text,
        interpretations=_coerce_list(content.get("interpretations")),
        uncertainties=_coerce_list(content.get("uncertainties")),
        provider_id=response.provider_id,
        model_id=response.model_id,
        generated_at=stamp,
        **deterministic,
    )


def attach_research_summary(report: Mapping[str, Any], summary: ResearchSummary) -> dict[str, Any]:
    """Return a copy of a deterministic report plus an ``ai_advisory`` block.

    The input report is never mutated: metrics, trades, datasets, parameters and
    any promotion/candidate flag are copied byte-for-byte.
    """
    if not isinstance(report, Mapping):
        raise TypeError("attach_research_summary() requires a mapping report")
    if not isinstance(summary, ResearchSummary):
        raise TypeError("attach_research_summary() requires a ResearchSummary")
    updated = dict(report)
    updated["ai_advisory"] = summary.to_dict()
    return updated


def _validated_content(
    content: Mapping[str, Any] | None, evidence: ResearchEvidence
) -> dict[str, Any]:
    payload = dict(content or {})
    for raw_key in payload:
        key = _normalize_key(raw_key)
        if key in _BANNED_EVIDENCE_KEYS:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                "AI research summary attempted to assert promotion/trading authority "
                f"(field '{key}'); deterministic report is unchanged",
            )
        if key not in ALLOWED_OUTPUT_KEYS:
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                f"AI research summary used unsupported field '{key}'",
            )
    for container in ("metrics", "metric_claims"):
        claims = payload.get(container)
        if claims is None:
            continue
        if not isinstance(claims, Mapping):
            raise AIError(
                AIStatus.INVALID_RESPONSE,
                f"AI research summary field '{container}' must be an object",
            )
        for raw_metric in claims:
            metric = _normalize_key(raw_metric)
            if metric not in ALLOWED_METRICS:
                raise AIError(
                    AIStatus.INVALID_RESPONSE,
                    f"AI research summary invented metric '{metric}'",
                )
            if metric in evidence.missing:
                raise AIError(
                    AIStatus.INVALID_RESPONSE,
                    f"AI research summary fabricated unavailable metric '{metric}'",
                )
    return payload


def _clean_text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(redact_secrets(value).split())
    if len(text) > limit:
        keep = max(1, limit - len(" [truncated]"))
        text = text[:keep].rstrip() + " [truncated]"
    return text


def _coerce_metric(value: object) -> str:
    if value is None:
        return NOT_AVAILABLE
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        text = value.strip()
        return text or NOT_AVAILABLE
    if isinstance(value, (Mapping, list, tuple)):
        text = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
        return text or NOT_AVAILABLE
    return str(value)


def _fit_budget(items: list[ResearchItem], budget: int) -> list[ResearchItem]:
    cleaned = [
        ResearchItem(
            name=item.name,
            value=_clean_text(item.value, MAX_METRIC_VALUE_CHARS) or NOT_AVAILABLE,
            classification=item.classification,
        )
        for item in items
    ]

    def total() -> int:
        return sum(len(item.name) + len(item.value) for item in cleaned)

    while cleaned and total() > budget:
        longest = max(cleaned, key=lambda item: len(item.value))
        overflow = total() - budget
        keep = max(len(NOT_AVAILABLE), len(longest.value) - overflow - len(" [truncated]"))
        if keep >= len(longest.value):
            break
        replacement = longest.value[:keep].rstrip() + " [truncated]"
        index = cleaned.index(longest)
        cleaned[index] = ResearchItem(
            name=longest.name, value=replacement, classification=longest.classification
        )
    return cleaned


def _coerce_list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))[:MAX_FACTORS]


def _clean_list(values: Sequence[str]) -> tuple[str, ...]:
    cleaned: list[str] = []
    for value in values[:MAX_FACTORS]:
        text = _clean_text(value, MAX_FACTOR_CHARS)
        if text:
            cleaned.append(text)
    return tuple(cleaned)


def _normalize_key(key: object) -> str:
    return redact_secrets(str(key)).strip().lower().replace("-", "_").replace(" ", "_")


__all__ = [
    "ALLOWED_METRICS",
    "ALLOWED_OUTPUT_KEYS",
    "CALCULATION",
    "FACT",
    "INTERPRETATION",
    "METRIC_CLASSIFICATIONS",
    "NOT_AVAILABLE",
    "PROMOTION_KEYS",
    "UNCERTAINTY",
    "ResearchEvidence",
    "ResearchItem",
    "ResearchSummary",
    "attach_research_summary",
    "build_research_evidence",
    "summarize_research",
]
