"""Validated application settings (Phase 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class ExchangeConfig(BaseModel):
    """Exchange adapter configuration (public market data only)."""

    name: str = "binance"
    timeout_ms: int = Field(default=10000, gt=0)
    max_retries: int = Field(default=3, ge=0)
    rate_limit: bool = True


class DataConfig(BaseModel):
    """Dataset scope and storage configuration."""

    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT"])
    timeframes: list[str] = Field(default_factory=lambda: ["1h"])
    storage_path: str = "data"
    format: Literal["parquet"] = "parquet"


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = "INFO"


class ModelConfig(BaseModel):
    """Phase 2 model training bounds (4 GB laptop: single-process)."""

    default_family: str = "logistic"
    random_state: int = 42
    max_rows_train: int | None = Field(default=2000, ge=1)
    chunk_rows: int = Field(default=5000, gt=0)
    n_jobs: int = Field(default=1, ge=1, le=1)


class RegistryConfig(BaseModel):
    """Local model registry storage configuration."""

    path: str = "models/artifacts"
    allow_overwrite: bool = False


class Settings(BaseModel):
    """Root validated settings object."""

    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    registry: RegistryConfig = Field(default_factory=RegistryConfig)

    @classmethod
    def from_toml_dict(cls, data: dict[str, Any]) -> Settings:
        """Build settings from a parsed TOML mapping."""
        return cls.model_validate(data)


def _discover_repo_root(start: Path) -> Path:
    """Walk up from ``start`` until a directory containing pyproject.toml is found."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise FileNotFoundError(
        "Could not locate repo root (no pyproject.toml found walking up from "
        f"{start}). Pass an explicit toml_path to load_settings()."
    )


def default_config_path() -> Path:
    """Return the default config path (config/default.toml under the repo root)."""
    root = _discover_repo_root(Path(__file__))
    return root / "config" / "default.toml"


def load_settings(toml_path: Path | None = None) -> Settings:
    """Load and validate settings from a TOML file.

    Args:
        toml_path: Explicit TOML path. Defaults to ``config/default.toml``
            discovered relative to the repo root.

    Raises:
        FileNotFoundError: If the TOML file does not exist, with an actionable message.
        ValidationError: If the TOML content fails pydantic validation (passthrough).
    """
    import tomllib

    path = Path(toml_path) if toml_path is not None else default_config_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Config file not found: {path}. "
            "Create config/default.toml (see config/default.toml in the repo) "
            "or pass --config <path> pointing at a valid TOML file."
        )
    with path.open("rb") as fh:
        raw: dict[str, Any] = tomllib.load(fh)
    return Settings.from_toml_dict(raw)


__all__ = [
    "DataConfig",
    "ExchangeConfig",
    "LoggingConfig",
    "ModelConfig",
    "RegistryConfig",
    "Settings",
    "default_config_path",
    "load_settings",
]
