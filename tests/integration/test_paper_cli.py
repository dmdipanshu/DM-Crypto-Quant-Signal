"""Phase 6 Track B: `sf paper` CLI tests (offline, deterministic)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from crypto_engine.backtesting.engine import BacktestResult
from crypto_engine.cli import app, signals_for_backtest
from crypto_engine.paper_trading.compare import compare_account_to_backtest

runner = CliRunner()


def _paper_cfg(path: Path) -> Path:
    path.write_text(
        "[signal]\ncalibration_required = false\nrequire_edge = false\n"
        "[risk]\nmax_position_pct = 25.0\n",
        encoding="utf-8",
    )
    return path


def _compounding_snapshot(storage_root: Path, n: int = 30) -> Path:
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


def _write_json(path: Path, payload: object) -> Path:
    if hasattr(payload, "model_dump_json"):
        path.write_text(payload.model_dump_json(), encoding="utf-8")  # type: ignore[union-attr]
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_paper_help() -> None:
    result = runner.invoke(app, ["paper", "--help"])
    assert result.exit_code == 0
    for cmd in ("init", "status", "step", "flatten", "verify", "compare"):
        assert cmd in result.output


def test_paper_init_status_step_verify_happy_path(tmp_path: Path) -> None:
    acct = tmp_path / "paper" / "account.json"
    result = runner.invoke(
        app,
        [
            "paper",
            "init",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--equity",
            "10000",
            "--account",
            str(acct),
        ],
    )
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["paper", "status", "--account", str(acct)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["cash"] == 10000.0

    from tests.fixtures.backtest_fixtures import BASE, paper_bar, paper_signal

    sig = paper_signal("LONG", BASE, seed=7)
    bar = paper_bar(BASE + __import__("datetime").timedelta(hours=1), 100.0, 102.0, 98.0, 101.0)
    sig_path = _write_json(tmp_path / "sig.json", sig)
    bar_path = _write_json(tmp_path / "bar.json", bar)
    result = runner.invoke(
        app,
        [
            "paper",
            "step",
            "--account",
            str(acct),
            "--signal",
            str(sig_path),
            "--bar",
            str(bar_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "accepted"
    result = runner.invoke(app, ["paper", "verify", "--account", str(acct)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True


def test_paper_duplicate_step_rejected(tmp_path: Path) -> None:
    from tests.fixtures.backtest_fixtures import BASE, paper_bar, paper_signal

    acct = tmp_path / "paper" / "account.json"
    assert runner.invoke(app, ["paper", "init", "--account", str(acct)]).exit_code == 0
    sig = paper_signal("LONG", BASE, seed=7)
    bar = paper_bar(BASE + __import__("datetime").timedelta(hours=1), 100.0, 102.0, 98.0, 101.0)
    sig_path = _write_json(tmp_path / "sig.json", sig)
    bar_path = _write_json(tmp_path / "bar.json", bar)
    args = [
        "paper",
        "step",
        "--account",
        str(acct),
        "--signal",
        str(sig_path),
        "--bar",
        str(bar_path),
    ]
    assert runner.invoke(app, args).exit_code == 0
    result = runner.invoke(app, args)
    assert result.exit_code == 0
    assert json.loads(result.output)["reason"] == "DUPLICATE"


def test_paper_verify_catches_tamper(tmp_path: Path) -> None:
    acct = tmp_path / "paper" / "account.json"
    assert runner.invoke(app, ["paper", "init", "--account", str(acct)]).exit_code == 0
    raw = acct.read_text(encoding="utf-8").replace('"status": "open"', '"status": "halted"')
    acct.write_text(raw, encoding="utf-8")
    result = runner.invoke(app, ["paper", "verify", "--account", str(acct)])
    assert result.exit_code == 1
    assert json.loads(result.output)["ok"] is False


def test_paper_flatten_and_status(tmp_path: Path) -> None:
    from tests.fixtures.backtest_fixtures import BASE, paper_bar, paper_signal

    acct = tmp_path / "paper" / "account.json"
    assert runner.invoke(app, ["paper", "init", "--account", str(acct)]).exit_code == 0
    sig = paper_signal("LONG", BASE, seed=7)
    bar = paper_bar(BASE + __import__("datetime").timedelta(hours=1), 100.0, 102.0, 98.0, 101.0)
    sig_path = _write_json(tmp_path / "sig.json", sig)
    bar_path = _write_json(tmp_path / "bar.json", bar)
    assert (
        runner.invoke(
            app,
            [
                "paper",
                "step",
                "--account",
                str(acct),
                "--signal",
                str(sig_path),
                "--bar",
                str(bar_path),
            ],
        ).exit_code
        == 0
    )
    result = runner.invoke(app, ["paper", "flatten", "--account", str(acct), "--mark", "105"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["reason"] == "MANUAL_FLATTEN"
    status = json.loads(runner.invoke(app, ["paper", "status", "--account", str(acct)]).output)
    assert status["position"] is None
    assert status["n_trades_closed"] == 1


def test_paper_compare_matches_backtest_cli_run(
    stub_registry_model_dir: Path, tmp_path: Path
) -> None:
    """Paper stepping identical bar+signal sequences reproduces backtest fills."""
    from crypto_engine.config.settings import load_settings
    from crypto_engine.datasets.snapshots import load_snapshot
    from crypto_engine.paper_trading.state import load_account
    from crypto_engine.registry.store import load_model

    snap_dir = _compounding_snapshot(tmp_path / "storage")
    cfg_path = _paper_cfg(tmp_path / "paper.toml")
    settings = load_settings(cfg_path)
    bt_out = tmp_path / "bt"
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
            str(bt_out),
            "--config",
            str(cfg_path),
        ],
    )
    assert result.exit_code == 0, result.output
    ref = BacktestResult.model_validate_json((bt_out / "result.json").read_text(encoding="utf-8"))
    assert int(ref.summary["n_trades"]) >= 1

    # Regenerate the identical signal sequence with the same helper the CLI uses.
    from crypto_engine.cli import _backtest_bars_from_snapshot
    from crypto_engine.signals.costs import CostModel
    from crypto_engine.signals.schema import CostEstimate

    bars, manifest = _backtest_bars_from_snapshot(snap_dir)
    fitted, record_meta = load_model(stub_registry_model_dir)
    cost_model = CostModel.from_config(
        fee_bp=float(settings.validation.fee_bp), slippage_bp=float(settings.validation.slippage_bp)
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
    signals = signals_for_backtest(
        bars, manifest, fitted, record_meta, settings, int(settings.signal.horizon_bars), cost
    )
    assert len(signals) >= 1

    acct = tmp_path / "paper" / "account.json"
    result = runner.invoke(
        app,
        ["paper", "init", "--account", str(acct), "--config", str(cfg_path), "--equity", "10000"],
    )
    assert result.exit_code == 0, result.output

    stepped = 0
    for sig in signals:
        gen = sig.generated_at
        loc = bars.index.get_loc(gen)
        entry_idx = int(loc) + 1 if not isinstance(loc, slice) else None
        if entry_idx is None or entry_idx >= len(bars):
            continue  # no future bar: engine skips as NO_FUTURE_BAR
        row = bars.iloc[entry_idx]
        bar_payload = {
            "ts": bars.index[entry_idx].isoformat(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        sig_path = _write_json(tmp_path / f"sig-{stepped}.json", sig)
        bar_path = _write_json(tmp_path / f"bar-{stepped}.json", bar_payload)
        step_res = runner.invoke(
            app,
            [
                "paper",
                "step",
                "--account",
                str(acct),
                "--signal",
                str(sig_path),
                "--bar",
                str(bar_path),
                "--config",
                str(cfg_path),
            ],
        )
        assert step_res.exit_code == 0, step_res.output
        assert json.loads(step_res.output)["status"] == "accepted", step_res.output
        stepped += 1
    assert stepped >= 1
    # Close any open position at the last close (mirrors END_OF_DATA marking).
    flat_res = runner.invoke(
        app,
        [
            "paper",
            "flatten",
            "--account",
            str(acct),
            "--mark",
            str(float(bars["close"].iloc[-1])),
            "--config",
            str(cfg_path),
        ],
    )
    assert flat_res.exit_code == 0, flat_res.output

    cmp_res = runner.invoke(
        app,
        [
            "paper",
            "compare",
            "--account",
            str(acct),
            "--backtest",
            str(bt_out / "result.json"),
            "--config",
            str(cfg_path),
        ],
    )
    assert cmp_res.exit_code == 0, cmp_res.output
    assert json.loads(cmp_res.output)["verdict"] == "match"

    # Direct API cross-check on the same artifacts.
    _ = datetime.now(UTC)
    _ = load_snapshot
    report = compare_account_to_backtest(load_account(acct), ref)
    assert report.verdict == "match", report.reasons
