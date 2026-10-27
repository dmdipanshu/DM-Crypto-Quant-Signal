"""Persistent paper-trading account state (Phase 6 Track B).

Advisory-only: this module records hypothetical fills and never places live
orders (by construction: no order/trade API imports anywhere here — only
``pandas``/``numpy``-free stdlib plus ``pydantic`` and the Phase 5/6 cost,
risk, and signal schemas).

Persistence: one JSON file per account, written atomically (tmp file +
``os.replace``), with a ``checksum`` field (sha256 over the canonical JSON
of every other field). :func:`load_account` verifies the checksum and raises
:exc:`CorruptStateError` on tamper; a missing file raises ``FileNotFoundError``
with an actionable message. Single-process: :func:`save_account` overwrites
the account's own file only.

Duplicate protection: every consumed signal id is recorded in
``processed_signal_ids`` (bounded FIFO, cap ``MAX_PROCESSED_IDS``) on both
accept AND reject, so replays are idempotent and never re-evaluated.

Kill switches: :func:`halt` flips ``status`` to ``"halted"``; a halted
account refuses new positions (existing positions are still marked to
market — halt is NOT a liquidation). Use :func:`flatten` to explicitly close
at a supplied mark with costs (reason ``MANUAL_FLATTEN``).

Bounds (4 GB laptop): ``processed_signal_ids`` (10000), ``rejections``
(10000), ``trades_closed`` (50000), ``equity_curve`` (50000), ``recent_bars``
(500) are hard FIFO caps — old entries are dropped with no silent row loss
elsewhere (state is append-only within the caps, documented here).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from crypto_engine.backtesting.costs import ExecutionCostModel

ACCOUNT_FILENAME = "account.json"
MAX_PROCESSED_IDS = 10000
MAX_REJECTIONS = 10000
MAX_TRADES_CLOSED = 50000
MAX_EQUITY_POINTS = 50000
BAR_WINDOW = 500


class CorruptStateError(ValueError):
    """Raised when a paper account file fails checksum or schema validation."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _checksum_of(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _require_positive_finite(value: float, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"PaperAccount: {name} must be numeric, got {value!r}.") from exc
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"PaperAccount: {name} must be > 0 finite, got {value!r}.")
    return v


class PaperAccount(BaseModel):
    """Persistent advisory-only paper account (single symbol/timeframe)."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = ""
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    cash: float = 0.0
    starting_equity: float = Field(gt=0)
    position: dict[str, Any] | None = None
    position_pct: float = Field(gt=0)
    max_holding_bars: int = Field(ge=0)
    require_levels: bool = False
    cost: dict[str, float] = Field(default_factory=dict)
    model_id: str = ""
    config_hash: str = ""
    trade_counter: int = Field(default=0, ge=0)
    bars_seen: int = Field(default=0, ge=0)
    processed_signal_ids: list[str] = Field(default_factory=list)
    rejections: list[dict[str, Any]] = Field(default_factory=list)
    trades_closed: list[dict[str, Any]] = Field(default_factory=list)
    equity_curve: list[list[Any]] = Field(default_factory=list)
    recent_bars: list[str] = Field(default_factory=list)
    equity_mark_ts: str | None = None
    equity_mark: float | None = None
    status: Literal["open", "halted"] = "open"
    halt_reason: str | None = None
    created_at: str = ""
    updated_at: str = ""
    checksum: str = ""

    def to_checksum_payload(self) -> dict[str, Any]:
        """Payload covered by the checksum (everything except ``checksum``)."""
        payload = self.model_dump(mode="json")
        payload.pop("checksum", None)
        return payload


def _cost_legs(cost: Any) -> dict[str, float]:
    if isinstance(cost, dict):
        legs = {
            k: float(cost.get(k, 0.0)) for k in ("fee_bp", "spread_bp", "slippage_bp", "funding_bp")
        }
    else:
        legs = {
            "fee_bp": float(cost.fee_bp),
            "spread_bp": float(cost.spread_bp),
            "slippage_bp": float(cost.slippage_bp),
            "funding_bp": float(cost.funding_bp),
        }
    for name, val in legs.items():
        if not math.isfinite(val) or val < 0 or val >= 10000.0:
            raise ValueError(f"PaperAccount: cost leg {name}={val!r} must be bp in [0, 10000).")
    return legs


def init_account(
    path: str | Path,
    *,
    symbol: str,
    timeframe: str,
    starting_equity: float,
    position_pct: float = 10.0,
    max_holding_bars: int = 24,
    require_levels: bool = False,
    cost: Any,
    model_id: str = "",
    config_hash: str = "",
    account_id: str = "",
) -> PaperAccount:
    """Create a new paper account file (refuses to overwrite an existing file).

    Raises:
        FileExistsError: If ``path`` already exists (no silent overwrite).
    """
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"PaperAccount: refusing to overwrite existing {target}.")
    equity0 = _require_positive_finite(starting_equity, "starting_equity")
    pct = _require_positive_finite(position_pct, "position_pct")
    if int(max_holding_bars) < 0:
        raise ValueError("PaperAccount: max_holding_bars must be >= 0.")
    now = _utc_now_iso()
    account = PaperAccount(
        account_id=account_id or f"paper-{symbol}-{timeframe}-{now}",
        symbol=symbol,
        timeframe=timeframe,
        cash=equity0,
        starting_equity=equity0,
        position=None,
        position_pct=pct,
        max_holding_bars=int(max_holding_bars),
        require_levels=bool(require_levels),
        cost=_cost_legs(cost),
        model_id=model_id,
        config_hash=config_hash,
        equity_mark_ts=None,
        equity_mark=equity0,
        created_at=now,
        updated_at=now,
    )
    save_account(account, target)
    return load_account(target)


def _atomic_write_json(target: Path, payload: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=True))
        os.replace(tmp_path, target)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def save_account(account: PaperAccount, path: str | Path) -> Path:
    """Recompute checksum, stamp ``updated_at``, and atomically persist."""
    target = Path(path)
    account.updated_at = _utc_now_iso()
    account.checksum = _checksum_of(account.to_checksum_payload())
    _atomic_write_json(target, account.model_dump(mode="json"))
    return target


def load_account(path: str | Path) -> PaperAccount:
    """Load and verify a paper account file.

    Raises:
        FileNotFoundError: With an actionable message when the file is missing.
        CorruptStateError: On checksum mismatch or schema violation.
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(
            f"PaperAccount: no account file at {target}. "
            "Create one with `sf paper init --symbol <s> --timeframe <t> "
            f"--equity <x> --account {target}`."
        )
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CorruptStateError(f"PaperAccount: cannot read {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CorruptStateError(f"PaperAccount: {target} is not a JSON object.")
    stored = str(raw.get("checksum", ""))
    try:
        account = PaperAccount.model_validate(raw)
    except ValueError as exc:
        raise CorruptStateError(f"PaperAccount: schema violation in {target}: {exc}") from exc
    expected = _checksum_of(account.to_checksum_payload())
    if stored != expected:
        raise CorruptStateError(
            f"PaperAccount: checksum mismatch in {target} "
            f"(stored={stored[:12]}..., computed={expected[:12]}...; "
            "file may be tampered or half-written — restore from backup)."
        )
    return account


def account_hash(account: PaperAccount) -> str:
    """Deterministic content hash (checksum inputs only, excludes checksum)."""
    return _checksum_of(account.to_checksum_payload())


def equity(account: PaperAccount, mark_price: float) -> float:
    """Pure mark-to-market equity at ``mark_price`` (no state change)."""
    px = _require_positive_finite(mark_price, "mark_price")
    pos = account.position
    if pos is None:
        return float(account.cash)
    qty = float(pos["qty"])
    if pos["side"] == "LONG":
        return float(account.cash) + qty * px
    return float(account.cash) - qty * px


def is_processed(account: PaperAccount, signal_id: str) -> bool:
    """True when ``signal_id`` was already consumed (accept or reject)."""
    return str(signal_id) in set(account.processed_signal_ids)


def mark_processed(account: PaperAccount, signal_id: str) -> None:
    """Record ``signal_id`` as consumed (idempotent; bounded FIFO cap)."""
    sid = str(signal_id)
    if sid in set(account.processed_signal_ids):
        return
    account.processed_signal_ids.append(sid)
    while len(account.processed_signal_ids) > MAX_PROCESSED_IDS:
        account.processed_signal_ids.pop(0)


def record_rejection(account: PaperAccount, signal_id: str, reason: str, ts: str) -> None:
    """Record a rejection (also marks the id processed; never re-evaluated)."""
    mark_processed(account, signal_id)
    account.rejections.append({"signal_id": str(signal_id), "reason": str(reason), "ts": str(ts)})
    while len(account.rejections) > MAX_REJECTIONS:
        account.rejections.pop(0)


def halt(account: PaperAccount, reason: str) -> None:
    """Halt the account: refuses new positions; existing position still marked."""
    account.status = "halted"
    account.halt_reason = str(reason)


def _execution_model(account: PaperAccount) -> ExecutionCostModel:
    from crypto_engine.signals.schema import CostEstimate

    return ExecutionCostModel(
        CostEstimate(
            fee_bp=account.cost["fee_bp"],
            spread_bp=account.cost["spread_bp"],
            slippage_bp=account.cost["slippage_bp"],
            funding_bp=account.cost["funding_bp"],
            source="configured",
        )
    )


def flatten(account: PaperAccount, mark_price: float, ts: str) -> dict[str, Any] | None:
    """Explicitly close the open position at ``mark_price`` (costed).

    Reason ``MANUAL_FLATTEN``. Halt does NOT auto-liquidate — flatten is the
    only close path besides step-driven exits. Returns the exit fill dict,
    or None when flat (no-op).
    """
    pos = account.position
    if pos is None:
        return None
    px_raw = _require_positive_finite(mark_price, "mark_price")
    model = _execution_model(account)
    side = str(pos["side"])
    exit_side = "SELL" if side == "LONG" else "BUY"
    px = model.fill_price(exit_side, px_raw)
    qty = float(pos["qty"])
    notional = qty * px
    fee = model.fill_fee(notional)
    if side == "LONG":
        account.cash += notional - fee
        gross = (px - float(pos["entry_price"])) * qty
    else:
        account.cash -= notional - fee
        gross = (float(pos["entry_price"]) - px) * qty
    costs = float(pos.get("entry_fee", 0.0)) + fee + float(pos.get("funding_accrued", 0.0))
    exit_fill = {
        "ts": str(ts),
        "side": exit_side,
        "price": px,
        "qty": qty,
        "fee_bp": model.fee_bp,
        "spread_bp": model.spread_bp,
        "slippage_bp": model.slippage_bp,
        "notional": notional,
        "cost_total": fee,
    }
    trade_id = (
        f"{account.symbol}|{account.timeframe}|{pos['entry_ts']}|{side}|{account.trade_counter}"
    )
    account.trade_counter += 1
    from crypto_engine.backtesting.types import Trade

    # NOTE: engine ``ExitReason`` has no manual-flatten member (Track A owns
    # that Literal and it must not be extended here), so the strict ``Trade``
    # field ``exit_reason`` stays None and the paper-only reason is recorded
    # in the documented extra key ``paper_reason`` (stripped by compare.py
    # before ``Trade`` validation). CLI output reports MANUAL_FLATTEN.
    trade = Trade(
        trade_id=trade_id,
        symbol=account.symbol,
        timeframe=account.timeframe,
        side=side,  # type: ignore[arg-type]
        entry=pos["entry_fill"],  # type: ignore[arg-type]
        exit=exit_fill,  # type: ignore[arg-type]
        exit_reason=None,
        gross_pnl=gross,
        costs_total=costs,
        net_pnl=gross - costs,
        bars_held=int(pos.get("bars_held", 0)),
    )
    entry_dict = trade.model_dump(mode="json")
    entry_dict["paper_reason"] = "MANUAL_FLATTEN"
    account.trades_closed.append(entry_dict)
    while len(account.trades_closed) > MAX_TRADES_CLOSED:
        account.trades_closed.pop(0)
    account.position = None
    # Keep the curve consistent: post-flatten equity is cash (flat).
    flat_mark = float(account.cash)
    account.equity_mark_ts = str(ts)
    account.equity_mark = flat_mark
    account.equity_curve.append([str(ts), flat_mark])
    while len(account.equity_curve) > MAX_EQUITY_POINTS:
        account.equity_curve.pop(0)
    return exit_fill


__all__ = [
    "ACCOUNT_FILENAME",
    "BAR_WINDOW",
    "MAX_EQUITY_POINTS",
    "MAX_PROCESSED_IDS",
    "MAX_REJECTIONS",
    "MAX_TRADES_CLOSED",
    "CorruptStateError",
    "PaperAccount",
    "account_hash",
    "equity",
    "flatten",
    "halt",
    "init_account",
    "is_processed",
    "load_account",
    "mark_processed",
    "record_rejection",
    "save_account",
]
