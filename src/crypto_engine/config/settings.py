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


class CalibrationConfig(BaseModel):
    """Phase 3 probability-calibration configuration (Track A owns semantics).

    Calibrated output estimates ``P(y == positive_class)`` on a
    chronologically-later split; see ``calibration/calibrators.py``.
    """

    method: Literal["sigmoid", "isotonic"] = "sigmoid"
    min_cal_samples: int = Field(default=20, ge=2)
    positive_class: int | None = None


class ValidationConfig(BaseModel):
    """Phase 3 walk-forward / holdout / cost validation configuration.

    Schema defined here so Track B never edits ``settings.py``; Track B
    owns the validation engine semantics in ``validation/``.
    """

    n_folds: int = Field(default=3, ge=1, le=10)
    train_window: int = Field(default=200, ge=1)
    val_window: int = Field(default=50, ge=1)
    embargo_bars: int = Field(default=24, ge=0)
    purge_overlaps: bool = True
    holdout_frac: float = Field(default=0.15, ge=0.0, le=0.4)
    min_fold_samples: int = Field(default=30, ge=1)
    max_bootstrap: int = Field(default=200, ge=1, le=1000)
    block_bootstrap_len: int = Field(default=20, ge=1)
    fee_bp: float = Field(default=10.0, ge=0.0)
    slippage_bp: float = Field(default=5.0, ge=0.0)
    random_state: int = 42


class AiProviderConfig(BaseModel):
    """Single AI provider instance (Phase 4 Track A; all disabled by default)."""

    provider_id: str = "openai"
    model_id: str = "gpt-4o-mini"
    enabled: bool = False
    timeout_s: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=2, ge=0, le=3)
    max_response_chars: int = Field(default=4000, gt=0)
    concurrency: int = Field(default=1, ge=1, le=1)
    base_url: str | None = None
    api_key_env: str | None = None
    capabilities: dict[str, bool] = Field(default_factory=dict)


class AiBudgetConfig(BaseModel):
    """In-memory spend caps for AI calls (None disables that cap)."""

    max_requests: int | None = Field(default=10, ge=1)
    max_chars: int | None = Field(default=200000, ge=1)


class AiConfig(BaseModel):
    """Phase 4 AI interface configuration (Track A owns semantics)."""

    default_provider: str = "none"
    fallback_order: list[str] = Field(default_factory=list)
    providers: list[AiProviderConfig] = Field(default_factory=list)
    budgets: AiBudgetConfig = Field(default_factory=AiBudgetConfig)
    cache_ttl_s: int = Field(default=0, ge=0)
    log_prompts: bool = False


class SignalConfig(BaseModel):
    """Phase 5 signal-generation policy defaults (Track A).

    4 GB laptop: one symbol/timeframe, single-process; thresholds and
    edge gates keep output conservative (NO_TRADE is expected).
    """

    horizon_bars: int = Field(default=24, ge=1)
    t_long: float = Field(default=0.6, gt=0.5, lt=1.0)
    t_short: float = Field(default=0.4, gt=0.0, lt=0.5)
    threshold_version: str = "v1"
    calibration_required: bool = True
    require_edge: bool = True
    edge_margin_bp: float = Field(default=0.0, ge=0.0)
    enabled_directions: list[str] = Field(
        default_factory=lambda: ["LONG", "SHORT", "NEUTRAL", "NO_TRADE"]
    )
    max_signal_age_min: int = Field(default=60, ge=1)
    staleness_limit_min: int = Field(default=90, ge=1)
    require_ai_veto: bool = False
    history_path: str = "data/signals"
    max_rows_signal: int = Field(default=500, ge=1)


class RiskConfig(BaseModel):
    """Phase 5 deterministic risk-gate bounds (Track B enforces; Track A declares)."""

    max_trade_risk_pct: float = Field(default=1.0, gt=0.0)
    max_position_pct: float = Field(default=10.0, gt=0.0)
    max_drawdown_pct: float = Field(default=5.0, gt=0.0)
    fail_closed: bool = True
    enabled: bool = True


class BacktestConfig(BaseModel):
    """Phase 6 event-safe backtest bounds (Track A; single symbol/timeframe).

    4 GB laptop: single-process, one symbol/timeframe, bounded bars.
    Costs come from a Phase 5 ``CostEstimate`` passed to the engine
    (never duplicated here).
    """

    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    starting_equity: float = Field(default=10000.0, gt=0)
    position_pct: float = Field(default=10.0, gt=0)
    max_holding_bars: int = Field(default=24, ge=0)
    require_levels: bool = False
    max_bars: int = Field(default=5000, ge=2)


class PaperConfig(BaseModel):
    """Phase 6 advisory-only paper trading bounds (Track B owns engine).

    Declared here so Track B never edits ``settings.py``; no live orders
    anywhere — paper state is advisory only.
    """

    account_path: str = "data/paper"
    poll_bars: int = Field(default=1, ge=1)
    max_pending_signals: int = Field(default=100, ge=1)
    fail_closed: bool = True


class ApiConfig(BaseModel):
    """Phase 7 read-only API + dashboard server bounds.

    Loopback-only by default; no trading, training, fetching, or mutation
    through HTTP. All list endpoints are bounded (page_size_max cap).
    """

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    allow_remote: bool = False
    cors_origins: list[str] = Field(default_factory=list)
    page_size_default: int = Field(default=50, ge=1, le=500)
    page_size_max: int = Field(default=500, ge=1, le=5000)
    ledger_scan_cap: int = Field(default=5000, ge=100)
    backtest_dir: str = "data/backtests"
    experiments_path: str = "reports/experiments.jsonl"
    holdout_ledger_path: str = "reports/holdout.json"


class Settings(BaseModel):
    """Root validated settings object."""

    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    registry: RegistryConfig = Field(default_factory=RegistryConfig)
    calibration: CalibrationConfig = Field(default_factory=CalibrationConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    ai: AiConfig = Field(default_factory=AiConfig)
    signal: SignalConfig = Field(default_factory=SignalConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    paper: PaperConfig = Field(default_factory=PaperConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)

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
    "AiBudgetConfig",
    "AiConfig",
    "AiProviderConfig",
    "ApiConfig",
    "BacktestConfig",
    "CalibrationConfig",
    "DataConfig",
    "ExchangeConfig",
    "LoggingConfig",
    "ModelConfig",
    "PaperConfig",
    "RegistryConfig",
    "RiskConfig",
    "Settings",
    "SignalConfig",
    "ValidationConfig",
    "default_config_path",
    "load_settings",
]
