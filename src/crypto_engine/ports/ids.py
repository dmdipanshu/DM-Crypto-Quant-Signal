"""Id generator port (DR-2).

Protocol for deterministic id generation. Injected so tests can use
deterministic ids; production uses content hashes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IdGenerator(Protocol):
    """Protocol for generating deterministic ids."""

    def signal_id(
        self,
        symbol: str,
        timeframe: str,
        generated_at: str,
        content_hash: str,
    ) -> str:
        """Generate a deterministic signal id from content and timestamp."""
        ...

    def run_id(
        self,
        dataset_id: str,
        strategy_id: str,
        parameters_hash: str,
    ) -> str:
        """Generate a deterministic backtest/research run id."""
        ...


__all__ = ["IdGenerator"]
