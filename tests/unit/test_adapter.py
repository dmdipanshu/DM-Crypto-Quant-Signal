"""Adapter tests with injected fake clients (offline, no network)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest

from crypto_engine.data.adapter import CcxtAdapter
from crypto_engine.data.errors import DataFetchError, UnsupportedEndpointError
from crypto_engine.data.types import FetchParams


def _transient_exc_type() -> type[BaseException]:
    try:
        import ccxt  # type: ignore[import-not-found]

        return ccxt.NetworkError
    except ImportError:
        return TimeoutError


class FakeClient:
    """Paginated fake: serves a fixed row list sliced by since/limit."""

    def __init__(self, rows: list[list[Any]]) -> None:
        self._rows = sorted(rows, key=lambda r: int(r[0]))
        self.calls: list[tuple[int | None, int | None]] = []

    @property
    def has_fetch_ohlcv(self) -> bool:
        return True

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        _ = (symbol, timeframe)
        self.calls.append((since, limit))
        rows = [r for r in self._rows if since is None or int(r[0]) >= since]
        return rows[: (limit or len(rows))]


class FakeFlaky(FakeClient):
    """Fails transiently ``failures`` times before serving rows."""

    def __init__(self, rows: list[list[Any]], failures: int = 1) -> None:
        super().__init__(rows)
        self.failures = failures
        self.attempts = 0

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise _transient_exc_type()("simulated transient outage")
        return super().fetch_ohlcv(symbol, timeframe, since, limit)


class FakeAlwaysDown:
    @property
    def has_fetch_ohlcv(self) -> bool:
        return True

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        raise _transient_exc_type()("persistent outage")


class FakeBadSymbol:
    @property
    def has_fetch_ohlcv(self) -> bool:
        return True

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        raise Exception(f"bad symbol {symbol}")


class FakeNoEndpoint:
    @property
    def has_fetch_ohlcv(self) -> bool:
        return False

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        raise AssertionError("should not be called")


def _params(**overrides: Any) -> FetchParams:
    base: dict[str, Any] = {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "limit": 4,
    }
    base.update(overrides)
    return FetchParams(**base)


def test_pagination_merges_pages(raw_rows: list[list[Any]]) -> None:
    """Multiple pages merge into one ordered result with tracked bounds."""
    adapter = CcxtAdapter(exchange_name="binance", max_retries=0, client=FakeClient(raw_rows))
    result = adapter.fetch_range(_params())
    assert result.row_count == len(raw_rows)
    assert result.actual_since is not None and result.actual_until is not None
    assert result.actual_since < result.actual_until
    assert result.source == "binance"
    assert len(FakeClient(raw_rows).calls) == 0  # sanity: fresh client unused
    assert len(adapter.client.calls) > 1  # type: ignore[union-attr]


def test_requested_vs_actual_tracking(raw_rows: list[list[Any]]) -> None:
    """Requested window is preserved alongside actual observed bounds."""
    since = datetime.fromtimestamp(raw_rows[0][0] / 1000.0, tz=UTC)
    until = datetime.fromtimestamp(raw_rows[-1][0] / 1000.0, tz=UTC)
    adapter = CcxtAdapter(exchange_name="binance", max_retries=0, client=FakeClient(raw_rows))
    result = adapter.fetch_range(_params(since=since, until=until))
    assert result.requested_since == since
    assert result.requested_until == until
    assert result.actual_since == since
    assert result.actual_until == until


def test_retry_on_transient(raw_rows: list[list[Any]]) -> None:
    """One transient failure is retried and then succeeds."""
    flaky = FakeFlaky(raw_rows, failures=1)
    adapter = CcxtAdapter(exchange_name="binance", max_retries=3, client=flaky)
    result = adapter.fetch_range(_params())
    assert result.row_count == len(raw_rows)
    assert flaky.attempts >= 2


def test_persistent_timeout_maps_to_data_fetch_error() -> None:
    """Exhausted retries surface as DataFetchError."""
    adapter = CcxtAdapter(exchange_name="binance", max_retries=1, client=FakeAlwaysDown())
    with pytest.raises(DataFetchError):
        adapter.fetch_range(_params())


def test_unsupported_symbol_raises() -> None:
    """Exchange bad-symbol errors map to UnsupportedEndpointError."""
    adapter = CcxtAdapter(exchange_name="binance", max_retries=0, client=FakeBadSymbol())
    with pytest.raises(UnsupportedEndpointError):
        adapter.fetch_range(_params(symbol="NOPE/USDT"))


def test_unsupported_endpoint_raises() -> None:
    """Exchanges without fetchOHLCV raise UnsupportedEndpointError immediately."""
    adapter = CcxtAdapter(exchange_name="binance", max_retries=0, client=FakeNoEndpoint())
    with pytest.raises(UnsupportedEndpointError):
        adapter.fetch_range(_params())


def test_stale_warning_for_old_data() -> None:
    """Candles far in the past produce a stale-data warning."""
    old = 1_500_000_000_000  # 2017
    rows = [[old + i * 3_600_000, 1.0, 2.0, 0.5, 1.5, 1.0] for i in range(3)]
    adapter = CcxtAdapter(exchange_name="binance", max_retries=0, client=FakeClient(rows))
    result = adapter.fetch_range(_params())
    assert result.row_count == 3
    assert any("stale" in w.lower() for w in result.warnings)


def test_empty_result_warns() -> None:
    """Zero rows yields a warning rather than a silent empty success."""
    adapter = CcxtAdapter(exchange_name="binance", max_retries=0, client=FakeClient([]))
    result = adapter.fetch_range(_params())
    assert result.row_count == 0
    assert any("no rows" in w.lower() for w in result.warnings)


class FakeRateLimitThenOk:
    """Raises ccxt.RateLimitExceeded once, then succeeds."""

    def __init__(self, rows: list[list[Any]]) -> None:
        self._rows = sorted(rows, key=lambda r: int(r[0]))
        self.attempts = 0

    @property
    def has_fetch_ohlcv(self) -> bool:
        return True

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        _ = (symbol, timeframe)
        self.attempts += 1
        if self.attempts == 1:
            import ccxt

            raise ccxt.RateLimitExceeded("rate limited")
        return [r for r in self._rows if since is None or int(r[0]) >= since][: (limit or len(self._rows))]


class FakeAlwaysDownTransient:
    """Always raises a transient error."""

    def __init__(self) -> None:
        self.attempts = 0

    @property
    def has_fetch_ohlcv(self) -> bool:
        return True

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        _ = (symbol, timeframe)
        self.attempts += 1
        raise _transient_exc_type()("persistent outage")


def test_max_duration_param_default() -> None:
    """Default max_duration_ms is 60000 (1 minute)."""
    adapter = CcxtAdapter(exchange_name="binance", max_retries=0, client=FakeAlwaysDownTransient())
    assert adapter.max_duration_ms == 60000


def test_max_duration_bounds_retries(raw_rows: list[list[Any]]) -> None:
    """Duration bound stops retries well before attempt bound is reached."""
    client = FakeAlwaysDownTransient()
    adapter = CcxtAdapter(
        exchange_name="binance", max_retries=100, max_duration_ms=1000, client=client
    )
    start = time.perf_counter()
    with pytest.raises(DataFetchError):
        adapter.fetch_range(_params())
    elapsed = time.perf_counter() - start
    assert elapsed < 10.0, f"duration bound did not kick in: {elapsed:.2f}s elapsed"


def test_rate_limit_exceeded_is_retried(raw_rows: list[list[Any]]) -> None:
    """ccxt.RateLimitExceeded is treated as transient and retried."""
    pytest.importorskip("ccxt")
    client = FakeRateLimitThenOk(raw_rows)
    adapter = CcxtAdapter(exchange_name="binance", max_retries=3, client=client)
    result = adapter.fetch_range(_params())
    assert result.row_count == len(raw_rows)
    assert client.attempts >= 2


def test_combined_stop_attempts_wins_first(raw_rows: list[list[Any]]) -> None:
    """Attempt bound wins over duration bound when retries exhaust quickly."""
    client = FakeAlwaysDownTransient()
    adapter = CcxtAdapter(
        exchange_name="binance", max_retries=1, max_duration_ms=600000, client=client
    )
    with pytest.raises(DataFetchError):
        adapter.fetch_range(_params())
    assert client.attempts == 2, f"expected 2 attempts (1 initial + 1 retry), got {client.attempts}"


