"""Config loading tests (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from crypto_engine.config.settings import Settings, load_settings


def test_load_default_config(default_config_path: Path) -> None:
    """Valid default.toml loads with expected sections."""
    assert default_config_path.is_file()
    settings = load_settings(default_config_path)
    assert settings.exchange.name == "binance"
    assert settings.data.symbols
    assert settings.data.timeframes
    assert settings.data.format == "parquet"


def test_missing_file_raises_actionable(tmp_path: Path) -> None:
    """Missing file raises FileNotFoundError with an actionable message."""
    missing = tmp_path / "nope.toml"
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_settings(missing)


def test_bad_type_raises_validation_error(tmp_path: Path) -> None:
    """Wrong types fail pydantic validation (passthrough)."""
    bad = tmp_path / "bad.toml"
    bad.write_text('[exchange]\nname = "binance"\ntimeout_ms = "not-a-number"\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(bad)


def test_from_toml_dict_roundtrip() -> None:
    """from_toml_dict validates a plain mapping."""
    settings = Settings.from_toml_dict(
        {"exchange": {"name": "kraken"}, "data": {"symbols": ["BTC/USDT"], "timeframes": ["1h"]}}
    )
    assert settings.exchange.name == "kraken"
    assert settings.data.symbols == ["BTC/USDT"]
