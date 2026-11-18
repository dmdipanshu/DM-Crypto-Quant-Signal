"""Q7 Phase 4 & 6: Simulator, resilience, replay, and candidate isolation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from crypto_engine.paper_trading.resilience import (
    CheckpointManager,
    EventLedger,
    deterministic_replay,
)
from crypto_engine.paper_trading.simulator import (
    Simulator,
    SimulatorConfig,
    SimulatorResult,
)
from crypto_engine.paper_trading.strategy_adapters import (
    CandidateIsolationManager,
    create_producers,
)


def _bars(n: int = 12, start_px: float = 100.0, step: float = 1.0) -> list[dict]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(n):
        ts = base + timedelta(hours=i)
        px = start_px + step * i
        bars.append({
            "ts": ts.isoformat(),
            "open": px,
            "high": px + 2.0,
            "low": px - 2.0,
            "close": px,
        })
    return bars


def _config() -> SimulatorConfig:
    return SimulatorConfig(
        starting_equity=10000.0,
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        fee_bp=0.0,
        spread_bp=0.0,
        slippage_bp=0.0,
        funding_bp=0.0,
    )


def test_simulator_run_produces_result():
    sim = Simulator(_config(), [])
    result = sim.run(_bars())
    assert isinstance(result, SimulatorResult)
    assert result.n_bars == 12
    assert result.starting_equity == 10000.0
    assert result.final_equity == pytest.approx(10000.0)
    assert len(result.equity_curve) == 12


def test_simulator_deterministic():
    config = SimulatorConfig(
        starting_equity=10000.0,
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        fee_bp=10.0,
        spread_bp=4.0,
        slippage_bp=6.0,
        funding_bp=1.0,
    )
    sim1 = Simulator(config, [])
    sim2 = Simulator(config, [])
    r1 = sim1.run(_bars())
    r2 = sim2.run(_bars())
    assert r1.config_hash == r2.config_hash
    assert r1.n_bars == r2.n_bars
    assert r1.final_equity == r2.final_equity
    assert r1.net_pnl == r2.net_pnl
    assert r1.trades == r2.trades
    assert r1.equity_curve == r2.equity_curve


def test_simulator_records_fills():
    config = SimulatorConfig(
        starting_equity=10000.0,
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        fee_bp=10.0,
        spread_bp=4.0,
        slippage_bp=6.0,
        funding_bp=1.0,
    )
    sim = Simulator(config, [])
    result = sim.run(_bars())
    assert isinstance(result.fills, list)


def test_simulator_equity_curve():
    sim = Simulator(_config(), [])
    result = sim.run(_bars(12))
    assert len(result.equity_curve) == 12
    for ts, eq in result.equity_curve:
        assert isinstance(ts, str)
        assert isinstance(eq, (int, float))


def test_simulator_max_drawdown():
    sim = Simulator(_config(), [])
    result = sim.run(_bars())
    assert result.max_drawdown_pct == 0.0


def test_simulator_rejected_signals():
    sim = Simulator(_config(), [])
    result = sim.run(_bars())
    assert isinstance(result.rejected_signals, list)


def test_simulator_daily_loss_breach():
    config = SimulatorConfig(
        starting_equity=10000.0,
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        fee_bp=0.0,
        spread_bp=0.0,
        slippage_bp=0.0,
        funding_bp=0.0,
        max_daily_loss_pct=5.0,
    )
    sim = Simulator(config, [])
    result = sim.run(_bars())
    assert isinstance(result.daily_loss_breaches, list)


def test_event_ledger_chain_valid():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ledger = EventLedger(Path(tmp) / "ledger.jsonl")
        h1 = ledger.append_event("test", {"data": "a"})
        h2 = ledger.append_event("test", {"data": "b"})
        assert h1 is not None
        assert h2 is not None
        assert h1 != h2
        assert ledger.verify_chain() is True


def test_event_ledger_chain_broken():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "ledger.jsonl"
        ledger = EventLedger(ledger_path)
        ledger.append_event("test", {"data": "a"})
        raw = ledger_path.read_text(encoding="utf-8")
        ledger_path.write_text(raw.replace('"data": "a"', '"data": "tampered"'), encoding="utf-8")
        assert ledger.verify_chain() is False


def test_checkpoint_save_and_load():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mgr = CheckpointManager(Path(tmp))
        sim = Simulator(_config(), [])
        sim.run(_bars())
        cp = mgr.save_checkpoint(sim, bar_index=5, event_hash_chain="chain123")
        assert cp is not None
        assert cp.bar_index == 5
        loaded = mgr.load_latest_checkpoint()
        assert loaded is not None
        assert loaded.checkpoint_id == cp.checkpoint_id
        assert loaded.bar_index == cp.bar_index
        assert loaded.equity == cp.equity


def test_checkpoint_integrity():
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mgr = CheckpointManager(Path(tmp))
        sim = Simulator(_config(), [])
        sim.run(_bars())
        cp = mgr.save_checkpoint(sim, bar_index=5, event_hash_chain="chain123")
        cp_file = mgr._ckpt_path(cp.checkpoint_id)
        raw = json.loads(cp_file.read_text(encoding="utf-8"))
        raw["equity"] = 99999.0
        cp_file.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
        with pytest.raises(ValueError):
            mgr.load_latest_checkpoint()


def test_candidate_isolation():
    producers = create_producers()
    mgr = CandidateIsolationManager(producers)
    assert mgr.get_producer("majority") is not None
    assert mgr.get_producer("trend") is not None
    assert mgr.get_producer("logistic") is not None
    assert mgr.get_producer("lightgbm") is not None
    assert mgr.get_producer("majority") is not mgr.get_producer("trend")
    active = mgr.active_producers()
    active_ids = {p.candidate_id for p in active}
    assert "lightgbm" not in active_ids


def test_lightgbm_not_evaluable():
    producers = create_producers()
    lgbm = producers["lightgbm"]
    assert lgbm.status == "NOT_EVALUABLE"
    result = lgbm.produce(
        0,
        {"ts": "2024-01-01T00:00:00+00:00", "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0},
        {},
        "2024-01-01T00:00:00+00:00",
    )
    assert result is None


def test_simulator_same_input_same_output():
    sim1 = Simulator(_config(), [])
    sim2 = Simulator(_config(), [])
    r1 = sim1.run(_bars())
    r2 = sim2.run(_bars())
    assert r1.config_hash == r2.config_hash
    assert r1.final_equity == r2.final_equity


def test_deterministic_replay_positional_args():
    sim = Simulator(_config(), [])
    result = deterministic_replay(sim, _bars())
    assert isinstance(result, SimulatorResult)
    assert result.n_bars == 12


__all__ = [
    "test_candidate_isolation",
    "test_checkpoint_integrity",
    "test_checkpoint_save_and_load",
    "test_deterministic_replay_positional_args",
    "test_event_ledger_chain_broken",
    "test_event_ledger_chain_valid",
    "test_lightgbm_not_evaluable",
    "test_simulator_daily_loss_breach",
    "test_simulator_deterministic",
    "test_simulator_equity_curve",
    "test_simulator_max_drawdown",
    "test_simulator_records_fills",
    "test_simulator_rejected_signals",
    "test_simulator_run_produces_result",
    "test_simulator_same_input_same_output",
]



