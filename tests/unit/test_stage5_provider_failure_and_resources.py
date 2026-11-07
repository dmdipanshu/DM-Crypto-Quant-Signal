"""Stage 5 provider-failure and resource-bound integration tests.

Every provider failure must become a normalized non-ok result; the deterministic
signal must stay unchanged. All transports are in-memory (offline).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from crypto_engine.ai import (
    AICapabilities,
    AIGateway,
    AIModel,
    AIModelRef,
    AIProviderSpec,
    AIRegistry,
    AIRequest,
    AIResourceLimits,
    AIStatus,
    AITask,
)
from crypto_engine.ai.advisory import apply_ai_context, build_signal_evidence, explain_signal
from crypto_engine.ai.providers import (
    MAX_RESPONSE_BYTES,
    OpenAICompatibleProvider,
    TransportResponse,
)
from crypto_engine.signals.schema import (
    CostEstimate,
    DecisionInfo,
    PredictionInfo,
    SignalRecord,
)

CUSTOM_KEY = "sk-stage5-test-value"


class FakeTransport:
    def __init__(
        self,
        response: TransportResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __call__(self, url, headers, body, timeout_s):
        self.calls.append({"url": url, "headers": dict(headers), "timeout_s": timeout_s})
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _json_response(payload: object, status: int = 200) -> TransportResponse:
    return TransportResponse(
        status_code=status,
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _record() -> SignalRecord:
    return SignalRecord(
        signal_id="0123456789abcdef",
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1h",
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        valid_until=datetime(2026, 1, 2, tzinfo=UTC),
        direction="NO_TRADE",
        prediction_horizon_bars=24,
        model_id="logistic",
        model_version="v1",
        model_artifact_hash="a" * 64,
        dataset_snapshot_id="snap-001",
        feature_snapshot_hash="b" * 64,
        prediction=PredictionInfo(raw_score=0.42, calibration_status="uncalibrated"),
        market_regime="trend_down",
        expected_edge_bp=-3.5,
        cost_estimate=CostEstimate(fee_bp=4.0, spread_bp=1.0, slippage_bp=2.0),
        data_quality="validated",
        decision=DecisionInfo(
            status="abstained", reason_codes=["EDGE_BELOW_MARGIN"], gate="policy"
        ),
    )


def _gateway(
    transport: FakeTransport,
    *,
    structured_output: str = "json_schema",
    limits: AIResourceLimits | None = None,
    environ: dict[str, str] | None = None,
    provider_timeout_s: float = 30.0,
    provider_max_retries: int = 2,
) -> AIGateway:
    spec = AIProviderSpec(
        provider_id="custom",
        display_name="Custom",
        base_url="https://ai.example.test/v1",
        api_key_env="CUSTOM_API_KEY",
        capabilities=AICapabilities(structured_output=structured_output),
        enabled=True,
        timeout_s=provider_timeout_s,
        max_retries=provider_max_retries,
    )
    model = AIModel(
        provider_id="custom",
        model_id="small-1",
        capabilities=AICapabilities(structured_output=structured_output),
        enabled=True,
    )
    registry = AIRegistry(enabled=True)
    registry.add_provider(spec)
    registry.add_model(model)
    registry.set_route(AITask.SIGNAL_EXPLANATION, [AIModelRef("custom", "small-1")])
    resolved_env = {"CUSTOM_API_KEY": CUSTOM_KEY} if environ is None else environ
    adapter = OpenAICompatibleProvider(
        spec,
        transport=transport,
        environ=resolved_env,
    )
    return AIGateway(
        registry,
        {"custom": adapter},
        limits or AIResourceLimits(max_retries=0),
        environ=resolved_env,
        sleeper=lambda _seconds: None,
    )


FAILURE_CASES = [
    ("provider_500", TransportResponse(status_code=500), AIStatus.PROVIDER_UNAVAILABLE),
    ("provider_429", TransportResponse(status_code=429), AIStatus.RATE_LIMITED),
    ("provider_401", TransportResponse(status_code=401), AIStatus.AUTHENTICATION_ERROR),
    ("provider_408", TransportResponse(status_code=408), AIStatus.TIMEOUT),
    (
        "invalid_json",
        TransportResponse(status_code=200, body=b"{not json"),
        AIStatus.INVALID_RESPONSE,
    ),
    ("malformed", _json_response({"unexpected": True}), AIStatus.INVALID_RESPONSE),
    ("missing_output", _json_response({"choices": []}), AIStatus.INVALID_RESPONSE),
    (
        "oversized_response",
        TransportResponse(status_code=200, body=b"x" * (MAX_RESPONSE_BYTES + 1)),
        AIStatus.INVALID_RESPONSE,
    ),
    ("timeout", None, AIStatus.TIMEOUT),
]


@pytest.mark.parametrize(("name", "response", "expected"), FAILURE_CASES)
def test_provider_failures_never_reach_deterministic_authority(
    name: str, response: TransportResponse | None, expected: AIStatus
) -> None:
    transport = FakeTransport(response=response, error=TimeoutError() if response is None else None)
    record = _record()
    explanation = explain_signal(record, _gateway(transport))
    updated = apply_ai_context(record, explanation)

    assert explanation.status is expected, name
    assert explanation.advisory_only is True
    assert updated.direction == "NO_TRADE"
    assert updated.expected_edge_bp == -3.5
    assert updated.decision == record.decision
    assert updated.prediction == record.prediction
    assert CUSTOM_KEY not in explanation.note


def test_unsupported_capability_is_refused_before_transport() -> None:
    transport = FakeTransport(_json_response({"choices": []}))
    explanation = explain_signal(_record(), _gateway(transport, structured_output="none"))
    assert explanation.status is AIStatus.UNSUPPORTED_CAPABILITY
    assert transport.calls == []


def test_missing_credential_is_auth_failure_before_transport() -> None:
    transport = FakeTransport(_json_response({"choices": []}))
    explanation = explain_signal(_record(), _gateway(transport, environ={}))
    assert explanation.status is AIStatus.AUTHENTICATION_ERROR
    assert transport.calls == []
    assert CUSTOM_KEY not in explanation.note


def test_retry_exhaustion_is_bounded_and_reported() -> None:
    transport = FakeTransport(TransportResponse(status_code=503))
    gateway = _gateway(transport, limits=AIResourceLimits(max_retries=2))
    request = AIRequest(
        task=AITask.SIGNAL_EXPLANATION,
        task_text="explain",
        evidence={"signal_id": "0123456789abcdef"},
        output_schema_id="signal_explanation.v1",
    )
    response = gateway.complete(request)
    assert response.status is AIStatus.PROVIDER_UNAVAILABLE
    assert response.attempts == 3
    assert len(transport.calls) == 3


def test_timeout_is_bounded_by_gateway_limits() -> None:
    transport = FakeTransport(_json_response({"choices": [{"message": {"content": "ok"}}]}))
    gateway = _gateway(transport, limits=AIResourceLimits(timeout_s=2.5, max_retries=0))
    request = AIRequest(
        task=AITask.SIGNAL_EXPLANATION,
        task_text="explain",
        output_schema_id="signal_explanation.v1",
    )
    response = gateway.complete(request)
    assert response.status is AIStatus.OK
    assert transport.calls[0]["timeout_s"] == 2.5


def test_provider_timeout_caps_the_global_ceiling() -> None:
    transport = FakeTransport(_json_response({"choices": [{"message": {"content": "ok"}}]}))
    gateway = _gateway(
        transport,
        limits=AIResourceLimits(timeout_s=30.0, max_retries=0),
        provider_timeout_s=4.0,
    )
    request = AIRequest(
        task=AITask.SIGNAL_EXPLANATION,
        task_text="explain",
        output_schema_id="signal_explanation.v1",
    )
    response = gateway.complete(request)
    assert response.status is AIStatus.OK
    assert transport.calls[0]["timeout_s"] == 4.0


def test_provider_retry_cap_wins_over_the_global_retry_budget() -> None:
    transport = FakeTransport(TransportResponse(status_code=503))
    gateway = _gateway(
        transport,
        limits=AIResourceLimits(max_retries=2),
        provider_max_retries=0,
    )
    request = AIRequest(
        task=AITask.SIGNAL_EXPLANATION,
        task_text="explain",
        output_schema_id="signal_explanation.v1",
    )
    response = gateway.complete(request)
    assert response.status is AIStatus.PROVIDER_UNAVAILABLE
    assert response.attempts == 1
    assert len(transport.calls) == 1


def test_oversized_request_is_refused_without_adapter_call() -> None:
    transport = FakeTransport(_json_response({"choices": []}))
    gateway = _gateway(transport, limits=AIResourceLimits(max_request_chars=100))
    request = AIRequest(
        task=AITask.SIGNAL_EXPLANATION,
        task_text="explain",
        evidence={"blob": "y" * 500},
        output_schema_id="signal_explanation.v1",
    )
    response = gateway.complete(request)
    assert response.status is AIStatus.INVALID_REQUEST
    assert transport.calls == []


def test_concurrency_above_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="exactly 1"):
        AIResourceLimits(max_concurrent_requests=2)


def test_truncated_evidence_still_fits_gateway_limits() -> None:
    evidence = build_signal_evidence(_record(), max_total_chars=300)
    assert sum(len(key) + len(value) for key, value in evidence.items()) <= 300
    request = AIRequest(
        task=AITask.SIGNAL_EXPLANATION,
        task_text="explain",
        evidence=evidence,
        output_schema_id="signal_explanation.v1",
    )
    assert request.char_size <= 8_000
