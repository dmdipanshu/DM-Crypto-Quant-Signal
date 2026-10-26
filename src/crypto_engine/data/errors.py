"""Market-data error hierarchy (Phase 1)."""


class SignalForgeDataError(RuntimeError):
    """Base class for market-data errors."""


class InvalidCandleError(SignalForgeDataError):
    """Raised when raw OHLCV rows fail validation."""


class DataFetchError(SignalForgeDataError):
    """Raised when an exchange fetch fails after bounded retries."""


class UnsupportedEndpointError(SignalForgeDataError):
    """Raised when the exchange does not support fetchOHLCV / the symbol."""


class CorruptSnapshotError(SignalForgeDataError):
    """Raised when a persisted snapshot fails integrity verification."""


__all__ = [
    "CorruptSnapshotError",
    "DataFetchError",
    "InvalidCandleError",
    "SignalForgeDataError",
    "UnsupportedEndpointError",
]
