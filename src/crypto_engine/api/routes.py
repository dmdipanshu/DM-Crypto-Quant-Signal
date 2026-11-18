"""GET-only v1 routes (Phase 7).

Every route is registered with ``methods=["GET"]`` explicitly; the app
build asserts no other method exists anywhere (docs routes excepted).
Handlers are thin: validate query params â†' call a repo reader â†' envelope.
No handler writes, trains, fetches, backtests, or trades â€” verified by
reading the code (all repo readers are pure reads) and by route-table tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from crypto_engine.api.schemas import Pagination, ok
from crypto_engine.api.security import ApiError

V1 = "/api/v1"


def register(router: Any, roots: Any, settings: Any) -> None:
    """Register every v1 GET route on ``router`` (an APIRouter)."""
    from crypto_engine.api import repos as R

    api = settings.api
    default_limit = int(api.page_size_default)
    max_limit = int(api.page_size_max)

    def _limit(value: int) -> int:
        return R.clamp_limit(value, default_limit, max_limit)

    def _roots() -> R.RepoRoots:
        return R.RepoRoots(
            signals_dir=_resolve(roots.signals_dir),
            registry_dir=_resolve(roots.registry_dir),
            data_storage=_resolve(roots.data_storage),
            backtest_dir=_resolve(roots.backtest_dir),
            paper_path=_resolve(roots.paper_path),
            experiments_path=_resolve(roots.experiments_path),
            holdout_ledger_path=_resolve(roots.holdout_ledger_path),
            research_release_path=_resolve(
                getattr(
                    roots,
                    "research_release_path",
                    Path("docs/validation/results/quantitative_release_q4.json"),
                )
            ),
            progress_dir=_resolve(getattr(roots, "progress_dir", Path("data/project_progress"))),
            ai_telemetry_path=_resolve(
                getattr(roots, "ai_telemetry_path", Path("data/ai_telemetry/events.jsonl"))
            ),
            scan_cap=int(api.ledger_scan_cap),
            health_checker=roots.health_checker,
            metrics_registry=roots.metrics_registry,
        )

    def _resolve(path: Any) -> Path:
        return Path(str(path))

    # -- health -----------------------------------------------------------
    @router.get("/health")
    @router.get(f"{V1}/health")
    def health() -> dict[str, Any]:
        from crypto_engine import __version__

        checker = roots.health_checker
        if checker is None:
            results: dict[str, Any] = {}
            overall_value = "ok"
            uptime_seconds = 0.0
        else:
            results = checker.run_checks()
            overall = checker.overall_status()
            overall_value = overall.value
            uptime_seconds = checker.uptime_seconds
        return ok(
            {
                "status": "ok",
                "package_version": __version__,
                "read_only": True,
                "trading_enabled": False,
                "checks": {
                    "status": overall_value,
                    "uptime_seconds": uptime_seconds,
                    "components": {
                        name: {
                            "status": ch.status.value,
                            "message": ch.message,
                            "last_check_utc": ch.last_check_utc,
                            "check_duration_ms": ch.check_duration_ms,
                            "details": ch.details,
                        }
                        for name, ch in results.items()
                    },
                },
            }
        )

    @router.get(f"{V1}/readiness")
    def readiness() -> dict[str, Any]:
        from crypto_engine import __version__

        checks: dict[str, Any] = {"status": "ok", "package_version": __version__}
        try:
            from crypto_engine.config.settings import default_config_path

            config_path = default_config_path()
            checks["config_exists"] = config_path.is_file()
        except FileNotFoundError:
            checks["config_exists"] = False
        # Storage presence (no writes, no downloads, no training).
        checks["signals_dir_present"] = _roots().signals_dir.is_dir()
        checks["registry_dir_present"] = _roots().registry_dir.is_dir()
        checks["data_storage_present"] = _roots().data_storage.is_dir()
        checks["backtest_dir_present"] = _roots().backtest_dir.is_dir()
        paper = R.read_paper(_roots())
        checks["paper_initialized"] = bool(paper.get("available"))
        audit = R.verify_signals(_roots())
        checks["audit_ok"] = bool(audit.get("available")) and bool(audit.get("ok", False))
        # Component health from observability checker.
        checker = roots.health_checker
        if checker is None:
            checks["status"] = "ok"
            checks["uptime_seconds"] = 0.0
            checks["components"] = {}
        else:
            results = checker.run_checks()
            overall = checker.overall_status()
            checks["status"] = overall.value
            checks["uptime_seconds"] = checker.uptime_seconds
            checks["components"] = {
                name: {
                    "status": ch.status.value,
                    "message": ch.message,
                    "last_check_utc": ch.last_check_utc,
                    "check_duration_ms": ch.check_duration_ms,
                    "details": ch.details,
                }
                for name, ch in results.items()
            }
        return ok(checks)

    # -- metrics ------------------------------------------------------------
    @router.get(f"{V1}/metrics")
    @router.get("/metrics")
    def metrics() -> Any:
        """Prometheus text exposition format metrics endpoint."""
        from fastapi import Response

        registry = roots.metrics_registry
        payload = registry.render_prometheus() if registry is not None else ""
        return Response(content=payload, media_type="text/plain; version=0.0.4; charset=utf-8")

    # -- health components --------------------------------------------------
    @router.get(f"{V1}/health/components")
    def health_components() -> dict[str, Any]:
        """Component-level health summary."""
        checker = roots.health_checker
        if checker is None:
            return ok({"status": "ok", "uptime_seconds": 0.0, "components": {}})
        results = checker.run_checks()
        overall = checker.overall_status()
        return ok({
            "status": overall.value,
            "uptime_seconds": checker.uptime_seconds,
            "components": {
                name: {
                    "status": ch.status.value,
                    "message": ch.message,
                    "last_check_utc": ch.last_check_utc,
                    "check_duration_ms": ch.check_duration_ms,
                    "details": ch.details,
                }
                for name, ch in results.items()
            },
        })

    # -- signals ----------------------------------------------------------
    @router.get(f"{V1}/signals/latest")
    def signals_latest(symbol: str | None = None, timeframe: str | None = None) -> dict[str, Any]:
        return ok(R.read_latest_signal(_roots(), symbol=symbol, timeframe=timeframe))

    @router.get(f"{V1}/signals")
    def signals(
        limit: int = 50,
        offset: int = 0,
        symbol: str | None = None,
        timeframe: str | None = None,
        direction: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict[str, Any]:
        payload = R.read_signals(
            _roots(),
            limit=_limit(limit),
            offset=offset,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            status=status,
            since=since,
            until=until,
        )
        meta = {
            "pagination": Pagination(
                total=payload.get("total", 0), limit=_limit(limit), offset=max(0, offset)
            ).model_dump()
        }
        data = {k: v for k, v in payload.items() if k != "total"}
        return ok(data, **meta)

    @router.get(f"{V1}/signals/{{signal_id}}")
    def signal_by_id(signal_id: str) -> dict[str, Any]:
        return ok(R.read_signal(_roots(), signal_id))

    # -- models -----------------------------------------------------------
    @router.get(f"{V1}/models")
    def models() -> dict[str, Any]:
        return ok(R.read_models(_roots()))

    @router.get(f"{V1}/models/status")
    def models_status() -> dict[str, Any]:
        return ok(R.read_models_status(_roots()))

    @router.get(f"{V1}/models/{{model_id}}")
    def model_by_id(model_id: str) -> dict[str, Any]:
        return ok(R.read_model(_roots(), model_id))

    # -- validation / research --------------------------------------------
    @router.get(f"{V1}/validation/summary")
    def validation_summary() -> dict[str, Any]:
        roots = _roots()
        experiments = R.read_experiments(roots, limit=1000, offset=0)
        by_status: dict[str, int] = {}
        trials = 0
        if experiments.get("available"):
            for entry in experiments.get("experiments", []):
                state = str(entry.get("status", "unknown"))
                by_status[state] = by_status.get(state, 0) + 1
                try:
                    trials += int(entry.get("n_trials", 0) or 0)
                except (TypeError, ValueError):
                    continue
        holdout = R.read_holdout_status(roots)
        return ok(
            {
                "experiments_available": bool(experiments.get("available")),
                "experiment_count": len(experiments.get("experiments", []))
                if experiments.get("available")
                else 0,
                "experiments_by_status": by_status,
                "recorded_trials": trials,
                "holdout": holdout,
                "note": "Exploratory vs confirmatory grading lives in memory/NEXT_STEPS.md; no profitability claim.",
            }
        )

    @router.get(f"{V1}/experiments")
    def experiments(limit: int = 50, offset: int = 0, status: str | None = None) -> dict[str, Any]:
        payload = R.read_experiments(_roots(), limit=_limit(limit), offset=offset)
        if payload.get("available") and status is not None:
            filtered = [e for e in payload["experiments"] if str(e.get("status")) == status]
            payload = {"available": True, "experiments": filtered, "total": len(filtered)}
        meta = {
            "pagination": Pagination(
                total=payload.get("total", 0), limit=_limit(limit), offset=max(0, offset)
            ).model_dump()
        }
        data = {k: v for k, v in payload.items() if k != "total"}
        return ok(data, **meta)

    @router.get(f"{V1}/experiments/{{experiment_id}}")
    def experiment_by_id(experiment_id: str) -> dict[str, Any]:
        return ok(R.read_experiment(_roots(), experiment_id))

    @router.get(f"{V1}/research/reports")
    def research_reports() -> dict[str, Any]:
        return ok(
            {
                "available": True,
                "reports": [
                    {
                        "id": "methodology",
                        "title": "Research methodology",
                        "path": "docs/RESEARCH_METHODOLOGY.md",
                    },
                    {
                        "id": "paper-trading",
                        "title": "Paper trading design",
                        "path": "docs/PAPER_TRADING.md",
                    },
                    {"id": "architecture", "title": "Architecture", "path": "docs/ARCHITECTURE.md"},
                ],
                "note": "Full report bodies live in docs/; the API surfaces recorded metrics only.",
            }
        )

    @router.get(f"{V1}/research/release")
    def research_release() -> dict[str, Any]:
        return ok(R.read_research_release(_roots()))

    @router.get(f"{V1}/validation/holdout")
    def holdout() -> dict[str, Any]:
        return ok(R.read_holdout_status(_roots()))

    # -- backtests ----------------------------------------------------------
    @router.get(f"{V1}/backtests")
    def backtests(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        payload = R.read_backtests(_roots(), limit=_limit(limit), offset=offset)
        meta = {
            "pagination": Pagination(
                total=payload.get("total", 0), limit=_limit(limit), offset=max(0, offset)
            ).model_dump()
        }
        data = {k: v for k, v in payload.items() if k != "total"}
        return ok(data, **meta)

    @router.get(f"{V1}/backtests/{{run_id}}")
    def backtest_by_id(run_id: str) -> dict[str, Any]:
        payload = R.read_backtest(_roots(), run_id)
        result = payload.get("result", {}) if payload.get("available") else {}
        slim = {
            k: result.get(k)
            for k in (
                "symbol",
                "timeframe",
                "n_bars",
                "summary",
                "warnings",
                "config_hash",
                "bar_start",
                "bar_end",
            )
            if k in result
        }
        slim["trade_count"] = (
            len(result.get("trades", []))
            if isinstance(result.get("trades"), list)
            else result.get("trade_count")
        )
        return ok(
            {
                "available": payload.get("available"),
                "run_id": run_id,
                "summary": slim,
                "reason": payload.get("reason", ""),
            }
        )

    @router.get(f"{V1}/backtests/{{run_id}}/metrics")
    def backtest_metrics(run_id: str) -> dict[str, Any]:
        return ok(R.read_backtest_slice(_roots(), run_id, "metrics"))

    @router.get(f"{V1}/backtests/{{run_id}}/trades")
    def backtest_trades(run_id: str, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        payload = R.read_backtest_slice(
            _roots(), run_id, "trades", limit=_limit(limit), offset=offset
        )
        meta = {
            "pagination": Pagination(
                total=payload.get("total", 0), limit=_limit(limit), offset=max(0, offset)
            ).model_dump()
        }
        return ok(payload, **meta)

    @router.get(f"{V1}/backtests/{{run_id}}/equity")
    def backtest_equity(run_id: str, limit: int = 200, offset: int = 0) -> dict[str, Any]:
        payload = R.read_backtest_slice(
            _roots(), run_id, "equity", limit=_limit(limit), offset=offset
        )
        meta = {
            "pagination": Pagination(
                total=payload.get("total", 0), limit=_limit(limit), offset=max(0, offset)
            ).model_dump()
        }
        return ok(payload, **meta)

    # -- paper --------------------------------------------------------------
    def _paper_or_unavailable() -> dict[str, Any]:
        return R.read_paper(_roots())

    @router.get(f"{V1}/paper/status")
    def paper_status() -> dict[str, Any]:
        payload = _paper_or_unavailable()
        if not payload.get("available"):
            return ok(payload)
        account = payload["account"]
        return ok(
            {
                "available": True,
                "initialized": True,
                "status": account.get("status"),
                "symbol": account.get("symbol"),
                "timeframe": account.get("timeframe"),
                "cash": account.get("cash"),
                "equity_mark": account.get("equity_mark"),
                "equity_mark_ts": account.get("equity_mark_ts"),
                "has_open_position": account.get("position") is not None,
                "trade_count": len(account.get("trades_closed", [])),
                "halt_reason": account.get("halt_reason"),
            }
        )

    @router.get(f"{V1}/paper/state")
    def paper_state() -> dict[str, Any]:
        return ok(_paper_or_unavailable())

    @router.get(f"{V1}/paper/trades")
    def paper_trades(limit: int = 200, offset: int = 0) -> dict[str, Any]:
        payload = _paper_or_unavailable()
        if not payload.get("available"):
            return ok(payload)
        trades = payload["account"].get("trades_closed", [])
        page = trades[offset : offset + _limit(limit)]
        return ok(
            {"available": True, "trades": page},
            pagination=Pagination(
                total=len(trades), limit=_limit(limit), offset=max(0, offset)
            ).model_dump(),
        )

    @router.get(f"{V1}/paper/ledger")
    def paper_ledger(limit: int = 200, offset: int = 0) -> dict[str, Any]:
        payload = _paper_or_unavailable()
        if not payload.get("available"):
            return ok(payload)
        account = payload["account"]
        curve = account.get("equity_curve", [])
        page = curve[offset : offset + _limit(limit)]
        return ok(
            {"available": True, "equity_curve": page, "equity_mark": account.get("equity_mark")},
            pagination=Pagination(
                total=len(curve), limit=_limit(limit), offset=max(0, offset)
            ).model_dump(),
        )

    @router.get(f"{V1}/paper/comparisons")
    def paper_comparisons(run_id: str | None = None) -> dict[str, Any]:
        from crypto_engine.backtesting.engine import BacktestResult
        from crypto_engine.paper_trading.compare import compare_account_to_backtest
        from crypto_engine.paper_trading.state import load_account

        paper = _paper_or_unavailable()
        if not paper.get("available"):
            return ok(paper)
        if run_id is None:
            return ok(
                {
                    "available": True,
                    "compared": False,
                    "reason": "no_run_selected",
                    "detail": "Pass ?run_id=<id> to compare against a frozen backtest ledger.",
                }
            )
        payload = R.read_backtest(_roots(), run_id)
        if not payload.get("available"):
            return ok(
                {
                    "available": True,
                    "compared": False,
                    "reason": payload.get("reason", "missing"),
                    "detail": "Referenced backtest run unavailable.",
                }
            )
        try:
            account = load_account(_roots().paper_path)
            result = BacktestResult.model_validate(payload["result"])
            report = compare_account_to_backtest(account, result)
        except Exception as exc:
            from crypto_engine.api.security import sanitize_error

            raise ApiError(422, "comparison_failed", sanitize_error(exc)) from None
        return ok({"available": True, "compared": True, "report": report.model_dump(mode="json")})

    # -- audit --------------------------------------------------------------
    @router.get(f"{V1}/audit/status")
    def audit_status() -> dict[str, Any]:
        return ok(R.verify_signals(_roots()))

    @router.get(f"{V1}/audit/signals")
    def audit_signals(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return signals(limit=limit, offset=offset)

    @router.get(f"{V1}/audit/signals/verify")
    def audit_verify() -> dict[str, Any]:
        return ok(R.verify_signals(_roots()))

    # -- data ---------------------------------------------------------------
    @router.get(f"{V1}/data/status")
    def data_status() -> dict[str, Any]:
        from crypto_engine import __version__

        payload = R.read_snapshots(_roots(), limit=1, offset=0)
        return ok(
            {
                "package_version": __version__,
                "snapshots_available": bool(payload.get("available")),
                "snapshot_count": int(payload.get("total", 0)),
                "reason": payload.get("reason", ""),
                "note": "Read-only manifest view; the API never fetches exchange data.",
            }
        )

    @router.get(f"{V1}/data/snapshots")
    def data_snapshots(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        payload = R.read_snapshots(_roots(), limit=_limit(limit), offset=offset)
        meta = {
            "pagination": Pagination(
                total=payload.get("total", 0), limit=_limit(limit), offset=max(0, offset)
            ).model_dump()
        }
        data = {k: v for k, v in payload.items() if k != "total"}
        return ok(data, **meta)

    @router.get(f"{V1}/data/snapshots/{{snapshot_id}}")
    def data_snapshot_by_id(snapshot_id: str) -> dict[str, Any]:
        return ok(R.read_snapshot(_roots(), snapshot_id))

    # -- AI status (advisory-only, read-only configuration view) ------------
    @router.get(f"{V1}/ai/status")
    def ai_status() -> dict[str, Any]:
        """AI enablement and bounded limits (advisory only, no secrets)."""
        return ok(R.read_ai_status())

    @router.get(f"{V1}/ai/metrics")
    def ai_metrics() -> dict[str, Any]:
        """AI observability metrics from stored telemetry (read-only, no secrets)."""
        return ok(R.read_ai_metrics(_roots()))

    # -- Q5 prospective collection (read-only) --------------------------------
    @router.get(f"{V1}/q5/collection/status")
    def q5_collection_status() -> dict[str, Any]:
        """Q5 prospective collection status (read-only, no secrets)."""
        return ok(R.read_q5_collection_status(_roots()))

    @router.get(f"{V1}/q5/prospective/governance")
    def q5_prospective_governance() -> dict[str, Any]:
        """Q5 prospective holdout governance state (read-only, no secrets)."""
        return ok(R.read_q5_prospective_governance(_roots()))

    # -- Q6 prospective strategy evaluation (read-only) -----------------------
    @router.get(f"{V1}/q6/evaluation")
    def q6_evaluation() -> dict[str, Any]:
        """Q6 prospective evaluation status (read-only, no secrets)."""
        return ok(R.read_q6_evaluation(_roots()))

    # -- Q6 historical validation (read-only) ---------------------------------
    @router.get(f"{V1}/historical-validation/status")
    def h_validation_status() -> dict[str, Any]:
        """Historical validation status (read-only, no secrets)."""
        return ok(R.read_historical_validation_status())

    @router.get(f"{V1}/historical-validation/manifest")
    def h_validation_manifest() -> dict[str, Any]:
        """Historical validation manifest (read-only, no secrets)."""
        import json as _json

        from crypto_engine.research.q6.historical_manifest import (
            DEFAULT_HISTORICAL_MANIFEST_PATH,
        )

        path = DEFAULT_HISTORICAL_MANIFEST_PATH
        if not path.is_file():
            return ok({"available": False, "reason": "manifest not found", "read_only": True})
        data = _json.loads(path.read_text(encoding="utf-8"))
        data["available"] = True
        data["read_only"] = True
        return ok(data)


__all__ = ["V1", "register"]
