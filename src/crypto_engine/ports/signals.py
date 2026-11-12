"""Signal store port (DR-2).

Protocol for the hash-chained signal audit history. Implementations live in
the infrastructure layer (e.g. JSONL append-only store).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SignalStore(Protocol):
    """Protocol for append-only signal audit history."""

    def append(self, record: dict) -> str:
        """Append a signal record to the hash chain.

        Returns the deterministic signal id. Raises on duplicate id.
        """
        ...

    def get(self, signal_id: str) -> dict | None:
        """Read one signal record by id (None when absent)."""
        ...

    def list(
        self,
        *,
        symbol: str | None = None,
        direction: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """List signal records newest-first with optional filters.

        Returns ``(records, total_count)``.
        """
        ...

    def verify_chain(self) -> tuple[bool, list[str]]:
        """Verify the hash chain integrity.

        Returns ``(ok, errors)``.
        """
        ...


__all__ = ["SignalStore"]
