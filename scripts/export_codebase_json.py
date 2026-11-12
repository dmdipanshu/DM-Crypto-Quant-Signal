#!/usr/bin/env python3
"""Export the SignalForge codebase to a machine-readable JSON bundle.

Deterministic apart from export timestamps and repository-state metadata.
Read-only: never modifies source, tests, config, artifacts, or git state.
Stdlib only. Run: ``uv run python scripts/export_codebase_json.py``.

Output: ``reports/generated/signalforge_codebase_documentation.json``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "reports" / "generated" / "signalforge_codebase_documentation.json"

INCLUDE_TOPS = [
    "src",
    "tests",
    "scripts",
    "config",
    ".opencode/skills",
    ".opencode/agents",
    "docs",
    "memory",
]
INCLUDE_FILES = [
    "README.md",
    "AGENTS.md",
    "DECISIONS.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    ".env.example",
    ".gitignore",
    "skills-lock.json",
]

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".agents",
    "data",
    "models",
    "reports",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    "htmlcov",
    ".opencode/.cache",
}
EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".pkl",
    ".pickle",
    ".parquet",
    ".bin",
    ".pt",
    ".pth",
    ".onnx",
    ".h5",
    ".ckpt",
    ".safetensors",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".crt",
}
EXCLUDE_NAMES = {".env", ".env.local", ".env.*.local", "id_rsa", "id_ed25519"}

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".txt",
    ".cfg",
    ".ini",
    ".example",
    ".gitignore",
    "",
    ".jsonc",
}

SECRET_PATTERNS = [
    (
        "pem_block",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
    ),
    (
        "prefixed_token",
        re.compile(
            r"\b(sk-(live|test)-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
            r"|xox[baprs]-[A-Za-z0-9\-]{10,}|AIza[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16})\b"
        ),
    ),
    (
        "secret_assignment",
        re.compile(
            r"(?im)^(?P<key>[A-Za-z0-9_.\- ]*(?:api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|"
            r"auth[_-]?token|password|passwd|pwd|client[_-]?secret|private[_-]?key|session[_-]?token)\b)"
            r"\s*[:=]\s*(?P<q>['\"])(?P<val>(?:(?!(?P=q)).)+)(?P=q)"
        ),
    ),
]

PLACEHOLDER_RE = re.compile(
    r"^(|example|examples|test|testing|changeme|xxx+|your[-_].*|.*placeholder.*|"
    r"<[^>]*>|\$\{[^}]*\}|%[A-Z_]+%|none|null|nil|n/a|todo| dummy|dummy|fake)$",
    re.IGNORECASE,
)


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _is_excluded(path: Path) -> tuple[bool, str]:
    parts = set(path.relative_to(REPO_ROOT).parts) if _is_within(path) else set()
    for d in EXCLUDE_DIRS:
        if d in path.parts or d in parts:
            if "reports" in str(path) and "generated" in str(path):
                return True, "generated-output"
            return True, f"excluded-dir:{d}"
    name = path.name
    if name in EXCLUDE_NAMES or name.startswith(".env."):
        return True, "secrets-adjacent-name"
    if path.suffix.lower() in EXCLUDE_SUFFIXES:
        return True, f"excluded-suffix:{path.suffix.lower()}"
    return False, ""


def _is_within(path: Path) -> bool:
    try:
        path.relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def iter_candidate_files() -> tuple[list[Path], dict[str, int]]:
    """Collect candidate files from include roots; count exclusions by reason."""
    found: list[Path] = []
    excluded: dict[str, int] = {}
    seen: set[str] = set()

    def consider(path: Path) -> None:
        if not path.is_file() or path.is_symlink():
            return
        key = _rel(path)
        if key in seen:
            return
        seen.add(key)
        excluded_flag, reason = _is_excluded(path)
        if excluded_flag:
            excluded[reason] = excluded.get(reason, 0) + 1
            return
        if key == _rel(OUT_PATH):
            excluded["self-output"] = excluded.get("self-output", 0) + 1
            return
        found.append(path)

    for top in INCLUDE_TOPS:
        base = REPO_ROOT / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not _is_excluded(Path(dirpath) / d)[0])
            for fn in sorted(filenames):
                consider(Path(dirpath) / fn)
    # Account for excluded dirs we pruned before descending (approximate count).
    for top in INCLUDE_TOPS:
        base = REPO_ROOT / top
        if base.is_dir():
            continue
    for name in INCLUDE_FILES:
        consider(REPO_ROOT / name)
    # Pruned-dir accounting: walk again counting files under excluded dirs.
    pruned: dict[str, int] = {}
    for top in INCLUDE_TOPS:
        base = REPO_ROOT / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            rel = os.path.relpath(dirpath, REPO_ROOT)
            parts = set(Path(rel).parts)
            hit = next((d for d in EXCLUDE_DIRS if d in parts), None)
            if hit:
                pruned[f"pruned-dir:{hit}"] = pruned.get(f"pruned-dir:{hit}", 0) + len(filenames)
                dirnames.clear()
    for reason, count in pruned.items():
        excluded[reason] = excluded.get(reason, 0) + count
    found.sort(key=_rel)
    return found, excluded


def redact_text(text: str) -> tuple[str, int]:
    """Redact likely credentials; return (redacted_text, redaction_count)."""
    count = 0
    text, n_pem = SECRET_PATTERNS[0][1].subn("[REDACTED-PEM-BLOCK]", text)
    count += n_pem
    text, n_tok = SECRET_PATTERNS[1][1].subn("[REDACTED-TOKEN]", text)
    count += n_tok

    total_assign = [0]

    def _sub_assign2(match: re.Match) -> str:
        val = match.group("val")
        if PLACEHOLDER_RE.match(val.strip()):
            return match.group(0)
        total_assign[0] += 1
        q = match.group("q")
        return match.group(0).replace(q + val + q, q + "[REDACTED]" + q, 1)

    text = SECRET_PATTERNS[2][1].sub(_sub_assign2, text)
    count += total_assign[0]
    return text, count


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node).strip()
    except Exception:
        return None


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    out = []
    for deco in node.decorator_list:
        text = _unparse(deco)
        out.append(text if text is not None else "<unrepresentable>")
    return out


def _func_info(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    klass: str | None,
    parents: tuple[str, ...],
) -> dict:
    args = node.args
    params: list[dict] = []

    def _add(arg: ast.arg, default: ast.AST | None, kind: str) -> None:
        params.append(
            {
                "name": arg.arg,
                "kind": kind,
                "annotation": _unparse(arg.annotation),
                "default": _unparse(default),
            }
        )

    pos_defaults = list(args.defaults)
    pos_start = len(args.posonlyargs) + len(args.args) - len(pos_defaults)
    for i, a in enumerate(list(args.posonlyargs) + list(args.args)):
        default = pos_defaults[i - pos_start] if i >= pos_start else None
        _add(a, default, "positional")
    for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        _add(a, d, "keyword-only")
    if args.vararg is not None:
        _add(args.vararg, None, "var-positional")
    if args.kwarg is not None:
        _add(args.kwarg, None, "var-keyword")

    raises: list[str] = []
    calls: list[str] = []
    nested: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            text = _unparse(child.exc) or "<raise>"
            raises.append(text[:200])
        elif isinstance(child, ast.Call):
            text = _unparse(child.func)
            if text:
                calls.append(text[:200])
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nested.append(child.name)
    seen_calls: list[str] = []
    for call in calls:
        if call not in seen_calls:
            seen_calls.append(call)
    return {
        "name": node.name,
        "qualified_name": ".".join([*parents, node.name]) if parents else node.name,
        "path": path,
        "lineno": node.lineno,
        "end_lineno": node.end_lineno,
        "signature": _unparse(node.args),
        "returns": _unparse(node.returns),
        "docstring": ast.get_docstring(node),
        "decorators": _decorators(node),
        "is_async": isinstance(node, ast.AsyncFunctionDef),
        "class": klass,
        "nested_functions": nested,
        "raises": sorted(set(raises)),
        "calls": seen_calls[:200],
    }


def _class_info(node: ast.ClassDef, path: str, parents: tuple[str, ...]) -> dict:
    methods = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(_func_info(child, path, node.name, (*parents, node.name)))
    return {
        "name": node.name,
        "qualified_name": ".".join([*parents, node.name]) if parents else node.name,
        "path": path,
        "lineno": node.lineno,
        "end_lineno": node.end_lineno,
        "bases": [_unparse(b) or "<unrepresentable>" for b in node.bases],
        "docstring": ast.get_docstring(node),
        "decorators": _decorators(node),
        "methods": [m["name"] for m in methods],
        "method_details": methods,
    }


def parse_python(path: Path, rel: str) -> tuple[dict | None, dict | None]:
    """Parse without importing. Returns (module_record, error_record)."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, {"path": rel, "error": f"unreadable: {exc.__class__.__name__}"}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, {"path": rel, "error": f"non-utf8: {exc}"}
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        return None, {"path": rel, "error": f"syntax-error line {exc.lineno}: {exc.msg}"}
    imports: list[str] = []
    internal_imports: list[str] = []
    for child in ast.walk(tree):
        if isinstance(child, ast.Import):
            for alias in child.names:
                imports.append(alias.name)
                if alias.name == "crypto_engine" or alias.name.startswith("crypto_engine."):
                    internal_imports.append(alias.name)
        elif isinstance(child, ast.ImportFrom):
            mod = "." * (child.level or 0) + (child.module or "")
            imports.append(f"from {mod} import {', '.join(a.name for a in child.names)}")
            if (child.module or "").startswith("crypto_engine") or child.level:
                internal_imports.append(mod or ".")
    functions: list[dict] = []
    classes: list[dict] = []

    def visit(node: ast.AST, klass: str | None, parents: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if klass is None:
                    functions.append(_func_info(child, rel, None, parents))
            elif isinstance(child, ast.ClassDef):
                classes.append(_class_info(child, rel, parents))
                visit(child, child.name, (*parents, child.name))
            elif isinstance(
                child,
                (
                    ast.Module,
                    ast.If,
                    ast.Try,
                    ast.With,
                    ast.For,
                    ast.While,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            ):
                visit(child, klass, parents)

    visit(tree, None, ())
    return {
        "docstring": ast.get_docstring(tree),
        "imports": sorted(set(imports)),
        "internal_imports": sorted(set(internal_imports)),
        "functions": functions,
        "classes": classes,
    }, None


def _typer_apps(tree: ast.Module) -> dict[str, str]:
    """Map variable name -> 'typer.Typer' for Typer() assignments."""
    apps: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            value = node.value
            if isinstance(value, ast.Call):
                func = _unparse(value.func) or ""
                if func.endswith("Typer") or func == "Typer":
                    apps[node.targets[0].id] = func
    return apps


def parse_cli_commands(path: Path, rel: str) -> tuple[list[dict], list[str]]:
    """Statically extract Typer commands from cli.py (best effort)."""
    try:
        tree = ast.parse(path.read_bytes().decode("utf-8"), filename=rel)
    except Exception as exc:
        return [], [f"{rel}: unparseable ({exc.__class__.__name__})"]
    _typer_apps(tree)
    adders: dict[str, str] = {}
    commands: list[dict] = []
    notes: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "add_typer"
                and isinstance(func.value, ast.Name)
            ):
                sub = ""
                name = ""
                for kw in node.value.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        name = str(kw.value.value)
                if node.value.args and isinstance(node.value.args[0], ast.Name):
                    sub = node.value.args[0].id
                if sub and name:
                    adders[sub] = name
    roots = {"app": "sf"}
    for var, sub in adders.items():
        roots[var] = f"sf {sub}"
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
                continue
            if deco.func.attr != "command":
                continue
            holder = deco.func.value.id if isinstance(deco.func.value, ast.Name) else "?"
            cmd_name = None
            if deco.args and isinstance(deco.args[0], ast.Constant):
                cmd_name = str(deco.args[0].value)
            if cmd_name is None:
                cmd_name = node.name.replace("_", "-")
            options = []
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                ann = _unparse(arg.annotation) or ""
                is_option = "typer.Option" in ann or "Option(" in ann
                options.append(
                    {
                        "name": arg.arg,
                        "annotation": ann[:500],
                        "is_typer_option": is_option,
                        "help": _extract_option_help(ann),
                    }
                )
            doc = ast.get_docstring(node) or ""
            commands.append(
                {
                    "path": f"{roots.get(holder, holder)} {cmd_name}".strip(),
                    "function": node.name,
                    "file": rel,
                    "lineno": node.lineno,
                    "help": doc.splitlines()[0] if doc else None,
                    "options": options,
                    "statically_resolved": holder in roots,
                }
            )
            if holder not in roots:
                notes.append(
                    f"{rel}:{node.lineno}: command holder {holder!r} not mapped to a CLI root"
                )
    commands.sort(key=lambda c: c["path"])
    return commands, notes


def _extract_option_help(annotation: str) -> str | None:
    try:
        tree = ast.parse(f"x = {annotation}", mode="eval")
    except SyntaxError:
        return None
    call = tree.body
    if not isinstance(call, ast.Call):
        return None

    def find_kw(node: ast.Call) -> str | None:
        for kw in node.keywords:
            if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        for arg in node.args:
            if isinstance(arg, ast.Call):
                found = find_kw(arg)
                if found is not None:
                    return found
        return None

    return find_kw(call)


def parse_fastapi_routes(path: Path, rel: str) -> tuple[list[dict], list[str]]:
    """Statically extract @router.<method>("<path>") routes (best effort)."""
    try:
        tree = ast.parse(path.read_bytes().decode("utf-8"), filename=rel)
    except Exception as exc:
        return [], [f"{rel}: unparseable ({exc.__class__.__name__})"]
    routes: list[dict] = []
    notes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
                continue
            method = deco.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"}:
                continue
            route_path = None
            if deco.args and isinstance(deco.args[0], ast.Constant):
                route_path = str(deco.args[0].value)
            routes.append(
                {
                    "method": method,
                    "path": route_path if route_path is not None else "<dynamic>",
                    "function": node.name,
                    "file": rel,
                    "lineno": node.lineno,
                    "statically_resolved": route_path is not None,
                }
            )
    routes.sort(key=lambda r: (str(r["path"]), r["method"]))
    return routes, notes


def _git(args: list[str]) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


def _frontmatter(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip("\"'")
    return meta


def build_bundle() -> dict:
    files, excluded_counts = iter_candidate_files()
    file_entries: list[dict] = []
    parse_errors: list[dict] = []
    modules: list[dict] = []
    classes: list[dict] = []
    functions: list[dict] = []
    redaction_total = 0
    redactions_by_category: dict[str, int] = {}
    missing_paths: list[str] = []

    for path in files:
        rel = _rel(path)
        try:
            raw = path.read_bytes()
        except OSError:
            missing_paths.append(rel)
            continue
        sha_source = _sha256(raw)
        suffix = path.suffix.lower()
        is_texty = (
            suffix in TEXT_SUFFIXES
            or path.name
            in {
                "README.md",
                "AGENTS.md",
                "DECISIONS.md",
                "CONTRIBUTING.md",
                ".env.example",
                ".gitignore",
                "SKILL.md",
            }
            or suffix == ""
        )
        if raw[:2] == b"\xff\xfe" or raw[:2] == b"\xfe\xff":
            is_texty = False
        if b"\x00" in raw[:8192]:
            is_texty = False
        if not is_texty:
            excluded_counts["binary-or-unknown-type"] = (
                excluded_counts.get("binary-or-unknown-type", 0) + 1
            )
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            parse_errors.append({"path": rel, "error": "non-utf8-decode"})
            excluded_counts["non-utf8"] = excluded_counts.get("non-utf8", 0) + 1
            continue
        redacted, n_red = redact_text(text)
        if n_red:
            redactions_by_category["likely-credentials"] = (
                redactions_by_category.get("likely-credentials", 0) + n_red
            )
        redaction_total += n_red
        try:
            lines = redacted.count("\n") + (0 if redacted.endswith("\n") or not redacted else 1)
        except Exception:
            lines = 0
        entry: dict = {
            "path": rel,
            "type": suffix.lstrip(".") or "no-extension",
            "sha256": _sha256(redacted.encode("utf-8")),
            "sha256_source": sha_source,
            "redactions_applied": n_red,
            "lines": lines,
            "content": redacted,
        }
        file_entries.append(entry)
        if suffix == ".py":
            record, error = parse_python(path, rel)
            if error is not None:
                parse_errors.append(error)
            elif record is not None:
                record["path"] = rel
                modules.append(record)
                for cls in record["classes"]:
                    classes.append(cls)
                    functions.extend(cls["method_details"])
                    del cls["method_details"]
                for fn in record["functions"]:
                    functions.append(fn)

    cli_commands: list[dict] = []
    cli_notes: list[str] = []
    api_routes: list[dict] = []
    api_notes: list[str] = []
    for entry in file_entries:
        if entry["path"] == "src/crypto_engine/cli.py":
            cmds, notes = parse_cli_commands(REPO_ROOT / entry["path"], entry["path"])
            cli_commands.extend(cmds)
            cli_notes.extend(notes)
        if entry["path"] in (
            "src/crypto_engine/api/routes.py",
            "src/crypto_engine/dashboard/routes.py",
        ):
            routes, notes = parse_fastapi_routes(REPO_ROOT / entry["path"], entry["path"])
            api_routes.extend(routes)
            api_notes.extend(notes)

    data_models: list[dict] = []
    for cls in classes:
        bases = " ".join(cls.get("bases", []))
        if "BaseModel" in bases or "pydantic" in bases:
            data_models.append(
                {
                    "name": cls["name"],
                    "qualified_name": cls["qualified_name"],
                    "path": cls["path"],
                    "docstring": (cls.get("docstring") or "")[:2000],
                }
            )
    data_models.sort(key=lambda m: (m["path"], m["name"]))

    config: dict = {}
    config_path = REPO_ROOT / "config" / "default.toml"
    if config_path.is_file():
        try:
            import tomllib

            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            config = {"_error": f"unparseable: {exc.__class__.__name__}"}

    dependencies: dict = {}
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = data.get("project", {})
            dependencies = {
                "name": project.get("name"),
                "requires_python": project.get("requires-python"),
                "dependencies": project.get("dependencies", []),
                "optional_dependencies": project.get("optional-dependencies", {}),
            }
        except Exception as exc:
            dependencies = {"_error": f"unparseable: {exc.__class__.__name__}"}

    test_files = [
        e for e in file_entries if "/tests/" in ("/" + e["path"]) or e["path"].startswith("tests/")
    ]
    test_count = 0
    for mod in modules:
        if mod["path"].startswith("tests/"):
            test_count += sum(1 for f in mod.get("functions", []) if f["name"].startswith("test_"))

    skills: list[dict] = []
    agents: list[dict] = []
    for entry in file_entries:
        if "/.opencode/skills/" in ("/" + entry["path"]) and entry["path"].endswith("/SKILL.md"):
            meta = _frontmatter(REPO_ROOT / entry["path"])
            skills.append(
                {
                    "id": meta.get("name", Path(entry["path"]).parent.name),
                    "name": meta.get("name"),
                    "description": meta.get("description"),
                    "path": entry["path"],
                    "complete_text_included": True,
                }
            )
        if "/.opencode/agents/" in ("/" + entry["path"]) and entry["path"].endswith(".md"):
            meta = _frontmatter(REPO_ROOT / entry["path"])
            agents.append(
                {
                    "id": Path(entry["path"]).stem,
                    "description": meta.get("description"),
                    "mode": meta.get("mode"),
                    "path": entry["path"],
                    "complete_text_included": True,
                }
            )
    skills.sort(key=lambda s: s["id"])
    agents.sort(key=lambda s: s["id"])

    git_branch = _git(["branch", "--show-current"]) or _git(["rev-parse", "--abbrev-ref", "HEAD"])
    git_commit = _git(["log", "-1", "--format=%H %ad %s", "--date=iso"])
    git_status = _git(["status", "--short"])
    tracked = _git(["ls-files"])
    untracked = _git(["ls-files", "--others", "--exclude-standard"])
    repository_status = {
        "branch": git_branch,
        "latest_commit": git_commit,
        "status_short": git_status,
        "tracked_file_count": len(tracked.splitlines()) if tracked is not None else None,
        "untracked_files": untracked.splitlines() if untracked is not None else None,
        "uncommitted_changes_present": bool((git_status or "").strip()),
    }

    tree: dict[str, Any] = {}
    for entry in file_entries:
        parts = entry["path"].split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        node[parts[-1]] = {
            "type": entry["type"],
            "lines": entry["lines"],
            "sha256": entry["sha256"][:12],
        }

    doc_entries = [
        e
        for e in file_entries
        if e["path"].startswith("docs/")
        or e["path"].startswith("memory/")
        or e["path"] in ("README.md", "AGENTS.md", "DECISIONS.md", "CONTRIBUTING.md")
    ]
    documentation = {
        "files": [
            {
                "path": e["path"],
                "lines": e["lines"],
                "sha256": e["sha256"],
                "complete_text_included": True,
            }
            for e in doc_entries
        ],
        "note": "Full text for each file is in source_files by path.",
    }

    completeness = {
        "scanned_candidate_files": len(files),
        "exported_files": len(file_entries),
        "excluded_files": excluded_counts,
        "documented_functions": len(functions),
        "documented_classes": len(classes),
        "documented_modules": len(modules),
        "cli_commands": len(cli_commands),
        "api_routes": len(api_routes),
        "parsing_errors": parse_errors,
        "missing_or_unreadable_paths": missing_paths,
        "redactions_applied": redaction_total,
        "redactions_by_category": redactions_by_category,
        "skills": len(skills),
        "agents": len(agents),
        "test_files": len(test_files),
        "test_functions_collected_statically": test_count,
    }

    bundle = {
        "schema_version": SCHEMA_VERSION,
        "export_metadata": {
            "generated_at": _utcnow(),
            "generator": "scripts/export_codebase_json.py",
            "project_root": str(REPO_ROOT),
            "deterministic_note": "Byte-identical across runs except generated_at and repository-state metadata.",
        },
        "project_overview": {
            "name": "DM Crypto Quant Signal / SignalForge",
            "kind": "standalone cryptocurrency quantitative signal research platform",
            "entry_points": {
                "cli": "sf (src/crypto_engine/cli.py)",
                "api": "FastAPI app factory (src/crypto_engine/api/app.py), served via `sf serve`",
                "dashboard": "server-rendered HTML (src/crypto_engine/dashboard/)",
            },
            "phase_history": "Phases 1-10 implemented per memory/SESSION_LOG.md and DECISIONS.md (historical context; source and tests are authoritative).",
        },
        "repository_status": repository_status,
        "directory_tree": tree,
        "architecture": {
            "pipeline": (
                "exchange (ccxt, public) -> market data -> features -> models -> "
                "calibration -> [optional AI context] -> signal decision policy -> "
                "risk gates -> validated signal record -> signal audit history -> "
                "backtests / paper sandbox -> read-only API + dashboard"
            ),
            "invariants": [
                "Read-only API and dashboard; no live orders anywhere.",
                "UTC-normalized, provenance-preserving market data; no silent fills.",
                "Deterministic risk gates independent of models/AI; NO_TRADE is valid output.",
                "No secrets in code/TOML/git; accuracy != win rate != profitability.",
            ],
            "source": "docs/ARCHITECTURE.md and AGENTS.md (full text in documentation/source_files).",
        },
        "modules": [
            {
                "path": m["path"],
                "docstring": (m.get("docstring") or "")[:2000],
                "imports": m.get("imports", []),
                "internal_imports": m.get("internal_imports", []),
                "functions": [f["name"] for f in m.get("functions", [])],
                "classes": [c["name"] for c in m.get("classes", [])],
            }
            for m in sorted(modules, key=lambda m: m["path"])
        ],
        "classes": [
            {k: v for k, v in c.items() if k != "method_details"}
            for c in sorted(classes, key=lambda c: (c["path"], c["name"]))
        ],
        "functions": sorted(functions, key=lambda f: (f["path"], f["lineno"])),
        "cli_commands": cli_commands,
        "cli_notes": cli_notes,
        "api_routes": sorted(api_routes, key=lambda r: (str(r["path"]), r["method"])),
        "api_notes": [
            *api_notes,
            "Routes are GET-only by construction; assert_get_only() enforces this at app build.",
            "Statically extracted; dynamic mounts (docs UI) are framework-provided.",
        ],
        "data_models_and_schemas": data_models,
        "configuration": config,
        "dependencies": dependencies,
        "tests": {
            "files": [{"path": e["path"], "lines": e["lines"]} for e in test_files],
            "test_functions_collected_statically": test_count,
            "note": "Counts only; outcomes are not executed by this exporter.",
        },
        "skills_and_agents": {"skills": skills, "agents": agents},
        "documentation": documentation,
        "source_files": [
            {
                "path": e["path"],
                "type": e["type"],
                "sha256": e["sha256"],
                "sha256_source": e["sha256_source"],
                "redactions_applied": e["redactions_applied"],
                "lines": e["lines"],
                "content": e["content"],
            }
            for e in file_entries
        ],
        "security_and_safety": {
            "no_live_orders": "No order placement, cancellation, deposit, withdrawal, or leverage code paths exist; verified by design and grep-based tests.",
            "read_only_api": "All data routes are GET-only; mutating HTTP methods rejected (405).",
            "loopback_default": "Server binds 127.0.0.1 by default; non-loopback requires explicit opt-in.",
            "secrets": "No secrets committed; redaction scan applied during export.",
            "redactions": {"total": redaction_total, "by_category": redactions_by_category},
            "exclusions": excluded_counts,
        },
        "known_limitations": {
            "note": "Curated from docs/RESEARCH_METHODOLOGY.md, docs/SECURITY.md, memory/CURRENT_STATE.md (verbatim text included under documentation/source_files); verify details there.",
            "items": [
                "Research platform only: no live trading, no real-money trading, no public exposure by default.",
                "Backtests are historical simulations, not live evidence; paper fills are hypothetical.",
                "Calibrated probabilities apply only to their documented event; raw scores are rankings, not probabilities or expected returns.",
                "Holdout sets already inspected in prior phases are not pristine; forward validation on genuinely new data is required for stronger claims.",
                "Model scores must not be treated as calibrated probabilities, win rates, or expected returns.",
                "Dashboard shows recorded artifacts only; nothing displayed is a live recommendation.",
                "Laptop constraints: single process, bounded batches, small fixtures; no GPU/LLM dependencies.",
            ],
            "inferred_not_verified": [
                "API endpoint availability statements reflect code, not a live deployment.",
            ],
        },
        "completeness": completeness,
    }
    return bundle


def verify_bundle(bundle: dict, out_path: Path) -> dict[str, Any]:
    """Re-parse and validate the written bundle. Returns a report dict."""
    report: dict[str, Any] = {"passed": True, "checks": []}

    def check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"check": name, "passed": bool(ok), "detail": detail})
        if not ok:
            report["passed"] = False

    raw = out_path.read_bytes()
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        check("json-parseable", False, f"{type(exc).__name__}: {exc}")
        return report
    check("json-parseable", True, f"{len(raw)} bytes")

    required = [
        "schema_version",
        "export_metadata",
        "project_overview",
        "repository_status",
        "directory_tree",
        "architecture",
        "modules",
        "classes",
        "functions",
        "cli_commands",
        "api_routes",
        "data_models_and_schemas",
        "configuration",
        "dependencies",
        "tests",
        "skills_and_agents",
        "documentation",
        "source_files",
        "security_and_safety",
        "known_limitations",
        "completeness",
    ]
    missing_sections = [s for s in required if s not in loaded]
    check("required-sections", not missing_sections, f"missing={missing_sections}")

    by_path = {e["path"]: e for e in loaded.get("source_files", [])}
    hash_mismatch: list[str] = []
    content_missing: list[str] = []
    for path, entry in by_path.items():
        for key in ("path", "type", "sha256", "sha256_source", "lines", "content"):
            if key not in entry:
                content_missing.append(f"{path}:{key}")
        full = REPO_ROOT / path
        if full.is_file() and _sha256(full.read_bytes()) != entry.get("sha256_source"):
            hash_mismatch.append(path)
        if not isinstance(entry.get("content"), str) or not entry.get("content"):
            # empty files are allowed to have empty content
            if entry.get("lines", 0) not in (0, 1):
                content_missing.append(f"{path}:empty-content")
        else:
            if _sha256(entry["content"].encode("utf-8")) != entry.get("sha256"):
                hash_mismatch.append(path + ":redacted-hash")
    check("source-hash-integrity", not hash_mismatch, f"mismatches={hash_mismatch[:10]}")
    check("complete-source-text", not content_missing, f"issues={content_missing[:10]}")

    text = raw.decode("utf-8")
    leaks: list[str] = []
    for _name, pattern in SECRET_PATTERNS[:2]:
        for match in pattern.finditer(text):
            leaks.append(match.group(0)[:24])
            if len(leaks) >= 5:
                break
    check("no-secret-patterns", not leaks, f"hits={leaks}")
    paths_in_bundle = [_rel(OUT_PATH)]
    self_included = any(e in by_path for e in paths_in_bundle)
    check("no-self-inclusion", not self_included, f"output={paths_in_bundle[0]}")
    required_shapes = [
        ("functions", {"name", "path", "lineno"}),
        ("classes", {"name", "path"}),
        ("cli_commands", {"path", "function"}),
    ]
    bad_shapes: list[str] = []
    for key, keys in required_shapes:
        for item in loaded.get(key, [])[:5000]:
            if not keys.issubset(item.keys()):
                bad_shapes.append(f"{key}:{item.get('name', '?')}")
                break
    check("entry-shapes", not bad_shapes, f"bad={bad_shapes[:5]}")
    return report


def main() -> int:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle()
    payload = json.dumps(bundle, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(OUT_PATH.parent),
        prefix=OUT_PATH.name + ".",
        suffix=".tmp",
    ) as fh:
        fh.write(payload)
        tmp_name = fh.name
    try:
        os.replace(tmp_name, OUT_PATH)
    except BaseException:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise
    report = verify_bundle(bundle, OUT_PATH)
    size = OUT_PATH.stat().st_size
    print(f"wrote: {OUT_PATH}")
    print(f"size_bytes: {size}")
    print(f"files_exported: {bundle['completeness']['exported_files']}")
    print(
        f"functions: {bundle['completeness']['documented_functions']}  "
        f"classes: {bundle['completeness']['documented_classes']}  "
        f"cli: {bundle['completeness']['cli_commands']}  "
        f"routes: {bundle['completeness']['api_routes']}"
    )
    print(f"redactions: {bundle['completeness']['redactions_applied']}")
    print(f"parse_errors: {len(bundle['completeness']['parsing_errors'])}")
    for check in report["checks"]:
        print(
            f"verify[{check['check']}]: {'PASS' if check['passed'] else 'FAIL'}"
            + (f" {check['detail']}" if check.get("detail") else "")
        )
    print(f"verification: {'PASSED' if report['passed'] else 'FAILED'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
