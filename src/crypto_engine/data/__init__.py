"""Market-data package."""

from crypto_engine.data.adapter import CcxtAdapter, ExchangeClient
from crypto_engine.data.errors import (
    CorruptSnapshotError,
    DataFetchError,
    InvalidCandleError,
    SignalForgeDataError,
    UnsupportedEndpointError,
)
from crypto_engine.data.types import Candle, FetchParams, FetchResult
from crypto_engine.data.validation import (
    detect_duplicates,
    find_missing_intervals,
    normalize_candles,
    to_frame,
)

__all__ = [
    "Candle",
    "CcxtAdapter",
    "CorruptSnapshotError",
    "DataFetchError",
    "ExchangeClient",
    "FetchParams",
    "FetchResult",
    "InvalidCandleError",
    "SignalForgeDataError",
    "UnsupportedEndpointError",
    "detect_duplicates",
    "find_missing_intervals",
    "normalize_candles",
    "to_frame",
]
