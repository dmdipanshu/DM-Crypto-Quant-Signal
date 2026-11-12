"""Signal composition — deterministic combination of strategy outputs.

Takes per-strategy predictions and produces a single signal direction with
reason codes. Never invokes risk gates — separation of concerns.
"""

from __future__ import annotations

from dataclasses import dataclass

from crypto_engine.domain.direction import Direction
from crypto_engine.domain.reason_codes import ReasonCode


@dataclass(frozen=True)
class StrategyOutput:
    """One strategy's contribution to signal composition."""

    strategy_id: str
    direction: Direction
    confidence: float  # 0.0 .. 1.0
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComposedSignal:
    """Result of signal composition."""

    direction: Direction
    confidence: float
    reason_codes: tuple[str, ...]
    contributing_strategies: tuple[str, ...]


def compose_signal(
    outputs: list[StrategyOutput],
    min_confidence: float = 0.0,
    require_unanimous: bool = False,
) -> ComposedSignal:
    """Compose multiple strategy outputs into a single signal.

    Rules:
    - If no outputs, returns NO_TRADE with reason INSUFFICIENT_DATA.
    - Filters outputs below min_confidence.
    - If require_unanimous, all contributing strategies must agree on direction.
    - Direction is the weighted average of confidences per direction.
    - Reason codes are collected from all contributing strategies.

    Never invokes risk gates. Risk is applied later, independently.
    """
    if not outputs:
        return ComposedSignal(
            direction=Direction.NO_TRADE,
            confidence=0.0,
            reason_codes=(ReasonCode.INSUFFICIENT_DATA,),
            contributing_strategies=(),
        )

    # Filter by confidence
    eligible = [o for o in outputs if o.confidence >= min_confidence]
    if not eligible:
        return ComposedSignal(
            direction=Direction.NO_TRADE,
            confidence=0.0,
            reason_codes=(ReasonCode.INSUFFICIENT_EDGE,),
            contributing_strategies=tuple(o.strategy_id for o in outputs),
        )

    # Unanimity check
    if require_unanimous:
        directions = {o.direction for o in eligible}
        if len(directions) > 1:
            return ComposedSignal(
                direction=Direction.NO_TRADE,
                confidence=max(o.confidence for o in eligible),
                reason_codes=(ReasonCode.DISAGREEMENT,),
                contributing_strategies=tuple(o.strategy_id for o in eligible),
            )

    # Weighted direction: sum confidence per direction
    direction_scores: dict[Direction, float] = {}
    all_reasons: list[str] = []
    for o in eligible:
        direction_scores[o.direction] = direction_scores.get(o.direction, 0.0) + o.confidence
        all_reasons.extend(o.reason_codes)

    best_direction = max(direction_scores, key=direction_scores.get)
    total_confidence = sum(direction_scores.values())
    best_confidence = (
        direction_scores[best_direction] / total_confidence if total_confidence > 0 else 0.0
    )

    # Deduplicate reason codes, preserve order
    seen: set[str] = set()
    unique_reasons: list[str] = []
    for r in all_reasons:
        if r not in seen:
            seen.add(r)
            unique_reasons.append(r)

    return ComposedSignal(
        direction=best_direction,
        confidence=round(best_confidence, 4),
        reason_codes=tuple(unique_reasons),
        contributing_strategies=tuple(o.strategy_id for o in eligible),
    )


__all__ = ["ComposedSignal", "StrategyOutput", "compose_signal"]
