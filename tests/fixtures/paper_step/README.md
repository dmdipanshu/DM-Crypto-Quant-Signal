# Paper Step Test Fixtures

**TEST FIXTURES ONLY — NOT PRODUCTION DATA**

This directory contains deterministic test fixture files for manual verification of
the `sf paper step` CLI command:

```powershell
uv run sf paper step --signal "<absolute-path-to-signal.json>" --bar "<absolute-path-to-bar.json>" --account "<account-path>"
```

## Files

| File | Purpose |
|------|---------|
| `signal.json` | Valid `SignalRecord` (LONG direction, BTC/USDT, 1h) conforming to `crypto_engine.signals.schema.SignalRecord`. Deterministic values; no real exchange data. |
| `bar.json` | Valid market data bar `{ts, open, high, low, close}` with `ts` after `signal.generated_at` (causality / next-bar rule). |

## Safety

- **Paper trading only.** No real exchange orders are ever placed.
- No Binance, ccxt, or external API calls are made by `sf paper step`.
- These fixtures do **not** modify Q5.2 collection data, thresholds, freeze IDs, or governance flags.
- Do **not** run against `data/paper/account.json` (the real persisted account) unless you explicitly intend to mutate live paper-trading state. For safe validation, copy the account file to a temporary path first:
  ```powershell
  Copy-Item "D:\DM-Crypto-Quant-Signal\data\paper\account.json" "C:\Users\you\Temp\paper_test_account.json"
  uv run sf paper step --signal "tests\fixtures\paper_step\signal.json" --bar "tests\fixtures\paper_step\bar.json" --account "C:\Users\you\Temp\paper_test_account.json"
  ```

## Schema conformance

The signal conforms to all `SignalRecord` validation rules:
- `signal_id`: 16 lowercase hex chars matching `^[0-9a-f]{16}$`
- `generated_at` / `valid_until`: timezone-aware ISO-8601, `valid_until > generated_at`
- `direction`: `LONG` with valid level geometry (`stop_loss < entry_ref < take_profit`)
- `prediction_horizon_bars >= 1`
- `data_quality`: `validated`
- `model_artifact_hash`: non-empty
- `cost_estimate`: non-null with `cost_bp_total` (15 bp) below `expected_edge_bp` (25 bp) so the edge gate passes
- `extra="forbid"`: no unknown fields

The bar conforms to `_parse_bar` validation:
- `ts`: timezone-aware ISO-8601
- `open` / `high` / `low` / `close`: all > 0 and finite
- `high >= low`; `open` and `close` within `[low, high]`
- `bar.ts > signal.generated_at` (causality)

## Determinism

All values are fixed constants. Running the same fixture repeatedly against a fresh
account produces identical results.
