"""Shared fixtures for offline tests (no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from crypto_engine.data.types import Candle
from crypto_engine.data.validation import normalize_candles


def sample_rows(
    start_ms: int = 1_700_000_000_000, n: int = 10, step_ms: int = 3_600_000
) -> list[list[Any]]:
    """Build ``n`` deterministic 1h ccxt-style rows for BTC/USDT."""
    rows: list[list[Any]] = []
    price = 40_000.0
    for i in range(n):
        ms = start_ms + i * step_ms
        o = price + i * 10.0
        h = o + 50.0
        low = o - 40.0
        c = o + 5.0
        v = 10.0 + i
        rows.append([ms, o, h, low, c, v])
    return rows


@pytest.fixture
def raw_rows() -> list[list[Any]]:
    """Ten valid 1h candles."""
    return sample_rows()


@pytest.fixture
def sample_candles(raw_rows: list[list[Any]]) -> list[Candle]:
    """Validated sample candles."""
    return normalize_candles(raw_rows)


@pytest.fixture
def tmp_storage(tmp_path: Path) -> Path:
    """Isolated storage root."""
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    return storage


@pytest.fixture
def repo_root() -> Path:
    """Discover the repo root (dir containing pyproject.toml)."""
    current = Path(__file__).resolve()
    for candidate in (current.parent, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise AssertionError("repo root not found")


@pytest.fixture
def default_config_path(repo_root: Path) -> Path:
    """Path to config/default.toml."""
    return repo_root / "config" / "default.toml"


def _dt(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# --- Track A additions (append-only): shared OHLCV frame fixtures ---------
# NOTE: appended 2026-10-27 by Track A; existing fixtures above untouched.


@pytest.fixture
def ohlcv_frame_120():
    """Deterministic 120-row UTC OHLCV frame (Track A shared fixture)."""
    from tests.fixtures.features_fixture import make_ohlcv_frame

    return make_ohlcv_frame(n=120, seed=42)


@pytest.fixture
def ohlcv_frame_300():
    """Deterministic 300-row UTC OHLCV frame (max Track A fixture size)."""
    from tests.fixtures.features_fixture import make_ohlcv_frame

    return make_ohlcv_frame(n=300, seed=42)


# --- Track B additions (append-only): supervised small-frame fixtures ----
# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


@pytest.fixture
def supervised_frame_200():
    """Deterministic 200-row (features, labels) pair for Track B model tests."""
    import numpy as np

    from crypto_engine.features.engineering import compute_features
    from tests.fixtures.features_fixture import make_ohlcv_frame

    df = make_ohlcv_frame(n=200, seed=7)
    feats = compute_features(df).dropna()
    cols = ["ret_1", "trend_ratio_20_50", "rsi_14", "realized_vol_20"]
    X = feats[cols]
    y = np.where(X["trend_ratio_20_50"].to_numpy() > 0, 1, -1).astype(np.int64)
    return X, y


@pytest.fixture
def registry_meta():
    """Minimal provenance meta for Track B registry tests."""
    return {
        "dataset_snapshot_id": "fixture-snap",
        "dataset_checksum": "fixture1234",
        "label_config": {"pt_mult": 2.0, "sl_mult": 1.0},
        "label_hash": "fixturelbl",
        "train_start": "2024-01-01T00:00:00+00:00",
        "train_end": "2024-01-05T00:00:00+00:00",
    }


# --- Track A calibration additions (append-only): synthetic score fixtures --
# NOTE: appended 2026-10-27 by Track A (calibration); existing fixtures above untouched.


@pytest.fixture
def calibration_synthetic_300():
    """Deterministic miscalibrated-score splits: 200 cal + 100 test rows (seed 0)."""
    import numpy as np
    from scipy.special import expit

    rng = np.random.default_rng(0)
    z_cal = rng.normal(size=200)
    z_test = rng.normal(size=100)
    p_cal, p_test = expit(z_cal), expit(z_test)
    s_cal = np.clip(p_cal + 0.25, 0.0, 1.0)
    s_test = np.clip(p_test + 0.25, 0.0, 1.0)
    y_cal = (rng.uniform(size=200) < p_cal).astype(int)
    y_test = (rng.uniform(size=100) < p_test).astype(int)
    y_cal[0], y_cal[1] = 0, 1
    y_test[0], y_test[1] = 0, 1
    return {"scores_cal": s_cal, "y_cal": y_cal, "scores_test": s_test, "y_test": y_test}


@pytest.fixture
def fitted_logistic_base():
    """Tiny fitted binary SklearnLogistic base (60 rows, seed 0) for calibrator tests."""
    import numpy as np
    import pandas as pd

    from crypto_engine.models.sklearn_wrappers import SklearnLogistic

    rng = np.random.default_rng(0)
    x = rng.normal(size=(60, 2))
    X = pd.DataFrame(x, columns=["a", "b"])
    y = (x[:, 0] > 0).astype(int)
    return SklearnLogistic(feature_names=["a", "b"]).fit(X, y)


# --- Track A AI additions (append-only): provider fixtures ------------------
# NOTE: appended 2026-10-27 by Track A; existing fixtures above untouched.


@pytest.fixture
def ai_request():
    """Minimal valid AiRequest for provider tests."""
    from crypto_engine.ai_providers.types import AiRequest

    return AiRequest(
        provider_id="openai",
        model_id="gpt-4o-mini",
        task="Assess trend.",
        evidence={"rsi": 55.0},
        schema_name="analysis",
        timeout_s=30.0,
        max_retries=2,
        correlation_id="corr-1",
    )


# --- Track B analyst/ablation additions (append-only) -----------------------
# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


@pytest.fixture
def analyst_evidence():
    """Deterministic analyst evidence set (quant + news + regime + model)."""
    from tests.fixtures.ai_fixtures import make_evidence

    return make_evidence()


@pytest.fixture
def ablation_frame_120():
    """Deterministic 120-row binary-label frame + timestamps for ablations."""
    import numpy as np

    from crypto_engine.validation.folds import make_folds

    rng = np.random.default_rng(7)
    n = 120
    y = (rng.uniform(size=n) > 0.45).astype(int)
    p = np.clip(0.5 + 0.2 * (y - 0.5) + rng.normal(0, 0.1, size=n), 0.01, 0.99)
    base = datetime(2024, 1, 1, tzinfo=UTC)
    ts = [(base + timedelta(hours=i)).isoformat() for i in range(n)]
    folds = make_folds(n, train_window=40, val_window=10, n_folds=3, min_samples=5)
    return {"y": y, "p": p, "ts": ts, "folds": folds}


# --- Track A signal additions (append-only): policy/signal fixtures -------
# NOTE: appended 2026-10-27 by Track A; existing fixtures above untouched.


@pytest.fixture
def signal_feature_frame_40():
    """Tiny deterministic binary feature frame (40 rows, seed 0)."""
    from tests.fixtures.signal_fixtures import make_feature_frame

    return make_feature_frame(n=40, seed=0)


@pytest.fixture
def signal_calibrated_pair():
    """Fitted (base, calibrator, calibrator_meta, row) for policy tests."""
    from tests.fixtures.signal_fixtures import make_calibrated_pair

    return make_calibrated_pair(seed=0)


# --- Track B signal additions (append-only): tiny snapshot/model/portfolio --
# NOTE: appended 2026-10-27 by Track B (signals); existing fixtures above untouched.


@pytest.fixture
def tiny_snapshot_dir(tmp_path):
    """Tiny 5-candle BTC/USDT 1h snapshot dir (offline, deterministic)."""
    from tests.fixtures.signal_fixtures import make_snapshot_dir

    snap_dir, _ = make_snapshot_dir(tmp_path / "storage")
    return snap_dir


@pytest.fixture
def stub_registry_model_dir(tmp_path):
    """Tiny fitted TrendBaseline persisted via registry save_model (offline)."""
    from tests.fixtures.signal_fixtures import make_registry_model_dir

    model_dir, _, _ = make_registry_model_dir(tmp_path / "registry")
    return model_dir


@pytest.fixture
def portfolio_valid():
    """Valid caller-supplied portfolio snapshot."""
    from tests.fixtures.signal_fixtures import make_portfolio

    return make_portfolio("valid")


@pytest.fixture
def portfolio_breached():
    """Breached caller-supplied portfolio snapshot (drawdown + limits)."""
    from tests.fixtures.signal_fixtures import make_portfolio

    return make_portfolio("breached")


# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


# --- Track B validation additions (append-only): hand-computed proxy case --
# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


@pytest.fixture
def proxy_labels_5():
    """Hand-computed 5-trade proxy case for exact cost-math tests.

    labels=[+1,-1,0,+1,-1], pt_ret=0.02, sl_ret=0.01, fee_bp=10,
    slippage_bp=5 -> cost=0.0015/trade; expected returns
    [0.0185,-0.0115,-0.0015,0.0185,-0.0115], mean 0.0025, win rate 2/5
    (the timeout row pays costs, so it is a nonzero outcome).
    """
    import numpy as np

    labels = np.asarray([1, -1, 0, 1, -1], dtype=np.int64)
    expected = np.asarray([0.0185, -0.0115, -0.0015, 0.0185, -0.0115])
    return {
        "labels": labels,
        "pt_ret": 0.02,
        "sl_ret": 0.01,
        "fee_bp": 10.0,
        "slippage_bp": 5.0,
        "expected": expected,
        "expected_mean": 0.0025,
    }


# --- Track A backtest additions (append-only): tiny OHLC + signal builders --
# NOTE: appended 2026-10-27 by Track A; existing fixtures above untouched.


@pytest.fixture
def backtest_monotone_12():
    """12-bar steady uptrend frame (Track A backtest fixture)."""
    from tests.fixtures.backtest_fixtures import monotone_frame

    return monotone_frame(n=12)


@pytest.fixture
def backtest_reversal_12():
    """12-bar rise-then-fall frame (Track A backtest fixture)."""
    from tests.fixtures.backtest_fixtures import reversal_frame

    return reversal_frame()


@pytest.fixture
def backtest_zero_cost():
    """All-zero CostEstimate (exact passthrough)."""
    from tests.fixtures.backtest_fixtures import zero_cost

    return zero_cost()


@pytest.fixture
def backtest_small_cost():
    """Hand-computable nonzero CostEstimate (fee 10 / spread 4 / slip 6 / fund 1)."""
    from tests.fixtures.backtest_fixtures import small_cost

    return small_cost()


@pytest.fixture
def backtest_config_default():
    """Default BacktestConfig (BTC/USDT 1h, equity 10000, 10%, hold 24)."""
    from crypto_engine.config.settings import BacktestConfig

    return BacktestConfig()


# --- Phase 6 Track B paper additions (append-only): cost/gate fixtures -----
# NOTE: appended 2026-10-27 by Track B; existing fixtures above untouched.


@pytest.fixture
def paper_cost_zero():
    """Zero-leg ExecutionCostModel (exact passthrough) for paper-step tests."""
    from crypto_engine.backtesting.costs import ExecutionCostModel
    from tests.fixtures.backtest_fixtures import zero_cost

    return ExecutionCostModel(zero_cost())


@pytest.fixture
def paper_cost_small():
    """Hand-computable nonzero ExecutionCostModel for paper-step tests."""
    from crypto_engine.backtesting.costs import ExecutionCostModel
    from tests.fixtures.backtest_fixtures import small_cost

    return ExecutionCostModel(small_cost())


@pytest.fixture
def paper_gates_factory():
    """Generous-age gate-context factory for paper-step unit tests."""
    from tests.fixtures.backtest_fixtures import paper_gates_ctx

    return paper_gates_ctx
