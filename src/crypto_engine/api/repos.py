"""Read-only repository adapters (Phase 7).

Thin wrappers over the canonical Phase 1-6 stores. Rules:

* Never write, train, fetch, or trade — pure reads only.
* Bounded scans: JSONL ledgers scan at most ``scan_cap`` newest lines;
  list endpoints paginate with server-side caps.
* Unavailable (not missing-data-fabrication): empty stores return
  ``{"available": False, "reason": ...}`` payloads, never invented rows.
* Checksummed state (paper) is verified before financially relevant values
  are exposed; corruption surfaces as unavailable, not as numbers.
* All paths resolve under configured roots (see ``security.confine``).

Dependency injection: every reader takes explicit root paths, so API tests
use tmp fixtures without touching the real repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from crypto_engine.api.security import ApiError, confine, validate_id

DirectionFilter = Literal["LONG", "SHORT", "NEUTRAL", "NO_TRADE"]
AVAILABILITY_REASONS = (
    "not_initialized",
    "empty",
    "missing",
    "corrupt",
    "optional_dependency_missing",
)


@dataclass
class RepoRoots:
    """Configured data roots for every read-only adapter."""

    signals_dir: Path = field(default_factory=lambda: Path("data/signals"))
    registry_dir: Path = field(default_factory=lambda: Path("models/artifacts"))
    data_storage: Path = field(default_factory=lambda: Path("data"))
    backtest_dir: Path = field(default_factory=lambda: Path("data/backtests"))
    paper_path: Path = field(default_factory=lambda: Path("data/paper/account.json"))
    experiments_path: Path = field(default_factory=lambda: Path("reports/experiments.jsonl"))
    holdout_ledger_path: Path = field(default_factory=lambda: Path("reports/holdout.json"))
    scan_cap: int = 5000


def _parse_ts(value: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "invalid_date", f"Malformed {field}; use ISO-8601 UTC.") from exc
    if dt.tzinfo is None:
        raise ApiError(400, "invalid_date", f"Malformed {field}; timezone required.")
    return dt


def unavailable(reason: str, detail: str = "") -> dict[str, Any]:
    """Standard unavailable payload (never fabricated rows)."""
    if reason not in AVAILABILITY_REASONS:
        reason = "missing"
    payload: dict[str, Any] = {"available": False, "reason": reason}
    if detail:
        payload["detail"] = detail
    return payload


def paginate(items: list[Any], limit: int, offset: int) -> tuple[list[Any], int]:
    """Bounded slice; callers clamp ``limit`` to the configured max first."""
    if limit < 1:
        raise ApiError(400, "invalid_pagination", "limit must be >= 1.")
    if offset < 0:
        raise ApiError(400, "invalid_pagination", "offset must be >= 0.")
    return items[offset : offset + limit], len(items)


def clamp_limit(limit: int, default: int, maximum: int) -> int:
    """Apply server-side pagination caps (never trust client values raw)."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_pagination", "limit must be an integer.") from None
    return max(1, min(value if value > 0 else default, maximum))


# -- signals ---------------------------------------------------------------


def _signal_history(roots: RepoRoots) -> Any:
    """Bind history WITHOUT creating anything (read-only: no mkdir side effects).

    Raises ``FileNotFoundError`` when uninitialized so callers report the
    empty state instead of fabricating it.
    """
    from crypto_engine.signals.history import FILENAME, SignalHistory

    path = roots.signals_dir
    if not path.is_dir() or not (path / FILENAME).is_file():
        raise FileNotFoundError(str(path))
    return SignalHistory(path)


def read_signals(
    roots: RepoRoots,
    *,
    limit: int = 50,
    offset: int = 0,
    symbol: str | None = None,
    timeframe: str | None = None,
    direction: str | None = None,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Read canonical signal records, newest-first, with optional filters."""
    if direction is not None and direction not in ("LONG", "SHORT", "NEUTRAL", "NO_TRADE"):
        raise ApiError(400, "invalid_filter", "Unsupported direction filter.")
    if status is not None and status not in ("approved", "abstained", "rejected"):
        raise ApiError(400, "invalid_filter", "Unsupported decision-status filter.")
    since_dt = _parse_ts(since, "since") if since else None
    until_dt = _parse_ts(until, "until") if until else None
    if since_dt and until_dt and since_dt > until_dt:
        raise ApiError(400, "invalid_date", "since must not be after until.")

    try:
        hist = _signal_history(roots)
        records = hist.list(symbol=symbol, direction=direction)
    except FileNotFoundError:
        return unavailable("not_initialized", "No signal history yet; run `sf signal generate`.")
    except OSError as exc:
        return unavailable("missing", f"Signal history unreadable: {type(exc).__name__}.")
    items: list[dict[str, Any]] = []
    for rec in reversed(records):
        payload = rec.model_dump(mode="json")
        if timeframe is not None and payload.get("timeframe") != timeframe:
            continue
        if status is not None and payload.get("decision", {}).get("status") != status:
            continue
        gen = payload.get("generated_at")
        try:
            gen_dt = _parse_ts(str(gen), "generated_at") if gen else None
        except ApiError:
            continue
        if since_dt and (gen_dt is None or gen_dt < since_dt):
            continue
        if until_dt and (gen_dt is None or gen_dt > until_dt):
            continue
        items.append(payload)
        if len(items) >= roots.scan_cap:
            break
    page, total = paginate(items, limit, offset)
    return {"available": True, "signals": page, "total": total}


def read_signal(roots: RepoRoots, signal_id: str) -> dict[str, Any]:
    """Read one canonical signal by id (404 when absent)."""
    validate_id(signal_id, "signal_id")
    try:
        hist = _signal_history(roots)
        rec = hist.get(signal_id)
    except FileNotFoundError:
        return unavailable("not_initialized", "No signal history yet.")
    except OSError:
        return unavailable("missing", "Signal history unreadable.")
    if rec is None:
        raise ApiError(404, "not_found", "No signal with that id.")
    return {"available": True, "signal": rec.model_dump(mode="json")}


def read_latest_signal(
    roots: RepoRoots, *, symbol: str | None = None, timeframe: str | None = None
) -> dict[str, Any]:
    """Newest canonical signal (never presented as a live recommendation)."""
    page = read_signals(roots, limit=1, offset=0, symbol=symbol, timeframe=timeframe)
    if not page.get("available"):
        return page
    signals = page.get("signals", [])
    if not signals:
        return unavailable("empty", "Signal history exists but holds no matching records.")
    return {"available": True, "signal": signals[0]}


def verify_signals(roots: RepoRoots) -> dict[str, Any]:
    """Read-only integrity check of the signal audit chain (never repairs)."""
    try:
        hist = _signal_history(roots)
        ok, errors = hist.verify()
    except FileNotFoundError:
        return unavailable("not_initialized", "No signal history yet.")
    except OSError:
        return unavailable("missing", "Signal history unreadable.")
    return {"available": True, "ok": ok, "errors": errors}


# -- models ----------------------------------------------------------------


def read_models(roots: RepoRoots) -> dict[str, Any]:
    """Registry metadata only (artifacts are never loaded or deserialized)."""
    from crypto_engine.registry.store import list_models

    try:
        records = list_models(roots.registry_dir)
    except OSError:
        return unavailable("missing", "Model registry unreadable.")
    return {
        "available": True,
        "models": [
            {
                "model_id": r.model_id,
                "family": r.family,
                "version": r.version,
                "status": r.status,
                "feature_schema_hash": r.feature_schema_hash,
                "dataset_snapshot_id": r.dataset_snapshot_id,
                "label_hash": r.label_hash,
                "created_at": r.created_at,
                "package_version": r.package_version,
                "limitations": r.limitations,
            }
            for r in records
        ],
    }


def read_model(roots: RepoRoots, model_id: str) -> dict[str, Any]:
    """One registry record by id (metadata only; 404 when absent)."""
    from crypto_engine.registry.store import list_models

    validate_id(model_id, "model_id")
    try:
        records = list_models(roots.registry_dir)
    except OSError:
        return unavailable("missing", "Model registry unreadable.")
    for record in records:
        if record.model_id == model_id:
            return {"available": True, "model": record.model_dump(mode="json")}
    raise ApiError(404, "not_found", "No model with that id.")


def read_models_status(roots: RepoRoots) -> dict[str, Any]:
    """Registry health: counts by status/family (no artifact loading)."""
    payload = read_models(roots)
    if not payload.get("available"):
        return payload
    models = payload["models"]
    by_status: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for model in models:
        by_status[str(model.get("status", "?"))] = (
            by_status.get(str(model.get("status", "?")), 0) + 1
        )
        by_family[str(model.get("family", "?"))] = (
            by_family.get(str(model.get("family", "?")), 0) + 1
        )
    return {"available": True, "count": len(models), "by_status": by_status, "by_family": by_family}


# -- validation / research -------------------------------------------------


def read_experiments(roots: RepoRoots, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Recorded experiment registry entries (never re-runs anything)."""
    from crypto_engine.validation.experiments import ExperimentRegistry

    path = roots.experiments_path
    if not path.is_file():
        return unavailable("not_initialized", "No experiment registry recorded yet.")
    try:
        registry = ExperimentRegistry(path)
        entries = registry.list()
    except (OSError, ValueError) as exc:
        return unavailable("corrupt", f"Experiment registry unreadable: {type(exc).__name__}.")
    page, total = paginate(list(reversed(entries)), limit, offset)
    return {"available": True, "experiments": page, "total": total}


def read_experiment(roots: RepoRoots, experiment_id: str) -> dict[str, Any]:
    """One recorded experiment by id (404 when absent)."""
    from crypto_engine.validation.experiments import ExperimentRegistry

    validate_id(experiment_id, "experiment_id")
    path = roots.experiments_path
    if not path.is_file():
        return unavailable("not_initialized", "No experiment registry recorded yet.")
    try:
        entry = ExperimentRegistry(path).get(experiment_id)
    except (OSError, ValueError):
        return unavailable("corrupt", "Experiment registry unreadable.")
    except KeyError:
        raise ApiError(404, "not_found", "No experiment with that id.") from None
    return {"available": True, "experiment": entry}


def read_holdout_status(roots: RepoRoots) -> dict[str, Any]:
    """Holdout access-ledger state (pristine vs used; never the data itself)."""
    from crypto_engine.validation.holdout import HoldoutLedger

    path = roots.holdout_ledger_path
    if not path.is_file():
        return unavailable("not_initialized", "No holdout ledger recorded yet.")
    try:
        ledger = HoldoutLedger.load(path)
    except (OSError, ValueError):
        return unavailable("corrupt", "Holdout ledger unreadable.")
    return {
        "available": True,
        "pristine": bool(ledger.is_pristine),
        "access_log": list(getattr(ledger, "access_log", [])),
        "created_at": str(getattr(ledger, "created_at", "")),
    }


# -- backtests -------------------------------------------------------------


def _backtest_runs(roots: RepoRoots) -> list[dict[str, Any]]:
    base = roots.backtest_dir
    if not base.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for result_path in sorted(base.rglob("result.json")):
        try:
            doc = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            runs.append(
                {
                    "run_id": result_path.parent.name,
                    "available": False,
                    "reason": "corrupt",
                    "path": str(result_path),
                }
            )
            continue
        if not isinstance(doc, dict):
            runs.append(
                {
                    "run_id": result_path.parent.name,
                    "available": False,
                    "reason": "corrupt",
                    "path": str(result_path),
                }
            )
            continue
        runs.append({"run_id": result_path.parent.name, "available": True, "result": doc})
    return runs


def read_backtests(roots: RepoRoots, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Persisted backtest runs (never executes a new backtest)."""
    runs = _backtest_runs(roots)
    if not runs:
        return unavailable("empty", "No backtest runs recorded; run `sf backtest run`.")
    summaries: list[dict[str, Any]] = []
    for run in runs:
        if not run["available"]:
            summaries.append(run)
            continue
        result = run["result"]
        summary = result.get("summary", {}) if isinstance(result, dict) else {}
        summaries.append(
            {
                "run_id": run["run_id"],
                "available": True,
                "symbol": result.get("symbol"),
                "timeframe": result.get("timeframe"),
                "trade_count": result.get("trade_count"),
                "net_pnl": (summary or {}).get("net_pnl"),
                "warnings": result.get("warnings", []),
            }
        )
    page, total = paginate(summaries, limit, offset)
    return {"available": True, "backtests": page, "total": total}


def read_backtest(roots: RepoRoots, run_id: str) -> dict[str, Any]:
    """One persisted backtest run document (404 when absent)."""
    validate_id(run_id, "run_id")
    try:
        target = confine(roots.backtest_dir, run_id, "result.json")
    except ApiError:
        raise ApiError(404, "not_found", "No backtest run with that id.") from None
    if not target.is_file():
        known = [r["run_id"] for r in _backtest_runs(roots) if not r["available"]]
        if run_id in known:
            return unavailable("corrupt", "Backtest artifact present but unreadable.")
        raise ApiError(404, "not_found", "No backtest run with that id.")
    try:
        doc = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return unavailable("corrupt", "Backtest artifact present but unreadable.")
    if not isinstance(doc, dict):
        return unavailable("corrupt", "Backtest artifact present but unreadable.")
    return {"available": True, "run_id": run_id, "result": doc}


def read_backtest_slice(
    roots: RepoRoots,
    run_id: str,
    section: Literal["metrics", "trades", "equity"],
    *,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Paginated slice of a persisted run (metrics singleton or array pages)."""
    payload = read_backtest(roots, run_id)
    if not payload.get("available"):
        return payload
    result = payload["result"]
    if section == "metrics":
        return {"available": True, "run_id": run_id, "metrics": result.get("summary", {})}
    key = "trades" if section == "trades" else "equity_curve"
    rows = result.get(key, [])
    if not isinstance(rows, list):
        return unavailable("corrupt", f"Backtest {key} is not an array.")
    page, total = paginate(rows, limit, offset)
    return {"available": True, "run_id": run_id, section: page, "total": total}


# -- paper -----------------------------------------------------------------


def read_paper(roots: RepoRoots) -> dict[str, Any]:
    """Paper sandbox state, checksum-verified (corrupt → unavailable)."""
    from crypto_engine.paper_trading.state import CorruptStateError, load_account

    path = roots.paper_path
    if not path.is_file():
        return unavailable("not_initialized", "No paper sandbox initialized; run `sf paper init`.")
    try:
        account = load_account(path)
    except CorruptStateError:
        return unavailable("corrupt", "Paper state failed checksum; not displaying values.")
    except (OSError, ValueError):
        return unavailable("corrupt", "Paper state unreadable.")
    return {"available": True, "account": account.model_dump(mode="json")}


# -- data ------------------------------------------------------------------


def read_snapshots(roots: RepoRoots, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Dataset manifests with integrity metadata (never fetches new data)."""
    from crypto_engine.datasets.snapshots import list_snapshots

    try:
        manifests = list_snapshots(roots.data_storage)
    except OSError:
        return unavailable("missing", "Data storage unreadable.")
    if not manifests:
        return unavailable("empty", "No dataset snapshots recorded.")
    items = [m.model_dump(mode="json") for m in manifests]
    page, total = paginate(list(reversed(items)), limit, offset)
    return {"available": True, "snapshots": page, "total": total}


def read_snapshot(roots: RepoRoots, snapshot_id: str) -> dict[str, Any]:
    """One dataset manifest by snapshot id (404 when absent)."""
    from crypto_engine.datasets.snapshots import list_snapshots

    validate_id(snapshot_id, "snapshot_id")
    try:
        manifests = list_snapshots(roots.data_storage)
    except OSError:
        return unavailable("missing", "Data storage unreadable.")
    for manifest in manifests:
        if manifest.snapshot_id == snapshot_id:
            return {"available": True, "snapshot": manifest.model_dump(mode="json")}
    raise ApiError(404, "not_found", "No snapshot with that id.")


__all__ = [
    "RepoRoots",
    "clamp_limit",
    "paginate",
    "read_backtest",
    "read_backtest_slice",
    "read_backtests",
    "read_experiment",
    "read_experiments",
    "read_holdout_status",
    "read_latest_signal",
    "read_model",
    "read_models",
    "read_models_status",
    "read_paper",
    "read_signal",
    "read_signals",
    "read_snapshot",
    "read_snapshots",
    "unavailable",
    "verify_signals",
]
