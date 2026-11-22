"""Dashboard HTML routes (Phase 7, GET-only, same repo readers as the API)."""

from __future__ import annotations

from typing import Any


def register(router: Any, roots: Any, settings: Any) -> None:
    """Register server-rendered pages (HTMLResponse, no JS framework)."""
    from fastapi.responses import HTMLResponse, RedirectResponse

    from crypto_engine.api import repos as R
    from crypto_engine.dashboard import pages as P

    api = settings.api
    default_limit = int(api.page_size_default)
    max_limit = int(api.page_size_max)

    def _limit(value: int) -> int:
        return R.clamp_limit(value, default_limit, max_limit)

    def _roots() -> R.RepoRoots:
        from pathlib import Path

        return R.RepoRoots(
            signals_dir=Path(str(roots.signals_dir)),
            registry_dir=Path(str(roots.registry_dir)),
            data_storage=Path(str(roots.data_storage)),
            backtest_dir=Path(str(roots.backtest_dir)),
            paper_path=Path(str(roots.paper_path)),
            experiments_path=Path(str(roots.experiments_path)),
            holdout_ledger_path=Path(str(roots.holdout_ledger_path)),
            research_release_path=Path(
                str(
                    getattr(
                        roots,
                        "research_release_path",
                        "docs/validation/results/quantitative_release_q4.json",
                    )
                )
            ),
            progress_dir=Path(str(getattr(roots, "progress_dir", "data/project_progress"))),
            ai_telemetry_path=Path(
                str(getattr(roots, "ai_telemetry_path", "data/ai_telemetry/events.jsonl"))
            ),
            scan_cap=int(api.ledger_scan_cap),
        )

    @router.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/dashboard/", status_code=307)

    @router.get("/dashboard/", response_class=HTMLResponse)
    def dash_overview() -> str:
        from crypto_engine import __version__

        roots_ = _roots()
        ctx: dict[str, Any] = {
            "health": {"status": "ok", "package_version": __version__},
            "data_status": R.read_snapshots(roots_, limit=1, offset=0),
            "latest_signal": R.read_latest_signal(roots_),
            "models_status": R.read_models_status(roots_),
            "backtests": R.read_backtests(roots_, limit=1, offset=0),
            "paper": R.read_paper(roots_),
            "audit": R.verify_signals(roots_),
            "ai_status": R.read_ai_status(),
            "ai_metrics": R.read_ai_metrics(roots_),
        }
        return P.overview(ctx)

    @router.get("/dashboard/signals", response_class=HTMLResponse)
    def dash_signals(limit: int = 50, offset: int = 0) -> str:
        payload = R.read_signals(_roots(), limit=_limit(limit), offset=max(0, offset))
        return P.signals_page(payload.get("signals", []), int(payload.get("total", 0)))

    @router.get("/dashboard/research", response_class=HTMLResponse)
    def dash_research() -> str:
        roots_ = _roots()
        experiments = R.read_experiments(roots_, limit=25, offset=0)
        holdout = R.read_holdout_status(roots_)
        entries = experiments.get("experiments", []) if experiments.get("available") else []
        by_status: dict[str, int] = {}
        trials = 0
        for entry in entries:
            state = str(entry.get("status", "unknown"))
            by_status[state] = by_status.get(state, 0) + 1
            try:
                trials += int(entry.get("n_trials", 0) or 0)
            except (TypeError, ValueError):
                continue
        summary = {
            "experiments_available": bool(experiments.get("available")),
            "experiment_count": len(entries),
            "experiments_by_status": by_status,
            "recorded_trials": trials,
            "holdout": holdout,
            "note": "Exploratory vs confirmatory grading lives in memory/NEXT_STEPS.md; no profitability claim.",
        }
        release_payload = R.read_research_release(roots_)
        return P.research_page(summary, entries, release_payload)

    @router.get("/dashboard/q5", response_class=HTMLResponse)
    def dash_q5() -> str:
        roots_ = _roots()
        q5_collection = R.read_q5_collection_status(roots_)
        q5_governance = R.read_q5_prospective_governance(roots_)
        return P.q5_page(q5_collection, q5_governance)

    @router.get("/dashboard/q6", response_class=HTMLResponse)
    def dash_q6() -> str:
        roots_ = _roots()
        q6_collection = R.read_q5_collection_status(roots_)
        q6_governance = R.read_q5_prospective_governance(roots_)
        q6_evaluation = R.read_q6_evaluation(roots_)
        return P.q6_page(q6_collection, q6_governance, q6_evaluation)

    @router.get("/dashboard/historical-validation", response_class=HTMLResponse)
    def dash_historical_validation() -> str:
        roots_ = _roots()
        h_collection = R.read_historical_validation_status()
        # Governance for historical track: fail-closed, never prospective/Q5.2 eligible.
        h_governance = {
            "historical_holdout_state": "CONTAMINATED",
            "prospective_state": "NOT_APPLICABLE_HISTORICAL_ACCELERATED",
            "eligible_for_evaluation": bool(h_collection.get("manifest_valid"))
            and bool(h_collection.get("ledger_valid"))
            and int(h_collection.get("observation_count", 0)) >= 168
            and int(h_collection.get("conflicting_count", 0)) == 0,
            "q5_2_eligible": False,
            "evaluation_blocked": not (
                bool(h_collection.get("manifest_valid"))
                and bool(h_collection.get("ledger_valid"))
                and int(h_collection.get("observation_count", 0)) >= 168
                and int(h_collection.get("conflicting_count", 0)) == 0
            ),
        }
        h_evaluation = R.read_q6_evaluation(
            roots_
        )  # prospective Q6 artifact, shown read-only if present
        h_comparison = {"available": False, "reason": "no comparison file"}
        from pathlib import Path

        _cmp_path = Path("docs/validation/results/historical_validation_comparison.json")
        if _cmp_path.is_file():
            try:
                import json as _json

                _cmp_data = _json.loads(_cmp_path.read_text(encoding="utf-8"))
                _cmp_data["available"] = True
                h_comparison = _cmp_data
            except (_json.JSONDecodeError, OSError) as exc:
                h_comparison = {
                    "available": False,
                    "reason": f"cannot read comparison: {exc}",
                }
        return P.q6_historical_page(h_collection, h_governance, h_evaluation, h_comparison)

    @router.get("/dashboard/backtests", response_class=HTMLResponse)
    def dash_backtests(limit: int = 50, offset: int = 0) -> str:
        payload = R.read_backtests(_roots(), limit=_limit(limit), offset=max(0, offset))
        runs: list[dict[str, Any]] = []
        for run in payload.get("backtests", []):
            if not run.get("available"):
                runs.append(run)
                continue
            full = R.read_backtest(_roots(), str(run.get("run_id", "")))
            result = full.get("result", {}) if full.get("available") else {}
            runs.append(
                {
                    "run_id": run.get("run_id"),
                    "available": full.get("available", False),
                    "symbol": result.get("symbol"),
                    "timeframe": result.get("timeframe"),
                    "trade_count": len(result.get("trades", []))
                    if isinstance(result.get("trades"), list)
                    else 0,
                    "net_pnl": (result.get("summary", {}) or {}).get("net_pnl"),
                    "warnings": result.get("warnings", []),
                    "reason": full.get("reason", ""),
                }
            )
        return P.backtests_page(runs, int(payload.get("total", 0)))

    @router.get("/dashboard/paper", response_class=HTMLResponse)
    def dash_paper() -> str:
        payload = R.read_paper(_roots())
        if not payload.get("available"):
            return P.paper_page(None, str(payload.get("reason", "missing")))
        return P.paper_page(payload.get("account"), "initialized")

    @router.get("/dashboard/models", response_class=HTMLResponse)
    def dash_models() -> str:
        payload = R.read_models(_roots())
        return P.models_page(payload.get("models", []) if payload.get("available") else [])

    # -- project progress control center (Phase 7 extension, read-only) -----
    from pathlib import Path as _Path

    progress_assets = _Path(__file__).resolve().parent / "progress"
    csp = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )

    @router.get("/dashboard/progress/", response_class=HTMLResponse)
    def dash_progress() -> Any:
        index = progress_assets / "index.html"
        if not index.is_file():
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="progress dashboard assets are missing")
        return HTMLResponse(
            index.read_text(encoding="utf-8"),
            headers={
                "Content-Security-Policy": csp,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    def _asset(name: str, media_type: str, base_dir: _Path = progress_assets) -> Any:
        from fastapi import HTTPException
        from fastapi.responses import Response

        path = base_dir / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="asset is missing")
        return Response(
            content=path.read_bytes(),
            media_type=media_type,
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/dashboard/progress/progress.css", include_in_schema=False)
    def progress_css() -> Any:
        return _asset("progress.css", "text/css; charset=utf-8")

    @router.get("/dashboard/progress/progress.js", include_in_schema=False)
    def progress_js() -> Any:
        return _asset("progress.js", "text/javascript; charset=utf-8")

    @router.get("/dashboard/monitor/", response_class=HTMLResponse)
    def dash_monitor() -> Any:
        monitor_assets = _Path(__file__).resolve().parent / "monitor"
        index = monitor_assets / "index.html"
        if not index.is_file():
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="monitor dashboard assets are missing")
        return HTMLResponse(
            index.read_text(encoding="utf-8"),
            headers={
                "Content-Security-Policy": csp,
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )

    @router.get("/dashboard/monitor/monitor.css", include_in_schema=False)
    def monitor_css() -> Any:
        from fastapi import HTTPException
        from fastapi.responses import Response
        path = _Path(__file__).resolve().parent / "monitor" / "monitor.css"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="asset is missing")
        return Response(
            content=path.read_bytes(),
            media_type="text/css; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/dashboard/monitor/monitor.js", include_in_schema=False)
    def monitor_js() -> Any:
        from fastapi import HTTPException
        from fastapi.responses import Response
        path = _Path(__file__).resolve().parent / "monitor" / "monitor.js"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="asset is missing")
        return Response(
            content=path.read_bytes(),
            media_type="text/javascript; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )


__all__ = ["register"]

