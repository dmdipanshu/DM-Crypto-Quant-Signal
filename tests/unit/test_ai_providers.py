"""Offline provider-adapter tests: mocked transports only, no keys, no network."""

from __future__ import annotations

import json

import pytest

from crypto_engine.ai_providers import anthropic_adapter as anth
from crypto_engine.ai_providers import openrouter_adapter as oro
from crypto_engine.ai_providers.base import AiProvider
from crypto_engine.ai_providers.budget import BudgetTracker, run_with_fallback
from crypto_engine.ai_providers.factory import SUPPORTED_IDS, create_provider, enabled_providers
from crypto_engine.ai_providers.types import AiRequest
from tests.fixtures.ai_fixtures import canned_ok, err_response, fake_transport, ok_response

API_KEY = "sk-test-0123456789abcdef"


def _req(**kw):
    base = {
        "provider_id": "openai",
        "model_id": "gpt-4o-mini",
        "task": "Assess trend.",
        "evidence": {"rsi": 55.0},
        "schema_name": "analysis",
        "timeout_s": 5.0,
        "max_retries": 2,
        "correlation_id": "corr-1",
    }
    base.update(kw)
    return AiRequest(**base)


@pytest.fixture
def env_keys(monkeypatch):
    """Set all provider keys to a known fake value."""
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
    ):
        monkeypatch.setenv(var, API_KEY)


# -- request mapping ------------------------------------------------------


def test_openai_request_mapping(env_keys):
    """OpenAI: Responses URL, Bearer auth, json_schema format."""
    calls: list = []
    provider = create_provider(
        "openai", "gpt-4o-mini", transport=fake_transport([ok_response(canned_ok("openai"))], calls)
    )
    resp = provider.generate(_req())
    assert resp.status == "ok"
    assert calls[0]["url"] == "https://api.openai.com/v1/responses"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {API_KEY}"
    body = json.loads(calls[0]["body"])
    assert body["model"] == "gpt-4o-mini"
    assert body["text"]["format"]["type"] == "json_schema"
    assert "Assess trend." in body["input"]


def test_anthropic_request_mapping(env_keys):
    """Anthropic: version header, x-api-key, system+messages."""
    calls: list = []
    provider = create_provider(
        "anthropic",
        "claude-3-5-haiku-latest",
        transport=fake_transport([ok_response(canned_ok("anthropic"))], calls),
    )
    resp = provider.generate(_req(provider_id="anthropic", model_id="claude-3-5-haiku-latest"))
    assert resp.status == "ok"
    assert calls[0]["url"] == "https://api.anthropic.com/v1/messages"
    assert calls[0]["headers"]["x-api-key"] == API_KEY
    assert calls[0]["headers"]["anthropic-version"] == anth.API_VERSION
    body = json.loads(calls[0]["body"])
    assert "Assess trend." in body["system"]
    assert body["messages"][0]["role"] == "user"


def test_gemini_request_mapping(env_keys):
    """Gemini: model in path, JSON mime, key kept out of response."""
    calls: list = []
    provider = create_provider(
        "gemini",
        "gemini-2.0-flash",
        transport=fake_transport([ok_response(canned_ok("gemini"))], calls),
    )
    resp = provider.generate(_req(provider_id="gemini", model_id="gemini-2.0-flash"))
    assert resp.status == "ok"
    assert "gemini-2.0-flash:generateContent" in calls[0]["url"]
    body = json.loads(calls[0]["body"])
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert API_KEY not in resp.raw_excerpt


def test_gemini_alt_key_env(monkeypatch):
    """Gemini falls back to GOOGLE_API_KEY when GEMINI_API_KEY is unset."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", API_KEY)
    calls: list = []
    provider = create_provider(
        "gemini",
        "gemini-2.0-flash",
        transport=fake_transport([ok_response(canned_ok("gemini"))], calls),
    )
    assert provider.generate(_req()).status == "ok"
    assert "key=" in calls[0]["url"]


def test_deepseek_request_mapping(env_keys):
    """DeepSeek: chat completions URL, Bearer, json_object."""
    calls: list = []
    provider = create_provider(
        "deepseek",
        "deepseek-chat",
        transport=fake_transport([ok_response(canned_ok("deepseek"))], calls),
    )
    resp = provider.generate(_req(provider_id="deepseek", model_id="deepseek-chat"))
    assert resp.status == "ok"
    assert calls[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert calls[0]["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert json.loads(calls[0]["body"])["response_format"] == {"type": "json_object"}


def test_openrouter_request_mapping(env_keys):
    """OpenRouter: Bearer + json_schema + optional attribution headers."""
    calls: list = []
    provider = create_provider(
        "openrouter",
        "openai/gpt-4o-mini",
        transport=fake_transport([ok_response(canned_ok("openrouter"))], calls),
        extra_headers={"HTTP-Referer": "https://example.test", "X-Title": "sf-test"},
    )
    resp = provider.generate(_req(provider_id="openrouter"))
    assert resp.status == "ok"
    assert calls[0]["url"] == oro.DEFAULT_BASE_URL + "/chat/completions"
    assert calls[0]["headers"]["HTTP-Referer"] == "https://example.test"
    assert json.loads(calls[0]["body"])["response_format"]["type"] == "json_schema"


def test_groq_request_mapping(env_keys):
    """Groq: OpenAI-compatible base URL, Bearer auth."""
    calls: list = []
    provider = create_provider(
        "groq",
        "llama-3.1-8b-instant",
        transport=fake_transport([ok_response(canned_ok("groq"))], calls),
    )
    assert provider.generate(_req(provider_id="groq")).status == "ok"
    assert calls[0]["url"].startswith("https://api.groq.com/openai/v1/chat/completions")


def test_ollama_request_mapping_no_auth():
    """Ollama: local /api/chat, format json, stream false, no auth header."""
    calls: list = []
    provider = create_provider(
        "ollama", "llama3.1:8b", transport=fake_transport([ok_response(canned_ok("ollama"))], calls)
    )
    resp = provider.generate(_req(provider_id="ollama", model_id="llama3.1:8b"))
    assert resp.status == "ok"
    assert calls[0]["url"] == "http://localhost:11434/api/chat"
    assert "Authorization" not in calls[0]["headers"]
    assert "x-api-key" not in calls[0]["headers"]
    body = json.loads(calls[0]["body"])
    assert body["format"] == "json" and body["stream"] is False


def test_compatible_request_mapping(env_keys):
    """Compatible: custom base_url honored, json_schema by default."""
    calls: list = []
    provider = create_provider(
        "compatible",
        "custom-model",
        config={"base_url": "https://llm.example.test/v1", "api_key_env": "OPENAI_API_KEY"},
        transport=fake_transport([ok_response(canned_ok("compatible"))], calls),
    )
    assert provider.generate(_req(provider_id="compatible")).status == "ok"
    assert calls[0]["url"] == "https://llm.example.test/v1/chat/completions"


def test_compatible_plain_json_object(env_keys):
    """Compatible with supports_json_schema=False sends json_object."""
    calls: list = []
    provider = create_provider(
        "compatible",
        "custom-model",
        config={"base_url": "https://llm.example.test/v1", "api_key_env": "OPENAI_API_KEY"},
        transport=fake_transport([ok_response(canned_ok("compatible"))], calls),
        supports_json_schema=False,
    )
    assert provider.generate(_req()).status == "ok"
    assert json.loads(calls[0]["body"])["response_format"] == {"type": "json_object"}


# -- structured parse -----------------------------------------------------


@pytest.mark.parametrize("pid", SUPPORTED_IDS)
def test_structured_parse_ok_all_providers(pid, env_keys):
    """Every adapter parses its canned 2xx shape to payload + usage."""
    model = {"compatible": "custom-model", "ollama": "llama3.1:8b"}.get(pid, "m")
    kwargs: dict = {}
    if pid == "compatible":
        kwargs = {
            "config": {"base_url": "https://llm.example.test/v1", "api_key_env": "OPENAI_API_KEY"}
        }
    calls: list = []
    provider = create_provider(
        pid,
        model,
        transport=fake_transport(
            [ok_response(canned_ok(pid, {"verdict": "long", "confidence": 0.7}))], calls
        ),
        **kwargs,
    )
    resp = provider.generate(_req(provider_id=pid, model_id=model))
    assert resp.status == "ok", resp
    assert resp.payload == {"verdict": "long", "confidence": 0.7}
    assert resp.usage.get("truncated") is False


def test_invalid_json_output(env_keys):
    """Non-JSON 2xx body -> invalid_output (never ok)."""
    calls: list = []
    provider = create_provider(
        "openai",
        "gpt-4o-mini",
        transport=fake_transport([ok_response(canned_ok("openai")[:0] + b"not json{{{")], calls),
    )
    resp = provider.generate(_req(max_retries=0))
    assert resp.status == "invalid_output"
    assert resp.payload is None


def test_non_object_json_output(env_keys):
    """JSON array 2xx body -> invalid_output."""
    calls: list = []
    provider = create_provider(
        "deepseek",
        "deepseek-chat",
        transport=fake_transport(
            [ok_response(json.dumps({"choices": [{"message": {"content": "[1,2]"}}]}).encode())],
            calls,
        ),
    )
    resp = provider.generate(_req(max_retries=0))
    assert resp.status == "invalid_output"


def test_timeout_mapping():
    """Transport TimeoutError -> timeout status."""
    provider = create_provider(
        "ollama",
        "llama3.1:8b",
        transport=fake_transport([TimeoutError("timed out")], []),
    )
    resp = provider.generate(_req(provider_id="ollama", max_retries=0))
    assert resp.status == "timeout"


def test_auth_failure_no_retry(env_keys):
    """HTTP 401 -> auth_failure with exactly one attempt (no retry)."""
    calls: list = []
    provider = create_provider(
        "openai",
        "gpt-4o-mini",
        transport=fake_transport([err_response(401, b'{"error":"bad key"}')], calls),
    )
    resp = provider.generate(_req())
    assert resp.status == "auth_failure"
    assert len(calls) == 1
    assert resp.retries == 0


def test_rate_limited_retry_after(env_keys):
    """HTTP 429 -> rate_limited; Retry-After captured; retried then ok."""
    calls: list = []
    provider = create_provider(
        "groq",
        "llama-3.1-8b-instant",
        transport=fake_transport(
            [err_response(429, b"{}", {"retry-after": "2"}), ok_response(canned_ok("groq"))], calls
        ),
    )
    resp = provider.generate(_req(provider_id="groq"))
    assert resp.status == "ok"
    assert resp.retries == 1
    assert resp.usage.get("truncated") is False


def test_rate_limited_header_captured(env_keys):
    """Persistent 429 surfaces retry_after_s from the header."""
    provider = create_provider(
        "groq", "m", transport=fake_transport([err_response(429, b"{}", {"Retry-After": "7"})], [])
    )
    resp = provider.generate(_req(provider_id="groq", max_retries=0))
    assert resp.status == "rate_limited"
    assert resp.usage.get("retry_after_s") == 7.0


def test_server_error_outage(env_keys):
    """HTTP 500 -> provider_outage."""
    provider = create_provider(
        "openai", "gpt-4o-mini", transport=fake_transport([err_response(500, b"boom")], [])
    )
    assert provider.generate(_req(max_retries=0)).status == "provider_outage"


def test_not_found_unsupported(env_keys):
    """HTTP 404 -> unsupported (model/endpoint unknown)."""
    provider = create_provider(
        "openai", "nope", transport=fake_transport([err_response(404, b"no such model")], [])
    )
    assert provider.generate(_req(max_retries=0)).status == "unsupported"


def test_bounded_retry_then_ok(env_keys):
    """Two outages then ok with max_retries=2 -> retries == 2."""
    calls: list = []
    provider = create_provider(
        "openai",
        "gpt-4o-mini",
        transport=fake_transport(
            [err_response(500, b"a"), err_response(503, b"b"), ok_response(canned_ok("openai"))],
            calls,
        ),
    )
    resp = provider.generate(_req())
    assert resp.status == "ok"
    assert resp.retries == 2
    assert len(calls) == 3


def test_retries_exhausted(env_keys):
    """Always-500 with max_retries=2 -> outage after exactly 3 attempts."""
    calls: list = []
    provider = create_provider(
        "openai", "gpt-4o-mini", transport=fake_transport([err_response(500, b"x")], calls)
    )
    resp = provider.generate(_req())
    assert resp.status == "provider_outage"
    assert len(calls) == 3


def test_missing_api_key_no_transport_call(monkeypatch):
    """Missing env key -> auth_failure before any transport use."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls: list = []
    provider = create_provider(
        "openai", "gpt-4o-mini", transport=fake_transport([ok_response(canned_ok("openai"))], calls)
    )
    resp = provider.generate(_req(max_retries=2))
    assert resp.status == "auth_failure"
    assert calls == []


def test_disabled_provider_short_circuits(env_keys):
    """enabled=False -> disabled status, transport untouched."""
    calls: list = []
    provider = create_provider(
        "openai",
        "gpt-4o-mini",
        transport=fake_transport([ok_response(canned_ok("openai"))], calls),
        enabled=False,
    )
    resp = provider.generate(_req())
    assert resp.status == "disabled"
    assert calls == []


def test_connection_refused_actionable():
    """Ollama connection failure -> outage with actionable message."""
    provider = create_provider(
        "ollama",
        "llama3.1:8b",
        transport=fake_transport([ConnectionError("refused")], []),
    )
    resp = provider.generate(_req(provider_id="ollama", max_retries=0))
    assert resp.status == "provider_outage"
    assert "ollama pull" in resp.raw_excerpt.lower() or "running" in resp.raw_excerpt.lower()


def test_truncation_flag(env_keys):
    """Oversized body is truncated; usage flag set; excerpt bounded."""
    inner = json.dumps({"verdict": "y" * 3000})
    raw = json.dumps({"output": [{"content": [{"text": inner}]}], "usage": {}}).encode()
    provider = create_provider(
        "openai",
        "gpt-4o-mini",
        transport=fake_transport([ok_response(raw)], []),
        max_response_chars=100,
    )
    resp = provider.generate(_req(max_retries=0))
    # Truncated mid-JSON -> parse fails, but truncation must be flagged honestly.
    assert resp.usage.get("truncated") is True
    assert len(resp.raw_excerpt) <= 500


def test_factory_unknown_id():
    """Unknown provider_id -> ValueError listing supported ids."""
    with pytest.raises(ValueError, match="openai"):
        create_provider("nope", "m")


def test_factory_supported_ids_complete():
    """All eight Track A providers are registered."""
    assert SUPPORTED_IDS == [
        "anthropic",
        "compatible",
        "deepseek",
        "gemini",
        "groq",
        "ollama",
        "openai",
        "openrouter",
    ]


def test_enabled_providers_default_none():
    """Default settings enable no providers."""
    from crypto_engine.config.settings import Settings

    assert enabled_providers(Settings()) == []


def test_enabled_providers_from_config():
    """enabled=true entries are listed; disabled ones are not."""
    from crypto_engine.config.settings import AiConfig, AiProviderConfig, Settings

    settings = Settings(
        ai=AiConfig(
            providers=[
                AiProviderConfig(provider_id="ollama", model_id="llama3.1:8b", enabled=True),
                AiProviderConfig(provider_id="openai", model_id="gpt-4o-mini", enabled=False),
            ]
        )
    )
    assert enabled_providers(settings) == ["ollama"]


def test_health_info_no_secrets(env_keys):
    """health_check/provider_info never expose the key."""
    provider = create_provider("openai", "gpt-4o-mini")
    blob = json.dumps(provider.health_check()) + json.dumps(provider.provider_info())
    assert API_KEY not in blob


def test_failure_statuses_never_ok(env_keys):
    """ok is returned only for parsed success; failures keep their status."""
    seen = set()
    for code, want in [
        (401, "auth_failure"),
        (429, "rate_limited"),
        (404, "unsupported"),
        (500, "provider_outage"),
    ]:
        p = create_provider(
            "openai", "gpt-4o-mini", transport=fake_transport([err_response(code, b"{}")], [])
        )
        seen.add(p.generate(_req(max_retries=0)).status)
        assert want in seen
    assert "ok" not in seen


# -- fallback + budget --------------------------------------------------------


def _ok_provider(pid: str, model: str, calls: list, **kw) -> AiProvider:
    return create_provider(
        pid, model, transport=fake_transport([ok_response(canned_ok(pid))], calls), **kw
    )


def test_fallback_ordering_primary_outage(env_keys):
    """Primary outage -> fallback ok; chain records both in order."""
    primary = create_provider(
        "openai", "gpt-4o-mini", transport=fake_transport([err_response(500, b"x")], [])
    )
    fb_calls: list = []
    fallback = _ok_provider("ollama", "llama3.1:8b", fb_calls)
    resp = run_with_fallback(primary, [fallback], _req(max_retries=0))
    assert resp.status == "ok"
    assert resp.fallback_chain == ["openai", "ollama"]
    assert resp.correlation_id == "corr-1"
    assert fb_calls, "fallback must have been attempted"


def test_budget_exhaustion_stops_before_paid_fallback(env_keys):
    """Exhausted budget: fallback transport never called; status honest."""
    primary = create_provider(
        "ollama", "llama3.1:8b", transport=fake_transport([err_response(500, b"x")], [])
    )
    fb_calls: list = []
    fallback = _ok_provider("openai", "gpt-4o-mini", fb_calls)
    budget = BudgetTracker(max_requests=0, max_chars=1)
    resp = run_with_fallback(primary, [fallback], _req(max_retries=0), budget)
    assert resp.status == "budget_exhausted"
    assert fb_calls == []
    assert resp.fallback_chain[0] == "ollama"


def test_budget_counts_attempts(env_keys):
    """BudgetTracker accounts each attempt (ok or failure)."""
    budget = BudgetTracker(max_requests=10, max_chars=10**9)
    provider = _ok_provider("ollama", "m", [])
    run_with_fallback(provider, [], _req(provider_id="ollama"), budget)
    assert budget.snapshot()["requests_used"] == 1


def test_all_unavailable_reports_last(env_keys):
    """All providers failing -> last failure status with full chain."""
    p1 = create_provider("openai", "m", transport=fake_transport([err_response(500, b"a")], []))
    p2 = create_provider("ollama", "m", transport=fake_transport([err_response(500, b"b")], []))
    resp = run_with_fallback(p1, [p2], _req(max_retries=0))
    assert resp.status == "provider_outage"
    assert resp.fallback_chain == ["openai", "ollama"]


def test_no_sdk_import_at_module_top():
    """Provider modules must not import SDKs at top level (stdlib urllib only)."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "crypto_engine" / "ai_providers"
    banned = {"openai", "anthropic", "google", "generativeai", "httpx"}
    for path in root.glob("*_adapter.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top_imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_imports.add(node.module.split(".")[0])
        assert not (top_imports & banned), f"{path.name} imports SDK at top: {top_imports & banned}"


def test_correlation_preserved_end_to_end(env_keys):
    """correlation_id survives retries + fallback to the final response."""
    p1 = create_provider("openai", "m", transport=fake_transport([err_response(500, b"a")], []))
    fb_calls: list = []
    p2 = _ok_provider("groq", "m", fb_calls)
    req = _req(correlation_id="trace-abc-123", max_retries=0)
    resp = run_with_fallback(p1, [p2], req)
    assert resp.correlation_id == "trace-abc-123"
    assert resp.request_id == req.request_id
