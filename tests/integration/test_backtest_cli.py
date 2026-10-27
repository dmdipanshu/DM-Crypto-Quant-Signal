"""Phase 6 Track B: `sf backtest` CLI tests (offline, deterministic)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from crypto_engine.backtesting.engine import BacktestResult
from crypto_engine.cli import app

runner = CliRunner()


def test_backtest_help() -> None:
    result = runner.invoke(app, ["backtest", "--help"])
    assert result.exit_code == 0
    assert "run" in result.output


def test_backtest_run_zero_trades_valid(
    tiny_snapshot_dir: Path, stub_registry_model_dir: Path, tmp_path: Path
) -> None:
    """Default config: calibration unavailable -> usually abstains; zero trades valid."""
    out = tmp_path / "bt"
    result = runner.invoke(
        app,
        [
            "backtest",
            "run",
            "--snapshot",
            str(tiny_snapshot_dir),
            "--model",
            str(stub_registry_model_dir),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload_path = out / "result.json"
    assert payload_path.is_file()
    parsed = BacktestResult.model_validate_json(payload_path.read_text(encoding="utf-8"))
    assert parsed.n_bars == 5
    summary = json.loads(result.output)
    assert summary["n_bars"] == 5
    assert "config_hash" in summary


def test_backtest_run_missing_model(tiny_snapshot_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "bt-missing"
    result = runner.invoke(
        app,
        [
            "backtest",
            "run",
            "--snapshot",
            str(tiny_snapshot_dir),
            "--model",
            str(tmp_path / "no-model"),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code != 0
    assert not (out / "result.json").is_file()


def test_backtest_run_missing_snapshot(stub_registry_model_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "bt-nosnap"
    result = runner.invoke(
        app,
        [
            "backtest",
            "run",
            "--snapshot",
            str(tmp_path / "no-snap"),
            "--model",
            str(stub_registry_model_dir),
            "--out",
            str(out),
        ],
    )
    assert result.exit_code != 0
    assert not (out / "result.json").is_file()


def test_backtest_max_bars_cap_explicit_error(
    tiny_snapshot_dir: Path, stub_registry_model_dir: Path, tmp_path: Path
) -> None:
    """Oversize snapshot vs cap is an explicit error, never a silent downsample."""
    out = tmp_path / "bt-cap"
    result = runner.invoke(
        app,
        [
            "backtest",
            "run",
            "--snapshot",
            str(tiny_snapshot_dir),
            "--model",
            str(stub_registry_model_dir),
            "--out",
            str(out),
            "--max-bars",
            "2",
        ],
    )
    assert result.exit_code != 0
    assert "exceeds cap" in result.output
    assert not (out / "result.json").is_file()


def _compounding_snapshot(storage_root: Path, n: int = 30) -> Path:
    """30-bar ~5%/bar rising snapshot (scores saturate LONG) for trade tests."""
    from crypto_engine.data.validation import normalize_candles
    from crypto_engine.datasets.snapshots import save_snapshot

    rows: list[list] = []
    base_ms = 1_700_000_000_000
    for i in range(n):
        o = 100.0 * (1.05**i)
        c = o * 1.02
        hi = max(o, c) * 1.005
        lo = min(o, c) * 0.995
        rows.append([base_ms + i * 3_600_000, o, hi, lo, c, 10.0 + i])
    candles = normalize_candles(rows)
    return save_snapshot(
        candles,
        {"requested_start": None, "requested_end": None, "warnings": [], "missing_count": 0},
        storage_root,
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1h",
    )


def _paper_enabled_config(path: Path) -> Path:
    path.write_text(
        "[signal]\ncalibration_required = false\nrequire_edge = false\n"
        "[risk]\nmax_position_pct = 25.0\n",
        encoding="utf-8",
    )
    return path


def test_backtest_run_with_trades_custom_config(
    stub_registry_model_dir: Path, tmp_path: Path
) -> None:
    snap_dir = _compounding_snapshot(tmp_path / "storage")
    cfg = _paper_enabled_config(tmp_path / "paper.toml")
    out = tmp_path / "bt-trades"
    result = runner.invoke(
        app,
        [
            "backtest",
            "run",
            "--snapshot",
            str(snap_dir),
            "--model",
            str(stub_registry_model_dir),
            "--out",
            str(out),
            "--config",
            str(cfg),
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = BacktestResult.model_validate_json((out / "result.json").read_text(encoding="utf-8"))
    assert parsed.n_bars == 30
    assert int(parsed.summary["n_trades"]) >= 1
