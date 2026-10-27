# INSTALL_WINDOWS.md — SignalForge (Phase 7)

## Prerequisites

- Windows 10/11, Python 3.12.10 via `py -3.12` (verify: `py -3.12 --version`).
- OneDrive path supported; keep repo path short if possible.

## Install (quant engine — no API deps)

```powershell
py -3.12 -m pip install uv
uv sync
```

Creates `.venv/` and installs the core dependencies from `uv.lock`.
The CLI, quant engine, and full offline test suite work without the API extra.

## Install (optional API + dashboard)

```powershell
uv sync --extra api
```

Installs `fastapi` + `uvicorn` (pinned in `uv.lock`, `api` extra in
`pyproject.toml`). Without it, `sf serve` prints an actionable error and
API/dashboard tests skip cleanly (`pytest.importorskip`).

## Verify

```powershell
uv run sf health
uv run sf config-show
uv run pytest
uv run ruff check .
uv run --extra api pytest            # includes API + dashboard tests
uv run sf serve --help
```

## Serve locally (loopback only)

```powershell
uv run --extra api sf serve --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/dashboard/` in a desktop browser.
API at `http://127.0.0.1:8000/api/v1/health`. Stop with Ctrl+C.

The server binds loopback by default and refuses non-loopback binds unless
`--allow-remote` is passed explicitly (documented auth/networking required;
never enable automatically). No training, fetching, backtesting, or mutation
happens at startup or per request.

## Diagnosing

| Symptom | Fix |
|---|---|
| `py -3.12` missing | install Python 3.12 from python.org, enable py launcher |
| `uv` missing | `py -3.12 -m pip install uv` |
| lock out of date | `uv sync` (lead only: `uv lock` then commit) |
| `sf serve` says extra missing | `uv sync --extra api` |
| port occupied | pick another `--port`; `sf serve` exits 1 with a clear message |
| OneDrive locks | close editors/sync pauses, rerun; avoid editing during sync storms |
| sandbox has no package network | use the committed `uv.lock` + local cache; do not add new deps |
