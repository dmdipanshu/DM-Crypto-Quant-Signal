"""Historical market-data adapter for accelerated validation.

Fetches historical OHLCV candles from public Binance Spot endpoints
for BTC/USDT 1h over an approximately 7-day window. This adapter is
used exclusively by the HISTORICAL_ACCELERATED_VALIDATION track and
must never be used to populate the prospective holdout dataset.

No API keys are required; only public market-data endpoints are used.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from crypto_engine.data.adapter import CcxtAdapter, ExchangeClient  # noqa: E402
from crypto_engine.data.types import FetchParams  # noqa: E402
from crypto_engine.data.validation import (  # noqa: E402
    timeframe_to_ms,
)

HISTORICAL_SCHEMA_VERSION = "historical_accelerated_v1"


class HistoricalDataAdapter:
    """Fetch historical OHLCV data from public Binance Spot endpoints.

    Uses ccxt fetchOHLCV with bounded pagination. All candles returned
    are fully closed (exchange server time confirms finalization).
    """

    def __init__(
        self,
        exchange_name: str = "binance",
        symbol: str = "BTC/USDT",
        timeframe: str = "1h",
        client: ExchangeClient | None = None,
    ) -> None:
        self.exchange_name = exchange_name
        self.symbol = symbol
        self.timeframe = timeframe
        self.adapter = CcxtAdapter(exchange_name=exchange_name, client=client)

    def fetch_historical(
        self,
        start_utc: datetime,
        end_utc: datetime,
        limit_per_page: int = 1000,
    ) -> dict[str, Any]:
        """Fetch historical candles for [start_utc, end_utc).

        Returns a dict with candles, metadata, and quality metrics.
        """
        since_ms = int(start_utc.timestamp() * 1000)
        until_ms = int(end_utc.timestamp() * 1000)
        interval_ms = timeframe_to_ms(self.timeframe)

        params = FetchParams(
            exchange=self.exchange_name,
            symbol=self.symbol,
            timeframe=self.timeframe,
            since=start_utc,
            until=end_utc,
            limit=limit_per_page,
        )

        result = self.adapter.fetch_range(params)

        candles = result.candles

        # Detect duplicates and conflicts
        dup_warning_count = 0
        conflict_count = 0
        seen: dict[str, Any] = {}
        for c in candles:
            key = f"{c.timestamp.isoformat()}"
            if key in seen:
                dup_warning_count += 1
                prev = seen[key]
                if (
                    abs(prev.open - c.open) > 1e-9
                    or abs(prev.high - c.high) > 1e-9
                    or abs(prev.low - c.low) > 1e-9
                    or abs(prev.close - c.close) > 1e-9
                    or abs(prev.volume - c.volume) > 1e-9
                ):
                    conflict_count += 1
            else:
                seen[key] = c

        # Compute dataset hash
        dataset_payload = [
            {
                "timestamp": c.timestamp.isoformat(),
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
            }
            for c in candles
        ]
        dataset_hash = hashlib.sha256(
            json.dumps(dataset_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()

        # Compute coverage
        expected_count = 0
        t = since_ms
        while t + interval_ms <= until_ms:
            expected_count += 1
            t += interval_ms

        coverage = len(candles) / expected_count if expected_count > 0 else 0.0

        return {
            "schema_version": HISTORICAL_SCHEMA_VERSION,
            "exchange": self.exchange_name,
            "market_type": "spot",
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "actual_since": result.actual_since.isoformat() if result.actual_since else None,
            "actual_until": result.actual_until.isoformat() if result.actual_until else None,
            "observation_count": len(candles),
            "expected_intervals": expected_count,
            "coverage_fraction": coverage,
            "duplicate_count": dup_warning_count,
            "conflicting_count": conflict_count,
            "missing_interval_count": len(result.missing_intervals),
            "raw_candles": candles,
            "dataset_hash": dataset_hash,
            "warnings": result.warnings,
            "source": self.exchange_name,
        }


__all__ = ["HISTORICAL_SCHEMA_VERSION", "HistoricalDataAdapter"]
