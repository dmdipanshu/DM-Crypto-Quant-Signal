"""Stage 5 architecture tests: AI advisory boundary, storage roles, no cycles.

These are offline, static (AST) and runtime checks. They do not call a provider,
do not need credentials and do not mutate the repository.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from crypto_engine.ai import (
    AIErrorInfo,
    AIRequest,
    AIResponse,
    AIStatus,
)
from crypto_engine.ai.advisory import (
    AIExplanation,
    ResearchSummary,
    apply_ai_context,
    explain_signal,
)
from crypto_engine.ai.advisory.signal import AUTHORITY_KEYS
from crypto_engine.signals.schema import (
    CostEstimate,
    DecisionInfo,
    PredictionInfo,
    SignalRecord,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "crypto_engine"

PROVIDER_SDKS = (
    "openai",
    "anthropic",
    "google",
    "meta",
    "httpx",
    "requests",
    "aiohttp",
    "fastapi",
)

EXECUTION_OR_RISK = (
    "crypto_engine.risk",
    "crypto_engine.portfolio",
    "crypto_engine.paper_trading",
    "crypto_engine.backtesting",
    "crypto_engine.exchange",
    "crypto_engine.agents",
    "crypto_engine.ai_providers",
)


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(["crypto_engine", *parts])


def _package_name(path: Path) -> str:
    module = _module_name(path)
    return module if path.name == "__init__.py" else module.rsplit(".", 1)[0]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_name(path)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                if node.level > 1:
                    base = base[: -(node.level - 1)]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            if module:
                found.add(module)
    return found


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _assert_no_imports(paths: list[Path], forbidden: tuple[str, ...]) -> None:
    for path in paths:
        for module in _imports(path):
            for prefix in forbidden:
                assert not _matches(module, prefix), f"{path.name} imports forbidden {module}"


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
        "prediction": PredictionInfo(raw_score=0.42, calibration_status="uncalibrated"),
        "market_regime": "trend_down",
        "expected_edge_bp": -3.5,
        "cost_estimate": CostEstimate(fee_bp=4.0, spread_bp=1.0, slippage_bp=2.0),
        "data_quality": "validated",
        "decision": DecisionInfo(
            status="abstained", reason_codes=["EDGE_BELOW_MARGIN"], gate="policy"
        ),
    }
    payload.update(overrides)
    return SignalRecord(**payload)  # type: ignore[arg-type]


def test_ai_core_is_provider_neutral() -> None:
    core = [
        SRC / "ai" / name
        for name in (
            "__init__.py",
            "contracts.py",
            "errors.py",
            "registry.py",
            "gateway.py",
            "configuration.py",
        )
    ]
    forbidden = (
        *PROVIDER_SDKS,
        "sqlite3",
        "crypto_engine.ai.providers",
        "crypto_engine.storage",
        *EXECUTION_OR_RISK,
        "crypto_engine.signals",
        "crypto_engine.data",
        "crypto_engine.models",
    )
    _assert_no_imports(core, forbidden)


def test_gateway_and_registry_never_import_adapters() -> None:
    _assert_no_imports(
        [SRC / "ai" / "gateway.py", SRC / "ai" / "registry.py", SRC / "ai" / "contracts.py"],
        ("crypto_engine.ai.providers", *PROVIDER_SDKS),
    )


def test_advisory_layers_do_not_import_execution_or_risk() -> None:
    _assert_no_imports(
        _python_files(SRC / "ai" / "advisory"),
        (*EXECUTION_OR_RISK, "crypto_engine.ai.providers", *PROVIDER_SDKS),
    )


def test_signals_and_risk_do_not_import_ai() -> None:
    _assert_no_imports(
        _python_files(SRC / "signals") + _python_files(SRC / "risk"),
        ("crypto_engine.ai", "crypto_engine.ai_providers"),
    )


def test_core_deterministic_pipeline_does_not_import_ai() -> None:
    pipeline = (
        "data",
        "datasets",
        "features",
        "labels",
        "models",
        "backtesting",
        "paper_trading",
        "registry",
        "calibration",
        "training",
        "validation",
    )
    paths = [path for name in pipeline for path in _python_files(SRC / name)]
    _assert_no_imports(
        paths,
        ("crypto_engine.ai", "crypto_engine.ai_providers"),
    )


def test_research_cannot_import_execution_mutation() -> None:
    paths = [
        path
        for name in ("backtesting", "validation", "training")
        for path in _python_files(SRC / name)
    ]
    _assert_no_imports(
        paths,
        (
            "crypto_engine.paper_trading",
            "crypto_engine.ai",
            "crypto_engine.execution",
        ),
    )


def test_ai_package_contains_no_credential_literals() -> None:
    patterns = (
        re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"(?i)authorization\s*[:=]\s*['\"](?!\{)"),
    )
    for path in _python_files(SRC / "ai"):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not pattern.search(text), f"{path.name} contains a credential-shaped literal"


def test_only_secret_ref_reads_the_environment() -> None:
    for path in _python_files(SRC / "ai"):
        if path.name == "contracts.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "environ":
                pytest.fail(f"{path.name} reads os.environ outside SecretRef")
            if isinstance(node, ast.Attribute) and node.attr == "getenv":
                pytest.fail(f"{path.name} calls os.getenv outside SecretRef")


def test_storage_roles_remain_separated() -> None:
    sqlite_imports = _imports(SRC / "storage" / "sqlite.py")
    assert "sqlite3" in sqlite_imports
    assert not any(_matches(module, "pandas") for module in sqlite_imports)
    assert not any(_matches(module, "pyarrow") for module in sqlite_imports)

    json_imports = _imports(SRC / "storage" / "json_store.py")
    assert "json" in json_imports
    assert "sqlite3" not in json_imports
    assert not any(_matches(module, "pandas") for module in json_imports)

    market_data = _python_files(SRC / "data") + _python_files(SRC / "datasets")
    _assert_no_imports(market_data, ("sqlite3",))

    ai_files = _python_files(SRC / "ai")
    _assert_no_imports(ai_files, ("sqlite3", "crypto_engine.storage"))


def _import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for path in _python_files(SRC):
        module = _module_name(path)
        graph[module]
        for imported in _imports(path):
            if imported.startswith("crypto_engine"):
                graph[module].add(imported)
    return graph


def _cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    color: dict[str, int] = defaultdict(int)
    stack: list[str] = []
    found: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        color[node] = 1
        stack.append(node)
        for dep in sorted(graph.get(node, ())):
            if dep not in graph:
                continue
            if color[dep] == 1:
                found.append((*stack[stack.index(dep) :], dep))
            elif color[dep] == 0:
                visit(dep)
        stack.pop()
        color[node] = 2

    for node in sorted(graph):
        if color[node] == 0:
            visit(node)
    return found


def test_no_import_cycles_involving_ai_or_storage() -> None:
    cycles = _cycles(_import_graph())
    offenders = [
        cycle
        for cycle in cycles
        if any(node.startswith(("crypto_engine.ai", "crypto_engine.storage")) for node in cycle)
    ]
    assert not offenders, offenders


def test_ai_response_and_explanations_have_no_authority_fields() -> None:
    forbidden = {
        "direction",
        "score",
        "risk_decision",
        "position_size",
        "approved",
        "execution",
        "circuit_breaker",
        "ledger",
    }
    for cls in (AIResponse, AIExplanation):
        names = {field.name for field in dataclasses.fields(cls)}
        assert not names & forbidden, (cls.__name__, names & forbidden)


@pytest.mark.parametrize("field", sorted(AUTHORITY_KEYS))
def test_ai_cannot_assert_authority_over_a_signal(field: str) -> None:
    record = _record()

    class _Gateway:
        def complete(self, request: AIRequest) -> AIResponse:
            return AIResponse(
                request_id=request.request_id,
                task=request.task,
                status=AIStatus.OK,
                provider_id="custom",
                model_id="small-1",
                text="advisory",
                content={field: "LONG" if field == "direction" else 1.0},
            )

    explanation = explain_signal(record, _Gateway())
    updated = apply_ai_context(record, explanation)

    assert explanation.status is AIStatus.INVALID_RESPONSE
    assert updated.direction == "NO_TRADE"
    assert updated.expected_edge_bp == -3.5
    assert updated.decision == record.decision
    assert updated.prediction == record.prediction
    assert updated.ai_context.status == "invalid"


def test_ai_failure_keeps_deterministic_core_operational() -> None:
    record = _record()

    class _FailingGateway:
        def complete(self, request: AIRequest) -> AIResponse:
            return AIResponse(
                request_id=request.request_id,
                task=request.task,
                status=AIStatus.PROVIDER_UNAVAILABLE,
                error=AIErrorInfo(code=AIStatus.PROVIDER_UNAVAILABLE, message="provider is down"),
            )

    updated = apply_ai_context(record, explain_signal(record, _FailingGateway()))
    assert updated.direction == record.direction
    assert updated.decision == record.decision
    assert updated.ai_context.status == "unavailable"


def test_research_summary_has_no_promotion_authority() -> None:
    summary = ResearchSummary(status=AIStatus.DISABLED)
    assert summary.promotion_authority is False
    assert summary.candidate_for_paper_testing is False
    with pytest.raises(ValueError):
        ResearchSummary(status=AIStatus.OK, candidate_for_paper_testing=True)


def test_ai_package_has_no_trading_state_mutation_surface() -> None:
    identifiers = {
        "circuit_breaker",
        "ledger",
        "portfolio",
        "place_order",
        "submit_order",
        "set_position_size",
        "approve_execution",
    }
    for path in _python_files(SRC / "ai"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert node.name not in identifiers, f"{path.name} defines {node.name}()"
