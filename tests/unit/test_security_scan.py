"""Unit tests for scripts.security_scan secret scanner."""

from __future__ import annotations

import io
from pathlib import Path

from scripts.security_scan import (
    SECRET_PATTERNS,
    main,
    scan_file,
    scan_files,
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_secret_detection_sk_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "secrets.env", "sk-live-abc1234567890\n")
    assert scan_file(path) == [1]


def test_secret_detection_bearer(tmp_path: Path) -> None:
    path = _write(tmp_path, "secrets.env", "Bearer token123\n")
    assert scan_file(path) == [1]


def test_secret_detection_api_key(tmp_path: Path) -> None:
    path = _write(tmp_path, "secrets.env", "api_key=secret123\n")
    assert scan_file(path) == [1]


def test_placeholder_not_flagged(tmp_path: Path) -> None:
    path = _write(tmp_path, "config.toml", "META_MUSE_API_KEY=\n")
    assert scan_file(path) == []


def test_commented_example_not_flagged(tmp_path: Path) -> None:
    path = _write(tmp_path, "config.toml", "# OPENAI_API_KEY=sk-real-key\n")
    assert scan_file(path) == []


def test_test_fixture_not_flagged(tmp_path: Path) -> None:
    path = _write(tmp_path, "conftest.py", 'api_key_env = "sk-test-abc123"\n')
    assert scan_file(path) == []


def test_private_key_detection(tmp_path: Path) -> None:
    path = _write(tmp_path, "key.pem", "-----BEGIN RSA PRIVATE KEY-----\n")
    assert scan_file(path) == [1]


def test_no_secret_exposure_in_output(tmp_path: Path) -> None:
    path = _write(tmp_path, "secrets.env", "sk-live-abc1234567890\n")
    stream = io.StringIO()
    status = main(argv=[str(path)], stream=stream)
    output = stream.getvalue()
    assert status == 1
    assert "sk-live-abc1234567890" not in output


def test_clean_files_pass(tmp_path: Path) -> None:
    path = _write(tmp_path, "clean.py", "x = 1 + 2\n")
    stream = io.StringIO()
    status = main(argv=[str(path)], stream=stream)
    assert status == 0
    assert scan_files([path]) == {}
    assert SECRET_PATTERNS
    assert "No secrets detected." in stream.getvalue()


def test_gh_token_detection(tmp_path: Path) -> None:
    path = _write(tmp_path, "secrets.env", "ghp_abc1234567890abcdefghij\n")
    assert scan_file(path) == [1]


__all__: list[str] = []
