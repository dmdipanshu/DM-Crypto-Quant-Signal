"""Read-only API package (Phase 7).

GET-only FastAPI surface over canonical SignalForge stores. Nothing here
writes, trains, fetches, or trades — repository readers are side-effect
free and every route is registered GET-only (asserted by tests).
"""

from __future__ import annotations

__all__: list[str] = []
