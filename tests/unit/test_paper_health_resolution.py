"""Regression tests for paper account path resolution and health checks."""

from __future__ import annotations

from pathlib import Path

from crypto_engine.api.repos import RepoRoots, read_paper
from crypto_engine.config.settings import PaperConfig, Settings
from crypto_engine.observability.health import HealthStatus, check_paper_account
from crypto_engine.paper_trading.state import ACCOUNT_FILENAME, init_account


def _settings_for_account_path(account_path: str | Path) -> Settings:
    return Settings(paper=PaperConfig(account_path=str(account_path)))


def test_check_paper_account_existing_valid_account_dir_config(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    account_file = paper_dir / ACCOUNT_FILENAME
    init_account(
        account_file,
        symbol="BTC/USDT",
        timeframe="1h",
        starting_equity=10000.0,
        cost={"fee_bp": 10.0, "spread_bp": 0.0, "slippage_bp": 5.0, "funding_bp": 0.0},
    )

    settings = _settings_for_account_path(paper_dir)
    status, message, details = check_paper_account(settings)

    assert status is HealthStatus.HEALTHY
    assert message == "paper account checksum verifies"
    assert details["verified"] is True
    assert Path(details["path"]) == account_file

    repo_payload = read_paper(RepoRoots(paper_path=account_file))
    assert repo_payload.get("available") is True
    assert Path(details["path"]) == RepoRoots(paper_path=account_file).paper_path


def test_check_paper_account_disabled_missing_account_is_not_applicable(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    settings = _settings_for_account_path(paper_dir)

    status, message, details = check_paper_account(settings)

    assert status is HealthStatus.HEALTHY
    assert "not applicable" in message
    assert details["applicable"] is False
    assert Path(details["path"]) == paper_dir / ACCOUNT_FILENAME


def test_check_paper_account_enabled_missing_account_is_unhealthy(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper"
    settings = Settings(
        paper=PaperConfig(account_path=str(paper_dir), enabled=True),
    )
    status, message, details = check_paper_account(settings)

    assert status is HealthStatus.UNHEALTHY
    assert message == "paper account file missing"
    assert details["applicable"] is True


def test_paper_path_resolution_consistent_between_health_and_repo(tmp_path: Path) -> None:
    paper_dir = tmp_path / "paper-root"
    paper_dir.mkdir()
    account_file = paper_dir / ACCOUNT_FILENAME
    init_account(
        account_file,
        symbol="BTC/USDT",
        timeframe="1h",
        starting_equity=10000.0,
        cost={"fee_bp": 10.0, "spread_bp": 0.0, "slippage_bp": 5.0, "funding_bp": 0.0},
    )

    # Default/configured form is a directory root; canonical account file is <root>/account.json.
    settings = _settings_for_account_path(paper_dir)
    status, _, details = check_paper_account(settings)
    canonical_file = paper_dir / ACCOUNT_FILENAME

    assert status is HealthStatus.HEALTHY
    assert Path(details["path"]) == canonical_file
    assert read_paper(RepoRoots(paper_path=canonical_file)).get("available") is True
    # Explicit file-path config must resolve to the same canonical file (no double-append).
    file_settings = _settings_for_account_path(account_file)
    f_status, _, f_details = check_paper_account(file_settings)
    assert f_status is HealthStatus.HEALTHY
    assert Path(f_details["path"]) == canonical_file
