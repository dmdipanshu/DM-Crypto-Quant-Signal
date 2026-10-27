"""Execution cost model built FROM Phase 5 ``CostEstimate`` (Phase 6 Track A).

Reuses legs, never duplicates them. All legs in basis points (bp);
``>= 10000`` rejected as decimal-mixup, consistent with
``signals/costs.py`` and ``signals/schema.py``.

Formulas (half-spread + slippage applied against the taker):
  BUY  fill = ref * (1 + (spread_bp / 2 + slippage_bp) / 1e4)
  SELL fill = ref * (1 - (spread_bp / 2 + slippage_bp) / 1e4)
  fee  = notional * fee_bp / 1e4
  funding(notional, funding_bp_per_bar, bars) = notional * funding_bp / 1e4 * bars

Assumption (documented): ``funding_bp`` from ``CostEstimate`` is a PER-BAR
rate charged as a cost regardless of side (conservative). It is NOT the
exchange funding settlement; long/short sign is ignored on purpose.
Pure functions, hand-computable, no network.
"""

from __future__ import annotations

import math

from crypto_engine.signals.schema import CostEstimate

_BP_CAP = 10000.0


def _guard_bp(value: float, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ExecutionCostModel: {name} must be numeric, got {value!r}.") from exc
    if not math.isfinite(v):
        raise ValueError(f"ExecutionCostModel: {name} must be finite, got {value!r}.")
    if v < 0:
        raise ValueError(f"ExecutionCostModel: {name} must be >= 0, got {v!r}.")
    if v >= _BP_CAP:
        raise ValueError(
            f"ExecutionCostModel: {name}={v} bp >= 10000 bp; likely decimal-mixup "
            "(pass basis points, e.g. 10.0 bp, not decimals like 0.001)."
        )
    return v


def _guard_price(value: float, name: str = "ref_price") -> float:
    try:
        v = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ExecutionCostModel: {name} must be numeric, got {value!r}.") from exc
    if not math.isfinite(v):
        raise ValueError(f"ExecutionCostModel: {name} must be finite, got {value!r}.")
    if v <= 0:
        raise ValueError(f"ExecutionCostModel: {name} must be > 0, got {v!r}.")
    return v


class ExecutionCostModel:
    """Fill-price + fee + funding math derived from a ``CostEstimate``."""

    def __init__(self, cost: CostEstimate) -> None:
        self.fee_bp = _guard_bp(cost.fee_bp, "fee_bp")
        self.spread_bp = _guard_bp(cost.spread_bp, "spread_bp")
        self.slippage_bp = _guard_bp(cost.slippage_bp, "slippage_bp")
        self.funding_bp = _guard_bp(cost.funding_bp, "funding_bp")

    def half_spread_plus_slip_bp(self) -> float:
        """Half-spread plus slippage in bp (the per-fill price penalty)."""
        return self.spread_bp / 2.0 + self.slippage_bp

    def fill_price(self, side: str, ref_price: float) -> float:
        """Cost-adjusted fill price for a ``BUY``/``SELL`` at ``ref_price``.

        BUY pays up (ref + penalty); SELL receives down (ref - penalty).
        Zero legs pass through exactly.
        """
        if side not in ("BUY", "SELL"):
            raise ValueError(f"fill_price: side must be BUY/SELL, got {side!r}.")
        ref = _guard_price(ref_price)
        penalty = self.half_spread_plus_slip_bp() / 1e4
        if side == "BUY":
            return ref * (1.0 + penalty)
        return ref * (1.0 - penalty)

    def fill_fee(self, notional: float) -> float:
        """Fee currency units: ``notional * fee_bp / 1e4``."""
        try:
            n = float(notional)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"fill_fee: notional must be numeric, got {notional!r}.") from exc
        if not math.isfinite(n):
            raise ValueError(f"fill_fee: notional must be finite, got {notional!r}.")
        if n < 0:
            raise ValueError(f"fill_fee: notional must be >= 0, got {notional!r}.")
        return n * self.fee_bp / 1e4

    def funding_for_bars(self, notional: float, bars: int) -> float:
        """Funding cost for ``bars`` held: ``notional * funding_bp/1e4 * bars``.

        ``funding_bp`` is documented as a per-bar rate; charged regardless
        of side (conservative assumption, see module docstring).
        """
        try:
            n = float(notional)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"funding_for_bars: notional must be numeric, got {notional!r}."
            ) from exc
        if not math.isfinite(n):
            raise ValueError(f"funding_for_bars: notional must be finite, got {notional!r}.")
        if n < 0:
            raise ValueError(f"funding_for_bars: notional must be >= 0, got {notional!r}.")
        if not isinstance(bars, int) or bars < 0:
            raise ValueError(f"funding_for_bars: bars must be a non-negative int, got {bars!r}.")
        return n * self.funding_bp / 1e4 * bars


__all__ = ["ExecutionCostModel"]
