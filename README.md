# DM Crypto Quant Signal — SignalForge (Phase 7 authorized, final phase)

Standalone crypto quant signal engine. Phase 5 adds the canonical signal
decision pipeline — schema-validated `SignalRecord` v1.0, deterministic
model-to-decision policy, calibration-aware handling, expected-edge vs cost
gating, deterministic risk gates (fail-closed), advisory-only AI context,
proposed level validation, a hash-chained audit history, and `sf signal`
CLI with offline replay — on top of the Phase 1–4 foundation. The engine
abstains (`NO_TRADE`) whenever mandatory evidence is missing, and it places
no orders, holds no positions, and tracks no live P&L. Phase 6 adds the
event-safe backtester (`sf backtest`: next-bar fills, full costs, conservative
ties, reproducible ledger) and the advisory-only paper sandbox (`sf paper`:
persistent state, duplicate protection, halt/flatten, divergence comparison).
Both are offline simulations — neither places orders nor trades real money.
Phase 7 adds the read-only API (`/api/v1`, GET-only) and a server-rendered
dashboard (`sf serve`, loopback-only by default). The dashboard never predicts
and the API never mutates, trains, fetches, or trades.

> Phase 7 only. API/dashboard are read-only by design. Live trading,
portfolio accounting beyond the paper sandbox, and public exposure are
**not implemented and not planned in this design**.

## Quickstart (Windows)

```powershell
py -3.12 -m pip install uv
uv sync
uv run sf health
uv run sf config-show
uv run pytest
uv run ruff check .
```

## Layout

```text
config/default.toml      validated defaults (no secrets; [ai] disabled, [signal]/[risk]/[backtest]/[paper]/[api] set)
src/crypto_engine/       engine: data, features, labels, models, registry,
                         calibration, validation, ai_providers (8 adapters),
                         agents (8 analysts), signals (schema/policy/costs/levels/history),
                         risk (deterministic gates), backtesting, paper_trading,
                         api (GET-only /api/v1), dashboard (server-rendered HTML)
tests/                   pytest suite (unit, integration, leakage; all offline;
                         API/dashboard tests skip cleanly without the api extra)
docs/                    architecture, methodology, AI_PROVIDERS, INSTALL_WINDOWS, security
memory/                  project memory, state, next steps, session log
.opencode/agents|skills|commands  agent roles, procedures, workflows
```

## Configuration

See `config/default.toml` (exchange, data symbols/timeframes, logging, model,
registry, calibration, validation, ai, signal, risk, backtest, paper, api).
AI providers are all disabled by default; risk gates fail closed by default;
the API binds loopback-only by default and refuses non-loopback binds without
explicit `--allow-remote`. Keys live only in env/`.env` (see `.env.example`);
TOML never holds secrets. Provider SDKs are bring-your-own
(`pip install openai …`) — the sandbox has no package-registry network, so
there are no ai-* extras. API extras install via `uv sync --extra api`.

## Status

Phase 7 complete (final) — read-only API + server-rendered dashboard + `sf serve`
(all verified; empty states explicit; no predictions, no mutations, no orders).
See `memory/CURRENT_STATE.md` and `memory/NEXT_STEPS.md` for verified state.
No 90% target claimed; no out-of-sample profitability claimed; no AI
predictive-value claim; no order placement, live readiness, real-money
trading, or public deployment — paper fills are hypothetical.

## License

MIT (see `DECISIONS.md` for stack/license notes, incl. VectorBT deferral).
