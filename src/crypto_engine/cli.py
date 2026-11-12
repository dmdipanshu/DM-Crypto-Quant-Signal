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
from crypto_engine.ai.env_loader import load_env

# Load .env before any command runs so AI credentials are available to the
# gateway without manual shell exports.
load_env()

app: typer.Typer = typer.Typer(name="sf", help="SignalForge Phase 1 CLI.")
data_app: typer.Typer = typer.Typer(help="Dataset snapshot commands.")
app.add_typer(data_app, name="data")
signal_app: typer.Typer = typer.Typer(help="Phase 5 signal generation + audit history.")
app.add_typer(signal_app, name="signal")
backtest_app: typer.Typer = typer.Typer(help="Phase 6 event-safe historical backtesting.")
app.add_typer(backtest_app, name="backtest")
paper_app: typer.Typer = typer.Typer(help="Phase 6 advisory-only paper trading (no live orders).")
app.add_typer(paper_app, name="paper")
model_app: typer.Typer = typer.Typer(
    help="Phase 8 model training on local snapshots (no live data)."
)
app.add_typer(model_app, name="model")
ai_app: typer.Typer = typer.Typer(help="Universal AI gateway (advisory only; offline by default).")
app.add_typer(ai_app, name="ai")

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

    if ai == "explain":
        from crypto_engine.ai.advisory import apply_ai_context, explain_signal
        from crypto_engine.ai.composition import build_configured_gateway
        from crypto_engine.ai.errors import AIError as AIBoundaryError

        try:
            ai_gateway = build_configured_gateway(settings.ai)
        except AIBoundaryError:
            ai_gateway = None
        record = apply_ai_context(record, explain_signal(record, ai_gateway))

    hist_dir = _resolve_history_dir(settings, root, history_path)
    from crypto_engine.signals.history import PersistenceError, SignalHistory

    try:
        SignalHistory(hist_dir).append(record)
    except PersistenceError as exc:
        typer.echo(f"warning: history append failed ({exc}); signal NOT recorded.", err=True)
    except ValueError as exc:
        typer.echo(f"warning: history append refused ({exc}); signal NOT recorded.", err=True)

    typer.echo(record.model_dump_json(indent=2))

    if ai == "explain":
        ac = record.ai_context
        if ac and ac.status == "ok":
            typer.echo("")
            typer.echo("=== AI Advisory Explanation ===")
            typer.echo(f"Provider: {ac.provider_id}")
            typer.echo(f"Model: {ac.model_id}")
            typer.echo(f"Status: {ac.status}")
            if ac.summary:
                typer.echo(f"Explanation: {ac.summary}")
            if ac.evidence_ids:
                typer.echo(f"Evidence: {', '.join(ac.evidence_ids)}")


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

__all__ = [
    "app",
    "backtest_app",
    "data_app",
    "paper_app",
    "require_single_row_inputs",
    "select_decision_row",
    "signal_app",
    "signals_for_backtest",
]


# --- Phase 6 Track B: `sf backtest` + `sf paper` sub-apps --------------------
# BACKTEST semantics (offline, deterministic, single symbol/timeframe):
# - `sf backtest run --snapshot <dir> --model <dir>`: loads a snapshot +
#   registry model, replays a per-bar decide() loop over the bar closes
#   (features on the tail slice only, EXACTLY ONE decision row per bar via
#   select_decision_row/require_single_row_inputs -- never future rows), runs
#   run_gates with a flat caller-supplied portfolio (exposure 0 at starting
#   equity), collects approved non-NO_TRADE signals, then run_backtest with
#   costs from validation fee/slippage legs.
# - CLI v1 has NO calibrator support: calibration_status is "unavailable",
#   so with calibration_required=true (default) policy usually abstains and
#   the result is ZERO TRADES -- valid output, exit 0 (degenerate metrics
#   documented in the summary warnings). Pass a config with
#   signal.calibration_required=false + require_edge=false to exercise fills.
# - Bars over the effective cap (config backtest.max_bars unless --max-bars
#   overrides) are an explicit error, never a silent downsample. Missing
#   model/snapshot/data problems exit 1 with no output file (no fabrication).
# PAPER semantics (advisory-only, offline, no live orders -- by construction
# no order/trade API imports; state in paper_trading/state.py, stepping in
# paper_trading/loop.py, divergence in paper_trading/compare.py):
# - `sf paper init/status/step/flatten/verify/compare`. A step consumes ONE
#   bar JSON {ts,open,high,low,close} + ONE signal JSON file; fills happen at
#   the stepped bar open with signal.generated_at < bar.ts enforced.
# - Compare exit codes: 0 match, 2 diverged (valid analytical outcome),
#   1 errors. Step exit codes: 0 processed (accepted OR clean rejection with
#   machine-readable reason in the printed JSON), 1 errors.


def signals_for_backtest(
    bars: object,
    manifest: object,
    model: object,
    record_meta: object,
    settings: object,
    horizon_bars: int,
    cost: object,
) -> list:
    """Replay per-bar decide()+gates over ``bars``; return approved signals.

    One decision row per bar (tail slice only, never future rows). Signals
    vetoed by gates or with direction NO_TRADE are skipped (not fabricated).
    ``generated_at`` is the bar open ts, so the engine fills at the next bar.
    Raises ValueError when the model record has no feature names.
    """
    import pandas as pd

    from crypto_engine.features.engineering import compute_features
    from crypto_engine.risk.gates import GateContext, run_gates
    from crypto_engine.signals.costs import CostModel as _CostModel
    from crypto_engine.signals.policy import PolicyInputs, decide
    from crypto_engine.signals.schema import AiContextSummary, CostEstimate

    _ = _CostModel
    if not isinstance(bars, pd.DataFrame) or len(bars) == 0:
        raise ValueError("signals_for_backtest: bars must be a non-empty DataFrame.")
    feature_names = list(getattr(record_meta, "feature_names", None) or [])
    if not feature_names:
        raise ValueError("signals_for_backtest: model record has empty feature_names.")
    max_rows = int(settings.signal.max_rows_signal)
    starting = float(settings.backtest.starting_equity)
    tf_min = TIMEFRAME_MINUTES.get(str(settings.backtest.timeframe), 60)
    cost_est = cost if isinstance(cost, CostEstimate) else CostEstimate(source="configured")
    signals: list = []
    for i in range(len(bars)):
        gen_dt = bars.index[i].to_pydatetime()
        if gen_dt.tzinfo is None:
            gen_dt = gen_dt.replace(tzinfo=UTC)
        val_dt = gen_dt + timedelta(hours=0, minutes=tf_min * int(horizon_bars))
        tail = bars.iloc[max(0, i + 1 - max_rows) : i + 1]
        try:
            feats = compute_features(tail)
        except Exception:
            continue
        if len(feats) == 0:
            continue
        try:
            last_row_all = select_decision_row(feats)
            feature_row = require_single_row_inputs(
                {name: float(last_row_all.get(name, float("nan"))) for name in feature_names}
            )
        except (TypeError, ValueError):
            continue
        import math as _math

        if any(not _math.isfinite(v) for v in feature_row.values()):
            continue
        try:
            label_set = [int(c) for c in (model.to_dict().get("classes", []) or [])]
        except Exception:
            label_set = []
        inputs = PolicyInputs(
            feature_row=feature_row,
            feature_names=feature_names,
            model=model,
            model_id=str(getattr(record_meta, "model_id", "")),
            model_version=str(getattr(record_meta, "version", "")),
            model_artifact_hash=str(getattr(record_meta, "artifact_sha256", "")),
            calibrator=None,
            calibration_required=bool(settings.signal.calibration_required),
            threshold_version=str(settings.signal.threshold_version),
            t_long=float(settings.signal.t_long),
            t_short=float(settings.signal.t_short),
            require_edge=bool(settings.signal.require_edge),
            expected_edge_bp=None,
            cost=cost_est,
            enabled_directions=set(settings.signal.enabled_directions),
            data_quality="validated",
            generated_at=gen_dt.isoformat(),
            valid_until=val_dt.isoformat(),
            symbol=str(settings.backtest.symbol),
            exchange=str(settings.exchange.name),
            timeframe=str(settings.backtest.timeframe),
            horizon_bars=int(horizon_bars),
            regime="",
            blocked_regimes=set(),
            ai=AiContextSummary(status="disabled", note=""),
            ai_required=bool(settings.signal.require_ai_veto),
            dataset_snapshot_id=str(getattr(manifest, "snapshot_id", "")),
            levels=None,
        )
        record, _trace = decide(inputs)
        portfolio = {
            "exposure_pct": 0.0,
            "symbol_exposure_pct": 0.0,
            "day_start_equity": starting,
            "current_equity": starting,
            "strategy_enabled": True,
            "circuit_open": False,
        }
        ctx = GateContext(
            now_utc=gen_dt,
            risk=settings.risk,
            max_age_min=float(settings.signal.max_signal_age_min),
            staleness_limit_min=float(settings.signal.staleness_limit_min),
            feature_timestamp=gen_dt,
            enabled_directions=set(settings.signal.enabled_directions),
            blocked_regimes=set(),
            require_edge=bool(settings.signal.require_edge),
            edge_margin_bp=float(settings.signal.edge_margin_bp),
            ai_required=bool(settings.signal.require_ai_veto),
            label_set=label_set or None,
            threshold_version=str(settings.signal.threshold_version),
            feature_names=feature_names,
            portfolio=portfolio,
        )
        gated, _gate_trace = run_gates(
            record, ctx, artifact_hash=str(getattr(record_meta, "artifact_sha256", ""))
        )
        if gated.decision.status == "rejected":
            continue
        if gated.direction == "NO_TRADE":
            continue
        signals.append(gated)
    return signals


def _backtest_bars_from_snapshot(snapshot_dir: Path) -> tuple:
    """Load snapshot -> (UTC-indexed OHLC frame, manifest); raises on problems."""
    import pandas as pd

    from crypto_engine.datasets.snapshots import load_snapshot

    frame, manifest = load_snapshot(snapshot_dir)
    try:
        ts = pd.to_datetime(frame["timestamp"], utc=True)
    except Exception as exc:
        raise ValueError(f"snapshot has no usable timestamp column: {exc}") from exc
    ohlcv = frame.set_index(ts).sort_index()
    for col in ("open", "high", "low", "close"):
        if col not in ohlcv.columns:
            raise ValueError(f"snapshot missing OHLC column {col!r}.")
        ohlcv[col] = pd.to_numeric(ohlcv[col], errors="coerce")
    if ohlcv[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("snapshot OHLC contains non-numeric values (no silent fills).")
    cols = ["open", "high", "low", "close"] + (["volume"] if "volume" in ohlcv.columns else [])
    return ohlcv[cols], manifest


def _resolve_account_file(settings: object, root: Path, account: Path | None) -> Path:
    from crypto_engine.paper_trading.state import ACCOUNT_FILENAME

    if account is not None:
        raw = Path(account)
        if raw.exists() and raw.is_dir():
            raw = raw / ACCOUNT_FILENAME
        return raw if raw.is_absolute() else root / raw
    base = Path(settings.paper.account_path)
    base = base if base.is_absolute() else root / base
    return base / ACCOUNT_FILENAME


def _paper_cost_from_account(account: object) -> object:
    from crypto_engine.backtesting.costs import ExecutionCostModel
    from crypto_engine.signals.schema import CostEstimate

    legs = dict(account.cost)
    return ExecutionCostModel(
        CostEstimate(
            fee_bp=float(legs.get("fee_bp", 0.0)),
            spread_bp=float(legs.get("spread_bp", 0.0)),
            slippage_bp=float(legs.get("slippage_bp", 0.0)),
            funding_bp=float(legs.get("funding_bp", 0.0)),
            source="configured",
        )
    )


def _paper_gates_fn(settings: object) -> object:
    from crypto_engine.paper_trading.loop import make_gates_ctx_fn

    return make_gates_ctx_fn(
        settings.risk,
        max_age_min=float(settings.signal.max_signal_age_min),
        staleness_limit_min=float(settings.signal.staleness_limit_min),
        require_edge=bool(settings.signal.require_edge),
        edge_margin_bp=float(settings.signal.edge_margin_bp),
    )


@backtest_app.command("run")
def backtest_run(
    snapshot: Annotated[Path | None, typer.Option(help="Snapshot directory.")] = None,
    model: Annotated[Path | None, typer.Option(help="Registry model directory.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
    max_bars: Annotated[
        int | None, typer.Option(help="Row-cap override (errors when exceeded).")
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Output directory for result.json.")] = None,
    horizon: Annotated[int | None, typer.Option(help="Prediction horizon in bars.")] = None,
) -> None:
    """Run an event-safe backtest; write result.json + print the summary."""
    import hashlib as _hashlib
    import os as _os
    import tempfile as _tempfile

    from crypto_engine.config.settings import load_settings

    if snapshot is None:
        console.print("[red]error: --snapshot <snapshot_dir> is required.[/red]")
        raise typer.Exit(code=1)
    if model is None:
        console.print("[red]error: --model <model_dir> is required.[/red]")
        raise typer.Exit(code=1)
    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        bars, manifest = _backtest_bars_from_snapshot(Path(snapshot))
    except Exception as exc:
        console.print(f"[red]error: cannot load snapshot {snapshot}: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    if (
        manifest.symbol != settings.backtest.symbol
        or manifest.timeframe != settings.backtest.timeframe
    ):
        console.print(
            f"[red]error: snapshot is {manifest.symbol} {manifest.timeframe}, "
            f"config backtest is {settings.backtest.symbol} "
            f"{settings.backtest.timeframe} (no silent mix).[/red]"
        )
        raise typer.Exit(code=1)
    cap = int(max_bars) if max_bars is not None else int(settings.backtest.max_bars)
    if cap < 2:
        console.print("[red]error: bar cap must be >= 2.[/red]")
        raise typer.Exit(code=1)
    if len(bars) > cap:
        console.print(
            f"[red]error: snapshot has {len(bars)} bars, exceeds cap {cap} "
            "(4GB bound; not downsampled — pass a smaller snapshot or raise "
            "--max-bars explicitly).[/red]"
        )
        raise typer.Exit(code=1)

    from crypto_engine.registry.store import load_model

    try:
        fitted, record_meta = load_model(Path(model))
    except Exception as exc:
        console.print(f"[red]error: cannot load model from {model}: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    from crypto_engine.signals.costs import CostModel
    from crypto_engine.signals.schema import CostEstimate

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
    horizon_bars = int(horizon) if horizon is not None else int(settings.signal.horizon_bars)
    if horizon_bars < 1:
        console.print("[red]error: --horizon must be >= 1.[/red]")
        raise typer.Exit(code=1)
    try:
        signals = signals_for_backtest(
            bars, manifest, fitted, record_meta, settings, horizon_bars, cost
        )
    except ValueError as exc:
        console.print(f"[red]error: signal replay failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    from crypto_engine.backtesting.engine import run_backtest

    cfg = settings.backtest.model_copy(update={"max_bars": cap})
    try:
        result = run_backtest(bars, signals, cfg, cost)
    except Exception as exc:
        console.print(f"[red]error: backtest failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    root = _repo_root()
    out_dir = Path(out) if out is not None else (root / "data" / "backtests" / manifest.snapshot_id)
    out_dir = out_dir if out_dir.is_absolute() else root / out_dir
    payload = result.model_dump_json(indent=2).encode("utf-8")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = _tempfile.mkstemp(prefix="result.", suffix=".tmp", dir=str(out_dir))
        try:
            with _os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            _os.replace(Path(tmp_name), out_dir / "result.json")
        except BaseException:
            try:
                if Path(tmp_name).exists():
                    Path(tmp_name).unlink()
            except OSError:
                pass
            raise
    except OSError as exc:
        console.print(f"[red]error: cannot write {out_dir}/result.json: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    _ = _hashlib
    typer.echo(
        json.dumps(
            {
                "out": str(out_dir / "result.json"),
                "n_bars": result.n_bars,
                "n_signals": len(signals),
                "n_trades": int(result.summary.get("n_trades", 0)),
                "net_pnl": float(result.summary.get("net_pnl", 0.0)),
                "final_equity": float(result.summary.get("final_equity", 0.0)),
                "n_skipped": len(result.skipped_signals),
                "warnings": list(result.warnings),
                "config_hash": result.config_hash,
            },
            indent=2,
        )
    )


@paper_app.command("init")
def paper_init(
    symbol: Annotated[str, typer.Option(help="Market symbol, e.g. BTC/USDT.")] = "BTC/USDT",
    timeframe: Annotated[str, typer.Option(help="Candle timeframe, e.g. 1h.")] = "1h",
    equity_: Annotated[float, typer.Option("--equity", help="Starting equity.")] = 10000.0,
    position_pct: Annotated[float | None, typer.Option(help="Position size pct.")] = None,
    max_holding_bars: Annotated[int | None, typer.Option(help="Timeout in bars.")] = None,
    require_levels: Annotated[bool, typer.Option(help="Require SL/TP levels.")] = False,
    model: Annotated[str, typer.Option(help="Model id for provenance.")] = "",
    account: Annotated[Path | None, typer.Option(help="Account file path.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Create a paper account file (advisory-only; never places orders)."""
    import hashlib as _hashlib

    from crypto_engine.config.settings import load_settings
    from crypto_engine.paper_trading.state import init_account
    from crypto_engine.signals.costs import CostModel

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    cost_model = CostModel.from_config(
        fee_bp=float(settings.validation.fee_bp),
        slippage_bp=float(settings.validation.slippage_bp),
    )
    cfg_hash = _hashlib.sha256(
        json.dumps(
            {
                "backtest": settings.backtest.model_dump(mode="json"),
                "paper": settings.paper.model_dump(mode="json"),
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    target = _resolve_account_file(settings, _repo_root(), account)
    try:
        acct = init_account(
            target,
            symbol=symbol,
            timeframe=timeframe,
            starting_equity=float(equity_),
            position_pct=float(position_pct)
            if position_pct is not None
            else float(settings.backtest.position_pct),
            max_holding_bars=int(max_holding_bars)
            if max_holding_bars is not None
            else int(settings.backtest.max_holding_bars),
            require_levels=bool(require_levels),
            cost={
                "fee_bp": cost_model.fee_bp,
                "spread_bp": cost_model.spread_bp,
                "slippage_bp": cost_model.slippage_bp,
                "funding_bp": cost_model.funding_bp,
            },
            model_id=model,
            config_hash=cfg_hash,
            account_id=f"{symbol}-{timeframe}",
        )
    except (FileExistsError, ValueError) as exc:
        console.print(f"[red]error: cannot init account at {target}: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"account": str(target), "account_id": acct.account_id}, indent=2))


@paper_app.command("status")
def paper_status(
    account: Annotated[Path | None, typer.Option(help="Account file path.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Print a paper account summary (offline read)."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.paper_trading.state import load_account

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    target = _resolve_account_file(settings, _repo_root(), account)
    try:
        acct = load_account(target)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(
        json.dumps(
            {
                "account_id": acct.account_id,
                "symbol": acct.symbol,
                "timeframe": acct.timeframe,
                "status": acct.status,
                "halt_reason": acct.halt_reason,
                "cash": acct.cash,
                "starting_equity": acct.starting_equity,
                "equity_mark": acct.equity_mark,
                "equity_mark_ts": acct.equity_mark_ts,
                "position": acct.position,
                "n_trades_closed": len(acct.trades_closed),
                "n_processed": len(acct.processed_signal_ids),
                "n_rejections": len(acct.rejections),
                "bars_seen": acct.bars_seen,
                "model_id": acct.model_id,
                "config_hash": acct.config_hash,
            },
            indent=2,
            default=str,
        )
    )


@paper_app.command("step")
def paper_step(
    signal: Annotated[Path | None, typer.Option(help="SignalRecord JSON file.")] = None,
    bar: Annotated[
        Path | None, typer.Option(help="Bar JSON file {ts,open,high,low,close}.")
    ] = None,
    account: Annotated[Path | None, typer.Option(help="Account file path.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Consume ONE bar + ONE signal; fills at the bar open (offline)."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.paper_trading.loop import step
    from crypto_engine.paper_trading.state import load_account, save_account
    from crypto_engine.signals.schema import SignalRecord

    if signal is None or bar is None:
        console.print("[red]error: --signal <signal.json> and --bar <bar.json> are required.[/red]")
        raise typer.Exit(code=1)
    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    target = _resolve_account_file(settings, _repo_root(), account)
    try:
        acct = load_account(target)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        sig = SignalRecord.model_validate_json(Path(signal).read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]error: cannot parse signal {signal}: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        bar_payload = json.loads(Path(bar).read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]error: cannot parse bar {bar}: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        new_acct, result = step(
            acct, sig, bar_payload, _paper_gates_fn(settings), _paper_cost_from_account(acct)
        )
    except (TypeError, ValueError) as exc:
        console.print(f"[red]error: step failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        save_account(new_acct, target)
    except OSError as exc:
        console.print(f"[red]error: cannot save {target}: {exc} (step NOT persisted).[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(result.model_dump_json(indent=2))


@paper_app.command("flatten")
def paper_flatten(
    mark: Annotated[float, typer.Option(help="Mark price to close at.")] = 0.0,
    account: Annotated[Path | None, typer.Option(help="Account file path.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Explicitly close the open position at --mark (costed, MANUAL_FLATTEN)."""
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    from crypto_engine.config.settings import load_settings
    from crypto_engine.paper_trading.state import flatten, load_account, save_account

    if not mark or mark <= 0:
        console.print("[red]error: --mark <positive price> is required.[/red]")
        raise typer.Exit(code=1)
    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    target = _resolve_account_file(settings, _repo_root(), account)
    try:
        acct = load_account(target)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        fill = flatten(acct, float(mark), _datetime.now(_UTC).isoformat())
    except ValueError as exc:
        console.print(f"[red]error: flatten failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        save_account(acct, target)
    except OSError as exc:
        console.print(f"[red]error: cannot save {target}: {exc} (flatten NOT persisted).[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"reason": "MANUAL_FLATTEN", "fill": fill}, indent=2, default=str))


@paper_app.command("verify")
def paper_verify(
    account: Annotated[Path | None, typer.Option(help="Account file path.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Verify a paper account checksum + schema (offline)."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.paper_trading.state import load_account

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    target = _resolve_account_file(settings, _repo_root(), account)
    try:
        acct = load_account(target)
    except FileNotFoundError as exc:
        typer.echo(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        typer.echo(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2))
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"ok": True, "errors": [], "account_id": acct.account_id}, indent=2))


@paper_app.command("compare")
def paper_compare(
    backtest: Annotated[Path | None, typer.Option(help="Backtest result.json file.")] = None,
    account: Annotated[Path | None, typer.Option(help="Account file path.")] = None,
    qty_rel_tol: Annotated[float, typer.Option(help="Qty relative tolerance.")] = 1e-9,
    pnl_abs_tol: Annotated[float, typer.Option(help="Net-PnL absolute tolerance.")] = 1e-6,
    equity_abs_tol: Annotated[float, typer.Option(help="End-equity absolute tolerance.")] = 1e-6,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Compare paper closed trades vs a frozen backtest ledger (offline)."""
    from crypto_engine.backtesting.engine import BacktestResult
    from crypto_engine.config.settings import load_settings
    from crypto_engine.paper_trading.compare import compare_account_to_backtest
    from crypto_engine.paper_trading.state import load_account

    if backtest is None:
        console.print("[red]error: --backtest <result.json> is required.[/red]")
        raise typer.Exit(code=1)
    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    target = _resolve_account_file(settings, _repo_root(), account)
    try:
        acct = load_account(target)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        ref = BacktestResult.model_validate_json(Path(backtest).read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]error: cannot parse backtest {backtest}: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    try:
        report = compare_account_to_backtest(
            acct,
            ref,
            qty_rel_tol=qty_rel_tol,
            pnl_abs_tol=pnl_abs_tol,
            equity_abs_tol=equity_abs_tol,
        )
    except (TypeError, ValueError) as exc:
        console.print(f"[red]error: compare failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    typer.echo(report.model_dump_json(indent=2))
    if report.verdict != "match":
        raise typer.Exit(code=2)


# --- Phase 7: `sf serve` read-only server ------------------------------------
# Loopback-only by default; explicit startup; no training, fetching, backtests,
# or mutations � the app only reads canonical stores. Requires the optional
# `api` extra (`uv sync --extra api`).


@app.command("serve")
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default from config).")] = None,
    port: Annotated[int | None, typer.Option(help="Bind port (default from config).")] = None,
    allow_remote: Annotated[
        bool, typer.Option(help="Allow non-loopback bind (explicit only).")
    ] = False,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Start the read-only SignalForge API + dashboard server (foreground)."""
    import ipaddress

    from crypto_engine.config.settings import load_settings

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    bind_host = host or settings.api.host
    bind_port = int(port) if port is not None else int(settings.api.port)
    remote_ok = bool(allow_remote) or bool(settings.api.allow_remote)
    try:
        is_loopback = ipaddress.ip_address(bind_host).is_loopback
    except ValueError:
        is_loopback = bind_host.lower() in ("localhost",)
    if not is_loopback and not remote_ok:
        console.print(
            f"[red]error: refusing non-loopback bind {bind_host} without --allow-remote "
            "(and documented auth/networking). Use 127.0.0.1.[/red]"
        )
        raise typer.Exit(code=1)

    try:
        from crypto_engine.api.app import create_app
        from crypto_engine.api.repos import RepoRoots
    except ImportError as exc:
        console.print(
            "[red]error: API extra not installed. "
            "Run `uv sync --extra api` (installs fastapi + uvicorn).[/red]"
        )
        raise typer.Exit(code=1) from exc

    root = _signal_repo_root()
    storage = _resolve_storage(settings, root)

    def _abs(value: str) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else root / candidate

    roots = RepoRoots(
        signals_dir=_resolve_history_dir(settings, root, None),
        registry_dir=_abs(settings.registry.path),
        data_storage=storage,
        backtest_dir=_abs(settings.api.backtest_dir),
        paper_path=_resolve_account_file(settings, root, None),
        experiments_path=_abs(settings.api.experiments_path),
        holdout_ledger_path=_abs(settings.api.holdout_ledger_path),
        progress_dir=_abs(settings.api.progress_dir),
        scan_cap=int(settings.api.ledger_scan_cap),
    )
    application = create_app(roots, settings)

    try:
        import uvicorn
    except ImportError as exc:
        console.print("[red]error: uvicorn not installed. Run `uv sync --extra api`.[/red]")
        raise typer.Exit(code=1) from exc
    console.print(
        f"[green]SignalForge read-only API serving on http://{bind_host}:{bind_port} "
        "(Ctrl+C to stop). No trading, training, or mutations.[/green]"
    )
    try:
        uvicorn.run(application, host=bind_host, port=bind_port, log_level="info")
    except OSError as exc:
        console.print(f"[red]error: cannot bind {bind_host}:{bind_port}: {exc}[/red]")
        raise typer.Exit(code=1) from exc


# --- Phase 8: `sf model` training commands (local snapshots only) ------------


@model_app.command("train")
def model_train(
    snapshot: Annotated[Path | None, typer.Option(help="Explicit snapshot directory.")] = None,
    symbol: Annotated[
        str | None, typer.Option(help="Symbol for snapshot discovery (e.g. BTC/USDT).")
    ] = None,
    timeframe: Annotated[
        str | None, typer.Option(help="Timeframe for snapshot discovery (e.g. 1h).")
    ] = None,
    registry: Annotated[
        Path | None, typer.Option(help="Registry root for the new artifact.")
    ] = None,
    train_frac: Annotated[float, typer.Option(help="Chronological training share (0,1).")] = 0.6,
    valid_frac: Annotated[
        float, typer.Option(help="Chronological validation share; rest is holdout.")
    ] = 0.2,
    pt_mult: Annotated[
        float | None, typer.Option(help="Triple-barrier profit multiple (default from config).")
    ] = None,
    sl_mult: Annotated[
        float | None, typer.Option(help="Triple-barrier stop multiple (default from config).")
    ] = None,
    max_horizon: Annotated[
        int | None, typer.Option(help="Triple-barrier horizon in bars (default from config).")
    ] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Train a logistic baseline on a local snapshot; print the run summary as JSON."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.training.pipeline import TrainRequest, train_model

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    if snapshot is None and (symbol is None or timeframe is None):
        console.print(
            "[red]error: pass --snapshot <dir>, or both --symbol and --timeframe "
            "for snapshot discovery. Training never fetches live data.[/red]"
        )
        raise typer.Exit(code=1)
    if (
        not (0.0 < train_frac < 1.0)
        or not (0.0 <= valid_frac < 1.0)
        or train_frac + valid_frac >= 1.0
    ):
        console.print("[red]error: need 0 < --train-frac < 1, 0 <= --valid-frac, sum < 1.[/red]")
        raise typer.Exit(code=2)
    for name, value in (("pt-mult", pt_mult), ("sl-mult", sl_mult)):
        if value is not None and not value > 0:
            console.print(f"[red]error: --{name} must be > 0.[/red]")
            raise typer.Exit(code=2)
    if max_horizon is not None and max_horizon < 1:
        console.print("[red]error: --max-horizon must be >= 1.[/red]")
        raise typer.Exit(code=2)

    root = _repo_root()
    reg_root = Path(registry) if registry is not None else Path(settings.registry.path)
    if not reg_root.is_absolute():
        reg_root = root / reg_root
    request = TrainRequest(
        snapshot_dir=Path(snapshot) if snapshot is not None else None,
        symbol=symbol,
        timeframe=timeframe,
        storage_root=_resolve_storage(settings, root),
        registry_root=reg_root,
        train_frac=float(train_frac),
        valid_frac=float(valid_frac),
        pt_mult=pt_mult,
        sl_mult=sl_mult,
        max_horizon_bars=max_horizon,
    )
    try:
        summary = train_model(request, settings)
    except FileNotFoundError as exc:
        console.print(f"[red]error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    except FileExistsError as exc:
        console.print(f"[red]error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        console.print(f"[red]error: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(summary, default=str))


@model_app.command("list")
def model_list(
    registry: Annotated[Path | None, typer.Option(help="Registry root to inspect.")] = None,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """List registered model artifacts (metadata only, never loads weights)."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.registry.store import list_models

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    root = _repo_root()
    reg_root = Path(registry) if registry is not None else Path(settings.registry.path)
    if not reg_root.is_absolute():
        reg_root = root / reg_root
    try:
        records = list_models(reg_root)
    except OSError as exc:
        console.print(f"[red]error: cannot read registry {reg_root}: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    table = Table(title=f"Models ({len(records)})")
    for col in ("model_id", "family", "version", "status", "dataset_snapshot_id", "created_at"):
        table.add_column(col)
    for record in records:
        table.add_row(
            record.model_id,
            record.family,
            record.version,
            str(record.status),
            record.dataset_snapshot_id,
            str(record.created_at),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Stage 5: universal AI gateway CLI (advisory only; offline/mock by default)
# ---------------------------------------------------------------------------


def _load_ai_config(config: Path | None):
    """Load validated settings and return the ``[ai]`` section."""
    from crypto_engine.config.settings import load_settings

    try:
        return load_settings(config).ai
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc


def _ai_registry_snapshot(config: Path | None):
    from crypto_engine.ai.configuration import build_registry
    from crypto_engine.ai.errors import AIError

    cfg = _load_ai_config(config)
    try:
        return build_registry(cfg).snapshot()
    except AIError as exc:
        console.print_json(
            json.dumps(
                {"error": {"code": exc.status.value, "message": exc.message}},
                sort_keys=True,
            )
        )
        raise typer.Exit(code=1) from exc


@ai_app.command("status")
def ai_status(
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Report AI enablement, registry counts and bounded limits (never secrets)."""
    from crypto_engine.ai.configuration import build_registry
    from crypto_engine.ai.errors import AIError

    cfg = _load_ai_config(config)
    error: dict[str, str] | None = None
    snapshot = None
    try:
        snapshot = build_registry(cfg).snapshot()
    except AIError as exc:
        error = {"code": exc.status.value, "message": exc.message}
    payload = {
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
    }
    console.print_json(json.dumps(payload, sort_keys=True))


@ai_app.command("config-check")
def ai_config_check(
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Validate providers/models/task routes without network access."""
    from crypto_engine.ai.composition import build_ports
    from crypto_engine.ai.configuration import build_registry
    from crypto_engine.ai.errors import AIError

    cfg = _load_ai_config(config)
    try:
        build_registry(cfg)
        build_ports(cfg)
        payload: dict[str, object] = {"ok": True, "errors": []}
    except AIError as exc:
        payload = {
            "ok": False,
            "errors": [{"code": exc.status.value, "message": exc.message}],
        }
    console.print_json(json.dumps(payload, sort_keys=True))
    if not payload["ok"]:
        raise typer.Exit(code=1)


@ai_app.command("providers")
def ai_providers(
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """List configured providers (credential references only, never values)."""
    snapshot = _ai_registry_snapshot(config)
    console.print_json(json.dumps({"providers": snapshot["providers"]}, sort_keys=True))


@ai_app.command("models")
def ai_models(
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """List configured models and their enablement."""
    snapshot = _ai_registry_snapshot(config)
    console.print_json(json.dumps({"models": snapshot["models"]}, sort_keys=True))


@ai_app.command("routes")
def ai_routes(
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """List deterministic task -> provider -> model routes."""
    snapshot = _ai_registry_snapshot(config)
    console.print_json(json.dumps({"routes": snapshot["routes"]}, sort_keys=True))


@ai_app.command("test")
def ai_test(
    task: Annotated[str, typer.Option(help="AI task to route (e.g. signal_explanation).")] = (
        "signal_explanation"
    ),
    live: Annotated[
        bool,
        typer.Option(
            "--live",
            help="Use the real provider endpoint (requires enabled config + credentials).",
        ),
    ] = False,
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Exercise the gateway; offline mock by default, real calls only with --live."""
    from crypto_engine.ai.composition import build_configured_gateway, mock_transport
    from crypto_engine.ai.contracts import AIRequest, AITask
    from crypto_engine.ai.errors import AIError

    cfg = _load_ai_config(config)
    try:
        resolved_task = AITask(task)
    except ValueError as exc:
        console.print(f"[red]error: unknown AI task {task!r}[/red]")
        raise typer.Exit(code=2) from exc
    if not cfg.enabled:
        console.print_json(
            json.dumps(
                {
                    "status": "disabled",
                    "detail": "AI is disabled in configuration; deterministic core runs alone.",
                },
                sort_keys=True,
            )
        )
        return
    try:
        gateway = build_configured_gateway(
            cfg,
            mock=not live,
            transport=None if live else mock_transport,
        )
    except AIError as exc:
        console.print_json(
            json.dumps({"status": exc.status.value, "error": exc.message}, sort_keys=True)
        )
        raise typer.Exit(code=1) from exc
    request = AIRequest(
        task=resolved_task,
        task_text="Connectivity check. Reply with one short advisory sentence.",
        evidence={"purpose": "sf ai test"},
        output_schema_id="text.v1",
    )
    response = gateway.complete(request)
    payload = {
        "status": response.status.value,
        "provider_id": response.provider_id,
        "model_id": response.model_id,
        "route_id": response.route_id,
        "attempts": response.attempts,
        "latency_ms": response.latency_ms,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        "advisory_only": response.advisory_only,
        "live": live,
        "text": (response.text or "")[:200],
        "error": (
            {
                "code": response.error.code.value,
                "message": response.error.message,
                "retryable": response.error.retryable,
                "provider_status": response.error.provider_status,
            }
            if response.error is not None
            else None
        ),
    }
    console.print_json(json.dumps(payload, sort_keys=True))
    if not response.ok:
        raise typer.Exit(code=1)


@ai_app.command("metrics")
def ai_metrics(
    config: Annotated[
        Path | None, typer.Option("--config", help="Path to a TOML config file.")
    ] = None,
) -> None:
    """Read-only AI observability metrics from stored telemetry (never secrets)."""
    from crypto_engine.ai.configuration import load_settings
    from crypto_engine.ai.telemetry import TelemetryStore

    try:
        settings = load_settings(config)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]error: cannot load config: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    ai_cfg = getattr(settings, "ai", None)
    telemetry_path = Path("data/ai_telemetry/events.jsonl")
    if ai_cfg is not None and hasattr(ai_cfg, "telemetry_path"):
        telemetry_path = Path(ai_cfg.telemetry_path)

    store = TelemetryStore(telemetry_path)
    metrics = store.metrics()

    table = Table(title="AI Observability Metrics (read-only, no secrets)")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    def _row(label: str, value: str) -> None:
        table.add_row(label, value)

    _row("Provider", ai_cfg.provider_id if hasattr(ai_cfg, "provider_id") else "n/a")
    _row("Total requests", str(metrics.total_requests))
    _row("Successful requests", str(metrics.successful_requests))
    _row("Failed requests", str(metrics.failed_requests))
    _row("Success percentage", f"{metrics.success_percentage}%")
    _row("Failure percentage", f"{metrics.failure_percentage}%")
    _row("Average latency (ms)", f"{metrics.average_latency_ms}")
    _row("Median latency (ms)", f"{metrics.median_latency_ms}")
    if metrics.p95_latency_ms is not None:
        _row("P95 latency (ms)", f"{metrics.p95_latency_ms}")
    _row("Total input tokens", str(metrics.total_input_tokens))
    _row("Total output tokens", str(metrics.total_output_tokens))
    _row("Total tokens", str(metrics.total_tokens))
    _row("Avg tokens/request", f"{metrics.average_tokens_per_request}")
    _row("Timeout count", str(metrics.timeout_count))
    _row("Invalid output count", str(metrics.invalid_output_count))
    _row("HTTP 401 count", str(metrics.http_401_count))
    _row("HTTP 402 count", str(metrics.http_402_count))
    _row("HTTP 429 count", str(metrics.http_429_count))
    _row("Sample size", str(metrics.sample_size))
    console.print(table)
