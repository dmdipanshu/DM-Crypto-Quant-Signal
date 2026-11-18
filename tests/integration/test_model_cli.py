"""CLI integration tests for `sf model` (offline, Typer runner, tmp dirs)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from crypto_engine.cli import app
from tests.fixtures.model_train_fixtures import make_oscillating_snapshot_dir

runner = CliRunner()


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "sf.toml"
    config.write_text('[exchange]\nname = "binance"\n', encoding="utf-8")
    return config


def test_model_help() -> None:
    result = runner.invoke(app, ["model", "--help"])
    assert result.exit_code == 0, result.output
    assert "train" in result.output


def test_model_train_help() -> None:
    result = runner.invoke(app, ["model", "train", "--help"])
    assert result.exit_code == 0, result.output
    assert "--snapshot" in result.output
    assert "--symbol" in result.output
    assert "--registry" in result.output


def test_model_train_missing_snapshot_args(tmp_path: Path) -> None:
    result = runner.invoke(app, ["model", "train", "--config", str(_config(tmp_path))])
    assert result.exit_code != 0
    assert "snapshot" in result.output.lower()


def test_model_train_bad_snapshot_dir(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "model",
            "train",
            "--snapshot",
            str(tmp_path / "nope"),
            "--config",
            str(_config(tmp_path)),
        ],
    )
    assert result.exit_code != 0
    assert "snapshot" in result.output.lower()


def test_model_train_bad_fractions(tmp_path: Path) -> None:
    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)
    result = runner.invoke(
        app,
        [
            "model",
            "train",
            "--snapshot",
            str(snap),
            "--train-frac",
            "0.9",
            "--valid-frac",
            "0.2",
            "--config",
            str(_config(tmp_path)),
        ],
    )
    assert result.exit_code == 2


def test_model_train_end_to_end(tmp_path: Path) -> None:
    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)
    result = runner.invoke(
        app,
        [
            "model",
            "train",
            "--snapshot",
            str(snap),
            "--registry",
            str(tmp_path / "registry"),
            "--config",
            str(_config(tmp_path)),
        ],
    )
    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["calibration"]["status"] == "uncalibrated"
    assert "profitab" in summary["disclaimer"]
    model_dir = Path(summary["artifact"]["model_dir"])
    assert (model_dir / "record.json").is_file()
    assert (model_dir / "artifact.json").is_file()
    assert summary["holdout_final"]["n"] >= 5
    assert summary["split"]["train_observable_max"] <= summary["split"]["train_cutoff"]


def test_model_train_by_symbol_discovery(tmp_path: Path) -> None:
    storage = tmp_path / "storage"
    make_oscillating_snapshot_dir(storage, n=400, symbol="BTC/USDT", timeframe="1h")
    config = tmp_path / "sf-data.toml"
    config.write_text(
        f'[exchange]\nname = "binance"\n[data]\nstorage_path = "{storage.as_posix()}"\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "model",
            "train",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--registry",
            str(tmp_path / "registry"),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["snapshot"]["symbol"] == "BTC/USDT"


def test_model_list(tmp_path: Path) -> None:
    snap = make_oscillating_snapshot_dir(tmp_path / "storage", n=400)
    registry = tmp_path / "registry"
    trained = runner.invoke(
        app,
        [
            "model",
            "train",
            "--snapshot",
            str(snap),
            "--registry",
            str(registry),
            "--config",
            str(_config(tmp_path)),
        ],
    )
    assert trained.exit_code == 0, trained.output
    result = runner.invoke(
        app, ["model", "list", "--registry", str(registry), "--config", str(_config(tmp_path))]
    )
    assert result.exit_code == 0, result.output
    model_id = json.loads(trained.output)["artifact"]["model_id"]
    # Rich tables truncate long ids to terminal width; match on family + count.
    assert "Models (1)" in result.output
    assert "logistic" in result.output
    assert model_id[:8] in result.output


def test_model_train_single_class_fails_cleanly(tmp_path: Path) -> None:
    from tests.fixtures.model_train_fixtures import make_monotone_snapshot_dir

    snap = make_monotone_snapshot_dir(tmp_path / "storage", n=200)
    result = runner.invoke(
        app,
        [
            "model",
            "train",
            "--snapshot",
            str(snap),
            "--registry",
            str(tmp_path / "registry"),
            "--config",
            str(_config(tmp_path)),
        ],
    )
    assert result.exit_code != 0
    assert "single-class" in result.output.lower()


