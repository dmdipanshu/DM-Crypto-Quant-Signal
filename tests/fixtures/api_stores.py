"""Populated read-only store fixtures for API/dashboard tests (Phase 7).

Builds a complete tmp repo slice: signal history (approved LONG, approved
NEUTRAL, NO_TRADE, rejected SHORT, one expired LONG), model registry (one
fitted logistic via save_model), one dataset snapshot, two experiments, a
pristine holdout ledger, one backtest run (real engine), one paper account.
All offline, deterministic, <= 60 bars.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path


def build_api_stores(root: Path) -> dict[str, Path]:
    """Populate and return named store paths under ``root``."""
    from crypto_engine.datasets.snapshots import save_snapshot
    from crypto_engine.paper_trading.state import init_account
    from crypto_engine.signals.history import SignalHistory
    from crypto_engine.validation.experiments import ExperimentRegistry
    from crypto_engine.validation.holdout import HoldoutLedger
    from tests.fixtures import backtest_fixtures as fx

    paths = {
        "signals": root / "signals",
        "registry": root / "registry",
        "storage": root / "data",
        "backtests": root / "backtests",
        "paper": root / "paper" / "account.json",
        "experiments": root / "reports" / "experiments.jsonl",
        "holdout": root / "reports" / "holdout.json",
    }
    paths["signals"].mkdir(parents=True, exist_ok=True)
    paths["backtests"].mkdir(parents=True, exist_ok=True)
    paths["experiments"].parent.mkdir(parents=True, exist_ok=True)

    # -- dataset snapshot -------------------------------------------------
    bars = fx.make_bars([100.0 + i for i in range(30)])
    from crypto_engine.data.validation import normalize_candles

    raw = [
        [int(ts.timestamp() * 1000), r.open, r.high, r.low, r.close, r.volume]
        for ts, r in zip(bars.index, bars.itertuples(index=False), strict=True)
    ]
    candles = normalize_candles(raw)
    save_snapshot(
        candles,
        {
            "exchange": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "requested_start": None,
            "requested_end": None,
            "actual_start": None,
            "actual_end": None,
            "missing_count": 0,
            "warnings": [],
            "source": "fixture",
        },
        paths["storage"],
    )

    # -- model registry ----------------------------------------------------
    from tests.fixtures.signal_fixtures import make_registry_model_dir

    make_registry_model_dir(paths["registry"], seed=0)

    # -- signal history ----------------------------------------------------
    from crypto_engine.signals.schema import DecisionInfo, SignalRecord

    def _rec(
        direction: str,
        gen: datetime,
        status: str,
        seed: int,
        reasons: list[str] | None = None,
    ) -> SignalRecord:
        entry = 100.0 + (gen.hour - 10)
        base = {
            "signal_id": f"{seed:016x}",
            "symbol": "BTC/USDT",
            "exchange": "binance",
            "timeframe": "1h",
            "generated_at": gen,
            "valid_until": gen + timedelta(hours=5),
            "direction": direction,
            "prediction_horizon_bars": 5,
            "model_id": "fixture-model",
            "model_version": "v1",
            "model_artifact_hash": "a" * 16,
            "dataset_snapshot_id": "snap",
            "feature_snapshot_hash": "b" * 16,
            "data_quality": "validated",
            "decision": DecisionInfo(status=status, reason_codes=reasons or ["OK"], gate="policy"),  # type: ignore[arg-type]
            "software_version": "test",
        }
        if direction == "LONG":
            base.update(
                {"entry_ref": entry, "stop_loss": entry * 0.97, "take_profit": entry * 1.05}
            )
        elif direction == "SHORT":
            base.update(
                {"entry_ref": entry, "stop_loss": entry * 1.03, "take_profit": entry * 0.95}
            )
        return SignalRecord(**base)  # type: ignore[arg-type]

    hist = SignalHistory(paths["signals"])
    now = datetime.now(UTC)
    hist.append(_rec("LONG", now - timedelta(minutes=30), "approved", 1, ["OK"]))
    hist.append(_rec("NEUTRAL", now - timedelta(minutes=90), "approved", 2, ["THRESHOLD_NOT_MET"]))
    hist.append(_rec("NO_TRADE", now - timedelta(minutes=120), "abstained", 3, ["UNCALIBRATED"]))
    hist.append(_rec("SHORT", now - timedelta(minutes=150), "rejected", 4, ["RISK_GATE_EDGE"]))
    hist.append(_rec("LONG", now - timedelta(days=2), "approved", 5, ["OK"]))

    # -- experiments + holdout ---------------------------------------------
    registry = ExperimentRegistry(paths["experiments"])
    base_exp = {
        "dataset_snapshot": "snap",
        "dataset_checksum": "abc123",
        "model_version": "fixture-model v1",
        "feature_schema": ["ret_1"],
        "label_def": {"kind": "triple-barrier"},
        "periods": {"train": "2024-01-01/2024-01-10"},
        "calibration": {"method": "sigmoid"},
        "hyperparams": {},
        "thresholds": {"t_long": 0.6},
        "metrics": {"accuracy": 0.52},
        "counts": {"n": 100},
        "software_versions": {"test": "1.0"},
        "seeds": [0],
        "holdout_status": {"used": False},
        "limitations": ["fixture"],
    }
    registry.record(
        {
            **base_exp,
            "config": {"family": "logistic", "folds": 2},
            "code_version": "test-v1",
            "status": "success",
            "n_trials": 1,
            "protocol": "walk-forward",
        }
    )
    registry.record(
        {
            **base_exp,
            "config": {"family": "trend", "folds": 2},
            "code_version": "test-v1",
            "status": "failed",
            "n_trials": 1,
            "protocol": "walk-forward",
        }
    )
    HoldoutLedger.create(paths["holdout"], holdout_start=250, n_total=300, holdout_frac=0.15)

    # -- backtest run -------------------------------------------------------
    from crypto_engine.backtesting.engine import run_backtest
    from crypto_engine.config.settings import BacktestConfig
    from crypto_engine.signals.schema import CostEstimate

    sig = fx.long_signal(bars.index[2], seed=11, entry=102.0, stop=98.0, take=110.0)
    cost = CostEstimate(
        fee_bp=10.0, spread_bp=5.0, slippage_bp=5.0, funding_bp=0.0, source="configured"
    )
    result = run_backtest(bars, [sig], BacktestConfig(), cost)
    run_dir = paths["backtests"] / "run-20240101-btc-1h"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")

    # -- paper account ------------------------------------------------------
    account = init_account(
        paths["paper"],
        symbol="BTC/USDT",
        timeframe="1h",
        starting_equity=10000.0,
        position_pct=10.0,
        max_holding_bars=24,
        require_levels=False,
        cost={"fee_bp": 10.0, "spread_bp": 5.0, "slippage_bp": 5.0, "funding_bp": 0.0},
        model_id="fixture-model",
        config_hash="cfg-test",
    )
    from crypto_engine.paper_trading.state import save_account

    save_account(account, paths["paper"])

    return paths


__all__ = ["build_api_stores"]
