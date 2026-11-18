"""Exchange adapter with bounded pagination and retries (Phase 1)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential,
)

from crypto_engine.data.errors import (
    DataFetchError,
    SignalForgeDataError,
    UnsupportedEndpointError,
)
from crypto_engine.data.types import Candle, FetchParams, FetchResult
from crypto_engine.data.validation import (
    detect_duplicates,
    find_missing_intervals,
    normalize_candles,
    timeframe_to_ms,
    timeframe_to_timedelta,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class ExchangeClient(Protocol):
    """Minimal ccxt-compatible surface used by :class:`CcxtAdapter`."""

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        """Fetch one page of OHLCV rows."""
        ...

    @property
    def has_fetch_ohlcv(self) -> bool:
        """Whether the exchange supports fetchOHLCV."""
        ...


def _transient_errors() -> tuple[type[BaseException], ...]:
    """Best-effort transient ccxt errors; fall back to generic Exception."""
    try:
        import ccxt  # type: ignore[import-not-found]

        return (
            ccxt.NetworkError,
            ccxt.ExchangeNotAvailable,
            ccxt.RequestTimeout,
            ccxt.DDoSProtection,
            ccxt.RateLimitExceeded,
        )
    except ImportError:
        return (TimeoutError, ConnectionError)


def _map_error(exc: BaseException, symbol: str, exchange: str) -> SignalForgeDataError:
    name = type(exc).__name__
    lower = f"{name} {exc}".lower()
    unsupported_markers = (
        "bad symbol",
        "badsymbol",
        "symbol not found",
        "not supported",
        "unsupported",
    )
    if name in {"BadSymbol", "NotSupported"} or any(m in lower for m in unsupported_markers):
        return UnsupportedEndpointError(
            f"{exchange}: unsupported symbol/endpoint for {symbol}: {exc}"
        )
    return DataFetchError(f"{exchange}: fetch failed for {symbol}: {exc}")


def _to_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    ts = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return int(ts.astimezone(UTC).timestamp() * 1000)


class CcxtAdapter:
    """Paginated OHLCV fetcher over a ccxt (or compatible fake) client."""

    def __init__(
        self,
        exchange_name: str = "binance",
        timeout_ms: int = 10000,
        max_retries: int = 3,
        max_duration_ms: int = 60000,
        rate_limit: bool = True,
        client: ExchangeClient | None = None,
    ) -> None:
        self.exchange_name = exchange_name
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.max_duration_ms = max_duration_ms
        self.rate_limit = rate_limit
        self._client = client

    @property
    def client(self) -> ExchangeClient:
        """Lazily construct the ccxt client so unit tests can inject fakes."""
        if self._client is None:
            try:
                import ccxt  # type: ignore[import-not-found]
            except ImportError as exc:
                raise DataFetchError(
                    "ccxt is not installed; install Phase 1 deps with `uv sync`."
                ) from exc
            try:
                cls = getattr(ccxt, self.exchange_name)
            except AttributeError as exc:
                raise UnsupportedEndpointError(f"Unknown exchange {self.exchange_name!r}") from exc
            self._client = cls({"enableRateLimit": self.rate_limit, "timeout": self.timeout_ms})
        return self._client

    def _fetch_page(
        self, symbol: str, timeframe: str, since_ms: int | None, limit: int
    ) -> list[list[Any]]:
        transient = _transient_errors()

        @retry(
            stop=stop_after_attempt(max(1, self.max_retries + 1))
            | stop_after_delay(self.max_duration_ms / 1000.0),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(transient),
            reraise=True,
        )
        def _call() -> list[list[Any]]:
            return self.client.fetch_ohlcv(symbol, timeframe, since_ms, limit)  # type: ignore[union-attr]

        return _call()

    def fetch_range(self, params: FetchParams) -> FetchResult:
        """Fetch the requested window with bounded pagination (no silent fills)."""
        client = self.client
        try:
            has = client.has_fetch_ohlcv  # type: ignore[union-attr]
            has_flag = has() if callable(has) else bool(has)
        except Exception:
            has_flag = True
        if not has_flag:
            raise UnsupportedEndpointError(f"{self.exchange_name}: fetchOHLCV not supported")

        since_ms = _to_ms(params.since)
        until_ms = _to_ms(params.until)
        interval_ms = timeframe_to_ms(params.timeframe)
        limit = params.limit

        raw_rows: list[list[Any]] = []
        cursor = since_ms
        max_pages = 1000
        try:
            for _ in range(max_pages):
                page = self._fetch_page(params.symbol, params.timeframe, cursor, limit)
                if not page:
                    break
                raw_rows.extend(page)
                last_ms = int(float(page[-1][0])) if not isinstance(page[-1], dict) else None
                if last_ms is None:
                    # Dict rows: derive cursor from normalized last timestamp.
                    last_candle = normalize_candles([page[-1]])[0]
                    last_ms = int(last_candle.timestamp.timestamp() * 1000)
                cursor = last_ms + interval_ms
                if until_ms is not None and cursor >= until_ms:
                    break
                if len(page) < limit:
                    break
            else:
                logger.warning("fetch pagination hit max_pages bound (%d)", max_pages)
        except SignalForgeDataError:
            raise
        except Exception as exc:
            transient = _transient_errors()
            if isinstance(exc, transient):
                raise DataFetchError(
                    f"{self.exchange_name}: transient failure exhausted: {exc}"
                ) from exc
            raise _map_error(exc, params.symbol, self.exchange_name) from exc

        # Filter rows beyond `until` (exchanges may overshoot the last page).
        if until_ms is not None:
            filtered: list[list[Any]] = []
            for row in raw_rows:
                ms = (
                    row[0]
                    if isinstance(row, (list, tuple))
                    else row.get("timestamp", row.get("ms"))
                )
                try:
                    ms_int = int(float(ms))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    filtered.append(row)
                    continue
                if ms_int <= until_ms:
                    filtered.append(row)
            raw_rows = filtered

        candles: list[Candle] = normalize_candles(raw_rows) if raw_rows else []
        # Defensive dedupe (normalize sorts; keep first occurrence).
        unique: dict[datetime, Candle] = {}
        for candle in candles:
            unique.setdefault(candle.timestamp, candle)
        candles = sorted(unique.values(), key=lambda c: c.timestamp)

        warnings: list[str] = []
        dupes = detect_duplicates(candles)
        if dupes:
            warnings.append(f"{len(dupes)} duplicate timestamp(s) deduplicated")
        missing = find_missing_intervals(candles, params.timeframe)
        if missing:
            warnings.append(f"{len(missing)} missing interval(s) detected (no fill applied)")
        actual_since = candles[0].timestamp if candles else None
        actual_until = candles[-1].timestamp if candles else None
        if not candles:
            warnings.append("no rows returned for requested window")
        else:
            interval = timeframe_to_timedelta(params.timeframe)
            now = datetime.now(UTC)
            if actual_until is not None and actual_until < now - 2 * interval:
                warnings.append(f"stale data: last candle {actual_until.isoformat()}")

        return FetchResult(
            candles=candles,
            requested_since=params.since,
            requested_until=params.until,
            actual_since=actual_since,
            actual_until=actual_until,
            row_count=len(candles),
            missing_intervals=missing,
            warnings=warnings,
            source=self.exchange_name,
        )


__all__ = ["CcxtAdapter", "ExchangeClient"]
