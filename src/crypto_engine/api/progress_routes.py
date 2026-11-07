"""GET-only progress endpoints (read-only control-center data).

The dashboard consumes only these endpoints. Every handler is a pure read over
the append-only ledger or the static project model; nothing executes git,
tests, training, AI providers, or writes.

Endpoints
---------

``GET /api/v1/progress/snapshot``
    Derived manifest, latest events, staleness, static project model.

``GET /api/v1/progress/events``
    Bounded, filtered, newest-first event page.

``GET /api/v1/progress/history``
    Larger bounded history page for the timeline view.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

V1 = "/api/v1"


def register(router: Any, roots: Any, settings: Any) -> None:
    """Register the read-only progress routes on ``router``."""
    from crypto_engine.api.schemas import ok
    from crypto_engine.api.security import ApiError
    from crypto_engine.progress.ledger import ProgressLedger
    from crypto_engine.progress.project_model import project_snapshot
    from crypto_engine.progress.schema import LEVELS, STATUSES

    api = settings.api
    default_limit = int(api.page_size_default)
    max_limit = int(api.page_size_max)
    progress_dir = Path(str(getattr(roots, "progress_dir", "data/project_progress")))
    ledger = ProgressLedger(progress_dir, scan_cap=int(api.ledger_scan_cap))

    def _limit(value: int) -> int:
        return max(1, min(int(value), max_limit))

    def _validate_filters(level: str | None, status: str | None) -> None:
        if level is not None and level not in LEVELS:
            raise ApiError(400, "invalid_level", "Unknown progress level.")
        if status is not None and status not in STATUSES:
            raise ApiError(400, "invalid_status", "Unknown progress status.")

    @router.get(f"{V1}/progress/snapshot")
    def progress_snapshot() -> dict[str, Any]:
        snapshot = ledger.snapshot()
        return ok(
            {
                "progress": snapshot,
                "project": project_snapshot(),
                "statuses": sorted(STATUSES),
                "levels": sorted(LEVELS),
            },
            available=bool(snapshot.get("available")),
            stale=bool(snapshot.get("stale", True)),
            stale_after_s=snapshot.get("stale_after_s"),
        )

    @router.get(f"{V1}/progress/events")
    def progress_events(
        limit: int = default_limit,
        offset: int = 0,
        level: str | None = None,
        status: str | None = None,
        phase: str | None = None,
        stage: int | None = None,
        workstream: str | None = None,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        _validate_filters(level, status)
        effective = _limit(limit)
        try:
            payload = ledger.read_events(
                limit=effective,
                offset=max(0, int(offset)),
                level=level,
                status=status,
                phase=phase,
                stage=stage,
                workstream=workstream,
                since=since,
                until=until,
                q=q,
            )
        except ValueError:
            raise ApiError(400, "invalid_timestamp", "since/until must be UTC ISO-8601.") from None
        return ok(
            payload,
            total=payload.get("total", 0),
            limit=effective,
            offset=max(0, int(offset)),
        )

    @router.get(f"{V1}/progress/history")
    def progress_history(
        limit: int = 200,
        offset: int = 0,
        level: str | None = None,
        status: str | None = None,
        phase: str | None = None,
        stage: int | None = None,
        workstream: str | None = None,
        since: str | None = None,
        until: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        _validate_filters(level, status)
        effective = _limit(limit)
        try:
            payload = ledger.read_events(
                limit=effective,
                offset=max(0, int(offset)),
                level=level,
                status=status,
                phase=phase,
                stage=stage,
                workstream=workstream,
                since=since,
                until=until,
                q=q,
            )
        except ValueError:
            raise ApiError(400, "invalid_timestamp", "since/until must be UTC ISO-8601.") from None
        return ok(
            payload,
            total=payload.get("total", 0),
            limit=effective,
            offset=max(0, int(offset)),
        )


__all__ = ["register"]
