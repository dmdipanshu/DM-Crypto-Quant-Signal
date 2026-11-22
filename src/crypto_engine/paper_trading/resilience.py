"""Operational resilience components for Q7 paper trading (Phase 6).

Checkpointing, an append-only event ledger protected by a hash chain, and
crash recovery for the deterministic paper-trading simulator. All writes are
atomic (tmp file + ``os.replace``); account state is checksummed and every
ledger entry is chained to its predecessor so tampering is detectable.
Advisory-only: no live orders, no network, no randomness.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from crypto_engine.paper_trading.orders import OrderManager
from crypto_engine.paper_trading.simulator import (
    Simulator,
    SimulatorResult,
)
from crypto_engine.paper_trading.state import (
    PaperAccount,
    _atomic_write_json,
    account_hash,
    equity,
    save_account,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _checksum_of(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _atomic_write_text(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            if tmp_path.exists():
                tmp_path.unlink()
        raise


class SimulatorCheckpoint(BaseModel):
    """Immutable snapshot of simulator state at a bar boundary."""

    model_config = ConfigDict(extra="forbid")

    checkpoint_id: str
    bar_index: int
    account_hash: str
    order_count: int
    fill_count: int
    trade_count: int
    equity: float
    cash: float
    created_at: str
    event_hash_chain: str


class CheckpointManager:
    """Atomic, checksummed checkpoint store for simulator runs."""

    def __init__(self, checkpoint_dir: str | Path) -> None:
        self.dir = Path(checkpoint_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _file_stem(self, checkpoint_id: str) -> str:
        return checkpoint_id.replace(":", "-").replace("/", "_").replace("\\", "_")

    def _ckpt_path(self, checkpoint_id: str) -> Path:
        return self.dir / f"{self._file_stem(checkpoint_id)}.json"

    def _account_path(self, checkpoint_id: str) -> Path:
        return self.dir / f"{self._file_stem(checkpoint_id)}.account.json"

    def _checksum_path(self, checkpoint_id: str) -> Path:
        return self.dir / f"{self._file_stem(checkpoint_id)}.checksum"

    def save_checkpoint(
        self,
        simulator: Simulator,
        bar_index: int,
        event_hash_chain: str,
        *,
        checkpoint_id: str | None = None,
    ) -> SimulatorCheckpoint:
        """Write an atomic, checksummed checkpoint for ``simulator`` at ``bar_index``."""
        account = simulator.account
        account_h = account_hash(account)
        order_count = len(simulator.order_manager.list_orders())
        fill_count = sum(len(order.fills) for order in simulator.order_manager.list_orders())
        trade_count = len(account.trades_closed)
        mark_price = float(account.equity_mark or account.cash or account.starting_equity)
        try:
            eq = equity(account, mark_price)
        except ValueError:
            eq = float(account.cash)
        created_at = _utc_now_iso()
        if checkpoint_id is None:
            checkpoint_id = f"ckpt-{bar_index:06d}-{created_at}"
        checkpoint = SimulatorCheckpoint(
            checkpoint_id=checkpoint_id,
            bar_index=int(bar_index),
            account_hash=account_h,
            order_count=order_count,
            fill_count=fill_count,
            trade_count=trade_count,
            equity=float(eq),
            cash=float(account.cash),
            created_at=created_at,
            event_hash_chain=event_hash_chain,
        )
        ckpt_path = self._ckpt_path(checkpoint_id)
        _atomic_write_json(ckpt_path, checkpoint.model_dump(mode="json"))
        digest = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()
        _atomic_write_text(self._checksum_path(checkpoint_id), digest)
        _atomic_write_json(
            self._account_path(checkpoint_id), account.model_dump(mode="json")
        )
        return checkpoint

    def list_checkpoints(self) -> list[SimulatorCheckpoint]:
        """Return all parseable checkpoints sorted by bar index."""
        checkpoints: list[SimulatorCheckpoint] = []
        for path in sorted(self.dir.glob("*.json")):
            if path.suffixes == [".account", ".json"]:
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                checkpoints.append(SimulatorCheckpoint.model_validate(raw))
            except (OSError, ValueError):
                continue
        checkpoints.sort(key=lambda checkpoint: checkpoint.bar_index)
        return checkpoints

    def load_latest_checkpoint(self) -> SimulatorCheckpoint | None:
        """Load and verify the highest bar-index checkpoint, or ``None``."""
        checkpoints = self.list_checkpoints()
        if not checkpoints:
            return None
        latest = checkpoints[-1]
        if not self.verify_checkpoint_integrity(latest.checkpoint_id):
            raise ValueError(
                f"CheckpointManager: latest checkpoint {latest.checkpoint_id!r} failed integrity check."
            )
        return latest

    def verify_checkpoint_integrity(self, checkpoint_id: str) -> bool:
        """True when the checkpoint file and its account snapshot are intact."""
        ckpt_path = self._ckpt_path(checkpoint_id)
        checksum_path = self._checksum_path(checkpoint_id)
        account_path = self._account_path(checkpoint_id)
        if not (
            ckpt_path.is_file()
            and checksum_path.is_file()
            and account_path.is_file()
        ):
            return False
        try:
            stored = checksum_path.read_text(encoding="utf-8").strip()
            computed = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()
            if stored != computed:
                return False
            checkpoint = SimulatorCheckpoint.model_validate(
                json.loads(ckpt_path.read_text(encoding="utf-8"))
            )
            raw = json.loads(account_path.read_text(encoding="utf-8"))
            account = PaperAccount.model_validate(raw)
            if account_hash(account) != checkpoint.account_hash:
                return False
        except (OSError, ValueError):
            return False
        return True


class EventLedger:
    """Append-only ledger of simulator events protected by a hash chain."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_events(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        events = raw.get("events", []) if isinstance(raw, dict) else raw
        return events if isinstance(events, list) else []

    def append_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        ts: str | None = None,
    ) -> dict[str, Any]:
        """Append an event and extend the hash chain; returns the stored entry."""
        events = self._read_events()
        seq = int(events[-1]["seq"]) + 1 if events else 1
        prev_hash = str(events[-1]["hash"]) if events else ""
        entry_ts = ts or _utc_now_iso()
        entry: dict[str, Any] = {
            "seq": seq,
            "ts": entry_ts,
            "event_type": event_type,
            "data": data or {},
            "prev_hash": prev_hash,
        }
        entry["hash"] = _checksum_of(
            {
                "seq": entry["seq"],
                "ts": entry["ts"],
                "event_type": entry["event_type"],
                "data": entry["data"],
                "prev_hash": entry["prev_hash"],
            }
        )
        events.append(entry)
        _atomic_write_json(self.path, {"events": events})
        return entry

    def verify_chain(self) -> bool:
        """True when every entry's hash and ``prev_hash`` chain are valid."""
        events = self._read_events()
        previous = ""
        for entry in events:
            expected_hash = _checksum_of(
                {
                    "seq": entry.get("seq"),
                    "ts": entry.get("ts"),
                    "event_type": entry.get("event_type"),
                    "data": entry.get("data"),
                    "prev_hash": entry.get("prev_hash"),
                }
            )
            if str(entry.get("hash")) != expected_hash:
                return False
            if str(entry.get("prev_hash")) != previous:
                return False
            previous = str(entry.get("hash"))
        return True

    def read_events(self) -> list[dict[str, Any]]:
        """Return a copy of all stored ledger events."""
        return [dict(event) for event in self._read_events()]


class RecoveryManager:
    """Crash recovery for paper-trading simulator state."""

    def __init__(
        self,
        state_dir: str | Path,
        checkpoint_dir: str | Path | None = None,
        ledger_path: str | Path | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_manager = CheckpointManager(
            checkpoint_dir or (self.state_dir / "checkpoints")
        )
        self.ledger = EventLedger(ledger_path or (self.state_dir / "events.json"))

    def save_state(
        self,
        account: PaperAccount,
        order_manager: OrderManager,
    ) -> Path:
        """Persist account and order-manager snapshots atomically."""
        account_path = self.state_dir / "account.json"
        save_account(account, account_path)
        orders_path = self.state_dir / "orders.json"
        payload = {
            "counter": order_manager._counter,
            "orders": [
                order.model_dump(mode="json") for order in order_manager.list_orders()
            ],
            "audit_events": list(order_manager.audit_log()),
        }
        _atomic_write_json(orders_path, payload)
        return account_path

    def recover_from_checkpoint(self, checkpoint_id: str) -> PaperAccount:
        """Restore a ``PaperAccount`` from a checkpoint's account snapshot."""
        account_path = self.checkpoint_manager._account_path(checkpoint_id)
        if not account_path.is_file():
            raise FileNotFoundError(
                f"RecoveryManager: no account snapshot for checkpoint {checkpoint_id!r}."
            )
        try:
            raw = json.loads(account_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"RecoveryManager: cannot read {account_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"RecoveryManager: {account_path} is not a JSON object.")
        try:
            account = PaperAccount.model_validate(raw)
        except ValueError as exc:
            raise ValueError(
                f"RecoveryManager: schema violation in {account_path}: {exc}"
            ) from exc
        expected = _checksum_of(account.to_checksum_payload())
        if str(account.checksum) != expected:
            raise ValueError(
                f"RecoveryManager: checksum mismatch in {account_path}; state is inconsistent."
            )
        return account

    def is_state_consistent(self, account: PaperAccount) -> bool:
        """True when the account's stored checksum matches its content."""
        try:
            expected = _checksum_of(account.to_checksum_payload())
        except ValueError:
            return False
        return str(account.checksum) == expected

    def replay_events(self, from_seq: int = 1) -> list[dict[str, Any]]:
        """Return ledger events at or after ``from_seq``."""
        events = self.ledger.read_events()
        return [
            event
            for event in events
            if int(event.get("seq", 0)) >= from_seq
        ]


def deterministic_replay(
    simulator: Simulator,
    bars: list[dict[str, Any]],
    from_checkpoint: SimulatorCheckpoint | None = None,
    recovery_manager: RecoveryManager | None = None,
) -> SimulatorResult:
    """Re-run the simulator deterministically, optionally resuming from a checkpoint."""
    if from_checkpoint is not None:
        if recovery_manager is None:
            raise ValueError(
                "deterministic_replay: recovery_manager is required when resuming from a checkpoint."
            )
        if not recovery_manager.checkpoint_manager.verify_checkpoint_integrity(
            from_checkpoint.checkpoint_id
        ):
            raise ValueError(
                f"deterministic_replay: checkpoint {from_checkpoint.checkpoint_id!r} failed integrity check."
            )
        account = recovery_manager.recover_from_checkpoint(from_checkpoint.checkpoint_id)
        simulator.account = account
        simulator._bar_counter = from_checkpoint.bar_index
        bars = list(bars[from_checkpoint.bar_index:])
    return simulator.run(bars)


__all__ = [
    "CheckpointManager",
    "EventLedger",
    "RecoveryManager",
    "SimulatorCheckpoint",
    "deterministic_replay",
]
