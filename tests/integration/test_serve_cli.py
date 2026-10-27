"""`sf serve` CLI tests (Phase 7, offline, no open ports by default).

Covers: help output, non-loopback refusal, missing-extra guidance path,
occupied-port graceful failure. A real startup is verified manually by the
lead (subprocess + loopback GET + shutdown) and recorded in memory — the
suite avoids binding real servers.
"""

from __future__ import annotations

import socket
from pathlib import Path

from typer.testing import CliRunner

from crypto_engine.cli import app

runner = CliRunner()


def test_serve_help() -> None:
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0, result.output
    assert "--host" in result.output and "--port" in result.output
    assert "127.0.0.1" in result.output or "loopback" in result.output.lower()


def test_serve_refuses_non_loopback(tmp_path: Path) -> None:
    config = tmp_path / "sf.toml"
    config.write_text('[exchange]\nname = "binance"\n', encoding="utf-8")
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0", "--config", str(config)])
    assert result.exit_code != 0
    assert "loopback" in result.output.lower()


def test_serve_occupied_port_fails_gracefully(tmp_path: Path) -> None:
    config = tmp_path / "sf.toml"
    config.write_text('[exchange]\nname = "binance"\n', encoding="utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
        sock.listen(1)
        result = runner.invoke(
            app, ["serve", "--host", "127.0.0.1", "--port", str(port), "--config", str(config)]
        )
        assert result.exit_code != 0
        lowered = result.output.lower()
        # With the api extra: port-occupied error. Without it: missing-extra
        # guidance (importorskip keeps the rest of the suite green either way).
        assert "cannot bind" in lowered or "address" in lowered or "extra" in lowered
    finally:
        sock.close()


__all__: list[str] = []
