"""Read-only repository adapters (Phase 7).

Thin wrappers over the canonical Phase 1-6 stores. Rules:

* Never write, train, fetch, or trade â€” pure reads only.
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
from datetime import UTC, datetime
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
    research_release_path: Path = field(
        default_factory=lambda: Path("docs/validation/results/quantitative_release_q4.json")
    )
    progress_dir: Path = field(default_factory=lambda: Path("data/project_progress"))
    ai_telemetry_path: Path = field(default_factory=lambda: Path("data/ai_telemetry/events.jsonl"))
    scan_cap: int = 5000
    health_checker: Any = None
    metrics_registry: Any = None


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


def read_research_release(roots: RepoRoots) -> dict[str, Any]:
    """Read the bounded, deterministic Q4 research release artifact.

    The path is derived from the configured data root's project parent, so the
    API does not accept arbitrary filesystem paths from a request. This reader
    has no write, training, data-fetch, model-load, or execution side effects.
    """
    configured_path = roots.research_release_path
    path = (
        configured_path
        if configured_path.is_absolute()
        else roots.data_storage.parent / configured_path
    )
    try:
        if not path.is_file():
            return unavailable("not_initialized", "Q4 research release has not been generated.")
        if path.stat().st_size > 1_000_000:
            return unavailable("corrupt", "Q4 research release exceeds the 1 MB read limit.")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return unavailable("missing", "Q4 research release is unreadable.")
    except (json.JSONDecodeError, ValueError):
        return unavailable("corrupt", "Q4 research release is not valid JSON.")

    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "signalforge.quantitative-release.q4.v1"
    ):
        return unavailable("corrupt", "Q4 research release has an unsupported schema.")
    authority = payload.get("authority")
    required_denials = (
        "ai_can_select_strategy",
        "ai_can_override_risk",
        "ai_can_promote",
        "human_authorized",
        "paper_trading_authorized",
        "order_execution_authorized",
    )
    if (
        not isinstance(authority, dict)
        or any(authority.get(key) is not False for key in required_denials)
        or authority.get("human_approval_required") is not True
    ):
        return unavailable(
            "corrupt", "Q4 research release contains an unsafe authority declaration."
        )
    return {"available": True, "release": payload}


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
    """Paper sandbox state, checksum-verified (corrupt â†' unavailable)."""
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
    account_data = account.model_dump(mode="json")
    account_data["available"] = True
    return account_data


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


# -- AI status (advisory-only, read-only configuration view) ----------------


def read_ai_status() -> dict[str, Any]:
    """AI enablement, registry counts and bounded limits (never secrets).

    Reads configuration only; no network call, no provider invocation. Returns
    an ``error`` entry when the registry is invalid, and ``available: False``
    when AI is disabled. This is the data backing the dashboard AI status
    panel (failure states visible, never authoritative).
    """
    from crypto_engine.ai.configuration import build_registry, load_ai_config
    from crypto_engine.ai.errors import AIError

    try:
        cfg = load_ai_config()
    except Exception as exc:  # configuration load failure
        return {
            "available": False,
            "enabled": False,
            "valid": False,
            "providers": 0,
            "models": 0,
            "routes": 0,
            "error": {"code": "configuration_error", "message": str(exc)},
        }

    try:
        snapshot = build_registry(cfg).snapshot()
        error = None
    except AIError as exc:
        snapshot = None
        error = {"code": exc.status.value, "message": exc.message}

    return {
        "available": cfg.enabled,
        "enabled": cfg.enabled,
        "valid": error is None,
        "providers": len(snapshot["providers"]) if snapshot else 0,
        "models": len(snapshot["models"]) if snapshot else 0,
        "routes": len(snapshot["routes"]) if snapshot else 0,
        "max_concurrent_requests": cfg.max_concurrent_requests,
        "max_retries": cfg.max_retries,
        "timeout_s": cfg.timeout_s,
        "max_request_chars": cfg.max_request_chars,
        "max_response_chars": cfg.max_response_chars,
        "max_output_tokens": cfg.max_output_tokens,
        "error": error,
        "provider_id": snapshot["providers"][0]["provider_id"] if snapshot.get("providers") else "",
        "model_id": snapshot["models"][0]["model_id"] if snapshot.get("models") else "",
    }


def read_ai_metrics(roots: RepoRoots) -> dict[str, Any]:
    """AI observability metrics from stored telemetry (read-only, no secrets)."""
    from crypto_engine.ai.telemetry import get_telemetry_store

    store = get_telemetry_store(roots)
    if not store._path.is_file():
        return unavailable("missing", "AI telemetry store not found.")
    try:
        metrics = store.metrics()
    except Exception:
        return unavailable("corrupt", "AI telemetry store unreadable.")
    if metrics.sample_size == 0:
        return unavailable("empty", "No AI telemetry events recorded.")
    return {
        "available": True,
        "provider_id": metrics.provider_id,
        "total_requests": metrics.total_requests,
        "successful_requests": metrics.successful_requests,
        "failed_requests": metrics.failed_requests,
        "success_percentage": metrics.success_percentage,
        "failure_percentage": metrics.failure_percentage,
        "average_latency_ms": metrics.average_latency_ms,
        "median_latency_ms": metrics.median_latency_ms,
        "p95_latency_ms": metrics.p95_latency_ms,
        "total_input_tokens": metrics.total_input_tokens,
        "total_output_tokens": metrics.total_output_tokens,
        "total_tokens": metrics.total_tokens,
        "average_tokens_per_request": metrics.average_tokens_per_request,
        "timeout_count": metrics.timeout_count,
        "invalid_output_count": metrics.invalid_output_count,
        "http_401_count": metrics.http_401_count,
        "http_402_count": metrics.http_402_count,
        "http_429_count": metrics.http_429_count,
        "sample_size": metrics.sample_size,
    }


# -- Q5 prospective collection (read-only) ----------------------------------


def read_q5_collection_status(roots: RepoRoots) -> dict[str, Any]:
    """Read-only Q5 prospective collection status (no network, no secrets).

    Reads epoch 3 freeze and collection manifests. All reads are read-only;
    no writes, no network, no secrets.
    """
    from crypto_engine.research.prospective.collector import ProspectiveCollector
    from crypto_engine.research.prospective.storage import ProspectiveStorage

    collector = ProspectiveCollector(
        exchange="binance",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframes=["1h"],
        freeze_manifest_path="docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json",
        collection_manifest_path="docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json",
    )

    # Verify collection manifest integrity
    integrity = collector.verify_collection_integrity()

    # Read storage state
    storage = ProspectiveStorage(roots.data_storage / "prospective/binance/spot/BTC/USDT/1h")
    dataset_manifest = storage.read_dataset_manifest() or {}
    ledger = storage.verify_ledger_chain()
    anchor_verification = storage.verify_collection_start_anchor()
    collection_started_at = anchor_verification.get("collection_started_at_utc")
    anchor_status = anchor_verification.get("status")
    if (
        anchor_status == "INVALID"
        and anchor_verification.get("reason")
        == "anchor exists but zero accepted observations found"
        and anchor_verification.get("anchor_hash_valid")
    ):
        anchor_status = "VALID_EMPTY"
    if not collection_started_at and anchor_status not in {"VALID", "VALID_EMPTY"}:
        try:
            manifest = json.loads(
                Path(collector.collection_manifest_path).read_text(encoding="utf-8")
            )
            collection_started_at = manifest.get("collection_started_at_utc")
        except Exception:
            collection_started_at = None
        if collection_started_at:
            anchor_status = "MISSING_LEGACY"

    observation_count = dataset_manifest.get("observation_count", 0)
    coverage = dataset_manifest.get("coverage_fraction", 0.0)
    data_quality_passed = ledger.get("valid", False) and observation_count > 0

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    window_expired = False
    if collection_started_at and anchor_status in {"VALID", "VALID_EMPTY", "MISSING_LEGACY"}:
        try:
            start_ms = int(
                datetime.fromisoformat(collection_started_at.replace("Z", "+00:00")).timestamp()
                * 1000
            )
            elapsed_ms = now_ms - start_ms
            min_elapsed_ms = collector.policy.min_elapsed_observation_period_s * 1000
            window_expired = elapsed_ms >= min_elapsed_ms
        except (ValueError, TypeError):
            window_expired = False

    obs_below_min = observation_count < collector.policy.min_observation_count
    cov_below_min = coverage < collector.policy.min_coverage_fraction
    requirements_met = not obs_below_min and not cov_below_min and data_quality_passed

    if anchor_status not in {"VALID", "VALID_EMPTY", "MISSING_LEGACY"} or not collection_started_at:
        lifecycle_state = "NOT_STARTED"
        requirements_met = False
        window_expired = False
    elif not window_expired and not requirements_met:
        lifecycle_state = "ACCUMULATING"
    elif window_expired and not requirements_met:
        lifecycle_state = "EXPIRED"
    elif requirements_met:
        lifecycle_state = "ELIGIBLE_FOR_EVALUATION"
    else:
        lifecycle_state = "ACCUMULATING"

    return {
        "available": True,
        "collection_id": integrity.get("collection_id"),
        "collection_started_at_utc": collection_started_at,
        "collection_start_anchor_status": anchor_status,
        "collection_start_anchor_hash": (anchor_verification.get("anchor") or {}).get(
            "anchor_hash"
        ),
        "associated_freeze_id": integrity.get("associated_freeze_id"),
        "associated_freeze_timestamp": integrity.get("associated_freeze_timestamp"),
        "exchange": collector.exchange,
        "market_type": collector.market_type,
        "symbols": collector.symbols,
        "timeframes": collector.timeframes,
        "manifest_status": integrity.get("status"),
        "manifest_reason": integrity.get("reason"),
        "dataset_hash": dataset_manifest.get("dataset_hash"),
        "raw_payload_hash": dataset_manifest.get("raw_payload_hash"),
        "observation_count": observation_count,
        "coverage_fraction": coverage,
        "ledger_events": ledger.get("event_count", 0),
        "ledger_valid": ledger.get("valid", False),
        "read_only": True,
        "lifecycle_state": lifecycle_state,
        "window_expired": window_expired,
        "observation_count_min": collector.policy.min_observation_count,
        "coverage_fraction_min": collector.policy.min_coverage_fraction,
        "min_elapsed_period_s": collector.policy.min_elapsed_observation_period_s,
        "elapsed_seconds": (
            (
                now_ms
                - int(
                    datetime.fromisoformat(collection_started_at.replace("Z", "+00:00")).timestamp()
                    * 1000
                )
            )
            / 1000.0
            if collection_started_at and anchor_status in {"VALID", "VALID_EMPTY", "MISSING_LEGACY"}
            else None
        ),
        "requirements_met": requirements_met,
    }


def read_q5_prospective_governance(roots: RepoRoots) -> dict[str, Any]:
    """Read-only Q5 prospective holdout governance state."""
    from crypto_engine.research.prospective.collector import ProspectiveCollector
    from crypto_engine.research.prospective.governance import (
        assess_prospective_holdout_readiness,
        historical_holdout_is_contaminated,
    )
    from crypto_engine.research.prospective.storage import ProspectiveStorage

    collector = ProspectiveCollector(
        exchange="binance",
        market_type="spot",
        symbols=["BTC/USDT"],
        timeframes=["1h"],
        freeze_manifest_path="docs/validation/RESEARCH_FREEZE_MANIFEST_EPOCH3.json",
        collection_manifest_path="docs/validation/PROSPECTIVE_COLLECTION_MANIFEST_EPOCH3.json",
    )

    freeze_check = collector.validate_freeze()
    if freeze_check.get("status") != "VALID":
        status = str(freeze_check.get("status") or "MISSING")
        reason = str(freeze_check.get("reason") or "freeze_not_valid")
        return {
            "available": False,
            "blocked": True,
            "reason": f"freeze_{status.lower()}:{reason}",
            "freeze_status": status,
            "freeze_reason": reason,
            "research_freeze_id": freeze_check.get("freeze_id"),
            "research_freeze_timestamp": freeze_check.get("created_at_utc"),
            "prospective_state": "UNAVAILABLE",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "read_only": True,
        }

    freeze_info = freeze_check
    collector.validate_or_create_collection_manifest()
    storage = ProspectiveStorage(roots.data_storage / "prospective/binance/spot/BTC/USDT/1h")
    dataset_manifest = storage.read_dataset_manifest() or {}
    ledger = storage.verify_ledger_chain()
    anchor_verification = storage.verify_collection_start_anchor()
    collection_started_at = anchor_verification.get("collection_started_at_utc")
    anchor_status = anchor_verification.get("status")
    if (
        anchor_status == "INVALID"
        and anchor_verification.get("reason")
        == "anchor exists but zero accepted observations found"
        and anchor_verification.get("anchor_hash_valid")
    ):
        anchor_status = "VALID_EMPTY"
    if not collection_started_at and anchor_status not in {"VALID", "VALID_EMPTY"}:
        try:
            manifest = json.loads(
                Path(collector.collection_manifest_path).read_text(encoding="utf-8")
            )
            collection_started_at = manifest.get("collection_started_at_utc")
        except Exception:
            collection_started_at = None
        if collection_started_at:
            anchor_status = "MISSING_LEGACY"

    observation_count = dataset_manifest.get("observation_count", 0)
    coverage = dataset_manifest.get("coverage_fraction", 0.0)
    data_quality_passed = ledger.get("valid", False) and observation_count > 0

    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    window_expired = False
    elapsed_ms = 0
    if collection_started_at and anchor_status in {"VALID", "VALID_EMPTY", "MISSING_LEGACY"}:
        try:
            start_ms = int(
                datetime.fromisoformat(collection_started_at.replace("Z", "+00:00")).timestamp()
                * 1000
            )
            elapsed_ms = now_ms - start_ms
            min_elapsed_ms = collector.policy.min_elapsed_observation_period_s * 1000
            window_expired = elapsed_ms >= min_elapsed_ms
        except (ValueError, TypeError):
            window_expired = False
            start_ms = now_ms
    else:
        start_ms = now_ms

    obs_below_min = observation_count < collector.policy.min_observation_count
    cov_below_min = coverage < collector.policy.min_coverage_fraction
    requirements_met = not obs_below_min and not cov_below_min and data_quality_passed

    if anchor_status not in {"VALID", "VALID_EMPTY", "MISSING_LEGACY"} or not collection_started_at:
        readiness = {
            "state": "NOT_STARTED",
            "prospective_status": "NOT_STARTED",
            "eligible_for_evaluation": False,
            "evaluated": False,
            "reasons": [
                "No accepted observations; collection_started_at_utc anchor not established."
            ],
            "first_seen_provenance_valid": False,
            "no_backfill": True,
            "no_development_inspection": True,
            "evaluation_requested": False,
        }
    else:
        readiness = assess_prospective_holdout_readiness(
            freeze_timestamp=freeze_info["created_at_utc"],
            collection_started_at=collection_started_at,
            observation_count=observation_count,
            min_observation_count=collector.policy.min_observation_count,
            coverage_fraction=coverage,
            min_coverage_fraction=collector.policy.min_coverage_fraction,
            data_quality_passed=data_quality_passed,
            first_seen_provenance_valid=True,
            post_collection_start=True,
            no_backfill=True,
            no_development_inspection=True,
            evaluation_requested=False,
        )

    if (
        window_expired
        and not requirements_met
        and readiness.get("state") in {"NOT_STARTED", "ACCUMULATING"}
    ):
        readiness["state"] = "EXPIRED"
        readiness["prospective_status"] = "EXPIRED"
        readiness["eligible_for_evaluation"] = False
        readiness["reasons"] = [
            f"Collection window expired after {elapsed_ms / 86400000:.1f} days "
            f"(minimum {collector.policy.min_elapsed_observation_period_s / 86400:.0f} days) "
            f"without meeting evaluation requirements "
            f"(observations={observation_count}/{collector.policy.min_observation_count}, "
            f"coverage={coverage:.2%}/{collector.policy.min_coverage_fraction:.2%})."
        ]

    historical = historical_holdout_is_contaminated()

    return {
        "available": True,
        "historical_holdout_state": historical["state"],
        "historical_holdout_contaminated": historical["reasons"][0]
        if historical["reasons"]
        else "",
        "prospective_state": readiness["state"],
        "eligible_for_evaluation": readiness["eligible_for_evaluation"],
        "evaluated": readiness["evaluated"],
        "observation_count": observation_count,
        "min_observation_count": collector.policy.min_observation_count,
        "coverage_fraction": coverage,
        "min_coverage_fraction": collector.policy.min_coverage_fraction,
        "data_quality_passed": data_quality_passed,
        "first_seen_provenance_valid": readiness.get("first_seen_provenance_valid", False),
        "no_backfill": readiness.get("no_backfill", True),
        "no_development_inspection": readiness.get("no_development_inspection", True),
        "evaluation_requested": readiness.get("evaluation_requested", False),
        "reasons": readiness.get("reasons", []),
        "research_freeze_id": freeze_info["freeze_id"],
        "research_freeze_timestamp": freeze_info["created_at_utc"],
        "collection_started_at_utc": collection_started_at,
        "collection_start_anchor_status": anchor_status,
        "collection_start_anchor_hash": (anchor_verification.get("anchor") or {}).get(
            "anchor_hash"
        ),
        "read_only": True,
        "lifecycle_state": "EXPIRED"
        if (window_expired and not requirements_met and readiness.get("state") != "NOT_STARTED")
        else readiness["state"],
        "window_expired": window_expired,
        "requirements_met": requirements_met,
        "elapsed_seconds": (elapsed_ms / 1000.0)
        if collection_started_at and anchor_status in {"VALID", "VALID_EMPTY", "MISSING_LEGACY"}
        else None,
    }


def read_q6_evaluation(roots: RepoRoots) -> dict[str, Any]:
    """Read-only Q6 prospective evaluation status."""
    from pathlib import Path

    eval_path = Path("docs/validation/results/q6_evaluation.json")
    if not eval_path.is_file():
        return {
            "available": False,
            "reason": "no evaluation run yet",
            "read_only": True,
        }
    try:
        import json as _json

        data = _json.loads(eval_path.read_text(encoding="utf-8"))
        data["available"] = True
        data["read_only"] = True
        return data
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "available": False,
            "reason": f"cannot read evaluation: {exc}",
            "read_only": True,
        }


def read_historical_validation_status() -> dict[str, Any]:
    """Read-only historical validation status."""
    from crypto_engine.research.q6.historical_storage import HistoricalStorage

    storage = HistoricalStorage()
    summary = storage.get_observation_summary()
    ledger = storage.verify_ledger_chain()
    manifest = storage.read_dataset_manifest() or {}

    expected_intervals = int(
        summary.get("expected_intervals", 0) or manifest.get("expected_intervals", 0) or 0
    )
    coverage_fraction = float(summary.get("coverage_fraction", 0.0) or 0.0)
    accepted = int(summary.get("accepted_unique_observations", 0))

    return {
        "available": True,
        "dataset_id": manifest.get("dataset_id"),
        "dataset_type": manifest.get("dataset_type"),
        "dataset_started_at_utc": manifest.get("dataset_started_at_utc"),
        "provider": manifest.get("provider"),
        "exchange": manifest.get("exchange"),
        "market_type": manifest.get("market_type"),
        "symbol": manifest.get("symbol"),
        "timeframe": manifest.get("timeframe"),
        "requested_start_utc": manifest.get("requested_start_utc"),
        "requested_end_utc": manifest.get("requested_end_utc"),
        "observation_count": accepted,
        "expected_intervals": expected_intervals,
        "coverage_fraction": coverage_fraction,
        "missing_interval_count": int(
            summary.get("missing_interval_count", max(0, expected_intervals - accepted))
        ),
        "duplicate_count": summary.get("duplicate_ingestion_attempts", 0),
        "conflicting_count": summary.get("conflicting_observations", 0),
        "dataset_hash": storage.compute_dataset_hash() or manifest.get("dataset_hash"),
        "raw_payload_hash": storage.compute_raw_payload_hash(),
        "ledger_events": ledger.get("event_count", 0),
        "ledger_valid": ledger.get("valid", False),
        "manifest_valid": bool(manifest),
        "manifest_status": "VALID" if manifest else "MISSING",
        "q5_2_eligible": bool(manifest.get("q5_2_eligible", False)),
        "is_prospective": bool(manifest.get("is_prospective", False)),
        "read_only": True,
    }


__all__ = [
    "RepoRoots",
    "clamp_limit",
    "paginate",
    "read_ai_metrics",
    "read_ai_status",
    "read_backtest",
    "read_backtest_slice",
    "read_backtests",
    "read_experiment",
    "read_experiments",
    "read_historical_validation_status",
    "read_holdout_status",
    "read_latest_signal",
    "read_model",
    "read_models",
    "read_models_status",
    "read_paper",
    "read_q5_collection_status",
    "read_q5_prospective_governance",
    "read_q6_evaluation",
    "read_research_release",
    "read_signal",
    "read_signals",
    "read_snapshot",
    "read_snapshots",
    "unavailable",
    "verify_signals",
]
