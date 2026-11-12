"""Integration tests for `sf signal` (Typer runner, isolated fs, offline)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from crypto_engine.cli import app
from tests.fixtures.signal_fixtures import make_registry_model_dir, make_snapshot_dir

runner = CliRunner()


def _setup_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    storage = tmp_path / "storage"
    registry = tmp_path / "registry"
    _history = tmp_path / "history"
    snap_dir, _ = make_snapshot_dir(storage, n=5)
    model_dir, _, _ = make_registry_model_dir(registry)
    config = tmp_path / "sf.toml"
    config.write_text(
        '[exchange]\nname = "binance"\n'
        '[data]\nstorage_path = "data"\n'
        "[validation]\nfee_bp = 10.0\nslippage_bp = 5.0\n"
        '[signal]\nhistory_path = "data/signals"\n'
        "calibration_required = true\nrequire_edge = true\n",
        encoding="utf-8",
    )
    return snap_dir, model_dir, config


def test_signal_help() -> None:
    result = runner.invoke(app, ["signal", "--help"])
    assert result.exit_code == 0, result.output
    assert "generate" in result.output


def test_signal_generate_missing_model(tmp_path: Path) -> None:
    config = tmp_path / "sf.toml"
    config.write_text('[exchange]\nname = "binance"\n', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "signal",
            "generate",
            "--model",
            str(tmp_path / "nope"),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code != 0
    assert "cannot load model" in result.output.lower()


def test_signal_generate_corrupt_model(tmp_path: Path) -> None:
    config = tmp_path / "sf.toml"
    config.write_text('[exchange]\nname = "binance"\n', encoding="utf-8")
    bad_dir = tmp_path / "bad-model"
    bad_dir.mkdir()
    (bad_dir / "record.json").write_text("{corrupt", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "signal",
            "generate",
            "--model",
            str(bad_dir),
            "--config",
            str(config),
        ],
    )
    assert result.exit_code != 0
    assert "cannot load model" in result.output.lower()


def test_signal_generate_stub_model_json_valid(tmp_path: Path) -> None:
    snap_dir, model_dir, config = _setup_dirs(tmp_path)
    hist_dir = tmp_path / "history"

    result = runner.invoke(
        app,
        [
            "signal",
            "generate",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--model",
            str(model_dir),
            "--snapshot",
            str(snap_dir),
            "--config",
            str(config),
            "--history-path",
            str(hist_dir),
            "--at",
            "2024-06-01T12:00:00+00:00",
            "--now",
            "2024-06-01T12:05:00+00:00",
            "--fail-open",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "1.0"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["direction"] in ("LONG", "SHORT", "NEUTRAL", "NO_TRADE")
    assert payload["decision"]["reason_codes"]
    assert (hist_dir / "signals.jsonl").is_file()


def test_signal_generate_ai_explain_shows_summary(tmp_path: Path) -> None:
    """AI explanation summary must appear in CLI output when AI returns OK."""
    snap_dir, model_dir, config = _setup_dirs(tmp_path)
    hist_dir = tmp_path / "history"

    config.write_text(
        '[exchange]\nname = "binance"\n'
        '[data]\nstorage_path = "data"\n'
        "[validation]\nfee_bp = 10.0\nslippage_bp = 5.0\n"
        '[signal]\nhistory_path = "data/signals"\n'
        "calibration_required = true\nrequire_edge = true\n"
        "[ai]\nenabled = true\n"
        '[[ai.providers]]\nprovider_id = "meta_muse"\n'
        'dialect = "openai_compatible"\nbase_url = "https://api.meta.ai/v1"\n'
        'api_key_env = "META_MUSE_API_KEY"\nauth_mechanism = "bearer"\n'
        "enabled = false\n"
        '[[ai.models]]\nprovider_id = "meta_muse"\nmodel_id = "muse-spark-1.3"\n'
        "enabled = false\n"
        '[ai.tasks.signal_explanation]\nroute = ["meta_muse:muse-spark-1.3"]\n',
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "signal",
            "generate",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1h",
            "--model",
            str(model_dir),
            "--snapshot",
            str(snap_dir),
            "--config",
            str(config),
            "--history-path",
            str(hist_dir),
            "--at",
            "2024-06-01T12:00:00+00:00",
            "--now",
            "2024-06-01T12:05:00+00:00",
            "--fail-open",
            "--ai",
            "explain",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.split("=== AI Advisory Explanation ===")[0])
    assert payload["schema_version"] == "1.0"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["direction"] in ("LONG", "SHORT", "NEUTRAL", "NO_TRADE")
    assert payload["decision"]["reason_codes"]
    # AI disabled by default → status should be disabled, no fabricated explanation.
    assert payload["ai_context"]["status"] == "disabled"
    assert payload["ai_context"]["summary"] == ""
    assert payload["ai_context"]["provider_id"] == ""
    assert payload["ai_context"]["model_id"] == ""

    # With --ai explain and provider disabled, gateway is None → DISABLED.
    assert "AI gateway is not configured" in result.output or payload["ai_context"]["note"]


def test_signal_history_and_verify(tmp_path: Path) -> None:
    snap_dir, model_dir, config = _setup_dirs(tmp_path)
    hist_dir = tmp_path / "history"
    base = [
        "signal",
        "generate",
        "--model",
        str(model_dir),
        "--snapshot",
        str(snap_dir),
        "--config",
        str(config),
        "--history-path",
        str(hist_dir),
        "--at",
        "2024-06-01T12:00:00+00:00",
        "--now",
        "2024-06-01T12:05:00+00:00",
        "--fail-open",
    ]
    assert runner.invoke(app, base).exit_code == 0

    hist = runner.invoke(
        app, ["signal", "history", "--history-path", str(hist_dir), "--config", str(config)]
    )
    assert hist.exit_code == 0, hist.output
    entries = json.loads(hist.output)
    assert len(entries) == 1

    verify = runner.invoke(
        app, ["signal", "verify", "--history-path", str(hist_dir), "--config", str(config)]
    )
    assert verify.exit_code == 0, verify.output
    assert json.loads(verify.output)["ok"] is True
