# Binance Transient Failure Handling

**Adapter:** `src/crypto_engine/data/adapter.py` — class `CcxtAdapter`  
**Phase:** Q18 (NEW: `max_duration_ms=60000`)  
**Credential values:** None printed or stored in this document

## Overview

`CcxtAdapter` fetches paginated OHLCV candles from ccxt-compatible exchanges
(default `binance`) over public Spot endpoints only. Transient network and
exchange failures are retried with a bounded policy; permanent endpoint/symbol
errors are mapped to `UnsupportedEndpointError` and not retried. All other
failures are mapped to `DataFetchError`.

## Retried Transient Errors

The following ccxt exception types are treated as transient and are retried:

- `ccxt.NetworkError`
- `ccxt.ExchangeNotAvailable`
- `ccxt.RequestTimeout`
- `ccxt.DDoSProtection`
- `ccxt.RateLimitExceeded` (HTTP 429)

These are resolved in `_transient_errors()` with a fallback to
`(TimeoutError, ConnectionError)` if `ccxt` is not importable.

## Retry Configuration

Defined in `CcxtAdapter.__init__` and applied in `_fetch_page`:

| Setting | Value |
|---|---|
| `max_retries` | 3 (4 total attempts) |
| `max_duration_ms` | 60000 (60s total retry duration boundary) |
| Backoff | `wait_exponential(multiplier=1, min=1, max=8)` → 1s, 2s, 4s, 8s |
| Stop condition | `stop_after_attempt(max_retries + 1) \| stop_after_delay(max_duration_ms / 1000)` — whichever comes first |
| Reraise | `reraise=True` — the last exception propagates after retry exhaustion |

Implementation (`src/crypto_engine/data/adapter.py:131-141`):

```python
@retry(
    stop=stop_after_attempt(max(1, self.max_retries + 1))
    | stop_after_delay(self.max_duration_ms / 1000.0),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(transient),
    reraise=True,
)
def _call() -> list[list[Any]]:
    return self.client.fetch_ohlcv(symbol, timeframe, since_ms, limit)
```

The `stop_after_delay` boundary (`max_duration_ms=60000`) is NEW in Q18 and
caps total retry wall-clock time at 60s regardless of attempt count.

## Non-Transient Errors

`_map_error` (`src/crypto_engine/data/adapter.py:66-80`) maps exceptions:

- `BadSymbol`, `NotSupported` (or message markers "bad symbol", "symbol not found",
  "not supported", "unsupported") → `UnsupportedEndpointError` — **not retried**
- All other exceptions → `DataFetchError`

In `fetch_range`, `SignalForgeDataError` is re-raised as-is; other exceptions
that are transient instances are wrapped in `DataFetchError` ("transient failure
exhausted"); everything else goes through `_map_error`.

## Collector Behavior

`ProspectiveCollector._collect_symbol_timeframe`
(`src/crypto_engine/research/prospective/collector.py:395-412`) catches
`DataFetchError` and `SignalForgeDataError` from `adapter.fetch_range`, records
a rejection entry (`reason: "exchange fetch failed: ..."`), and returns empty
observations so collection continues for the remaining symbols/timeframes rather
than aborting the whole cycle.

## No exchangeInfo Code Path

The adapter only calls `fetch_ohlcv` (via `fetch_range`). There is no
`load_markets`/`exchangeInfo` call in `CcxtAdapter`, so exchange metadata
failures do not enter the retry path. Connectivity is validated implicitly by
the first `fetch_ohlcv` page and the `has_fetch_ohlcv` capability flag.

## Rate Limiting

Rate limiting is handled by ccxt's internal rate limiter
(`enableRateLimit=True` passed to the ccxt client constructor). There is no
`Retry-After` header parsing; `RateLimitExceeded` (HTTP 429) is retried through
the exponential backoff policy above.

## Error Mapping Summary

| Exception class | Retried? | Mapped to |
|---|---|---|
| `ccxt.NetworkError` | Yes | propagates after retry exhaustion |
| `ccxt.ExchangeNotAvailable` | Yes | propagates after retry exhaustion |
| `ccxt.RequestTimeout` | Yes | propagates after retry exhaustion |
| `ccxt.DDoSProtection` | Yes | propagates after retry exhaustion |
| `ccxt.RateLimitExceeded` (429) | Yes | propagates after retry exhaustion |
| `ccxt.BadSymbol` | No | `UnsupportedEndpointError` |
| `ccxt.NotSupported` | No | `UnsupportedEndpointError` |
| Other ccxt exceptions | No | `DataFetchError` |
