# ARCHITECTURE.md — SignalForge (Phase 6)

## Canonical pipeline

```text
exchange (ccxt, public) → market data → features → models → calibration
  → [optional AI context] → signal decision policy → risk gates
  → validated signal record → signal audit history
  → (Phase 6: backtests/paper implemented; Phase 7: API/dashboard consumers)
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
| 8 | Feature builder | 2 | causal, chunked, registry-documented |
| 9 | Label builder | 2 | triple-barrier, observable-at, incomplete excluded |
| 10 | Model interface | 2 | fitted-only predict/score, schema-checked, checksummed |
| 11 | Training pipeline | 2 | baselines (trend/majority/logistic), n_jobs=1 |
| 12 | Calibration | 3 | P(y==positive_class), sigmoid/isotonic, never on holdout |
| 13 | Inference service | 5 | single-row decide(), fitted artifacts only, no retraining |
| 14 | Signal composer | 5 | canonical SignalRecord v1.0, deterministic ids, NO_TRADE first-class |
| 15 | Backtest engine (causal) | 6 | implemented: next-bar fills, costed, conservative ties, reproducible ledger |
| 16 | Risk gates (deterministic) | 5 | fixed order A→B→C→D, fail-closed, AI never overrides; `NO_TRADE` valid |
| 17 | AI provider adapters | 4 | 8 adapters, disabled default, advisory-only to signals |
| 18 | API layer | 7 | planned, not implemented in Phase 5 |
| 19 | Dashboard | 7 | planned, not implemented in Phase 5 |
| 20 | Paper-trading loop | 6 | implemented: advisory-only sandbox, persistent state, duplicate protection, no orders |

## Phase 6 boundaries

Phase 6 implements component 15 (event-safe backtester: `sf backtest`) and
component 20 (advisory-only paper sandbox: `sf paper`), consuming canonical
`SignalRecord`s and deterministic risk gates. Components 18–19 (API/dashboard)
remain documented interfaces, not code. No live orders; no real-money trading;
no portfolio accounting beyond the paper sandbox: exposure checks still use
caller-supplied snapshots only.
