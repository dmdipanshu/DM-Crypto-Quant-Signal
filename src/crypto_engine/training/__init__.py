"""Model training workflow (Phase 8).

Chronological, leakage-safe logistic-baseline training over a local Parquet
snapshot: causal features + triple-barrier labels, purge/embargo splits,
train-only scaling, registry persistence with full provenance.

Nothing here touches the network, trains on future data, or claims
profitability. Single-process, bounded rows (4 GB laptop).
"""

from __future__ import annotations

__all__: list[str] = []
