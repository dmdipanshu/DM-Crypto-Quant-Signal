"""Phase 6 Track A backtest fixtures (offline, deterministic, <=60 bars).

Handcrafted OHLC frames (monotone drift, reversal, gap) plus approved
LONG/SHORT/NEUTRAL/NO_TRADE ``SignalRecord`` builders. No network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def make_bars(
    closes: list[float],
    start: datetime = BASE,
    step_hours: int = 1,
    high_pad: float = 2.0,
    low_pad: float = 2.0,
) -> pd.DataFrame:
    """Build an OHLC frame from a close series (open = prev close, padded h/l)."""
    idx = [start + timedelta(hours=step_hours * i) for i in range(len(closes))]
    opens = [closes[0], *list(closes[:-1])]
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": [c + high_pad for c in closes],
            "low": [c - low_pad for c in closes],
            "close": closes,
            "volume": [10.0] * len(closes),
        },
        index=pd.DatetimeIndex(idx, tz=UTC),
    )
    return frame


def monotone_frame(n: int = 12, start_px: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    """Steady uptrend: close rises ``step`` per bar (8-20 bars typical)."""
    return make_bars([start_px + step * i for i in range(n)])


def reversal_frame() -> pd.DataFrame:
    """Rise 6 bars then fall 6 bars (12 bars; exercises TP then SL paths)."""
    ups = [100.0 + i for i in range(6)]
    downs = [105.0 - i for i in range(1, 7)]
    return make_bars(ups + downs)


def gap_frame() -> pd.DataFrame:
    """Frame with a gap-up open at bar 6 (open != prev close there)."""
    frame = monotone_frame(n=12)
    frame.iloc[6, frame.columns.get_loc("open")] = frame["close"].iloc[5] + 10.0
    frame.iloc[6, frame.columns.get_loc("high")] = frame["open"].iloc[6] + 2.0
    frame.iloc[6, frame.columns.get_loc("low")] = frame["close"].iloc[6] - 2.0
    return frame


def _signal(
    direction: str,
    bar_time: datetime,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    valid_hours: int = 5,
    entry: float | None = 100.0,
    stop: float | None = None,
    take: float | None = None,
    seed: int = 0,
) -> object:
    from crypto_engine.signals.schema import SignalRecord

    stop_d, take_d = stop, take
    if direction == "LONG" and stop_d is None and take_d is None and entry is not None:
        stop_d, take_d = entry * 0.97, entry * 1.05
    if direction == "SHORT" and stop_d is None and take_d is None and entry is not None:
        stop_d, take_d = entry * 1.03, entry * 0.95
    if direction in ("NEUTRAL", "NO_TRADE"):
        entry = None
        stop_d = take_d = None
    return SignalRecord(
        signal_id=f"{seed:016x}",
        symbol=symbol,
        exchange="binance",
        timeframe=timeframe,
        generated_at=bar_time,
        valid_until=bar_time + timedelta(hours=valid_hours),
        direction=direction,  # type: ignore[arg-type]
        prediction_horizon_bars=5,
        model_id="fixture-model",
        model_version="v1",
        model_artifact_hash="a" * 16,
        dataset_snapshot_id="snap",
        feature_snapshot_hash="b" * 16,
        expected_edge_bp=25.0 if direction in ("LONG", "SHORT") else None,
        entry_ref=entry,
        stop_loss=stop_d,
        take_profit=take_d,
        data_quality="validated",
        decision={
            "status": "approved" if direction in ("LONG", "SHORT") else "abstained",
            "reason_codes": ["OK"],
            "gate": "policy",
        },
        software_version="test",
    )


def long_signal(bar_time: datetime, **kw) -> object:
    """Approved LONG with valid stop/take geometry."""
    return _signal("LONG", bar_time, **kw)


def short_signal(bar_time: datetime, **kw) -> object:
    """Approved SHORT with valid stop/take geometry."""
    return _signal("SHORT", bar_time, **kw)


def neutral_signal(bar_time: datetime, **kw) -> object:
    """NEUTRAL (levels forced None)."""
    return _signal("NEUTRAL", bar_time, **kw)


def no_trade_signal(bar_time: datetime, **kw) -> object:
    """NO_TRADE with abstained decision."""
    sig = _signal("NO_TRADE", bar_time, **kw)
    return sig


def zero_cost() -> object:
    """CostEstimate with all legs zero (exact passthrough)."""
    from crypto_engine.signals.schema import CostEstimate

    return CostEstimate(
        fee_bp=0.0, spread_bp=0.0, slippage_bp=0.0, funding_bp=0.0, source="configured"
    )


def small_cost() -> object:
    """Hand-computable nonzero legs: fee 10bp, spread 4bp, slip 6bp, fund 1bp."""
    from crypto_engine.signals.schema import CostEstimate

    return CostEstimate(
        fee_bp=10.0,
        spread_bp=4.0,
        slippage_bp=6.0,
        funding_bp=1.0,
        source="configured",
    )


__all__ = [
    "gap_frame",
    "long_signal",
    "make_bars",
    "monotone_frame",
    "neutral_signal",
    "no_trade_signal",
    "paper_bar",
    "paper_gates_ctx",
    "paper_signal",
    "reversal_frame",
    "short_signal",
    "small_cost",
    "zero_cost",
]


# --- Phase 6 Track B additions (append-only): paper signal/bar/gate builders --
# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


def paper_signal(
    direction: str,
    generated_at,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    seed: int = 0,
    raw_score: float = 0.7,
    edge_bp: float | None = 25.0,
    entry: float | None = 100.0,
    stop: float | None = None,
    take: float | None = None,
):
    """Approved gate-passable signal for paper-step tests.

    Unlike Track A builders, sets ``prediction.raw_score`` (gates OUTPUT check
    vetoes None) with ``uncalibrated`` status (passes calibration gate; only
    ``mismatch`` vetoes). Step a strictly LATER bar (``signal.generated_at <
    bar.ts``, else ``STALE_FUTURE``).
    """
    from datetime import timedelta

    from crypto_engine.signals.schema import CostEstimate, SignalRecord

    stop_d, take_d = stop, take
    if direction == "LONG" and stop_d is None and take_d is None and entry is not None:
        stop_d, take_d = entry * 0.97, entry * 1.05
    if direction == "SHORT" and stop_d is None and take_d is None and entry is not None:
        stop_d, take_d = entry * 1.03, entry * 0.95
    if direction in ("NEUTRAL", "NO_TRADE"):
        entry = None
        stop_d = take_d = None
        edge_bp = None
    # Gate edge check needs a cost estimate on the record: fixed hand-computed
    # legs (total 21bp) under the default 25bp edge, so approved LONG/SHORT
    # fixtures pass gates with either zero or small execution costs.
    cost_est = CostEstimate(
        fee_bp=10.0, spread_bp=4.0, slippage_bp=6.0, funding_bp=1.0, source="configured"
    )
    return SignalRecord(
        signal_id=f"{seed:016x}",
        symbol=symbol,
        exchange="binance",
        timeframe=timeframe,
        generated_at=generated_at,
        valid_until=generated_at + timedelta(hours=5),
        direction=direction,  # type: ignore[arg-type]
        prediction_horizon_bars=5,
        model_id="fixture-model",
        model_version="v1",
        model_artifact_hash="a" * 16,
        dataset_snapshot_id="snap",
        feature_snapshot_hash="b" * 16,
        prediction={"raw_score": raw_score, "calibration_status": "uncalibrated"},
        expected_edge_bp=edge_bp,
        cost_estimate=cost_est if edge_bp is not None else None,
        entry_ref=entry,
        stop_loss=stop_d,
        take_profit=take_d,
        data_quality="validated",
        decision={
            "status": "approved" if direction in ("LONG", "SHORT") else "abstained",
            "reason_codes": ["OK"],
            "gate": "policy",
        },
        software_version="test",
    )


def paper_bar(ts, open_: float, high: float, low: float, close: float) -> dict:
    """Single bar dict for paper steps (ts may be datetime or ISO string)."""
    from datetime import UTC, datetime

    iso = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    if isinstance(ts, datetime) and ts.tzinfo is None:
        iso = ts.replace(tzinfo=UTC).isoformat()
    return {"ts": iso, "open": open_, "high": high, "low": low, "close": close}


def paper_gates_ctx(max_age_min: float = 120.0):
    """Caller-supplied gate context factory (generous age for unit tests)."""
    from crypto_engine.config.settings import RiskConfig
    from crypto_engine.paper_trading.loop import make_gates_ctx_fn

    return make_gates_ctx_fn(RiskConfig(), max_age_min=max_age_min, staleness_limit_min=600.0)
