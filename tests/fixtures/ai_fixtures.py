"""Fake transports + canned provider payloads for offline AI tests (Track A)."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from crypto_engine.agents.schemas import EvidenceItem
from crypto_engine.ai_providers.base import AiProvider, TransportResponse
from crypto_engine.ai_providers.types import AiRequest, AiResponse


def canned_ok(provider_id: str, inner: dict | None = None) -> bytes:
    """Canned 2xx body per provider shape with ``inner`` as the JSON payload."""
    inner = inner if inner is not None else {"verdict": "ok", "confidence": 0.5}
    text = json.dumps(inner)
    if provider_id == "openai":
        return json.dumps(
            {
                "output": [{"content": [{"text": text}]}],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        ).encode()
    if provider_id == "anthropic":
        return json.dumps(
            {"content": [{"text": text}], "usage": {"input_tokens": 10, "output_tokens": 5}}
        ).encode()
    if provider_id == "gemini":
        return json.dumps(
            {
                "candidates": [{"content": {"parts": [{"text": text}]}}],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 15,
                },
            }
        ).encode()
    if provider_id == "ollama":
        return json.dumps(
            {"message": {"content": text}, "prompt_eval_count": 10, "eval_count": 5}
        ).encode()
    # deepseek / openrouter / groq / compatible: chat completions
    return json.dumps(
        {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    ).encode()


def fake_transport(
    bodies: list[TransportResponse | Exception],
    calls: list[dict[str, Any]],
) -> Any:
    """Build a recording fake transport replaying ``bodies`` in order."""

    def _send(method: str, url: str, headers: dict, body: bytes | None, timeout: float):
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        item = bodies[min(len(calls) - 1, len(bodies) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    return _send


def ok_response(body: bytes, headers: dict | None = None) -> TransportResponse:
    """Wrap ``body`` as a 200 transport response."""
    return TransportResponse(status_code=200, headers=headers or {}, body=body)


def err_response(
    status: int, body: bytes = b"{}", headers: dict | None = None
) -> TransportResponse:
    """Wrap ``body`` as an error transport response."""
    return TransportResponse(status_code=status, headers=headers or {}, body=body)


__all__ = ["canned_ok", "err_response", "fake_transport", "ok_response"]


# --- Track B additions (append-only): analyst/ablation fixtures ----------------
# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


def make_evidence(
    n_quant: int = 2, n_news: int = 2, base_ts: str = "2024-01-01T00:00:00+00:00"
) -> list[EvidenceItem]:
    """Small deterministic evidence set (quant + news + regime + model)."""
    items: list[EvidenceItem] = []
    for i in range(n_quant):
        items.append(
            EvidenceItem(
                id=f"q{i}",
                kind="feature_summary",
                source="quant-engine",
                observed_at=base_ts,
                payload={
                    "rsi_14": 55.0 + i,
                    "trend_ratio_20_50": 0.02 * (i + 1),
                    "realized_vol_20": 0.3,
                },
                trust="quant",
            )
        )
    for i in range(n_news):
        items.append(
            EvidenceItem(
                id=f"n{i}",
                kind="news",
                source="news-wire",
                observed_at=base_ts,
                payload={"headline": f"headline {i}", "sentiment": 0.2 * (i + 1)},
                trust="external",
            )
        )
    items.append(
        EvidenceItem(
            id="r0",
            kind="regime",
            source="regime-model",
            observed_at=base_ts,
            payload={"label": "trend", "confidence": 0.6, "score": 0.3},
            trust="model",
        )
    )
    items.append(
        EvidenceItem(
            id="m0",
            kind="model_output",
            source="quant-engine",
            observed_at=base_ts,
            payload={"p_up": 0.62, "prediction": 1},
            trust="model",
        )
    )
    items.append(
        EvidenceItem(
            id="c0",
            kind="macro",
            source="onchain-feed",
            observed_at=base_ts,
            payload={"metric": "funding_rate", "value": 0.0001, "score": 0.1},
            trust="external",
        )
    )
    return items


class FakeProvider(AiProvider):
    """Offline canned provider for analyst tests (no network, no SDKs)."""

    provider_id = "fake"
    capabilities: ClassVar[dict] = {
        "structured_output": True,
        "system_messages": False,
        "token_usage": False,
        "retry_after": False,
    }

    def __init__(
        self,
        model_id: str = "fake-1",
        canned: dict | None = None,
        status: str = "ok",
        **kw,
    ) -> None:
        super().__init__(model_id, enabled=True, **kw)
        self.canned = dict(canned or {"summary": "canned note", "score": 0.1})
        self.forced_status = status
        self.seen_tasks: list[str] = []
        self.calls: list[AiRequest] = []

    def build_request(self, request: AiRequest, api_key: str | None):
        return "fake://local", {}, {"task": request.task, "evidence": request.evidence}

    def parse_success(self, request: AiRequest, body_text: str):
        return dict(self.canned), {"truncated": False}

    def generate(self, request: AiRequest) -> AiResponse:  # type: ignore[override]
        """Canned outcome; records the task string for injection assertions."""
        from crypto_engine.ai_providers.base import utc_now_iso

        self.seen_tasks.append(request.task)
        self.calls.append(request)
        if self.forced_status != "ok":
            return AiResponse(
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                provider_id=self.provider_id,
                model_id=request.model_id,
                status=self.forced_status,  # type: ignore[arg-type]
                payload=None,
                raw_excerpt=f"fake forced {self.forced_status}",
                usage={"truncated": False},
                latency_ms=0.0,
                retries=0,
                fallback_chain=[],
                validation_errors=[f"fake forced {self.forced_status}"],
                generated_at=utc_now_iso(),
            )
        return AiResponse(
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            status="ok",
            payload=dict(self.canned),
            raw_excerpt="canned",
            usage={"truncated": False},
            latency_ms=0.0,
            retries=0,
            fallback_chain=[],
            validation_errors=[],
            generated_at=utc_now_iso(),
        )
