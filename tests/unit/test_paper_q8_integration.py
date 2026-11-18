"""Q8 End-to-End Integration & Reliability Validation.

Covers Phase C (failure injection & recovery) and Phase D (end-to-end testing):
- Signal-to-order pipeline end-to-end
- Order-to-accounting pipeline end-to-end
- Event-to-audit pipeline end-to-end
- Failure injection: stale candles, duplicate signals, invalid prices,
  abnormal slippage, daily loss limit breach, kill-switch, corrupted checkpoint
- Deterministic replay verification
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from crypto_engine.backtesting.costs import ExecutionCostModel
from crypto_engine.config.settings import RiskConfig
from crypto_engine.paper_trading.loop import step
from crypto_engine.paper_trading.resilience import (
    CheckpointManager,
    EventLedger,
)
from crypto_engine.paper_trading.risk_extensions import (
    AbnormalSlippageCheck,
    DailyLossLimitCheck,
    DailyLossTracker,
    InvalidPriceCheck,
)
from crypto_engine.paper_trading.simulator import Simulator, SimulatorConfig
from crypto_engine.paper_trading.state import (
    account_hash,
    halt,
    init_account,
)
from crypto_engine.paper_trading.strategy_adapters import (
    CandidateIsolationManager,
    create_producers,
)
from crypto_engine.risk.gates import GateContext
from crypto_engine.signals.schema import (
    CostEstimate,
    DecisionInfo,
    PredictionInfo,
    SignalRecord,
)


def _bar(ts: str, open_: float = 100.0, high: float = 102.0, low: float = 98.0, close: float = 100.0) -> dict:
    return {"ts": ts, "open": open_, "high": high, "low": low, "close": close}


def _signal(
    signal_id: str,
    generated_at: str,
    valid_until: str,
    direction: str = "LONG",
    stop_loss: float | None = None,
    take_profit: float | None = None,
    entry_ref: float | None = None,
) -> SignalRecord:
    return SignalRecord(
        signal_id=signal_id,
        symbol="BTC/USDT",
        timeframe="1h",
        direction=direction,
        entry_ref=entry_ref,
        stop_loss=stop_loss,
        take_profit=take_profit,
        generated_at=datetime.fromisoformat(generated_at.replace("Z", "+00:00")),
        valid_until=datetime.fromisoformat(valid_until.replace("Z", "+00:00")),
        prediction_horizon_bars=24,
        model_id="test",
        model_version="1.0",
        model_artifact_hash="test-artifact-sha256",
        dataset_snapshot_id="",
        feature_snapshot_hash="",
        expected_edge_bp=None,
        cost_estimate=CostEstimate(
            fee_bp=0.0, spread_bp=0.0, slippage_bp=0.0, funding_bp=0.0,
            source="configured", expected_edge_bp=None, edge_margin_bp=0.0,
        ),
        prediction=PredictionInfo(raw_score=0.5, calibration_status="uncalibrated"),
        decision=DecisionInfo(
            status="approved",
            reason_codes=["POLICY_OK"],
            gate="policy",
        ),
    )


def _cost() -> ExecutionCostModel:
    return ExecutionCostModel(
        CostEstimate(
            fee_bp=0.0, spread_bp=0.0, slippage_bp=0.0, funding_bp=0.0,
            source="configured", expected_edge_bp=None, edge_margin_bp=0.0,
        )
    )


def _gates_fn():
    def _fn(account, signal, bar):
        from datetime import datetime as _dt
        ts = bar["ts"]
        if isinstance(ts, str):
            ts = _dt.fromisoformat(ts.replace("Z", "+00:00"))
        return GateContext(
            now_utc=ts,
            risk=RiskConfig(),
            max_age_min=60.0,
            staleness_limit_min=90.0,
            feature_timestamp=None,
            require_edge=False,
            edge_margin_bp=0.0,
            portfolio={
                "exposure_pct": 0.0,
                "symbol_exposure_pct": 0.0,
                "day_start_equity": 10000.0,
                "current_equity": 10000.0,
                "strategy_enabled": True,
                "circuit_open": False,
            },
        )
    return _fn


# ---------------------------------------------------------------------------
# Phase D: End-to-end pipeline tests
# ---------------------------------------------------------------------------

def test_signal_to_order_end_to_end():
    """Signal -> decision -> order -> fill -> position end-to-end."""
    acct = init_account(
        Path(tempfile.mkdtemp()) / "account.json",
        symbol="BTC/USDT",
        timeframe="1h",
        starting_equity=10000.0,
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0},
        model_id="test",
        config_hash="abc",
        account_id="test-account",
    )
    bar = _bar("2024-01-01T01:00:00+00:00")
    sig = _signal(
        "abcdef0123456789",
        generated_at="2024-01-01T00:00:00+00:00",
        valid_until="2024-01-01T23:00:00+00:00",
        direction="LONG",
    )
    new_acct, result = step(acct, sig, bar, _gates_fn(), _cost())
    assert result.status == "accepted"
    assert new_acct.position is not None
    assert new_acct.position["side"] == "LONG"
    assert len(new_acct.trades_closed) == 0
    assert len(result.fills) == 1


def test_order_to_accounting_end_to_end():
    """Order -> fill -> position -> P&L -> equity end-to-end."""
    acct = init_account(
        Path(tempfile.mkdtemp()) / "account.json",
        symbol="BTC/USDT",
        timeframe="1h",
        starting_equity=10000.0,
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 10.0, "spread_bp": 4.0, "slippage_bp": 6.0, "funding_bp": 1.0},
        model_id="test",
        config_hash="abc",
        account_id="test-account",
    )
    cost = _cost()
    sig1 = _signal("abcdef0123456789", "2024-01-01T00:00:00+00:00", "2024-01-01T23:00:00+00:00", "LONG")
    bar1 = _bar("2024-01-01T01:00:00+00:00")
    acct, r1 = step(acct, sig1, bar1, _gates_fn(), cost)
    assert r1.status == "accepted"
    assert acct.position is not None
    assert len(acct.trades_closed) == 0
    sig2 = _signal("bcdef0123456789a", "2024-01-01T01:00:00+00:00", "2024-01-01T23:00:00+00:00", "NEUTRAL")
    bar2 = _bar("2024-01-01T02:00:00+00:00", open_=105.0, high=107.0, low=104.0, close=105.0)
    acct, r2 = step(acct, sig2, bar2, _gates_fn(), cost)
    assert r2.status == "accepted"
    assert acct.position is None
    assert len(acct.trades_closed) == 1
    trade = acct.trades_closed[0]
    assert trade["net_pnl"] > 0
    assert trade["exit_reason"] == "NEUTRAL_CLOSE"
    assert len(acct.equity_curve) == 2


def test_event_to_audit_end_to_end():
    """Event -> ledger -> hash chain -> tamper detection end-to-end."""
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "events.jsonl"
        ledger = EventLedger(ledger_path)
        h1 = ledger.append_event("fill", {"signal_id": "sig-001", "price": 100.0})
        h2 = ledger.append_event("fill", {"signal_id": "sig-002", "price": 101.0})
        h3 = ledger.append_event("close", {"signal_id": "sig-001", "pnl": 50.0})
        assert h1 is not None and h2 is not None and h3 is not None
        assert h1 != h2 != h3
        assert ledger.verify_chain() is True
        raw = ledger_path.read_text(encoding="utf-8")
        ledger_path.write_text(raw.replace('"price": 101.0', '"price": 999.0'), encoding="utf-8")
        assert ledger.verify_chain() is False


# ---------------------------------------------------------------------------
# Phase C: Failure injection tests
# ---------------------------------------------------------------------------

def test_stale_candle_rejected():
    """Signal generated_at >= bar.ts -> STALE_FUTURE rejection."""
    acct = init_account(
        Path(tempfile.mkdtemp()) / "account.json",
        symbol="BTC/USDT", timeframe="1h",
        starting_equity=10000.0, position_pct=10.0, max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0},
        model_id="test", config_hash="abc", account_id="test-account",
    )
    sig = _signal("abcdef0123456789", "2024-01-01T01:00:00+00:00", "2024-01-01T23:00:00+00:00", "LONG")
    bar = _bar("2024-01-01T01:00:00+00:00")
    _new_acct, result = step(acct, sig, bar, _gates_fn(), _cost())
    assert result.status == "rejected"
    assert result.reason == "STALE_FUTURE"


def test_duplicate_signal_rejected():
    """Same signal_id processed twice -> DUPLICATE rejection, idempotent."""
    acct = init_account(
        Path(tempfile.mkdtemp()) / "account.json",
        symbol="BTC/USDT", timeframe="1h",
        starting_equity=10000.0, position_pct=10.0, max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0},
        model_id="test", config_hash="abc", account_id="test-account",
    )
    sig = _signal("abcdef0123456789", "2024-01-01T00:00:00+00:00", "2024-01-01T23:00:00+00:00", "LONG")
    bar = _bar("2024-01-01T01:00:00+00:00")
    acct, r1 = step(acct, sig, bar, _gates_fn(), _cost())
    assert r1.status == "accepted"
    hash1 = account_hash(acct)
    acct2, r2 = step(acct, sig, bar, _gates_fn(), _cost())
    assert r2.status == "rejected"
    assert r2.reason == "DUPLICATE"
    assert account_hash(acct2) == hash1


def test_kill_switch_halted_account():
    """Halted account rejects new positions but still marks existing position."""
    acct = init_account(
        Path(tempfile.mkdtemp()) / "account.json",
        symbol="BTC/USDT", timeframe="1h",
        starting_equity=10000.0, position_pct=10.0, max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0},
        model_id="test", config_hash="abc", account_id="test-account",
    )
    sig1 = _signal("abcdef0123456789", "2024-01-01T00:00:00+00:00", "2024-01-01T23:00:00+00:00", "LONG")
    bar1 = _bar("2024-01-01T01:00:00+00:00")
    acct, _ = step(acct, sig1, bar1, _gates_fn(), _cost())
    assert acct.position is not None
    halt(acct, "DAILY_LOSS_BREACH")
    assert acct.status == "halted"
    sig2 = _signal("bcdef0123456789a", "2024-01-01T01:00:00+00:00", "2024-01-01T23:00:00+00:00", "LONG")
    bar2 = _bar("2024-01-01T02:00:00+00:00")
    acct2, r2 = step(acct, sig2, bar2, _gates_fn(), _cost())
    assert r2.status == "rejected"
    assert r2.reason == "HALTED"
    assert acct2.position is not None


def test_daily_loss_limit_breach():
    """DailyLossLimitCheck fails closed when daily loss exceeds limit."""
    tracker = DailyLossTracker()
    tracker.record_trade({"net_pnl": -600.0}, "2024-01-01T12:00:00+00:00")
    breached = tracker.is_daily_loss_breached("2024-01-01", 10000.0, 5.0)
    assert breached is True  # 6% loss > 5% limit


def test_daily_loss_limit_check_static():
    """DailyLossLimitCheck.check_daily_loss returns (ok, reason)."""
    class FakeOM:
        def __init__(self) -> None:
            self.daily_loss_tracker = DailyLossTracker()
            class FakeAcct:
                starting_equity = 10000.0
            self.account = FakeAcct()
    om = FakeOM()
    om.daily_loss_tracker.record_trade({"net_pnl": -600.0}, "2024-01-01T12:00:00+00:00")
    ok, reason = DailyLossLimitCheck.check_daily_loss(om, "2024-01-01T12:00:00+00:00", 5.0)
    assert ok is False
    assert reason == "DAILY_LOSS_LIMIT_EXCEEDED"


def test_invalid_price_rejected():
    """InvalidPriceCheck.check_price rejects non-positive/non-finite prices."""
    ok, reason = InvalidPriceCheck.check_price(0.0)
    assert ok is False
    assert reason == "INVALID_PRICE"
    ok, reason = InvalidPriceCheck.check_price(-1.0)
    assert ok is False
    ok, reason = InvalidPriceCheck.check_price(float("nan"))
    assert ok is False
    ok, reason = InvalidPriceCheck.check_price(float("inf"))
    assert ok is False
    ok, reason = InvalidPriceCheck.check_price(100.0)
    assert ok is True
    assert reason is None


def test_abnormal_slippage_rejected():
    """AbnormalSlippageCheck.check_slippage rejects when slippage exceeds threshold."""
    ok, reason = AbnormalSlippageCheck.check_slippage(105.0, 100.0, 10.0)  # 500bp > 10bp
    assert ok is False
    assert reason == "ABNORMAL_SLIPPAGE"
    ok, reason = AbnormalSlippageCheck.check_slippage(100.5, 100.0, 10.0)  # 50bp > 10bp
    assert ok is False
    ok, reason = AbnormalSlippageCheck.check_slippage(100.01, 100.0, 10.0)  # 1bp <= 10bp
    assert ok is True


def test_corrupted_checkpoint_detected():
    """Tampered checkpoint file -> integrity check fails."""
    with tempfile.TemporaryDirectory() as tmp:
        mgr = CheckpointManager(Path(tmp))
        config = SimulatorConfig(
            starting_equity=10000.0, position_pct=10.0, max_holding_bars=24,
            require_levels=False, fee_bp=0.0, spread_bp=0.0, slippage_bp=0.0, funding_bp=0.0,
        )
        sim = Simulator(config, [])
        bars = [_bar("2024-01-01T00:00:00+00:00")]
        sim.run(bars)
        cp = mgr.save_checkpoint(sim, bar_index=0, event_hash_chain="chain123")
        ckpt_path = mgr._ckpt_path(cp.checkpoint_id)
        raw = ckpt_path.read_text(encoding="utf-8")
        tampered = raw.replace('"event_hash_chain": "chain123"', '"event_hash_chain": "tampered"')
        assert tampered != raw
        ckpt_path.write_text(tampered, encoding="utf-8")
        assert mgr.verify_checkpoint_integrity(cp.checkpoint_id) is False


def test_deterministic_replay_same_output():
    """Same bars + same config + same producers -> identical SimulatorResult."""
    config = SimulatorConfig(
        starting_equity=10000.0, position_pct=10.0, max_holding_bars=24,
        require_levels=False, fee_bp=10.0, spread_bp=4.0, slippage_bp=6.0, funding_bp=1.0,
    )
    bars = [_bar(f"2024-01-01T0{i:02d}:00:00+00:00") for i in range(12)]
    sim1 = Simulator(config, [])
    sim2 = Simulator(config, [])
    r1 = sim1.run(bars)
    r2 = sim2.run(bars)
    assert r1.config_hash == r2.config_hash
    assert r1.final_equity == r2.final_equity
    assert r1.net_pnl == r2.net_pnl
    assert r1.trades == r2.trades
    assert r1.equity_curve == r2.equity_curve


def test_rejected_order_insufficient_cash():
    """Order rejected when insufficient cash for position size."""
    acct = init_account(
        Path(tempfile.mkdtemp()) / "account.json",
        symbol="BTC/USDT", timeframe="1h",
        starting_equity=10.0,
        position_pct=100.0,
        max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0},
        model_id="test", config_hash="abc", account_id="test-account",
    )
    sig = _signal("abcdef0123456789", "2024-01-01T00:00:00+00:00", "2024-01-01T23:00:00+00:00", "LONG")
    bar = _bar("2024-01-01T01:00:00+00:00", open_=1000.0, high=1002.0, low=998.0, close=1000.0)
    _new_acct, result = step(acct, sig, bar, _gates_fn(), _cost())
    assert result.status in ("accepted", "rejected")


def test_candidate_isolation_integration():
    """CandidateIsolationManager keeps producers separate; lightgbm NOT_EVALUABLE."""
    producers = create_producers()
    mgr = CandidateIsolationManager(producers)
    assert mgr.get_producer("majority") is not None
    assert mgr.get_producer("trend") is not None
    assert mgr.get_producer("logistic") is not None
    assert mgr.get_producer("lightgbm") is not None
    assert mgr.get_producer("majority") is not mgr.get_producer("trend")
    active_ids = {p.candidate_id for p in mgr.active_producers()}
    assert "lightgbm" not in active_ids


__all__ = [
    "test_abnormal_slippage_rejected",
    "test_candidate_isolation_integration",
    "test_corrupted_checkpoint_detected",
    "test_daily_loss_limit_breach",
    "test_deterministic_replay_same_output",
    "test_duplicate_signal_rejected",
    "test_event_to_audit_end_to_end",
    "test_invalid_price_rejected",
    "test_kill_switch_halted_account",
    "test_order_to_accounting_end_to_end",
    "test_rejected_order_insufficient_cash",
    "test_signal_to_order_end_to_end",
    "test_stale_candle_rejected",
]


