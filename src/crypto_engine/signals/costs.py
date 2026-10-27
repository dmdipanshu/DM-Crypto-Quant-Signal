"""Round-trip cost model in basis points (Phase 5 Track A).

Estimates only — not execution accounting. Phase 6 owns fills
(fees tier-by-tier, spread capture, slippage vs arrival price,
funding accrual). ``CostModel`` gives a deterministic ex-ante
``total_bp`` used by policy edge checks
(``edge_clears_cost``); realized P&L must come from
walk-forward/cost validation, never from confidence scores.

4 GB laptop: pure math, no network, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass

_BP_CAP = 10000.0


def _guard_bp(value: float, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CostModel: {name} must be numeric, got {value!r}.") from exc
    import math

    if not math.isfinite(v):
        raise ValueError(f"CostModel: {name} must be finite, got {value!r}.")
    if v < 0:
        raise ValueError(f"CostModel: {name} must be >= 0, got {v!r}.")
    if v >= _BP_CAP:
        raise ValueError(
            f"CostModel: {name}={v} bp >= 10000 bp; likely decimal-mixup "
            "(pass basis points, e.g. 10.0 bp, not decimals like 0.001)."
        )
    return v


@dataclass
class CostModel:
    """Additive round-trip cost estimate (all fields in basis points)."""

    fee_bp: float = 0.0
    spread_bp: float = 0.0
    slippage_bp: float = 0.0
    funding_bp: float = 0.0
    source: str = "configured"

    def __post_init__(self) -> None:
        self.fee_bp = _guard_bp(self.fee_bp, "fee_bp")
        self.spread_bp = _guard_bp(self.spread_bp, "spread_bp")
        self.slippage_bp = _guard_bp(self.slippage_bp, "slippage_bp")
        self.funding_bp = _guard_bp(self.funding_bp, "funding_bp")
        if self.source not in ("configured", "walkforward-proxy", "exchange"):
            raise ValueError(f"CostModel: unknown source {self.source!r}.")

    def total_bp(self) -> float:
        """Deterministic additive total (bp)."""
        return float(self.fee_bp + self.spread_bp + self.slippage_bp + self.funding_bp)

    @classmethod
    def from_config(
        cls,
        fee_bp: float = 10.0,
        slippage_bp: float = 5.0,
        spread_bp: float = 0.0,
        funding_bp: float = 0.0,
        source: str = "configured",
    ) -> CostModel:
        """Build from validation-like fee/slippage + signal funding config."""
        return cls(
            fee_bp=fee_bp,
            spread_bp=spread_bp,
            slippage_bp=slippage_bp,
            funding_bp=funding_bp,
            source=source,
        )


def edge_clears_cost(edge_bp: float, cost_bp: float, margin_bp: float = 0.0) -> bool:
    """Pure edge gate: ``edge_bp >= cost_bp + margin_bp`` (all in bp)."""
    import math

    for name, v in (("edge_bp", edge_bp), ("cost_bp", cost_bp), ("margin_bp", margin_bp)):
        if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            raise ValueError(f"edge_clears_cost: {name} must be finite, got {v!r}.")
    return float(edge_bp) >= float(cost_bp) + float(margin_bp)


__all__ = ["CostModel", "edge_clears_cost"]
