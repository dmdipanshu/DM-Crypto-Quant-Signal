# Prospective Data Collection — Q5

Generated: 2026-11-18T15:12:23.648985+00:00

## Collection Architecture

- Exchange: Binance Spot (public market-data endpoints only)
- Market Type: spot
- Symbols: BTC/USDT
- Timeframe: 1h
- Collection Mode: forward-only, closed candles only
- No trading credentials, no private endpoints, no order placement

## Research Freeze Association

- Freeze ID: freeze-aaa4dd57057a
- Freeze Timestamp: 2026-11-12T19:11:47.293700+00:00
- Freeze Status: VALID

## Collection Manifest

- Collection ID: collect-52cefa0dda64
- Collection Started: 2026-11-13T06:15:46.915986+00:00
- Exchange: binance
- Market Type: spot
- Symbols: BTC/USDT
- Timeframes: 1h
- Collector Version: q5-v1
- Data Schema Version: ohlcv-v1

## First-Seen Methodology

- `first_seen_at_utc` is assigned on first successful observation
- Never reset during retries or duplicate ingestion
- Not derived from candle event timestamps or file modification times
- If provenance cannot be verified, the candle is rejected

## Timestamp Definitions

- Event timestamp: when the market event occurred (candle open/close)
- Exchange candle open time: Binance OHLCV open timestamp
- Exchange candle close time: open time + interval
- Collector observation time: when the collector observed the candle
- First-seen timestamp: first successful observation by the collector
- Ingestion timestamp: when the data was stored
- Research freeze timestamp: from RESEARCH_FREEZE_MANIFEST.json
- Collection epoch start: from PROSPECTIVE_COLLECTION_MANIFEST.json

## Collection Results

- Status: DATA_INVALID
- Collector Status: unknown
- Dataset Quality: unknown
- Observations Collected: 1 unique
- Expected Intervals: 128
- Coverage Fraction: 0.78%
- Gaps: 0
- Duplicates: 0
- Conflicts: 0
- Rejections: 128
- Errors: 0
- Evaluation Blocked: True

## Quality Reasons

- All quality checks passed.

## Data Storage

- Location: `data/prospective/binance/spot/BTC/USDT/1h/`
- Format: JSON Lines (append-only)
- Files: `raw_observations.jsonl`, `normalized_candles.jsonl`, `ingestion_ledger.jsonl`, `dataset_manifest.json`
- Tamper-evident hash chain on ingestion ledger
- Dataset hash: SHA-256 over normalized candles
- Raw payload hash: SHA-256 over raw observations

## Safety Restrictions

- Public Binance market-data access only
- Read-only collection
- No API trading credentials
- No private account endpoints
- No order placement
- No live trading
- No autonomous execution
- No paper promotion
- No automatic model promotion
- NO_TRADE remains valid
- Missing or invalid evidence fails closed

## Known Limitations

- No live Binance data collected in this run (dry-run/mocked mode)
- Prospective dataset starts as ACCUMULATING
- Evaluation requires explicit authorized action
- Q4 remains NO_STRATEGY_ELIGIBLE
