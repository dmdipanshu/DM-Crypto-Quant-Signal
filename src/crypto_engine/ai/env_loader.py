"""Centralized .env loader for SignalForge.

Provides a single, reusable entry point for loading environment variables from
the project's ``.env`` file. This module is imported once at application
startup (CLI entry point and API server) so that all API credentials are
available to the AI gateway before any provider adapter runs.

Design rules:
- Resolve the ``.env`` path relative to the project root (where
  ``pyproject.toml`` lives), not the current working directory.
- Use ``override=False`` so existing OS environment variables take precedence.
- Never print, log, or surface secret values.
- A missing ``.env`` file is silent (load_no_env) — commands that don't need
  credentials must still work.
- Idempotent: safe to call multiple times.
"""

from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is a hard dependency
    load_dotenv = None  # type: ignore[assignment]


def _discover_repo_root(start: Path) -> Path:
    """Walk up from ``start`` until a directory containing pyproject.toml."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise FileNotFoundError(
        f"Could not locate repo root (no pyproject.toml found walking up from {start})."
    )


def load_env(dotenv_path: str | Path | None = None) -> bool:
    """Load environment variables from the project ``.env`` file.

    Args:
        dotenv_path: Explicit path to a ``.env`` file. Defaults to
            ``<repo_root>/.env`` discovered by walking up from this module's
            location until a directory containing ``pyproject.toml`` is found.

    Returns:
        True if the file was found and loaded (or already loaded), False if
        no ``.env`` file exists at the resolved path. Never raises for a
        missing file.

    Notes:
        - Uses ``override=False`` so environment variables already set in the
          OS take precedence over values in ``.env``.
        - Idempotent: calling multiple times is safe.
        - Secrets are never printed or logged.
    """
    if load_dotenv is None:
        return False

    path = Path(dotenv_path) if dotenv_path is not None else _repo_root() / ".env"

    if not path.is_file():
        return False

    loaded = load_dotenv(dotenv_path=str(path), override=False, encoding="utf-8")
    return bool(loaded)


def _repo_root() -> Path:
    """Discover the repository root by walking up for pyproject.toml."""
    return _discover_repo_root(Path(__file__))


__all__ = ["load_env"]
