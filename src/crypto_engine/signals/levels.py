"""Level validation + suggestion helpers (Phase 5 Track A).

``validate_levels`` enforces direction geometry (LONG: stop < entry,
take > entry; SHORT mirrored; NEUTRAL/NO_TRADE: all None) and applies
conservative tick rounding when ``tick_size`` is given (entry rounds
to the tick, stop rounds DOWN for LONG / UP for SHORT so the stop
only ever tightens). ``suggest_levels`` is pure math
(``entry * (1 +/- mult * vol)``) marked estimate-only — never a fill
guarantee; execution belongs to Phase 6.

4 GB laptop: pure math, no network, no randomness.
"""

from __future__ import annotations

import math

from crypto_engine.signals.schema import SignalLevels


def _round_tick(value: float, tick: float, mode: str) -> float:
    if mode == "nearest":
        return round(value / tick) * tick
    if mode == "down":
        return math.floor(value / tick) * tick
    if mode == "up":
        return math.ceil(value / tick) * tick
    raise ValueError(f"unknown rounding mode {mode!r}.")


def validate_levels(
    direction: str,
    entry: float | None,
    stop: float | None,
    take: float | None,
    tick_size: float | None = None,
) -> tuple[SignalLevels | None, str | None]:
    """Validate (and optionally tick-round) levels for a direction.

    Returns ``(levels, None)`` on success or ``(None, error)`` on
    violation — never raises for geometry (raises only for a bad
    ``tick_size`` or unknown direction). Take-profit is optional
    (``None`` allowed for runners); entry/stop follow the same
    optionality but geometry applies whenever both sides present.
    """
    if direction not in ("LONG", "SHORT", "NEUTRAL", "NO_TRADE"):
        raise ValueError(f"validate_levels: unknown direction {direction!r}.")
    if tick_size is not None:
        try:
            tick = float(tick_size)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"validate_levels: tick_size must be numeric, got {tick_size!r}."
            ) from exc
        if not math.isfinite(tick) or tick <= 0:
            raise ValueError(f"validate_levels: tick_size must be > 0, got {tick_size!r}.")
        if entry is not None:
            entry = _round_tick(float(entry), tick, "nearest")
        if stop is not None:
            stop = _round_tick(float(stop), tick, "down" if direction == "LONG" else "up")
        if take is not None:
            take = _round_tick(float(take), tick, "nearest")
    for name, val in (("entry", entry), ("stop", stop), ("take", take)):
        if val is not None and (not math.isfinite(float(val)) or float(val) <= 0):
            return None, f"{name} must be > 0 and finite (got {val!r})."
    try:
        levels = SignalLevels(
            direction=direction,  # type: ignore[arg-type]
            entry_ref=entry,
            stop_loss=stop,
            take_profit=take,
        )
    except ValueError as exc:
        return None, str(exc)
    return levels, None


def suggest_levels(
    direction: str,
    entry: float,
    atr_or_vol: float,
    pt_mult: float = 2.0,
    sl_mult: float = 1.0,
) -> dict[str, float]:
    """Suggest stop/take from volatility multiples (estimate-only math).

    LONG: ``stop = entry * (1 - sl_mult * vol)``,
    ``take = entry * (1 + pt_mult * vol)``; SHORT mirrored.
    Raises on non-positive inputs. Never a fill guarantee.
    """
    if direction not in ("LONG", "SHORT"):
        raise ValueError(f"suggest_levels: direction must be LONG/SHORT, got {direction!r}.")
    for name, val in (
        ("entry", entry),
        ("atr_or_vol", atr_or_vol),
        ("pt_mult", pt_mult),
        ("sl_mult", sl_mult),
    ):
        if not isinstance(val, (int, float)) or not math.isfinite(float(val)) or float(val) <= 0:
            raise ValueError(f"suggest_levels: {name} must be > 0 finite, got {val!r}.")
    vol = float(atr_or_vol)
    if direction == "LONG":
        stop = float(entry) * (1.0 - float(sl_mult) * vol)
        take = float(entry) * (1.0 + float(pt_mult) * vol)
    else:
        stop = float(entry) * (1.0 + float(sl_mult) * vol)
        take = float(entry) * (1.0 - float(pt_mult) * vol)
    if stop <= 0 or take <= 0:
        raise ValueError(
            f"suggest_levels: multiples imply non-positive level (stop={stop!r}, take={take!r})."
        )
    risk = abs(float(entry) - stop)
    reward = abs(take - float(entry))
    return {
        "entry_ref": float(entry),
        "stop_loss": stop,
        "take_profit": take,
        "risk_reward": (reward / risk) if risk > 0 else 0.0,
    }


__all__ = ["suggest_levels", "validate_levels"]
