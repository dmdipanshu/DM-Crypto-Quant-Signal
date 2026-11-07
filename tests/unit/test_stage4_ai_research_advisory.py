"""Stage 4 tests: AI research summary is downstream and non-authoritative.

Offline and deterministic: a fake gateway replaces the provider. No network,
no credentials, no provider SDK.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from crypto_engine.ai import AIError, AIErrorInfo, AIRequest, AIResponse, AIStatus, AITask
from crypto_engine.ai.advisory import (
    ALLOWED_METRICS,
    CALCULATION,
    FACT,
    NOT_AVAILABLE,
    ResearchSummary,
    attach_research_summary,
    build_research_evidence,
    summarize_research,
)
from crypto_engine.ai.advisory.research import PROMOTION_KEYS

GENERATED_AT = "2026-01-01T00:00:00+00:00"

METRICS: dict[str, Any] = {
    "dataset_id": "snap-001",
    "exchange": "binance",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "sample_size": 1000,
    "train_period": "2024-01/2024-06",
    "validation_period": "2024-07/2024-08",
    "holdout_period": "2024-09/2024-10",
    "trade_count": 412,
    "total_return_bp": 123.4,
    "max_drawdown_bp": -88.0,
    "profit_factor": 1.31,
    "win_rate": 0.52,
    "cost_stress_return_bp": 41.0,
    "walk_forward_return_bp": 77.0,
    "holdout_return_bp": 12.0,
    "robustness_score": 0.66,
    "failure_diagnostics": "none",
}


class _FakeGateway:
    def __init__(
        self,
        *,
        text: str = "Deterministic research summary.",
        content: dict[str, Any] | None = None,
        failure: AIStatus | None = None,
    ) -> None:
        self.text = text
        self.content = content
        self.failure = failure
        self.requests: list[AIRequest] = []

    def complete(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        if self.failure is not None:
            return AIResponse(
                request_id=request.request_id,
                task=request.task,
                status=self.failure,
                provider_id="custom",
                model_id="small-1",
                error=AIErrorInfo(code=self.failure, message="provider failed"),
            )
        return AIResponse(
            request_id=request.request_id,
            task=request.task,
            status=AIStatus.OK,
            provider_id="custom",
            model_id="small-1",
            text=self.text,
            content=self.content,
        )


def test_research_summary_success_is_classified() -> None:
    gateway = _FakeGateway(
        text="Cost-stressed returns remain positive but thin.",
        content={
            "interpretations": ["edge is small relative to costs"],
            "uncertainties": ["holdout sample is short"],
        },
    )
    summary = summarize_research(METRICS, gateway, generated_at=GENERATED_AT)

    assert summary.status is AIStatus.OK
    assert summary.summary.startswith("Cost-stressed")
    assert "trade_count=412" in summary.facts
    assert "sample_size=1000" in summary.facts
    assert "total_return_bp=123.4" in summary.calculations
    assert summary.interpretations == ("edge is small relative to costs",)
    assert summary.uncertainties == ("holdout sample is short",)
    assert summary.missing == ()
    assert summary.advisory_only is True
    assert summary.promotion_authority is False
    assert summary.candidate_for_paper_testing is False

    request = gateway.requests[0]
    assert request.task is AITask.RESEARCH_SUMMARY
    assert request.output_schema_id == "research_summary.v1"
    assert request.evidence["trade_count"] == "FACT: 412"
    assert request.evidence["total_return_bp"] == "CALCULATION: 123.4"
    assert request.char_size <= 8_000


def test_evidence_classification_is_fact_or_calculation() -> None:
    evidence = build_research_evidence(METRICS)
    assert {item.classification for item in evidence.items} <= {FACT, CALCULATION}
    assert evidence.as_evidence()["dataset_id"] == "FACT: snap-001"
    assert evidence.as_evidence()["profit_factor"] == "CALCULATION: 1.31"


def test_missing_metric_is_not_available_never_fabricated() -> None:
    partial = {"dataset_id": "snap-001", "trade_count": 10}
    evidence = build_research_evidence(partial)
    values = evidence.as_evidence()
    assert values["win_rate"] == f"{CALCULATION}: {NOT_AVAILABLE}"
    assert values["profit_factor"] == f"{CALCULATION}: {NOT_AVAILABLE}"
    assert "win_rate" in evidence.missing
    assert "profit_factor" in evidence.missing

    summary = summarize_research(partial, None)
    assert summary.status is AIStatus.DISABLED
    assert summary.missing == evidence.missing
    assert not any("win_rate=" in fact for fact in summary.calculations)


def test_invalid_metric_is_rejected() -> None:
    with pytest.raises(AIError) as info:
        build_research_evidence({"sharpe_ratio": 2.1})
    assert info.value.status is AIStatus.INVALID_REQUEST
    assert "sharpe_ratio" in info.value.message


@pytest.mark.parametrize("field", sorted(PROMOTION_KEYS))
def test_authority_metric_is_rejected(field: str) -> None:
    with pytest.raises(AIError) as info:
        build_research_evidence({"trade_count": 1, field: True})
    assert info.value.status is AIStatus.INVALID_REQUEST
    assert field in info.value.message


def test_ai_cannot_claim_unavailable_metric() -> None:
    partial = {"dataset_id": "snap-001", "trade_count": 10}
    gateway = _FakeGateway(content={"metric_claims": {"win_rate": 0.9}})
    summary = summarize_research(partial, gateway, generated_at=GENERATED_AT)
    assert summary.status is AIStatus.INVALID_RESPONSE
    assert "fabricated" in summary.note
    assert "win_rate" in summary.missing


def test_ai_cannot_invent_metric() -> None:
    gateway = _FakeGateway(content={"metrics": {"sharpe_ratio": 3.2}})
    summary = summarize_research(METRICS, gateway, generated_at=GENERATED_AT)
    assert summary.status is AIStatus.INVALID_RESPONSE
    assert "sharpe_ratio" in summary.note


@pytest.mark.parametrize("field", ["sharpe_ratio", "alpha_bp", "win_probability"])
def test_hallucinated_top_level_field_is_rejected(field: str) -> None:
    gateway = _FakeGateway(content={field: 1.23})
    summary = summarize_research(METRICS, gateway, generated_at=GENERATED_AT)
    assert summary.status is AIStatus.INVALID_RESPONSE
    assert field in summary.note


def test_ai_promotion_attempt_is_rejected() -> None:
    gateway = _FakeGateway(content={"candidate_for_paper_testing": True})
    summary = summarize_research(METRICS, gateway, generated_at=GENERATED_AT)
    assert summary.status is AIStatus.INVALID_RESPONSE
    assert summary.candidate_for_paper_testing is False
    assert "authority" in summary.note


def test_provider_failure_preserves_deterministic_sections() -> None:
    metrics = copy.deepcopy(METRICS)
    gateway = _FakeGateway(failure=AIStatus.PROVIDER_UNAVAILABLE)
    summary = summarize_research(metrics, gateway, generated_at=GENERATED_AT)
    assert summary.status is AIStatus.PROVIDER_UNAVAILABLE
    assert "trade_count=412" in summary.facts
    assert "total_return_bp=123.4" in summary.calculations
    assert metrics == METRICS


def test_ai_disabled_keeps_deterministic_report() -> None:
    summary = summarize_research(METRICS, None, generated_at=GENERATED_AT)
    assert summary.status is AIStatus.DISABLED
    assert "trade_count=412" in summary.facts
    assert summary.interpretations == ()
    assert summary.candidate_for_paper_testing is False


def test_deterministic_report_is_not_mutated() -> None:
    report = {
        "metrics": {"trade_count": 412, "total_return_bp": 123.4},
        "candidate_for_paper_testing": False,
        "no_trade": True,
        "parameters": {"fee_bp": 4.0},
    }
    before = copy.deepcopy(report)
    summary = summarize_research(METRICS, None, generated_at=GENERATED_AT)
    updated = attach_research_summary(report, summary)

    assert report == before
    assert "ai_advisory" not in report
    assert updated["metrics"] == report["metrics"]
    assert updated["parameters"] == report["parameters"]
    assert updated["candidate_for_paper_testing"] is False
    assert updated["no_trade"] is True
    assert updated["ai_advisory"]["status"] == "disabled"
    assert updated["ai_advisory"]["promotion_authority"] is False


def test_research_summary_cannot_be_promoted_by_construction() -> None:
    with pytest.raises(ValueError, match="promotion authority"):
        ResearchSummary(status=AIStatus.OK, promotion_authority=True)
    with pytest.raises(ValueError, match="paper testing"):
        ResearchSummary(status=AIStatus.OK, candidate_for_paper_testing=True)
    with pytest.raises(ValueError, match="advisory only"):
        ResearchSummary(status=AIStatus.OK, advisory_only=False)


def test_research_evidence_is_bounded_and_secret_free() -> None:
    metrics = dict(METRICS)
    metrics["failure_diagnostics"] = "Bearer sk-abcdefghijklmnop leaked"
    evidence = build_research_evidence(metrics, max_total_chars=600)
    values = evidence.as_evidence()
    assert sum(len(key) + len(value) for key, value in values.items()) <= 700
    assert "sk-abcdefghijklmnop" not in " ".join(values.values())
    assert all(len(value) <= 520 for value in values.values())


def test_stage4_task_vocabulary_is_available() -> None:
    for name in (
        "RESEARCH_SUMMARY",
        "BACKTEST_EXPLANATION",
        "STRATEGY_COMPARISON_SUMMARY",
        "ROBUSTNESS_SUMMARY",
        "FAILURE_ANALYSIS",
        "REGIME_EXPLANATION",
        "RISK_EXPLANATION",
    ):
        assert hasattr(AITask, name)


def test_all_allowlisted_metrics_have_a_classification() -> None:
    assert set(ALLOWED_METRICS) == {
        "dataset_id",
        "exchange",
        "symbol",
        "timeframe",
        "sample_size",
        "train_period",
        "validation_period",
        "holdout_period",
        "trade_count",
        "failure_diagnostics",
        "total_return_bp",
        "max_drawdown_bp",
        "profit_factor",
        "win_rate",
        "cost_stress_return_bp",
        "walk_forward_return_bp",
        "holdout_return_bp",
        "robustness_score",
    }
