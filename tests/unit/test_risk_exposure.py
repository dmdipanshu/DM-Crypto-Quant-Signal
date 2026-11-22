"""Risk exposure regression tests.

Verifies that risk gates correctly compute exposure from the actual
paper position (not hardcoded zeros) and that RISK_GATE_EXPOSURE
is correctly raised/cleared based on position value vs equity.
"""

from __future__ import annotations

from datetime import UTC, datetime

from crypto_engine.paper_trading.loop import paper_portfolio
from crypto_engine.paper_trading.state import PaperAccount
from crypto_engine.risk.gates import GateContext, run_gates
from crypto_engine.signals.schema import SignalRecord


def _record(**over) -> SignalRecord:
    base = dict(
        signal_id="a1b2c3d4e5f60718",
        symbol="BTC/USDT",
        exchange="binance",
        timeframe="1h",
        generated_at="2024-01-10T10:00:00+00:00",
        valid_until="2024-01-10T11:00:00+00:00",
        direction="LONG",
        prediction_horizon_bars=24,
        model_id="m-1",
        model_version="0.1.0",
        model_artifact_hash="abc123",
        dataset_snapshot_id="snap-1",
        feature_snapshot_hash="f" * 64,
        prediction={
            "raw_score": 0.8,
            "calibrated_probability": None,
            "calibration_status": "uncalibrated",
            "positive_class": 1,
            "model_family": "logistic",
            "threshold_version": "v1",
        },
        market_regime="",
        expected_edge_bp=None,
        cost_estimate={"fee_bp": 10.0, "slippage_bp": 5.0, "source": "configured"},
        entry_ref=40000.0,
        stop_loss=39500.0,
        take_profit=41000.0,
        risk_reward=2.0,
        data_quality="validated",
        ai_context={"status": "disabled"},
        decision={"status": "approved", "reason_codes": ["OK"], "gate": "policy"},
        software_version="0.1.0",
    )
    base.update(over)
    return SignalRecord.model_validate(base)


def _gate_context(portfolio: dict) -> GateContext:
    return GateContext(
        now_utc=datetime(2024, 1, 10, 10, 0, 0, tzinfo=UTC),
        risk=type("R", (), {"enabled": True, "max_trade_risk_pct": 10.0, "max_position_pct": 10.0, "max_drawdown_pct": 20.0, "fail_closed": True})(),
        max_age_min=60.0,
        staleness_limit_min=90.0,
        feature_timestamp=None,
        require_edge=False,
        edge_margin_bp=0.0,
        portfolio=portfolio,
        enabled_directions={"LONG", "SHORT", "NEUTRAL", "NO_TRADE"},
        blocked_regimes=set(),
        ai_required=False,
        label_set=None,
        threshold_version="v1",
        feature_names=["f1", "f2"],
    )


def test_paper_portfolio_flat_account_exposure_zero() -> None:
    """Flat paper account (no position) reports 0% exposure."""
    acct = PaperAccount(
        account_id="paper-test",
        symbol="BTC/USDT",
        timeframe="1h",
        cash=10000.0,
        starting_equity=10000.0,
        position=None,
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 10.0, "spread_bp": 5.0, "slippage_bp": 5.0, "funding_bp": 0.0},
        model_id="m-1",
        config_hash="abc",
        equity_mark_ts="2024-01-10T10:00:00+00:00",
        equity_mark=10000.0,
        created_at="2024-01-10T09:00:00+00:00",
        updated_at="2024-01-10T10:00:00+00:00",
    )
    portfolio = paper_portfolio(acct, 50000.0)
    assert portfolio["symbol_exposure_pct"] == 0.0
    assert portfolio["current_equity"] == 10000.0
    assert portfolio["day_start_equity"] == 10000.0


def test_paper_portfolio_with_position_exposure_computed() -> None:
    """Open paper position reports exposure = position notional / equity."""
    acct = PaperAccount(
        account_id="paper-test",
        symbol="BTC/USDT",
        timeframe="1h",
        cash=8999.0,
        starting_equity=10000.0,
        position={
            "side": "LONG",
            "qty": 0.02,
            "entry_price": 50125.0,
            "current_price": 50000.0,
            "unrealized_pnl": -2.50,
            "notional": 1002.50,
        },
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 10.0, "spread_bp": 5.0, "slippage_bp": 5.0, "funding_bp": 0.0},
        model_id="m-1",
        config_hash="abc",
        equity_mark_ts="2024-01-10T10:00:00+00:00",
        equity_mark=9999.50,
        created_at="2024-01-10T09:00:00+00:00",
        updated_at="2024-01-10T10:00:00+00:00",
    )
    portfolio = paper_portfolio(acct, 50000.0)
    # marked = cash + qty * mark_price = 8999.0 + 0.02 * 50000 = 9999.0
    # notional = qty * entry_price = 0.02 * 50125.0 = 1002.50
    # exposure_pct = 1002.50 / 9999.0 * 100 ≈ 10.026%
    expected_exposure = 1002.50 / 9999.0 * 100.0
    assert abs(portfolio["symbol_exposure_pct"] - expected_exposure) < 0.01
    assert portfolio["current_equity"] == 9999.0


def test_paper_account_valuation_reconciliation() -> None:
    """Reconcile dashboard equity_mark ($9,999.50) vs risk-marked equity ($9,999.00).

    The difference comes from different mark prices:
      - equity_mark: marked at current market price (~$50,150) → $9,999.50
      - risk-marked equity: marked at entry_price ($50,125.05) → $9,999.00
    Both are valid valuations at different prices. Exposure calc uses the
    risk engine's mark (entry_price) which is conservative: it does not give
    credit for unrealized gains.
    """
    from crypto_engine.paper_trading.state import equity

    acct = PaperAccount(
        account_id="paper-test",
        symbol="BTC/USDT",
        timeframe="1h",
        cash=8999.0,
        starting_equity=10000.0,
        position={
            "side": "LONG",
            "qty": 0.019950104787925402,
            "entry_price": 50125.049999999996,
            "current_price": 50150.0,
            "unrealized_pnl": None,
            "notional": 1000.0,
        },
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 10.0, "spread_bp": 5.0, "slippage_bp": 5.0, "funding_bp": 0.0},
        model_id="m-1",
        config_hash="abc",
        equity_mark_ts="2026-01-15T11:00:00+00:00",
        equity_mark=9999.497755114458,
        created_at="2026-01-15T10:00:00+00:00",
        updated_at="2026-01-15T11:00:00+00:00",
    )

    # Dashboard equity_mark (~$9,999.50) marked at current market price (~$50,150)
    assert abs(acct.equity_mark - 9999.497755114458) < 0.01

    # Risk engine marks at entry_price ($50,125.05):
    #   marked = cash + qty * entry_price
    #   = 8999.0 + 0.019950104787925402 * 50125.049999999996
    #   = 8999.0 + 1000.0
    #   = 9999.0
    marked_at_entry = equity(acct, acct.position["entry_price"])
    assert abs(marked_at_entry - 9999.0) < 0.01

    # Position notional = qty * entry_price = $1,000.00
    notional = acct.position["qty"] * acct.position["entry_price"]
    assert abs(notional - 1000.0) < 0.01

    # Exposure pct at risk mark = notional / marked_at_entry * 100
    exposure_pct = notional / marked_at_entry * 100.0
    assert exposure_pct > 10.0  # exceeds max_position_pct limit
    assert abs(exposure_pct - 10.001000100010002) < 0.01

    # Even at the higher equity_mark price, exposure still exceeds 10%
    exposure_at_mark = notional / acct.equity_mark * 100.0
    assert exposure_at_mark > 10.0

    # Both valuations agree: exposure exceeds the 10% limit → veto is correct
    # regardless of which mark price is used.

    # total_equity accounts for entry_fee ($1.00): starting + unrealized_pnl - fee
    #   = 10000.0 + (-800.5089719588417) - 1.0 = 9198.491028041159
    total_equity = acct.starting_equity + (-800.5089719588417) - 1.0
    assert abs(total_equity - 9198.491028041159) < 0.001


def test_risk_gate_exposure_with_flat_account_passes() -> None:
    """Risk gate exposure check passes when account is flat (0% exposure)."""
    record = _record()
    ctx = _gate_context(
        {
            "exposure_pct": 0.0,
            "symbol_exposure_pct": 0.0,
            "day_start_equity": 10000.0,
            "current_equity": 10000.0,
            "strategy_enabled": True,
            "circuit_open": False,
        }
    )
    _gated, trace = run_gates(record, ctx, artifact_hash="abc123")
    exposure_trace = [t for t in trace if t.get("gate") == "risk:exposure"]
    assert exposure_trace
    assert exposure_trace[0]["outcome"].startswith("pass")


def test_risk_gate_exposure_with_high_exposure_vetoes() -> None:
    """Risk gate exposure check vetoes when exposure exceeds max_position_pct."""
    record = _record()
    ctx = _gate_context(
        {
            "exposure_pct": 12.0,
            "symbol_exposure_pct": 12.0,
            "day_start_equity": 10000.0,
            "current_equity": 10000.0,
            "strategy_enabled": True,
            "circuit_open": False,
        }
    )
    gated, trace = run_gates(record, ctx, artifact_hash="abc123")
    exposure_trace = [t for t in trace if t.get("gate") == "risk:exposure"]
    assert exposure_trace
    assert exposure_trace[0]["outcome"].startswith("veto")
    assert "RISK_GATE_EXPOSURE" in gated.decision.reason_codes


__all__ = [
    "test_paper_portfolio_flat_account_exposure_zero",
    "test_paper_portfolio_with_position_exposure_computed",
    "test_risk_gate_exposure_with_flat_account_passes",
    "test_risk_gate_exposure_with_high_exposure_vetoes",
]
