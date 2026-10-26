"""CLI integration tests (offline; `data fetch` is never invoked)."""

from __future__ import annotations

from typer.testing import CliRunner

from crypto_engine.cli import app

runner = CliRunner()


def test_health_exit_zero() -> None:
    """`sf health` exits 0."""
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output


def test_help_exit_zero() -> None:
    """`sf --help` exits 0."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output


def test_config_show_exit_zero() -> None:
    """`sf config-show` exits 0 and prints exchange config."""
    result = runner.invoke(app, ["config-show"])
    assert result.exit_code == 0, result.output
    assert "binance" in result.output


def test_data_help() -> None:
    """`sf data --help` lists the data subcommands."""
    result = runner.invoke(app, ["data", "--help"])
    assert result.exit_code == 0, result.output
    assert "status" in result.output


def test_data_status_empty_storage() -> None:
    """`sf data status` on empty storage reports zero without crashing."""
    result = runner.invoke(app, ["data", "status"])
    assert result.exit_code == 0, result.output
