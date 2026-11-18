"""Stage 5 CLI integration tests (`sf ai ...`) - offline and mock by default."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from crypto_engine.cli import app
from tests.fixtures.signal_fixtures import make_registry_model_dir, make_snapshot_dir

runner = CliRunner()

MOCK_KEY_ENV = "SF_STAGE5_TEST_KEY"


def _write_mock_config(
    tmp_path: Path,
    *,
    base_url: str | None = "https://ai.example.test/v1",
    telemetry_path: Path | str | None = None,
) -> Path:
    ai_lines = [
        "[ai]",
        "enabled = true",
    ]
    if telemetry_path is not None:
        p_str = Path(telemetry_path).as_posix()
        ai_lines.append(f'telemetry_path = "{p_str}"')
    provider_lines = [
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
        *ai_lines,
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


def test_ai_status_enabled_and_valid() -> None:
    result = runner.invoke(app, ["ai", "status"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["enabled"] is True
    assert payload["valid"] is True
    assert payload["providers"] == 1
    assert payload["max_concurrent_requests"] == 1


def test_ai_config_check_default_is_ok() -> None:
    result = runner.invoke(app, ["ai", "config-check"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"ok": True, "errors": []}


def test_ai_metrics_shows_provider_id_from_telemetry(tmp_path: Path) -> None:
    """CLI ai metrics shows provider_id from telemetry records, not 'n/a'."""
    from crypto_engine.ai.telemetry import TelemetryRecord, TelemetryStore

    # 1. Create a temporary telemetry JSONL file
    telemetry_file = tmp_path / "data" / "ai_telemetry" / "events.jsonl"
    telemetry_file.parent.mkdir(parents=True, exist_ok=True)

    # 2. Write a valid successful AI telemetry event with provider_id meta_muse,
    #    model_id muse-spark-1.3, status ok, and a realistic nonzero latency
    record = TelemetryRecord(
        timestamp="2026-01-01T00:00:00+00:00",
        request_id="r1",
        provider_id="meta_muse",
        model_id="muse-spark-1.3",
        task="signal_explanation",
        ai_status="ok",
        latency_ms=250,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        reasoning_tokens=None,
        finish_reason="stop",
        retry_count=0,
        error_classification=None,
        explanation_available=True,
    )
    store = TelemetryStore(telemetry_file)
    store.record(record)
    assert telemetry_file.is_file(), "Temporary telemetry file was not created"

    # 3. Create a temporary TOML config pointing to that telemetry file
    config = _write_mock_config(tmp_path, telemetry_path=telemetry_file)

    # 4. Invoke CLI command passing the temporary config through --config
    result = runner.invoke(app, ["ai", "metrics", "--config", str(config)])
    assert result.exit_code == 0, result.output

    # 5. Assert that the CLI displays meta_muse
    assert "meta_muse" in result.output
    provider_lines = [line for line in result.output.splitlines() if "Provider" in line]
    assert len(provider_lines) == 1
    assert "meta_muse" in provider_lines[0]
    assert "n/a" not in provider_lines[0]

    # 6. Assert the metrics reflect the inserted event
    sample_lines = [line for line in result.output.splitlines() if "Sample size" in line]
    assert len(sample_lines) == 1
    assert "1" in sample_lines[0]
    total_lines = [line for line in result.output.splitlines() if "Total requests" in line]
    assert len(total_lines) == 1
    assert "1" in total_lines[0]
    assert "250.0" in result.output or "250" in result.output


def test_ai_metrics_shows_n_a_when_no_providers(tmp_path: Path) -> None:
    """CLI ai metrics shows 'n/a' when no telemetry records exist."""
    empty_telemetry = tmp_path / "empty_telemetry" / "events.jsonl"
    config = _write_mock_config(tmp_path, telemetry_path=empty_telemetry)
    result = runner.invoke(app, ["ai", "metrics", "--config", str(config)])
    assert result.exit_code == 0, result.output
    provider_lines = [line for line in result.output.splitlines() if "Provider" in line]
    assert len(provider_lines) == 1
    assert "n/a" in provider_lines[0]
    sample_lines = [line for line in result.output.splitlines() if "Sample size" in line]
    assert len(sample_lines) == 1
    assert "0" in sample_lines[0]


def test_ai_metrics_custom_telemetry_path_respected(tmp_path: Path) -> None:
    """Configured telemetry_path is respected and isolated from other stores."""
    from crypto_engine.ai.telemetry import TelemetryRecord, TelemetryStore

    custom_telemetry = tmp_path / "custom_dir" / "custom_events.jsonl"
    store = TelemetryStore(custom_telemetry)
    store.record(
        TelemetryRecord(
            timestamp="2026-01-01T00:00:00+00:00",
            request_id="custom-1",
            provider_id="meta_muse",
            model_id="muse-spark-1.3",
            task="signal_explanation",
            ai_status="ok",
            latency_ms=180,
            prompt_tokens=20,
            completion_tokens=10,
            total_tokens=30,
            reasoning_tokens=None,
            finish_reason="stop",
            retry_count=0,
            error_classification=None,
            explanation_available=True,
        )
    )
    config = _write_mock_config(tmp_path, telemetry_path=custom_telemetry)
    result = runner.invoke(app, ["ai", "metrics", "--config", str(config)])
    assert result.exit_code == 0, result.output
    assert "meta_muse" in result.output
    assert "180.0" in result.output or "180" in result.output


def test_ai_metrics_default_telemetry_path_works() -> None:
    """Invoking ai metrics without --config executes cleanly on the default store."""
    result = runner.invoke(app, ["ai", "metrics"])
    assert result.exit_code == 0, result.output
    assert "AI Observability Metrics" in result.output


def test_ai_metrics_multiple_providers_shows_mixed(tmp_path: Path) -> None:
    """When multiple providers exist in telemetry, CLI displays 'mixed'."""
    from crypto_engine.ai.telemetry import TelemetryRecord, TelemetryStore

    telemetry_file = tmp_path / "mixed_events.jsonl"
    store = TelemetryStore(telemetry_file)
    store.record(
        TelemetryRecord(
            timestamp="2026-01-01T00:00:00+00:00",
            request_id="m1",
            provider_id="meta_muse",
            model_id="muse-spark-1.3",
            task="signal_explanation",
            ai_status="ok",
            latency_ms=100,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            reasoning_tokens=None,
            finish_reason="stop",
            retry_count=0,
            error_classification=None,
            explanation_available=True,
        )
    )
    store.record(
        TelemetryRecord(
            timestamp="2026-01-01T00:01:00+00:00",
            request_id="m2",
            provider_id="custom",
            model_id="small-1",
            task="signal_explanation",
            ai_status="ok",
            latency_ms=150,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            reasoning_tokens=None,
            finish_reason="stop",
            retry_count=0,
            error_classification=None,
            explanation_available=True,
        )
    )
    config = _write_mock_config(tmp_path, telemetry_path=telemetry_file)
    result = runner.invoke(app, ["ai", "metrics", "--config", str(config)])
    assert result.exit_code == 0, result.output
    provider_lines = [line for line in result.output.splitlines() if "Provider" in line]
    assert len(provider_lines) == 1
    assert "mixed" in provider_lines[0]


def test_ai_metrics_windows_path_handling(tmp_path: Path) -> None:
    """Windows paths with backslashes in TOML are handled correctly."""
    from crypto_engine.ai.telemetry import TelemetryRecord, TelemetryStore

    telemetry_file = tmp_path / "win_events.jsonl"
    store = TelemetryStore(telemetry_file)
    store.record(
        TelemetryRecord(
            timestamp="2026-01-01T00:00:00+00:00",
            request_id="w1",
            provider_id="meta_muse",
            model_id="muse-spark-1.3",
            task="signal_explanation",
            ai_status="ok",
            latency_ms=320,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            reasoning_tokens=None,
            finish_reason="stop",
            retry_count=0,
            error_classification=None,
            explanation_available=True,
        )
    )
    win_path_str = str(telemetry_file).replace("/", "\\")
    escaped_win_path = win_path_str.replace("\\", "\\\\")
    config_file = tmp_path / "win_config.toml"
    config_content = (
        f'[ai]\nenabled = true\ntelemetry_path = "{escaped_win_path}"\n'
        '[[ai.providers]]\nprovider_id = "meta_muse"\ndisplay_name = "Meta Muse"\n'
        'dialect = "openai_compatible"\nenabled = true\napi_key_env = "MOCK_KEY"\n'
        'base_url = "https://ai.example.test/v1"\n'
        '[[ai.models]]\nprovider_id = "meta_muse"\nmodel_id = "muse-spark-1.3"\nenabled = true\n'
        '[ai.tasks.signal_explanation]\nroute = ["meta_muse:muse-spark-1.3"]\n'
    )
    config_file.write_text(config_content, encoding="utf-8")
    result = runner.invoke(app, ["ai", "metrics", "--config", str(config_file)])
    assert result.exit_code == 0, result.output
    assert "meta_muse" in result.output


def test_ai_metrics_empty_telemetry_unavailable_state(tmp_path: Path) -> None:
    """Empty and missing telemetry stores report unavailable states in repos adapter."""
    from crypto_engine.api.repos import RepoRoots, read_ai_metrics

    # Missing file
    missing_roots = RepoRoots(ai_telemetry_path=tmp_path / "does_not_exist.jsonl")
    missing_res = read_ai_metrics(missing_roots)
    assert missing_res["available"] is False
    assert missing_res["reason"] == "missing"

    # Empty file
    empty_file = tmp_path / "empty.jsonl"
    empty_file.write_text("", encoding="utf-8")
    empty_roots = RepoRoots(ai_telemetry_path=empty_file)
    empty_res = read_ai_metrics(empty_roots)
    assert empty_res["available"] is False
    assert empty_res["reason"] == "empty"


def test_cli_and_dashboard_use_consistent_telemetry(tmp_path: Path) -> None:
    """CLI and dashboard read-only repository agree on telemetry metrics."""
    from crypto_engine.ai.telemetry import TelemetryRecord, TelemetryStore
    from crypto_engine.api.repos import RepoRoots, read_ai_metrics

    telemetry_file = tmp_path / "shared_events.jsonl"
    store = TelemetryStore(telemetry_file)
    store.record(
        TelemetryRecord(
            timestamp="2026-01-01T00:00:00+00:00",
            request_id="sync-1",
            provider_id="meta_muse",
            model_id="muse-spark-1.3",
            task="signal_explanation",
            ai_status="ok",
            latency_ms=450,
            prompt_tokens=40,
            completion_tokens=20,
            total_tokens=60,
            reasoning_tokens=None,
            finish_reason="stop",
            retry_count=0,
            error_classification=None,
            explanation_available=True,
        )
    )

    # CLI read
    config = _write_mock_config(tmp_path, telemetry_path=telemetry_file)
    cli_res = runner.invoke(app, ["ai", "metrics", "--config", str(config)])
    assert cli_res.exit_code == 0, cli_res.output
    assert "meta_muse" in cli_res.output
    assert "450.0" in cli_res.output or "450" in cli_res.output
    assert "60" in cli_res.output

    # Dashboard / repository read
    roots = RepoRoots(ai_telemetry_path=telemetry_file)
    repo_res = read_ai_metrics(roots)
    assert repo_res["available"] is True
    assert repo_res["provider_id"] == "meta_muse"
    assert repo_res["total_requests"] == 1
    assert repo_res["successful_requests"] == 1
    assert repo_res["average_latency_ms"] == 450.0
    assert repo_res["total_tokens"] == 60


def test_ai_registry_commands_default_to_empty_lists() -> None:
    for command, key, expected in (
        (
            "providers",
            "providers",
            [
                {"provider_id": "meta_muse"},
            ],
        ),
        (
            "models",
            "models",
            [
                {"provider_id": "meta_muse", "model_id": "muse-spark-1.3"},
            ],
        ),
        (
            "routes",
            "routes",
            {
                "signal_explanation": [
                    {"provider_id": "meta_muse", "model_id": "muse-spark-1.3"},
                ]
            },
        ),
    ):
        result = runner.invoke(app, ["ai", command])
        assert result.exit_code == 0, result.output
        actual = json.loads(result.output)[key]
        # Check key fields match; don't assert full equality since display_name etc. may vary
        if key == "providers":
            assert len(actual) == 1
            provider_ids = {p["provider_id"] for p in actual}
            assert provider_ids == {p["provider_id"] for p in expected}
        elif key == "models":
            assert len(actual) == 1
            model_keys = {(m["provider_id"], m["model_id"]) for m in actual}
            assert model_keys == {(m["provider_id"], m["model_id"]) for m in expected}
        elif key == "routes":
            assert len(actual) == 1
            route = actual["signal_explanation"]
            assert len(route) == 1
            route_keys = {(r["provider_id"], r["model_id"]) for r in route}
            assert route_keys == {
                (r["provider_id"], r["model_id"]) for r in expected["signal_explanation"]
            }


def test_ai_test_without_live_flag_makes_no_call() -> None:
    """ai test without --live must not make any network call."""
    result = runner.invoke(app, ["ai", "test"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["live"] is False


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


def test_signal_generate_ai_explain_preserves_deterministic_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIGNALFORGE_ALLOW_FAIL_OPEN", "1")
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


