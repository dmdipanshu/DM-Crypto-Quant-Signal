# ARCHITECTURE.md — SignalForge (Phase 7, final)

## Canonical pipeline

```text
exchange (ccxt, public) → market data → features → models → calibration
  → [optional AI context] → signal decision policy → risk gates
  → validated signal record → signal audit history
  → (Phase 6: backtests/paper implemented; Phase 7: API/dashboard implemented, read-only)
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
| 18 | API layer | 7 | implemented: GET-only /api/v1, versioned envelopes, no mutations |
| 19 | Dashboard | 7 | implemented: server-rendered, shared adapters, escaped, no trading controls |
| 20 | Paper-trading loop | 6 | implemented: advisory-only sandbox, persistent state, duplicate protection, no orders |

## Phase 7 boundaries (final)

Phase 7 implements components 18–19 (read-only API: `sf serve` + `/api/v1`;
server-rendered dashboard consuming shared repo readers). All 20 components
are now implemented as designed — except nothing beyond this design exists:
no live orders, no real-money trading, no public exposure by default, no
portfolio accounting beyond the paper sandbox. The dashboard never predicts;
the API never mutates, trains, fetches, or trades.

## Target architecture set (`docs/architecture/`)

`docs/architecture/` contains the **target** architecture: layers and ports,
dependency rules, storage roles (Parquet / SQLite WAL / JSON), the universal
AI gateway, security boundaries, resource budget and a proposed roadmap. It is
additive documentation and does not authorize a new phase.

Two parts of that set are already implemented and must not be read as design
only:

- the provider-neutral AI gateway in `src/crypto_engine/ai/` (Stages 0–5:
  contracts, registry, gateway, one OpenAI-compatible adapter, bounded
  signal/research advisory, composition root, `sf ai` CLI). Advisory only: no
  provider SDK is required, no live credential is stored, and AI output can
  never change direction, score, risk, position size, approval or execution;
- the storage role contracts in `src/crypto_engine/storage/`
  (`contracts.py`, `sqlite.py` with enforced WAL, `json_store.py`). They create
  no database file, no migration and no `data/state/` directory.

Everything else in the set — the layered `domain/`/`application/`/`ports/`
split, the target package layout, roadmap stages and deferred subsystems —
remains design intent only. This document remains the truthful description of
the current code.
