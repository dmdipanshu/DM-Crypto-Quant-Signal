"""Stage 12 architecture tests: dependency rules enforcement.

These offline, static (AST) checks enforce DEPENDENCY_RULES.md DR-1 … DR-12
against the current codebase. They do not call a provider, do not need
credentials and do not mutate the repository.

DR-1..DR-12 are written for the target layered architecture (domain/, ports/,
application/, etc.). Where a target package does not yet exist, the test
records its absence rather than failing. Where the current structure already
realises a rule's intent, the test enforces it.
"""

from __future__ import annotations

import ast
import re
import tempfile
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "crypto_engine"

# Layers as they exist today (target layout in DEPENDENCY_RULES.md §2).
PRESENTATION = ("cli", "api", "dashboard")
INFRASTRUCTURE = (
    "market_data",
    "storage",
    "execution",
    "scheduler",
    "notifications",
    "observability",
)
RESEARCH = ("research", "backtesting", "validation", "training")
EXECUTION_OR_CREDENTIAL = (
    "crypto_engine.execution",
    "crypto_engine.paper_trading",
    "crypto_engine.scheduler",
)


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


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


def _matches(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


def _assert_no_imports(paths: list[Path], forbidden: tuple[str, ...]) -> None:
    for path in paths:
        for module in _imports(path):
            for prefix in forbidden:
                assert not _matches(module, prefix), f"{path.name} imports forbidden {module}"


def _all_src_files() -> list[Path]:
    return _python_files(SRC)


def _import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for path in _all_src_files():
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
                found.append((*tuple(stack[stack.index(dep) :]), dep))
            elif color[dep] == 0:
                visit(dep)
        stack.pop()
        color[node] = 2

    for node in sorted(graph):
        if color[node] == 0:
            visit(node)
    return found


# ---------------------------------------------------------------------------
# DR-7: No import cycles
# ---------------------------------------------------------------------------


def test_no_import_cycles() -> None:
    """The internal import graph must be a DAG (DR-7)."""
    graph = _import_graph()
    cycles = _cycles(graph)
    assert not cycles, cycles


# ---------------------------------------------------------------------------
# DR-6: Presentation isolation — presentation must not import infrastructure
# ---------------------------------------------------------------------------


def test_presentation_does_not_import_infrastructure() -> None:
    """cli/, api/, dashboard/ must not import concrete infrastructure adapters
    (DR-6). They call application use cases and serialize results."""
    paths = [path for name in PRESENTATION for path in _python_files(SRC / name)]
    forbidden = tuple(f"crypto_engine.{infra}" for infra in INFRASTRUCTURE)
    _assert_no_imports(paths, forbidden)


# ---------------------------------------------------------------------------
# DR-8: Research isolation — research cannot reach execution/credentials
# ---------------------------------------------------------------------------


def test_research_cannot_import_execution_or_credentials() -> None:
    """research/, backtesting/, validation/, training/ must not import
    execution, paper trading, scheduler, or credential-loading code (DR-8)."""
    paths = [path for name in RESEARCH for path in _python_files(SRC / name)]
    _assert_no_imports(paths, (*EXECUTION_OR_CREDENTIAL, "crypto_engine.ai"))


# ---------------------------------------------------------------------------
# DR-9 / DR-13: AI invisible to the decision path
# ---------------------------------------------------------------------------


def test_signals_risk_do_not_import_ai() -> None:
    """signals/ and risk/ must not import ai/ or ai_providers/ (DR-9, DR-13)."""
    paths = _python_files(SRC / "signals") + _python_files(SRC / "risk")
    _assert_no_imports(paths, ("crypto_engine.ai", "crypto_engine.ai_providers"))


def test_core_pipeline_does_not_import_ai() -> None:
    """data/, datasets/, features/, labels/, models/, backtesting/,
    paper_trading/, registry/, calibration/, training/, validation/ must not
    import ai/ or ai_providers/ (DR-13)."""
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
    _assert_no_imports(paths, ("crypto_engine.ai", "crypto_engine.ai_providers"))


# ---------------------------------------------------------------------------
# DR-10: Optional modules are leaves
# ---------------------------------------------------------------------------


def test_optional_modules_are_leaves() -> None:
    """ai/, ai_providers/ must not be imported by any core use case module
    (DR-10). They are constructed only when enabled."""
    core = (
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
        "signals",
        "risk",
    )
    paths = [path for name in core for path in _python_files(SRC / name)]
    _assert_no_imports(paths, ("crypto_engine.ai", "crypto_engine.ai_providers"))


# ---------------------------------------------------------------------------
# DR-15: One AI entry point — ai.providers.* imported only by composition root
# ---------------------------------------------------------------------------


def test_ai_providers_imported_only_by_composition_root() -> None:
    """crypto_engine.ai.providers.* may be imported only by
    ai/composition.py (DR-15). gateway.py, registry.py, contracts.py must
    stay adapter-free."""
    adapter_importers = []
    for path in _all_src_files():
        module = _module_name(path)
        # Skip the providers package itself (its __init__ re-exports its own
        # submodules — that is not a violation of DR-15).
        if module == "crypto_engine.ai.providers":
            continue
        imports = _imports(path)
        if (
            any(_matches(m, "crypto_engine.ai.providers") for m in imports)
            and module != "crypto_engine.ai.composition"
        ):
            adapter_importers.append(module)
    assert not adapter_importers, adapter_importers


# ---------------------------------------------------------------------------
# Target-layout absence recording (DR-1, DR-2, DR-3, DR-4, DR-5, DR-11, DR-12)
# ---------------------------------------------------------------------------


def test_target_packages_absence_recorded() -> None:
    """The target layered packages (domain/, ports/, application/) — domain/
    and ports/ now exist (Stage 2 complete); application/ does not yet."""
    missing = []
    for pkg in ("domain", "ports", "application"):
        if not (SRC / pkg).is_dir():
            missing.append(pkg)
    # domain/ and ports/ are now implemented (Stage 2). application/
    # remains absent, awaiting Stage 9 (IMPLEMENTATION_ROADMAP.md).
    assert missing == ["application"], missing


def test_bootstrap_absence_recorded() -> None:
    """bootstrap.py (composition root) now exists (Stage 8 complete).
    DR-12 is enforced via the architecture test suite itself."""
    assert (SRC / "bootstrap.py").is_file()


# ---------------------------------------------------------------------------
# DR-16 / DR-17: Registry hygiene and AI output storage (covered by
# test_stage5_architecture.py; re-asserted here for completeness)
# ---------------------------------------------------------------------------


def test_registry_contains_no_secret_literals() -> None:
    """The ai/ package contains no hard-coded credential literals (DR-16)."""
    patterns = (
        re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"(?i)authorization\s*[:=]\s*['\"][^{]"),
    )
    for path in _python_files(SRC / "ai"):
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not pattern.search(text), f"{path.name} contains a credential-shaped literal"


# ---------------------------------------------------------------------------
# API route table: GET-only assertion
# ---------------------------------------------------------------------------


def test_api_routes_are_get_only() -> None:
    """The FastAPI route table registers only GET methods (DR-6 presentation
    rule + Phase 7 read-only contract). Non-GET routes abort startup."""
    pytest.importorskip("fastapi")
    from crypto_engine.api.app import create_app
    from crypto_engine.api.repos import RepoRoots
    from crypto_engine.config.settings import load_settings

    with tempfile.TemporaryDirectory() as td:
        settings = load_settings()
        roots = RepoRoots(
            signals_dir=td + "/signals",
            registry_dir=td + "/registry",
            data_storage=td + "/storage",
            backtest_dir=td + "/backtests",
            paper_path=td + "/paper.json",
            experiments_path=td + "/experiments.jsonl",
            holdout_ledger_path=td + "/holdout.json",
            scan_cap=5000,
        )
        app = create_app(roots, settings)
    non_get: set[str] = set()
    for route in app.routes:
        if hasattr(route, "methods") and route.methods:
            non_get.update(m for m in route.methods if m not in ("GET", "HEAD"))
    assert not non_get, f"non-GET methods found: {non_get}"


__all__ = [
    "test_ai_providers_imported_only_by_composition_root",
    "test_api_routes_are_get_only",
    "test_bootstrap_absence_recorded",
    "test_core_pipeline_does_not_import_ai",
    "test_no_import_cycles",
    "test_optional_modules_are_leaves",
    "test_presentation_does_not_import_infrastructure",
    "test_registry_contains_no_secret_literals",
    "test_research_cannot_import_execution_or_credentials",
    "test_signals_risk_do_not_import_ai",
    "test_target_packages_absence_recorded",
]
