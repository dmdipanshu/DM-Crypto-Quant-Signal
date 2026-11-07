"""Stage 5 CLI integration tests (`sf ai ...`) - offline and mock by default."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from crypto_engine.cli import app
from tests.fixtures.signal_fixtures import make_registry_model_dir, make_snapshot_dir

runner = CliRunner()

MOCK_KEY_ENV = "SF_STAGE5_TEST_KEY"


def _write_mock_config(
    tmp_path: Path, *, base_url: str | None = "https://ai.example.test/v1"
) -> Path:
    provider_lines = [
        "[ai]",
        "enabled = true",
        "",
        "[[ai.providers]]",
        'provider_id = "custom"',
        'display_name = "Custom"',
        'dialect = "openai_compatible"',
        "enabled = true",
        f'api_key_env = "{MOCK_KEY_ENV}"',
        'structured_output = "json_schema"',
    ]
    if base_url is not None:
        provider_lines.append(f'base_url = "{base_url}"')
    lines = [
        *provider_lines,
        "",
        "[[ai.models]]",
        'provider_id = "custom"',
        'model_id = "small-1"',
        "enabled = true",
        'structured_output = "json_schema"',
        "",
        "[ai.tasks.signal_explanation]",
        'route = ["custom:small-1"]',
        "",
    ]
    path = tmp_path / "ai.toml"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_ai_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["ai", "--help"])
    assert result.exit_code == 0, result.output
    for command in ("status", "config-check", "providers", "models", "routes", "test"):
        assert command in result.output


def test_ai_status_default_is_disabled_and_valid() -> None:
    result = runner.invoke(app, ["ai", "status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["enabled"] is False
    assert payload["valid"] is True
    assert payload["providers"] == 0
    assert payload["max_concurrent_requests"] == 1


def test_ai_config_check_default_is_ok() -> None:
    result = runner.invoke(app, ["ai", "config-check"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"ok": True, "errors": []}


def test_ai_registry_commands_default_to_empty_lists() -> None:
    for command, key, expected in (
        ("providers", "providers", []),
        ("models", "models", []),
        ("routes", "routes", {}),
    ):
        result = runner.invoke(app, ["ai", command])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)[key] == expected


def test_ai_test_disabled_config_makes_no_call() -> None:
    result = runner.invoke(app, ["ai", "test"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "disabled"


def test_ai_test_offline_mock_routes_without_network(tmp_path: Path) -> None:
    config = _write_mock_config(tmp_path)
    result = runner.invoke(app, ["ai", "test", "--config", str(config)])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["live"] is False
    assert payload["route_id"] == "signal_explanation:custom:small-1"
    assert payload["advisory_only"] is True
    assert payload["provider_id"] == "custom"
    assert payload["model_id"] == "small-1"
    assert "offline-mock-placeholder" not in result.output


def test_ai_routes_and_providers_show_configuration(tmp_path: Path) -> None:
    config = _write_mock_config(tmp_path)
    routes = runner.invoke(app, ["ai", "routes", "--config", str(config)])
    assert routes.exit_code == 0, routes.output
    assert json.loads(routes.output)["routes"]["signal_explanation"] == [
        {"model_id": "small-1", "provider_id": "custom"}
    ]

    providers = runner.invoke(app, ["ai", "providers", "--config", str(config)])
    assert providers.exit_code == 0, providers.output
    payload = json.loads(providers.output)["providers"]
    assert payload[0]["api_key_env"] == MOCK_KEY_ENV
    assert payload[0]["base_url"] == "https://ai.example.test/v1"


def test_ai_commands_never_print_secret_values(tmp_path: Path, monkeypatch) -> None:
    secret = "super-secret-value-do-not-print"
    monkeypatch.setenv(MOCK_KEY_ENV, secret)
    config = _write_mock_config(tmp_path)
    for args in (
        ["ai", "status", "--config", str(config)],
        ["ai", "providers", "--config", str(config)],
        ["ai", "config-check", "--config", str(config)],
        ["ai", "test", "--config", str(config)],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert secret not in result.output


def test_ai_config_check_rejects_enabled_provider_without_base_url(tmp_path: Path) -> None:
    config = _write_mock_config(tmp_path, base_url=None)
    result = runner.invoke(app, ["ai", "config-check", "--config", str(config)])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "base_url" in payload["errors"][0]["message"]


def test_ai_test_live_requires_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(MOCK_KEY_ENV, raising=False)
    config = _write_mock_config(tmp_path)
    result = runner.invoke(app, ["ai", "test", "--live", "--config", str(config)])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "auth_failure"
    assert payload["live"] is True


def test_ai_test_unknown_task_exits_two(tmp_path: Path) -> None:
    config = _write_mock_config(tmp_path)
    result = runner.invoke(app, ["ai", "test", "--task", "not_a_task", "--config", str(config)])
    assert result.exit_code == 2, result.output


def _signal_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    storage = tmp_path / "storage"
    registry = tmp_path / "registry"
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


def test_signal_generate_ai_explain_preserves_deterministic_output(tmp_path: Path) -> None:
    snap_dir, model_dir, config = _signal_dirs(tmp_path)
    base = [
        "signal",
        "generate",
        "--model",
        str(model_dir),
        "--snapshot",
        str(snap_dir),
        "--config",
        str(config),
        "--at",
        "2024-06-01T12:00:00+00:00",
        "--now",
        "2024-06-01T12:05:00+00:00",
        "--fail-open",
    ]
    plain = runner.invoke(app, [*base, "--history-path", str(tmp_path / "h1")])
    explained = runner.invoke(
        app, [*base, "--history-path", str(tmp_path / "h2"), "--ai", "explain"]
    )
    assert plain.exit_code == 0, plain.output
    assert explained.exit_code == 0, explained.output

    before = json.loads(plain.output)
    after = json.loads(explained.output)
    for field in ("direction", "expected_edge_bp", "prediction", "decision", "risk_reward"):
        assert after[field] == before[field]
    assert after["ai_context"]["status"] == "disabled"
