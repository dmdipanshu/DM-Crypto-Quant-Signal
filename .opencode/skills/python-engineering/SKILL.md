---
name: python-engineering
description: Apply typed, modular, reproducible Python engineering practices
---

# Python Engineering

## Procedure

1. Use the project uv environment (`.venv/`) and Python 3.12; never system Python.
2. Keep modules focused; type public interfaces; validate config (pydantic);
   make errors explicit; bound side effects.
3. Use `pathlib` for Windows-safe paths; keep OneDrive writes small and short-lived.
4. Prefer deterministic functions and dependency injection for external clients.
5. Run pytest and Ruff for changed code before completion.

## Validation standards

- `uv run pytest` green for affected tests; `uv run ruff check .` clean.
- No empty placeholder modules; no secrets in code/config; no Phase 2+ imports
  in Phase 1 code paths.

## Failure handling

- Failing tests/lint → fix or record as pre-existing with evidence; never ship
  red without noting it. Reproduce defects before claiming fixes.

## Completion requirements

Handoff states: changed files, exact test/lint commands + counts, limitations,
unresolved risks, next gate.
