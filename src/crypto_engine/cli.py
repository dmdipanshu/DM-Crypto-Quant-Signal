"""SignalForge Phase 1 CLI (``sf`` entry point)."""

from __future__ import annotations

import json
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from crypto_engine import __version__

app: typer.Typer = typer.Typer(name="sf", help="SignalForge Phase 1 CLI.")
data_app: typer.Typer = typer.Typer(help="Dataset snapshot commands.")
app.add_typer(data_app, name="data")

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


if __name__ == "__main__":  # pragma: no cover
    _main()

__all__ = ["app", "data_app"]
