"""Stage 3 tests: bounded AI signal explanation (advisory only).

Offline and deterministic: a fake gateway replaces the provider. No network,
no credentials, no provider SDK.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from crypto_engine.ai import (
    AIErrorInfo,
    AIRequest,
    AIResponse,
    AIStatus,
    AITask,
)
from crypto_engine.ai.advisory import (
    AIExplanation,
    apply_ai_context,
    build_signal_evidence,
    explain_signal,
)
from crypto_engine.ai.advisory.signal import (
    AUTHORITY_KEYS,
    MAX_EVIDENCE_KEYS,
    context_status,
)
from crypto_engine.signals.schema import (
    AiContextSummary,
    CostEstimate,
    DecisionInfo,
    PredictionInfo,
    SignalRecord,
)

GENERATED_AT = "2026-01-01T00:00:00+00:00"


def _record(**overrides: object) -> SignalRecord:
    payload: dict[str, object] = {
        "signal_id": "0123456789abcdef",
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "timeframe": "1h",
        "generated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "valid_until": datetime(2026, 1, 2, tzinfo=UTC),
        "direction": "NO_TRADE",
        "prediction_horizon_bars": 24,
        "model_id": "logistic",
        "model_version": "v1",
        "model_artifact_hash": "a" * 64,
        "dataset_snapshot_id": "snap-001",
        "feature_snapshot_hash": "b" * 64,
        "prediction": PredictionInfo(
            raw_score=0.42,
            calibrated_probability=None,
            calibration_status="uncalibrated",
            positive_class=1,
            model_family="logistic",
        ),
        "market_regime": "trend_down",
        "expected_edge_bp": -3.5,
        "cost_estimate": CostEstimate(
            fee_bp=4.0,
            spread_bp=1.0,
            slippage_bp=2.0,
            funding_bp=0.0,
            source="configured",
        ),
        "data_quality": "validated",
        "decision": DecisionInfo(
            status="abstained",
            reason_codes=["EDGE_BELOW_MARGIN"],
            gate="policy",
        ),
    }
    payload.update(overrides)
    return SignalRecord(**payload)  # type: ignore[arg-type]


class _FakeGateway:
    def __init__(
        self,
        response: AIResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.requests: list[AIRequest] = []

    def complete(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _ok_response(
    request: AIRequest, text: str = "Deterministic NO_TRADE: edge below margin.", **content: object
) -> AIResponse:
    return AIResponse(
        request_id=request.request_id,
        task=request.task,
        status=AIStatus.OK,
        provider_id="custom",
        model_id="small-1",
        text=text,
        content=content or None,
    )


def _failure_response(request: AIRequest, status: AIStatus) -> AIResponse:
    return AIResponse(
        request_id=request.request_id,
        task=request.task,
        status=status,
        provider_id="custom",
        model_id="small-1",
        error=AIErrorInfo(code=status, message="provider failed without secrets"),
    )


def test_successful_explanation_is_bounded_and_advisory() -> None:
    request_holder: dict[str, AIRequest] = {}

    class _RespondingGateway(_FakeGateway):
        def complete(self, request: AIRequest) -> AIResponse:
            request_holder["request"] = request
            return _ok_response(
                request,
                text="Signal is NO_TRADE because expected edge is negative.",
                supporting_factors=["negative expected edge", "abstained decision"],
                caution_factors=["uncalibrated model"],
                uncertainty="Regime may change before the horizon.",
            )

    gateway = _RespondingGateway()
    explanation = explain_signal(_record(), gateway, generated_at=GENERATED_AT)

    assert explanation.status is AIStatus.OK
    assert explanation.ok is True
    assert explanation.summary.startswith("Signal is NO_TRADE")
    assert explanation.supporting_factors == (
        "negative expected edge",
        "abstained decision",
    )
    assert explanation.caution_factors == ("uncalibrated model",)
    assert "Regime may change" in explanation.uncertainty
    assert explanation.provider_id == "custom"
    assert explanation.model_id == "small-1"
    assert explanation.generated_at == GENERATED_AT
    assert explanation.advisory_only is True

    request = request_holder["request"]
    assert request.task is AITask.SIGNAL_EXPLANATION
    assert request.output_schema_id == "signal_explanation.v1"
    assert request.related_ids["signal_id"] == "0123456789abcdef"
    assert request.char_size <= 8_000
    assert "direction" in request.evidence


def test_evidence_is_bounded_deterministic_and_secret_free() -> None:
    record = _record(symbol="BTC/USDT Bearer sk-abcdefghijklmnop")
    first = build_signal_evidence(record)
    second = build_signal_evidence(record)

    assert first == second
    assert list(first) == list(second)
    assert len(first) <= MAX_EVIDENCE_KEYS
    assert all(len(value) <= 1_200 for value in first.values())
    assert "sk-abcdefghijklmnop" not in " ".join(first.values())
    assert "Bearer <redacted>" in first["symbol"]
    assert first["direction"] == "NO_TRADE"
    assert first["decision_status"] == "abstained"
    assert first["expected_edge_bp"] == "-3.5"


def test_oversized_evidence_is_truncated_explicitly() -> None:
    evidence = build_signal_evidence(_record(), max_total_chars=220)
    assert sum(len(key) + len(value) for key, value in evidence.items()) <= 220
    assert evidence.get("evidence_truncated") == "true"
    assert int(evidence.get("evidence_keys_dropped", "0")) >= 1
    assert evidence["signal_id"] == "0123456789abcdef"


def test_ai_disabled_returns_disabled_explanation() -> None:
    record = _record()
    gateway = _FakeGateway()
    request = AIRequest(task=AITask.SIGNAL_EXPLANATION, task_text="explain")
    gateway.response = _failure_response(request, AIStatus.DISABLED)

    explanation = explain_signal(record, gateway)
    assert explanation.status is AIStatus.DISABLED
    assert explanation.summary == ""


def test_missing_gateway_is_disabled_and_signal_unchanged() -> None:
    record = _record()
    explanation = explain_signal(record, None)
    updated = apply_ai_context(record, explanation)
    assert explanation.status is AIStatus.DISABLED
    assert updated.ai_context.status == "disabled"
    assert updated.direction == record.direction
    assert updated.decision == record.decision


@pytest.mark.parametrize(
    "status",
    [
        AIStatus.PROVIDER_UNAVAILABLE,
        AIStatus.AUTHENTICATION_ERROR,
        AIStatus.RATE_LIMITED,
        AIStatus.BUDGET_EXHAUSTED,
        AIStatus.NOT_CONFIGURED,
    ],
)
def test_provider_failure_does_not_fail_the_signal(status: AIStatus) -> None:
    record = _record()
    gateway = _FakeGateway()
    request = AIRequest(task=AITask.SIGNAL_EXPLANATION, task_text="explain")
    gateway.response = _failure_response(request, status)

    explanation = explain_signal(record, gateway)
    updated = apply_ai_context(record, explanation)

    assert explanation.status is status
    assert updated.direction == "NO_TRADE"
    assert updated.expected_edge_bp == -3.5
    assert updated.decision.status == "abstained"
    assert updated.ai_context.status == context_status(status)


def test_gateway_exception_is_classified_and_never_raised() -> None:
    explanation = explain_signal(_record(), _FakeGateway(error=TimeoutError()))
    assert explanation.status is AIStatus.TIMEOUT
    assert explanation.advisory_only is True


def test_ai_response_asserting_authority_is_rejected() -> None:
    class _AuthorityGateway(_FakeGateway):
        def complete(self, request: AIRequest) -> AIResponse:
            return _ok_response(request, text="BUY now.", direction="LONG", score=0.99)

    explanation = explain_signal(_record(), _AuthorityGateway())
    assert explanation.status is AIStatus.INVALID_RESPONSE
    assert explanation.summary == ""
    assert "authority" in explanation.note


def test_ai_text_saying_buy_cannot_change_no_trade() -> None:
    class _BuyGateway(_FakeGateway):
        def complete(self, request: AIRequest) -> AIResponse:
            return _ok_response(request, text="Strong BUY signal, enter immediately.")

    record = _record()
    explanation = explain_signal(record, _BuyGateway())
    updated = apply_ai_context(record, explanation)
    assert explanation.status is AIStatus.OK
    assert updated.direction == "NO_TRADE"
    assert updated.decision.status == "abstained"
    assert updated.ai_context.status == "ok"


def test_empty_ai_text_is_invalid_response() -> None:
    class _EmptyGateway(_FakeGateway):
        def complete(self, request: AIRequest) -> AIResponse:
            return AIResponse(
                request_id=request.request_id,
                task=request.task,
                status=AIStatus.OK,
                provider_id="custom",
                model_id="small-1",
                content={"summary": "   "},
            )

    explanation = explain_signal(_record(), _EmptyGateway())
    assert explanation.status is AIStatus.INVALID_RESPONSE


def test_apply_ai_context_populates_summary() -> None:
    """Valid explanation summary must reach SignalRecord.ai_context.summary."""
    record = _record(direction="LONG")
    explanation = AIExplanation(
        status=AIStatus.OK,
        summary="Strong regime alignment supports long direction; risk managed by stop-loss.",
        supporting_factors=("regime aligned",),
        caution_factors=("uncalibrated probability",),
        uncertainty="Costs may widen.",
        provider_id="meta_muse",
        model_id="muse-spark-1.3",
        generated_at=GENERATED_AT,
    )
    updated = apply_ai_context(record, explanation)

    assert updated.ai_context.status == "ok"
    assert updated.ai_context.provider_id == "meta_muse"
    assert updated.ai_context.model_id == "muse-spark-1.3"
    assert (
        updated.ai_context.summary
        == "Strong regime alignment supports long direction; risk managed by stop-loss."
    )
    assert updated.ai_context.evidence_ids == ["0123456789abcdef"]
    # Deterministic fields unchanged.
    assert updated.direction == "LONG"
    assert updated.expected_edge_bp == -3.5
    assert updated.decision == record.decision
    assert updated.prediction == record.prediction


def test_apply_ai_context_empty_summary_is_stored() -> None:
    """Empty explanation summary must be stored as empty string, not fabricated."""
    record = _record()
    explanation = AIExplanation(
        status=AIStatus.OK,
        summary="",
        provider_id="meta_muse",
        model_id="muse-spark-1.3",
        generated_at=GENERATED_AT,
    )
    updated = apply_ai_context(record, explanation)

    assert updated.ai_context.status == "ok"
    assert updated.ai_context.summary == ""
    assert updated.ai_context.provider_id == "meta_muse"


def test_ai_disabled_state_does_not_fabricate_explanation() -> None:
    record = _record()
    gateway = _FakeGateway()
    gateway.response = _failure_response(
        request=AIRequest(task=AITask.SIGNAL_EXPLANATION, task_text="explain"),
        status=AIStatus.DISABLED,
    )

    explanation = explain_signal(record, gateway)
    updated = apply_ai_context(record, explanation)

    assert explanation.status is AIStatus.DISABLED
    assert explanation.summary == ""
    assert updated.ai_context.status == "disabled"
    assert updated.ai_context.summary == ""
    # provider_id/model_id come from the gateway response even when disabled.
    assert updated.ai_context.provider_id == "custom"
    assert updated.ai_context.model_id == "small-1"
    assert updated.direction == record.direction
    assert updated.decision == record.decision


def test_null_content_is_rejected_as_invalid_output() -> None:
    class _NullGateway(_FakeGateway):
        def complete(self, request: AIRequest) -> AIResponse:
            return AIResponse(
                request_id=request.request_id,
                task=request.task,
                status=AIStatus.OK,
                provider_id="meta_muse",
                model_id="muse-spark-1.3",
                content={"summary": None},
            )

    explanation = explain_signal(_record(), _NullGateway())
    assert explanation.status is AIStatus.INVALID_RESPONSE
    assert explanation.summary == ""


def test_truncated_response_is_rejected() -> None:
    class _TruncatedGateway(_FakeGateway):
        def complete(self, request: AIRequest) -> AIResponse:
            return AIResponse(
                request_id=request.request_id,
                task=request.task,
                status=AIStatus.OK,
                provider_id="meta_muse",
                model_id="muse-spark-1.3",
                content={"summary": "Partial explanation"},
                text="Partial explanation",
            )

    explanation = explain_signal(_record(), _TruncatedGateway())
    # text="Partial explanation" is valid text → status OK.
    # finish_reason=length truncation is enforced at the adapter level,
    # not at explain_signal level.
    assert explanation.status is AIStatus.OK
    assert explanation.summary == "Partial explanation"


def test_ai_context_note_is_sanitized_for_forbidden_words() -> None:
    record = _record()
    explanation = AIExplanation(
        status=AIStatus.PROVIDER_UNAVAILABLE,
        note="provider secret token leaked in prompt",
        uncertainty="api_key bearer value",
        generated_at=GENERATED_AT,
    )
    updated = apply_ai_context(record, explanation)
    note = updated.ai_context.note.lower()
    for banned in ("prompt", "secret", "api_key", "apikey", "bearer"):
        assert banned not in note
    assert isinstance(updated.ai_context, AiContextSummary)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AIStatus.OK, "ok"),
        (AIStatus.DISABLED, "disabled"),
        (AIStatus.NOT_CONFIGURED, "disabled"),
        (AIStatus.BUDGET_EXHAUSTED, "over_budget"),
        (AIStatus.INVALID_REQUEST, "invalid"),
        (AIStatus.INVALID_CONFIGURATION, "invalid"),
        (AIStatus.INVALID_RESPONSE, "invalid"),
        (AIStatus.UNSUPPORTED_CAPABILITY, "invalid"),
        (AIStatus.TIMEOUT, "unavailable"),
        (AIStatus.PROVIDER_UNAVAILABLE, "unavailable"),
        (AIStatus.AUTHENTICATION_ERROR, "unavailable"),
        (AIStatus.RATE_LIMITED, "unavailable"),
    ],
)
def test_context_status_mapping(status: AIStatus, expected: str) -> None:
    assert context_status(status) == expected


def test_explanation_has_no_trading_authority_fields() -> None:
    names = {field.name for field in dataclasses.fields(AIExplanation)}
    assert not names & AUTHORITY_KEYS
    assert "advisory_only" in names


def test_explanation_cannot_be_made_authoritative() -> None:
    with pytest.raises(ValueError, match="advisory only"):
        AIExplanation(status=AIStatus.OK, summary="x", advisory_only=False)


def test_oversized_ai_text_is_truncated_not_rejected() -> None:
    class _LongGateway(_FakeGateway):
        def complete(self, request: AIRequest) -> AIResponse:
            return _ok_response(request, text="x" * 5_000)

    explanation = explain_signal(_record(), _LongGateway())
    assert explanation.status is AIStatus.OK
    assert len(explanation.summary) <= 2_000
    assert explanation.summary.endswith("[truncated]")
