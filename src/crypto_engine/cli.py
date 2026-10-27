"""SignalForge Phase 1 CLI (``sf`` entry point)."""

from __future__ import annotations

import json
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from crypto_engine import __version__

app: typer.Typer = typer.Typer(name="sf", help="SignalForge Phase 1 CLI.")
data_app: typer.Typer = typer.Typer(help="Dataset snapshot commands.")
app.add_typer(data_app, name="data")
signal_app: typer.Typer = typer.Typer(help="Phase 5 signal generation + audit history.")
app.add_typer(signal_app, name="signal")

console = Console()


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


@app.command("health")
def health() -> None:
    """Report process health: version, config presence, storage writability."""
    from crypto_engine.config.settings import default_config_path

    try:
        config_path = default_config_path()
    except FileNotFoundError:
        config_path = _repo_root() / "config" / "default.toml"
    storage = _repo_root() / "data"
    writable = False
    try:
        storage.mkdir(parents=True, exist_ok=True)
        probe = storage / ".sf_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError:
        writable = False
    payload = {
        "status": "ok",
        "package_version": __version__,
        "python": platform.python_version(),
        "config_path": str(config_path),
        "config_exists": config_path.is_file(),
        "storage_path": str(storage),
        "storage_writable": writable,
    }
    console.print_json(json.dumps(payload))


@app.command("config-show")
def config_show(
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Load, validate, and print the effective configuration as JSON."""
    from crypto_engine.config.settings import load_settings

    settings = load_settings(config)
    console.print_json(settings.model_dump_json())


@data_app.command("status")
def data_status(
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Summarize persisted snapshots per symbol/timeframe."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.datasets.snapshots import list_snapshots

    settings = load_settings(config)
    root = _repo_root()
    storage = Path(settings.data.storage_path)
    if not storage.is_absolute():
        storage = root / storage
    manifests = list_snapshots(storage)
    if not manifests:
        console.print(f"No snapshots under {storage} (row count 0).")
        return
    table = Table(title=f"Snapshots ({len(manifests)})")
    table.add_column("exchange")
    table.add_column("symbol")
    table.add_column("timeframe")
    table.add_column("rows", justify="right")
    table.add_column("actual_start")
    table.add_column("actual_end")
    table.add_column("snapshot_id")
    for manifest in manifests:
        table.add_row(
            manifest.exchange,
            manifest.symbol,
            manifest.timeframe,
            str(manifest.row_count),
            manifest.actual_start or "-",
            manifest.actual_end or "-",
            manifest.snapshot_id,
        )
    console.print(table)


@data_app.command("fetch")
def data_fetch(
    symbol: Annotated[str, typer.Option(help="Market symbol, e.g. BTC/USDT.")] = "BTC/USDT",
    timeframe: Annotated[str, typer.Option(help="Candle timeframe, e.g. 1h.")] = "1h",
    since: Annotated[str | None, typer.Option(help="ISO start (inclusive).")] = None,
    until: Annotated[str | None, typer.Option(help="ISO end (inclusive).")] = None,
    limit: Annotated[int, typer.Option(help="Rows per page.")] = 1000,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Fetch live public OHLCV and persist an immutable snapshot (explicit network use)."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.data.adapter import CcxtAdapter
    from crypto_engine.data.types import FetchParams
    from crypto_engine.datasets.snapshots import save_snapshot

    def _parse(value: str | None) -> datetime | None:
        if value is None:
            return None
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

    settings = load_settings(config)
    adapter = CcxtAdapter(
        exchange_name=settings.exchange.name,
        timeout_ms=settings.exchange.timeout_ms,
        max_retries=settings.exchange.max_retries,
        rate_limit=settings.exchange.rate_limit,
    )
    params = FetchParams(
        exchange=settings.exchange.name,
        symbol=symbol,
        timeframe=timeframe,
        since=_parse(since),
        until=_parse(until),
        limit=limit,
    )
    result = adapter.fetch_range(params)
    root = _repo_root()
    storage = Path(settings.data.storage_path)
    if not storage.is_absolute():
        storage = root / storage
    snap_dir = save_snapshot(
        result.candles,
        result,
        storage,
        exchange=settings.exchange.name,
        symbol=symbol,
        timeframe=timeframe,
    )
    console.print(f"Saved snapshot {snap_dir} ({result.row_count} rows).")
    for warning in result.warnings:
        console.print(f"[yellow]warning: {warning}[/yellow]")


def _main() -> None:  # pragma: no cover - console entry
    app()


# --- Phase 5 Track B: `sf signal` sub-app ---------------------------------
# CLI v1 semantics (offline default, deterministic replay):
# - Loads settings (signal + risk sections), a registry model dir, and a
#   snapshot dir (explicit --snapshot, latest-under-storage, or --fetch).
# - Computes features over the tail slice and passes EXACTLY ONE row (the
#   last/most-recent) to decide(). Multi-row frames are never forwarded:
#   select_decision_row() raises on empty input, and callers must slice the
#   tail first. decide() therefore never reads future rows.
# - CLI v1 has NO calibrator support: calibration_status is "unavailable",
#   so with calibration_required=true (default) the policy outcome is
#   usually NO_TRADE(UNCALIBRATED) -- printed as valid output, exit 0.
# - Gates run with no portfolio snapshot (caller-supplied-only rule), so
#   exposure checks report EXPOSURE_UNKNOWN and fail closed (rejected)
#   unless --fail-open is passed (demo only: passes with a warning).
# - live --fetch requires explicit opt-in; otherwise fully offline.

TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


def _signal_repo_root() -> Path:
    return _repo_root()


def _resolve_storage(settings: object, root: Path) -> Path:
    storage = Path(getattr(settings.data, "storage_path", "data"))
    return storage if storage.is_absolute() else root / storage


def _resolve_history_dir(settings: object, root: Path, override: Path | None) -> Path:
    raw = Path(override) if override is not None else Path(settings.signal.history_path)
    return raw if raw.is_absolute() else root / raw


def _parse_cli_ts(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(f"{name} must be ISO-8601, got {value!r}: {exc}") from exc
    if dt.tzinfo is None:
        raise typer.BadParameter(f"{name} must be timezone-aware (UTC), got {value!r}.")
    return dt


def select_decision_row(features: object) -> dict[str, float]:
    """Return the last (most-recent) feature row as ``{name: float}``.

    Raises:
        ValueError: On empty input. Callers must slice the snapshot tail
            first; a multi-row frame is never passed to ``decide()`` -- only
            this single returned row is. Perturbing non-tail rows therefore
            cannot change the resulting ``signal_id``.
    """
    import pandas as pd

    if not isinstance(features, pd.DataFrame) or len(features) == 0:
        raise ValueError("select_decision_row: need a non-empty feature frame.")
    row = features.iloc[-1]
    out: dict[str, float] = {}
    for col in list(features.columns):
        try:
            out[str(col)] = float(row[col])
        except (TypeError, ValueError):
            out[str(col)] = float("nan")
    return out


def require_single_row_inputs(feature_row: object) -> dict[str, float]:
    """Accept exactly one decision row; reject multi-row input explicitly.

    ``decide()`` takes a single ``{name: float}`` row (the snapshot tail).
    Multi-row frames/lists are rejected with ``TypeError`` -- callers must
    slice the tail via :func:`select_decision_row` first, so policy inputs
    can never silently span rows or read future rows.
    """
    if isinstance(feature_row, dict) and all(
        isinstance(v, (int, float)) for v in feature_row.values()
    ):
        return {str(k): float(v) for k, v in feature_row.items()}
    raise TypeError(
        "require_single_row_inputs: decide() takes exactly one row "
        f"as {{name: float}}; got {type(feature_row).__name__}."
    )


def _latest_snapshot_dir(storage: Path, exchange: str, symbol: str, timeframe: str) -> Path | None:
    from crypto_engine.datasets.snapshots import list_snapshots

    candidates = [
        m
        for m in list_snapshots(storage)
        if m.exchange == exchange and m.symbol == symbol and m.timeframe == timeframe
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda m: (m.actual_end or "", m.snapshot_id))
    chosen = candidates[-1]
    from crypto_engine.datasets.snapshots import symbol_to_dir

    return (
        storage
        / "processed"
        / chosen.exchange
        / symbol_to_dir(chosen.symbol)
        / chosen.timeframe
        / chosen.snapshot_id
    )


@signal_app.command("generate")
def signal_generate(
    symbol: Annotated[str, typer.Option(help="Market symbol, e.g. BTC/USDT.")] = "BTC/USDT",
    timeframe: Annotated[str, typer.Option(help="Candle timeframe, e.g. 1h.")] = "1h",
    model: Annotated[Path | None, typer.Option(help="Registry model directory.")] = None,
    snapshot: Annotated[Path | None, typer.Option(help="Snapshot directory.")] = None,
    horizon: Annotated[int | None, typer.Option(help="Prediction horizon in bars.")] = None,
    at: Annotated[
        str | None, typer.Option("--at", help="Fixed signal timestamp (ISO, replay).")
    ] = None,
    now: Annotated[
        str | None, typer.Option("--now", help="Gate evaluation time (ISO, replay).")
    ] = None,
    fetch: Annotated[
        bool, typer.Option("--fetch", help="Live-fetch a snapshot first (network).")
    ] = False,
    fetch_limit: Annotated[int, typer.Option(help="Rows per page for --fetch.")] = 50,
    ai: Annotated[str, typer.Option(help="AI context: off (default).")] = "off",
    fail_open: Annotated[
        bool,
        typer.Option("--fail-open", help="Demo only: exposure-unknown passes with warning."),
    ] = False,
    history_path: Annotated[
        Path | None, typer.Option(help="Override for signal history dir.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Generate one signal from a model + snapshot; print schema-valid JSON."""
    import pandas as pd

    from crypto_engine.config.settings import load_settings

    if model is None:
        console.print("[red]error: --model <model_dir> is required.[/red]")
        raise typer.Exit(code=1)
    if snapshot is not None and fetch:
        console.print("[red]error: pass either --snapshot or --fetch, not both.[/red]")
        raise typer.Exit(code=1)

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    horizon_bars = int(horizon) if horizon is not None else int(settings.signal.horizon_bars)
    if horizon_bars < 1:
        console.print("[red]error: --horizon must be >= 1.[/red]")
        raise typer.Exit(code=1)

    from crypto_engine.models.base import CorruptArtifactError, UntrainedModelError
    from crypto_engine.registry.store import load_model

    try:
        fitted, record_meta = load_model(model)
    except (CorruptArtifactError, UntrainedModelError, OSError, ValueError) as exc:
        console.print(f"[red]error: cannot load model from {model}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    root = _signal_repo_root()
    storage = _resolve_storage(settings, root)

    snap_dir: Path | None = snapshot
    if fetch:
        from crypto_engine.data.adapter import CcxtAdapter
        from crypto_engine.data.types import FetchParams
        from crypto_engine.datasets.snapshots import save_snapshot

        def _parse(value: str | None) -> datetime | None:
            if value is None:
                return None
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        try:
            adapter = CcxtAdapter(
                exchange_name=settings.exchange.name,
                timeout_ms=settings.exchange.timeout_ms,
                max_retries=settings.exchange.max_retries,
                rate_limit=settings.exchange.rate_limit,
            )
            params = FetchParams(
                exchange=settings.exchange.name,
                symbol=symbol,
                timeframe=timeframe,
                since=_parse(None),
                until=_parse(None),
                limit=int(fetch_limit),
            )
            result = adapter.fetch_range(params)
            snap_dir = save_snapshot(
                result.candles,
                result,
                storage,
                exchange=settings.exchange.name,
                symbol=symbol,
                timeframe=timeframe,
            )
        except Exception as exc:
            console.print(f"[red]error: live fetch failed: {exc}[/red]")
            raise typer.Exit(code=1) from exc
    if snap_dir is None:
        snap_dir = _latest_snapshot_dir(storage, settings.exchange.name, symbol, timeframe)
    if snap_dir is None:
        console.print(
            "[red]error: no snapshot found under "
            f"{storage} for {symbol} {timeframe}; pass --snapshot or --fetch.[/red]"
        )
        raise typer.Exit(code=1)

    from crypto_engine.datasets.snapshots import load_snapshot

    try:
        frame, manifest = load_snapshot(snap_dir)
    except Exception as exc:
        console.print(f"[red]error: cannot load snapshot {snap_dir}: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if manifest.symbol != symbol or manifest.timeframe != timeframe:
        console.print(
            f"[red]error: snapshot is {manifest.symbol} {manifest.timeframe}, "
            f"requested {symbol} {timeframe}.[/red]"
        )
        raise typer.Exit(code=1)

    try:
        ts = pd.to_datetime(frame["timestamp"], utc=True)
    except Exception as exc:
        console.print(f"[red]error: snapshot has no usable timestamp column: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    ohlcv = frame.set_index(ts).sort_index()
    for col in ("open", "high", "low", "close", "volume"):
        ohlcv[col] = pd.to_numeric(ohlcv[col], errors="coerce")
    max_rows = int(settings.signal.max_rows_signal)
    tail = ohlcv.tail(max_rows)

    from crypto_engine.features.engineering import compute_features

    feats = compute_features(tail)
    if len(feats) == 0:
        console.print("[red]error: snapshot too small: no feature rows.[/red]")
        raise typer.Exit(code=1)
    last_row_all = select_decision_row(feats)

    model_features = list(record_meta.feature_names or [])
    if not model_features:
        console.print("[red]error: model record has empty feature_names.[/red]")
        raise typer.Exit(code=1)
    feature_row = require_single_row_inputs(
        {name: float(last_row_all.get(name, float("nan"))) for name in model_features}
    )

    gen_dt = _parse_cli_ts(at, "--at")
    if gen_dt is None:
        try:
            gen_dt = datetime.fromisoformat(str(manifest.actual_end).replace("Z", "+00:00"))
            if gen_dt.tzinfo is None:
                gen_dt = gen_dt.replace(tzinfo=UTC)
        except ValueError:
            gen_dt = datetime.now(UTC)
    tf_min = TIMEFRAME_MINUTES.get(timeframe, 60)
    val_dt = gen_dt + timedelta(hours=0, minutes=tf_min * horizon_bars)
    gate_now = _parse_cli_ts(now, "--now") or datetime.now(UTC)

    from crypto_engine.signals.costs import CostModel
    from crypto_engine.signals.policy import PolicyInputs, decide
    from crypto_engine.signals.schema import AiContextSummary, CostEstimate

    cost_model = CostModel.from_config(
        fee_bp=float(settings.validation.fee_bp),
        slippage_bp=float(settings.validation.slippage_bp),
    )
    cost = CostEstimate(
        fee_bp=cost_model.fee_bp,
        spread_bp=cost_model.spread_bp,
        slippage_bp=cost_model.slippage_bp,
        funding_bp=cost_model.funding_bp,
        source="configured",
        expected_edge_bp=None,
        edge_margin_bp=float(settings.signal.edge_margin_bp),
    )
    ai_summary = AiContextSummary(
        status="disabled" if ai == "off" else "unavailable",
        note="" if ai == "off" else "CLI v1: no provider call; advisory only.",
    )
    inputs = PolicyInputs(
        feature_row=feature_row,
        feature_names=model_features,
        model=fitted,
        model_id=record_meta.model_id,
        model_version=record_meta.version,
        model_artifact_hash=record_meta.artifact_sha256,
        calibrator=None,
        calibration_required=bool(settings.signal.calibration_required),
        threshold_version=str(settings.signal.threshold_version),
        t_long=float(settings.signal.t_long),
        t_short=float(settings.signal.t_short),
        require_edge=bool(settings.signal.require_edge),
        expected_edge_bp=None,
        cost=cost,
        enabled_directions=set(settings.signal.enabled_directions),
        data_quality="validated",
        generated_at=gen_dt.isoformat(),
        valid_until=val_dt.isoformat(),
        symbol=symbol,
        exchange=settings.exchange.name,
        timeframe=timeframe,
        horizon_bars=horizon_bars,
        regime="",
        blocked_regimes=set(),
        ai=ai_summary,
        ai_required=bool(settings.signal.require_ai_veto),
        dataset_snapshot_id=manifest.snapshot_id,
        levels=None,
    )
    record, _policy_trace = decide(inputs)

    from crypto_engine.risk.gates import GateContext, run_gates

    try:
        label_set = [int(c) for c in (fitted.to_dict().get("classes", []) or [])]
    except Exception:
        label_set = []
    try:
        feat_ts = tail.index[-1].to_pydatetime()
        if feat_ts.tzinfo is None:
            feat_ts = feat_ts.replace(tzinfo=UTC)
    except Exception:
        feat_ts = None
    ctx = GateContext(
        now_utc=gate_now,
        risk=settings.risk,
        max_age_min=float(settings.signal.max_signal_age_min),
        staleness_limit_min=float(settings.signal.staleness_limit_min),
        feature_timestamp=feat_ts,
        enabled_directions=set(settings.signal.enabled_directions),
        blocked_regimes=set(),
        require_edge=bool(settings.signal.require_edge),
        edge_margin_bp=float(settings.signal.edge_margin_bp),
        ai_required=bool(settings.signal.require_ai_veto),
        label_set=label_set or None,
        threshold_version=str(settings.signal.threshold_version),
        feature_names=model_features,
        portfolio=None,
        fail_closed=False if fail_open else None,
    )
    record, gate_trace = run_gates(
        record, ctx, artifact_hash=record_meta.artifact_sha256, feature_schema=model_features
    )
    _ = gate_trace

    hist_dir = _resolve_history_dir(settings, root, history_path)
    from crypto_engine.signals.history import PersistenceError, SignalHistory

    try:
        SignalHistory(hist_dir).append(record)
    except PersistenceError as exc:
        typer.echo(f"warning: history append failed ({exc}); signal NOT recorded.", err=True)
    except ValueError as exc:
        typer.echo(f"warning: history append refused ({exc}); signal NOT recorded.", err=True)

    typer.echo(record.model_dump_json(indent=2))


@signal_app.command("history")
def signal_history(
    list_all: Annotated[bool, typer.Option("--list/--no-list", help="List stored signals.")] = True,
    symbol: Annotated[str | None, typer.Option(help="Filter by symbol.")] = None,
    direction: Annotated[str | None, typer.Option(help="Filter by direction.")] = None,
    history_path: Annotated[
        Path | None, typer.Option(help="Override for signal history dir.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """List (or count) signals in the persistent audit history."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.signals.history import SignalHistory

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    hist_dir = _resolve_history_dir(settings, _signal_repo_root(), history_path)
    records = SignalHistory(hist_dir).list(symbol=symbol, direction=direction)
    if not list_all:
        typer.echo(json.dumps({"count": len(records)}))
        return
    typer.echo(json.dumps([r.model_dump(mode="json") for r in records], indent=2))


@signal_app.command("verify")
def signal_verify(
    history_path: Annotated[
        Path | None, typer.Option(help="Override for signal history dir.")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Verify the hash-chained signal audit history."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.signals.history import SignalHistory

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    hist_dir = _resolve_history_dir(settings, _signal_repo_root(), history_path)
    ok, errors = SignalHistory(hist_dir).verify()
    typer.echo(json.dumps({"ok": ok, "errors": errors}, indent=2))
    if not ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    _main()

__all__ = ["app", "data_app", "require_single_row_inputs", "select_decision_row", "signal_app"]
