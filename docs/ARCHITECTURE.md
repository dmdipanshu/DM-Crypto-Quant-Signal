# ARCHITECTURE.md — SignalForge (Phase 1)

## Canonical pipeline

```text
exchange (ccxt, public) → market data → features → models → signals
  → risk gates → results → (API/dashboard consumers)
```

Dashboard/API consume only canonical result schemas, never engine internals.

## 20 components

| # | Component | Phase | Notes |
|---|---|---|---|
| 1 | Exchange adapter (ccxt) | 1 | public read-only, rate-limited, bounded retries |
| 2 | OHLCV schema + validation | 1 | UTC, monotonic, no duplicates |
| 3 | Provenance tracker | 1 | source, fetch time, params, checksum |
| 4 | Snapshot store (parquet) | 1 | immutable, reproducible datasets |
| 5 | Config loader (TOML+env) | 1 | pydantic-validated; no secrets in TOML |
| 6 | CLI (`sf health`, `sf config-show`) | 1 | typer + rich |
| 7 | Logging | 1 | level from config, override via env |
| 8 | Feature builder | 2 | planned, not implemented in Phase 1 |
| 9 | Label builder | 2 | planned, not implemented in Phase 1 |
| 10 | Model interface | 2 | planned, not implemented in Phase 1 |
| 11 | Training pipeline | 2 | planned, not implemented in Phase 1 |
| 12 | Calibration | 2 | planned, not implemented in Phase 1 |
| 13 | Inference service | 2 | planned, not implemented in Phase 1 |
| 14 | Signal composer | 3 | planned, not implemented in Phase 1 |
| 15 | Backtest engine (causal) | 3 | planned, not implemented in Phase 1 |
| 16 | Risk gates (deterministic) | 3 | planned, not implemented in Phase 1; `NO_TRADE` valid |
| 17 | AI provider adapters | 4 | planned, not implemented in Phase 1 |
| 18 | API layer | 5 | planned, not implemented in Phase 1 |
| 19 | Dashboard | 5 | planned, not implemented in Phase 1 |
| 20 | Paper-trading loop | 6 | planned, not implemented in Phase 1, advisory-only |

## Phase 1 boundaries

Phase 1 implements components 1–7 only. Components 8–20 are documented interfaces,
not code.
