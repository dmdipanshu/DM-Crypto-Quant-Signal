"""Budgets + sequential fallback router (Phase 4 Track A, concurrency 1)."""

from __future__ import annotations

from typing import Any, Literal

from crypto_engine.ai_providers.base import AiProvider
from crypto_engine.ai_providers.types import AiRequest, AiResponse

CheckResult = Literal["ok", "budget_exhausted"]


class BudgetTracker:
    """In-memory spend caps (per-task counters, no persistence)."""

    def __init__(
        self,
        max_requests: int | None = 10,
        max_chars: int | None = 200_000,
    ) -> None:
        self.max_requests = max_requests
        self.max_chars = max_chars
        self.requests_used = 0
        self.chars_used = 0

    def check(self, request: AiRequest) -> CheckResult:
        """Return ``ok`` or ``budget_exhausted`` without side effects."""
        if self.max_requests is not None and self.requests_used >= self.max_requests:
            return "budget_exhausted"
        if self.max_chars is not None:
            est = len(request.task) + len(str(request.evidence))
            if self.chars_used + est > self.max_chars:
                return "budget_exhausted"
        return "ok"

    def record(self, request: AiRequest, response: AiResponse) -> None:
        """Account one completed attempt (ok or failure)."""
        self.requests_used += 1
        self.chars_used += len(request.task) + len(response.raw_excerpt)

    def snapshot(self) -> dict[str, Any]:
        """Current counters (no secrets)."""
        return {
            "requests_used": self.requests_used,
            "chars_used": self.chars_used,
            "max_requests": self.max_requests,
            "max_chars": self.max_chars,
        }


def _budget_exhausted_response(
    request: AiRequest, provider_id: str, chain: list[str]
) -> AiResponse:
    from crypto_engine.ai_providers.base import utc_now_iso

    return AiResponse(
        request_id=request.request_id,
        correlation_id=request.correlation_id,
        provider_id=provider_id,
        model_id=request.model_id,
        status="budget_exhausted",
        payload=None,
        raw_excerpt="budget exhausted; request not sent.",
        usage={"truncated": False},
        latency_ms=None,
        retries=0,
        fallback_chain=list(chain),
        validation_errors=["budget exhausted; request not sent."],
        generated_at=utc_now_iso(),
    )


def run_with_fallback(
    primary: AiProvider,
    fallbacks: list[AiProvider],
    request: AiRequest,
    budget: BudgetTracker | None = None,
) -> AiResponse:
    """Try ``primary`` then each fallback sequentially (concurrency 1).

    Stops at the first ``ok``; stops entirely when the budget is exhausted
    (never escalates to a paid fallback). ``correlation_id`` is preserved
    end-to-end via the shared ``request``; ``fallback_chain`` records every
    provider attempted in order.
    """
    chain: list[str] = []
    history: list[dict[str, Any]] = []
    for provider in [primary, *fallbacks]:
        chain.append(provider.provider_id)
        if budget is not None and budget.check(request) != "ok":
            resp = _budget_exhausted_response(request, provider.provider_id, chain)
            resp.fallback_chain = list(chain)
            return resp
        resp = provider.generate(request)
        if budget is not None:
            budget.record(request, resp)
        history.append({"provider_id": provider.provider_id, "status": resp.status})
        resp.fallback_chain = list(chain)
        if resp.status == "ok":
            usage = dict(resp.usage)
            usage["attempt_history"] = history
            resp.usage = usage
            return resp
        if resp.status == "budget_exhausted":
            return resp
    assert isinstance(resp, AiResponse)
    usage = dict(resp.usage)
    usage["attempt_history"] = history
    resp.usage = usage
    return resp


__all__ = ["BudgetTracker", "CheckResult", "run_with_fallback"]
