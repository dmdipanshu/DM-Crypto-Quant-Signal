# INSTALL_WINDOWS.md — SignalForge (Phase 1)

## Prerequisites

- Windows 10/11, Python 3.12.10 via `py -3.12` (verify: `py -3.12 --version`).
- OneDrive path supported; keep repo path short if possible.

## Install

```powershell
py -3.12 -m pip install uv
uv sync
```

This creates `.venv/` and installs Phase 1 dependencies from `uv.lock`.

## Verify

```powershell
uv run sf health
uv run sf config-show
uv run pytest
uv run ruff check .
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `py -3.12` missing | install Python 3.12 from python.org, enable py launcher |
| `uv` missing | `py -3.12 -m pip install uv` |
| lock out of date | `uv sync` (lead only: `uv lock` then commit) |
| OneDrive locks | close editors/sync pauses, rerun; avoid editing during sync storms |
