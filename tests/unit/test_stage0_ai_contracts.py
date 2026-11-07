"""Stage 0 / A0 AI contract tests: validation, bounds, secrets, authority, imports.

Offline and deterministic: no provider SDK, no network, no credentials.
"""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

import crypto_engine
from crypto_engine.ai import (
    MAX_CONCURRENT_AI_REQUESTS,
    MAX_EVIDENCE_KEYS,
    MAX_EVIDENCE_VALUE_CHARS,
    MAX_RETRY_CEILING,
    WORKLOAD_BUDGET_BYTES,
    AICapabilities,
    AIError,
    AIModel,
    AIProviderSpec,
    AIRequest,
    AIResourceLimits,
    AIResponse,
    AIStatus,
    AITask,
    SecretRef,
)

FORBIDDEN_FIELDS = (
    "signal_direction",
    "score",
    "risk_limit",
    "position_size",
    "approval_state",
    "execution_authorized",
    "circuit_breaker",
    "ledger",
)

FORBIDDEN_IMPORTS = (
    "openai",
    "anthropic",
    "google",
    "meta",
    "nvidia",
    "httpx",
    "requests",
    "aiohttp",
    "fastapi",
    "sqlite3",
    "ccxt",
    "torch",
    "tensorflow",
    "transformers",
    "ollama",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_required_failure_states_exist() -> None:
    required = {
        "NOT_CONFIGURED",
        "DISABLED",
        "INVALID_CONFIGURATION",
        "AUTHENTICATION_ERROR",
        "RATE_LIMITED",
        "TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "INVALID_RESPONSE",
        "UNSUPPORTED_CAPABILITY",
        "INTERNAL_ERROR",
    }
    assert required <= {member.name for member in AIStatus}
    assert AIStatus.OK.retryable is False
    assert AIStatus.TIMEOUT.retryable is True
    assert AIStatus.RATE_LIMITED.retryable is True
    assert AIStatus.AUTHENTICATION_ERROR.retryable is False


def test_task_vocabulary_covers_stage0_tasks() -> None:
    required = {
        "signal_explanation",
        "research_summary",
        "market_context",
        "assistant",
        "report_generation",
    }
    assert required <= {task.value for task in AITask}


def test_provider_spec_stores_a_secret_reference_only() -> None:
    spec = AIProviderSpec(
        provider_id="custom",
        display_name="Custom",
        base_url="https://ai.example.test/v1",
        api_key_env="CUSTOM_API_KEY",
        enabled=True,
    )
    assert spec.secret_ref == SecretRef("CUSTOM_API_KEY")
    assert json.dumps(spec.secret_ref.env_var) == '"CUSTOM_API_KEY"'


@pytest.mark.parametrize("bad", ["sk-live-abc", "openai-key", "key with space", ""])
def test_provider_spec_rejects_non_reference_api_key_env(bad: str) -> None:
    with pytest.raises(ValueError):
        AIProviderSpec(provider_id="custom", display_name="Custom", api_key_env=bad)


def test_provider_spec_rejects_credentials_in_base_url() -> None:
    with pytest.raises(ValueError, match="credentials"):
        AIProviderSpec(
            provider_id="custom",
            display_name="Custom",
            base_url="https://user:pass@ai.example.test/v1",
        )


def test_capabilities_reject_tools_and_non_text_modalities() -> None:
    with pytest.raises(ValueError):
        AICapabilities(tool_calling=True)
    with pytest.raises(ValueError):
        AICapabilities(modalities=("text", "image"))
    with pytest.raises(ValueError):
        AICapabilities(structured_output="vendor_specific")


def test_model_ids_are_validated() -> None:
    with pytest.raises(ValueError):
        AIModel(provider_id="custom", model_id="bad model id")
    model = AIModel(provider_id="custom", model_id="vendor/model-1.0")
    assert model.display_name == "vendor/model-1.0"


def test_request_bounds_are_enforced() -> None:
    with pytest.raises(ValueError):
        AIRequest(
            task=AITask.ASSISTANT,
            task_text="x",
            evidence={f"k{index}": "v" for index in range(MAX_EVIDENCE_KEYS + 1)},
        )
    with pytest.raises(ValueError):
        AIRequest(
            task=AITask.ASSISTANT,
            task_text="x",
            evidence={"k": "v" * (MAX_EVIDENCE_VALUE_CHARS + 1)},
        )
    request = AIRequest(task=AITask.ASSISTANT, task_text="explain", evidence={"signal_id": "s1"})
    assert request.char_size > len("explain")
    with pytest.raises(TypeError):
        request.evidence["other"] = "x"  # type: ignore[index]


def test_request_evidence_is_immutable() -> None:
    evidence = {"signal_id": "s1"}
    request = AIRequest(task=AITask.SIGNAL_EXPLANATION, task_text="explain", evidence=evidence)
    evidence["signal_id"] = "mutated"
    assert request.evidence["signal_id"] == "s1"


def test_resource_limits_default_to_single_flight() -> None:
    limits = AIResourceLimits()
    assert limits.max_concurrent_requests == MAX_CONCURRENT_AI_REQUESTS == 1
    assert limits.max_retries <= MAX_RETRY_CEILING
    assert WORKLOAD_BUDGET_BYTES == 4 * 1024**3


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_concurrent_requests": 2},
        {"max_concurrent_requests": 0},
        {"timeout_s": 0},
        {"max_retries": 99},
        {"max_request_chars": 0},
        {"max_queue_depth": 10_000},
    ],
)
def test_invalid_resource_limits_are_rejected(overrides: dict) -> None:
    with pytest.raises(ValueError):
        AIResourceLimits(**overrides)


def test_secret_ref_never_exposes_value() -> None:
    ref = SecretRef("OPENAI_API_KEY")
    environ = {"OPENAI_API_KEY": "sk-test-secret-value"}
    assert ref.resolve(environ) == "sk-test-secret-value"
    assert ref.is_set(environ) is True
    assert ref.is_set({}) is False
    assert "sk-test-secret-value" not in repr(ref)
    assert "sk-test-secret-value" not in str(ref)
    assert dataclasses.asdict(ref) == {"env_var": "OPENAI_API_KEY"}
    assert json.dumps(dataclasses.asdict(ref)) == '{"env_var": "OPENAI_API_KEY"}'


def test_secret_ref_missing_raises_authentication_error() -> None:
    with pytest.raises(AIError) as info:
        SecretRef("MISSING_KEY").require({})
    assert info.value.status is AIStatus.AUTHENTICATION_ERROR
    assert "MISSING_KEY" in info.value.message


def test_response_is_frozen_advisory_data() -> None:
    response = AIResponse(request_id="r1", task=AITask.SIGNAL_EXPLANATION, text="advisory only")
    assert response.advisory_only is True
    assert response.ok is True
    for name in FORBIDDEN_FIELDS:
        assert not hasattr(response, name)
    with pytest.raises(dataclasses.FrozenInstanceError):
        response.text = "mutated"  # type: ignore[misc]
    with pytest.raises(ValueError):
        AIResponse(request_id="r1", task=AITask.ASSISTANT, text="x", advisory_only=False)


def test_failed_response_requires_an_error_record() -> None:
    with pytest.raises(ValueError):
        AIResponse(request_id="r1", task=AITask.ASSISTANT, status=AIStatus.TIMEOUT)
    with pytest.raises(ValueError):
        AIResponse(request_id="r1", task=AITask.ASSISTANT)


def test_error_messages_are_redacted() -> None:
    error = AIError(AIStatus.PROVIDER_UNAVAILABLE, "upstream failed api_key=sk-live-supersecret")
    assert "sk-live-supersecret" not in error.message
    assert "<redacted>" in error.message


def test_ai_package_is_provider_neutral() -> None:
    package = Path(crypto_engine.__file__).parent / "ai"
    files = sorted(package.glob("*.py"))
    assert files, "expected the Stage 0 ai package to exist"
    for path in files:
        modules = _imported_modules(path)
        for module in modules:
            for forbidden in FORBIDDEN_IMPORTS:
                assert not (module == forbidden or module.startswith(f"{forbidden}.")), (
                    f"{path.name} imports forbidden module {module}"
                )
            assert not module.startswith("crypto_engine.ai_providers"), (
                f"{path.name} imports the legacy Phase 4 adapter layer"
            )
            for forbidden_package in (
                "crypto_engine.risk",
                "crypto_engine.signals",
                "crypto_engine.paper_trading",
                "crypto_engine.backtesting",
                "crypto_engine.dashboard",
                "crypto_engine.api",
            ):
                assert not module.startswith(forbidden_package), (
                    f"{path.name} imports {module} outside the AI boundary"
                )
