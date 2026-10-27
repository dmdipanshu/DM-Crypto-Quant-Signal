# PAPER_TRADING.md — SignalForge (Phase 6, implemented)

> Implemented in Phase 6 Track B. Advisory-only: this loop records
> hypothetical fills and NEVER places live orders. No order/trade API imports
> exist anywhere in `paper_trading/` (grep-verified); the engine consumes
> only `pandas`-free stdlib plus `pydantic` and the Phase 5/6 cost, risk, and
> signal schemas.

## Modules

- `paper_trading/state.py` — persistent `PaperAccount` JSON (atomic tmp +
  `os.replace`, `checksum` field, UTC `created_at`/`updated_at`).
- `paper_trading/loop.py` — `step()`: consumes exactly ONE new bar + at most
  ONE pending approved signal, mirroring `backtesting/engine.py` semantics.
- `paper_trading/compare.py` — `compare_to_backtest()` /
  `compare_account_to_backtest()`: divergence vs a frozen backtest ledger.

## State file layout

One JSON file per account (default `data/paper/account.json`):

- Identity/provenance: `account_id`, `symbol`, `timeframe`, `model_id`,
  `config_hash` (sha256 of backtest+paper settings at init).
- Economics: `cash`, `starting_equity`, `position` (side/entry_ts/entry_price/
  qty/notional/entry_fee/funding_accrued/entry_fill/stop_loss/take_profit/
  bars_held) or `None`, `position_pct`, `max_holding_bars`, `require_levels`,
  `cost` legs snapshot (fee/spread/slippage/funding bp).
- Audit: `processed_signal_ids` (bounded FIFO, cap 10000), `rejections`
  (cap 10000, `{signal_id, reason, ts}`), `trades_closed` (cap 50000, strict
  `Trade` dicts plus paper-only `paper_reason`, e.g. `MANUAL_FLATTEN`),
  `equity_curve` (`[ts, equity]` points, cap 50000), `recent_bars` (last 500
  bar ts), `equity_mark_ts`/`equity_mark`, `trade_counter`, `bars_seen`.
- Kill switch: `status` (`open`|`halted`), `halt_reason`.

Integrity: `load()` verifies the checksum and raises `CorruptStateError` on
tamper (schema violations included); a missing file raises
`FileNotFoundError` with the exact `sf paper init` command to run.
`save()` recomputes the checksum and writes atomically. Single-process:
`init` refuses to overwrite an existing file; `save` overwrites only the
account's own file.

## Duplicate protection

Every consumed signal id is recorded on BOTH accept and reject
(`mark_processed` / `record_rejection`), so replays are idempotent and never
re-evaluated. A `DUPLICATE` re-step returns the account byte-identical
(no bar append, no equity point). `HALTED` refusals consume nothing.

## Halt vs flatten

- `halt(reason)` flips `status` to `halted`: new positions are refused, but
  any open position is still marked to market. Halt is NOT a liquidation.
- `flatten(mark)` is the only halt-independent close path: closes at the
  supplied mark with costs, records the `Trade` with strict-schema
  `exit_reason=None` plus `paper_reason="MANUAL_FLATTEN"` (the engine
  `ExitReason` literal has no manual-flatten member and must not be extended
  from Track B), and appends a flat equity point so the curve stays
  consistent. No position -> no-op (`null` fill).

## Step semantics

Per-step order (mirrors the backtest engine): halt check -> duplicate check
-> bar append (bounded window) -> signal validation (`SYMBOL_MISMATCH`,
`STALE_FUTURE` when `signal.generated_at >= bar.ts`, `EXPIRED`,
`LEVELS_REQUIRED`) -> `TIMEOUT` close at the bar open before evaluating the
new signal -> risk gates via caller-supplied context (veto recorded as the
`RISK_GATE_*` code, no position change) -> direction actions at the bar open
(`NEUTRAL_CLOSE` closes, `NO_TRADE`/`NO_POSITION` no-op, same-direction
`ALREADY_POSITIONED` no-op, opposing `SIGNAL_FLIP` close + same-bar reopen)
-> SL/TP on the step bar high/low (entry bar included; same-bar touch of
both exits `STOP_LOSS`) -> funding for one bar while still open
(side-agnostic per-bar rate) -> mark at close. `step()` is pure-ish
(deep-copies the account; caller saves), so identical sequences give
identical account hashes and a step never reads future bars (it consumes
exactly the passed bar).

## Compare workflow

1. `sf backtest run --snapshot <dir> --model <dir> --out <dir>` writes a
   frozen `result.json` ledger.
2. Step a paper account over the identical bar + signal sequence
   (`sf paper step` per bar), `sf paper flatten` at the last close to mirror
   the engine `END_OF_DATA` marking convention.
3. `sf paper compare --account <file> --backtest <result.json>` checks
   trade-count match, per-trade entry_ts/side/qty (relative tol) and
   net_pnl (absolute tol), missing/extra trades, and end-equity diff.
   Verdict `match`/`diverged` with named reasons. Exit codes: 0 match,
   2 diverged (valid analytical outcome), 1 errors. `exit_reason` is
   intentionally NOT compared (paper flatten reasons have no engine
   counterpart); `paper_reason` keys are stripped before `Trade` validation.

## Residual risks / limitations

- Single position only; no pyramiding, no partial fills, no short-beyond-cash
  modeling beyond the engine's cash-credit convention.
- Funding is a side-agnostic per-bar cost, NOT exchange funding settlement.
- `END_OF_DATA` is a forced mark, not a real fill; paper inherits the
  convention via explicit `flatten`.
- Paper `ALREADY_POSITIONED`/gate-reject paths assume the caller steps bars
  in order, one signal per bar; out-of-order stepping is rejected
  (`STALE_FUTURE`) but not re-sequenced.
- Caps are FIFO (processed ids 10000, equity points 50000, trades 50000,
  rejections 10000, bar window 500): within-cap history is exact; beyond-cap
  history is summarized only by counts/cash. Paper is NOT portfolio
  accounting and never overrides `NO_TRADE`.
