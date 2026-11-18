"""Q5 prospective data collection configuration and policy.

All thresholds are predeclared and must NOT be tuned against collected
prospective outcomes. They define when a prospective holdout becomes
ELIGIBLE_FOR_EVALUATION (never automatically EVALUATED).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_COLLECTION_MANIFEST_PATH = Path("docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json")
DEFAULT_COLLECTION_HISTORY_PATH = Path("docs/validation/PROSPECTIVE_COLLECTION_HISTORY.jsonl")

DEFAULT_PROSPECTIVE_STORAGE_ROOT = Path("data/prospective")

COLLECTION_SCHEMA_VERSION = "prospective-collection-manifest.v1"
COLLECTION_STORAGE_FORMAT = "jsonl"

#: Default exchange and instrument scope (matches existing canonical config).
DEFAULT_EXCHANGE = "binance"
DEFAULT_MARKET_TYPE = "spot"
DEFAULT_SYMBOLS = ("BTC/USDT",)
DEFAULT_TIMEFRAMES = ("1h",)

#: Safety margin (seconds) before accepting a candle as finalized.
#: Binance 1h candles close at :00 UTC; we require :00 + safety margin.
DEFAULT_CANDLE_FINALIZATION_MARGIN_S = 300  # 5 minutes

#: Maximum ingestion latency (seconds) for a candle to be considered fresh.
DEFAULT_MAX_INGESTION_LATENCY_S = 900  # 15 minutes

#: Default collection policy thresholds (must be approved before use).
DEFAULT_MIN_OBSERVATION_COUNT = 168  # 1 week of hourly candles
DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S = 604800  # 7 days
DEFAULT_MIN_COVERAGE_FRACTION = 0.95  # 95% of expected intervals


@dataclass(frozen=True, slots=True)
class ProspectiveCollectionPolicy:
    """Predeclared prospective collection policy.

    These thresholds govern when the prospective holdout may transition
    from ACCUMULATING to ELIGIBLE_FOR_EVALUATION. They must not be
    optimized against collected outcomes.
    """

    min_observation_count: int = DEFAULT_MIN_OBSERVATION_COUNT
    min_elapsed_observation_period_s: int = DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S
    min_coverage_fraction: float = DEFAULT_MIN_COVERAGE_FRACTION
    candle_finalization_margin_s: int = DEFAULT_CANDLE_FINALIZATION_MARGIN_S
    max_ingestion_latency_s: int = DEFAULT_MAX_INGESTION_LATENCY_S
    exchange: str = DEFAULT_EXCHANGE
    market_type: str = DEFAULT_MARKET_TYPE
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES

    def __post_init__(self) -> None:
        if self.min_observation_count < 1:
            raise ValueError("min_observation_count must be >= 1")
        if not 0.0 < self.min_coverage_fraction <= 1.0:
            raise ValueError("min_coverage_fraction must be in (0, 1]")
        if self.candle_finalization_margin_s < 0:
            raise ValueError("candle_finalization_margin_s must be >= 0")
        if self.max_ingestion_latency_s < 0:
            raise ValueError("max_ingestion_latency_s must be >= 0")


def hash_collection_config(config: dict[str, Any]) -> str:
    """Deterministic sha256 over collection configuration.

    Uses stable key ordering (sort_keys=True), deterministic JSON serialization,
    and str() fallback for non-serializable values. All entry points must call
    this function on the output of ``canonical_collection_config()`` to produce
    identical hashes.
    """
    import hashlib
    import json

    payload = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_collection_config(
    *,
    exchange: str = DEFAULT_EXCHANGE,
    market_type: str = DEFAULT_MARKET_TYPE,
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
    policy: ProspectiveCollectionPolicy | None = None,
) -> dict[str, Any]:
    """Build the canonical collection configuration dict used for hashing.

    All Q5 entry points (runner, CLI collect, CLI collect-status, CLI
    validate-prospective, collector) must use this function to construct the
    configuration dictionary before hashing. This guarantees that every entry
    point produces the identical hash for the same logical configuration.

    The configuration stored in the immutable collection manifest
    (``docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json``) is authoritative
    for the existing collection epoch. This function must produce a dict whose
    hash matches ``collection_config_hash`` in that manifest.
    """
    if symbols is None:
        symbols = list(DEFAULT_SYMBOLS)
    if timeframes is None:
        timeframes = list(DEFAULT_TIMEFRAMES)
    if policy is None:
        policy = ProspectiveCollectionPolicy(
            exchange=exchange,
            market_type=market_type,
            symbols=tuple(symbols),
            timeframes=tuple(timeframes),
        )

    return {
        "exchange": exchange,
        "market_type": market_type,
        "symbols": list(symbols),
        "timeframes": list(timeframes),
        "policy": {
            "min_observation_count": policy.min_observation_count,
            "min_elapsed_observation_period_s": policy.min_elapsed_observation_period_s,
            "min_coverage_fraction": policy.min_coverage_fraction,
            "candle_finalization_margin_s": policy.candle_finalization_margin_s,
            "max_ingestion_latency_s": policy.max_ingestion_latency_s,
        },
    }


def load_collection_config() -> dict[str, Any]:
    """Load the canonical collection configuration for the existing epoch.

    Returns the configuration that matches the immutable collection manifest
    at ``docs/validation/PROSPECTIVE_COLLECTION_MANIFEST.json``. This is the
    single source of truth for all Q5 entry points. Using any other
    configuration source (e.g. settings.data.symbols) will produce a different
    hash and trigger CONFIG_MISMATCH.
    """
    return canonical_collection_config(
        exchange=DEFAULT_EXCHANGE,
        market_type=DEFAULT_MARKET_TYPE,
        symbols=list(DEFAULT_SYMBOLS),
        timeframes=list(DEFAULT_TIMEFRAMES),
        policy=ProspectiveCollectionPolicy(
            exchange=DEFAULT_EXCHANGE,
            market_type=DEFAULT_MARKET_TYPE,
            symbols=DEFAULT_SYMBOLS,
            timeframes=DEFAULT_TIMEFRAMES,
        ),
    )


__all__ = [
    "COLLECTION_SCHEMA_VERSION",
    "COLLECTION_STORAGE_FORMAT",
    "DEFAULT_CANDLE_FINALIZATION_MARGIN_S",
    "DEFAULT_COLLECTION_HISTORY_PATH",
    "DEFAULT_COLLECTION_MANIFEST_PATH",
    "DEFAULT_EXCHANGE",
    "DEFAULT_MARKET_TYPE",
    "DEFAULT_MAX_INGESTION_LATENCY_S",
    "DEFAULT_MIN_COVERAGE_FRACTION",
    "DEFAULT_MIN_ELAPSED_OBSERVATION_PERIOD_S",
    "DEFAULT_MIN_OBSERVATION_COUNT",
    "DEFAULT_PROSPECTIVE_STORAGE_ROOT",
    "DEFAULT_SYMBOLS",
    "DEFAULT_TIMEFRAMES",
    "ProspectiveCollectionPolicy",
    "canonical_collection_config",
    "hash_collection_config",
    "load_collection_config",
]
