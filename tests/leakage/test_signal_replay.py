"""Signal replay determinism + tail-only leakage tests (Phase 5 Track B).

Same snapshot + model + config + fixed timestamps must yield the identical
``signal_id`` and record. The CLI forwards exactly one row (the snapshot
tail) to ``decide()``: multi-row input is rejected explicitly, and
perturbing the tail row changes the id. Offline, deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from crypto_engine.cli import (
    app,
    require_single_row_inputs,
    select_decision_row,
)
from crypto_engine.datasets.snapshots import load_snapshot
from crypto_engine.features.engineering import compute_features
from tests.fixtures.signal_fixtures import make_registry_model_dir, make_snapshot_dir

runner = CliRunner()
AT = "2024-06-01T12:00:00+00:00"
NOW = "2024-06-01T12:05:00+00:00"


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "sf.toml"
    config.write_text(
        '[exchange]\nname = "binance"\n'
        '[data]\nstorage_path = "data"\n'
        "[validation]\nfee_bp = 10.0\nslippage_bp = 5.0\n"
        '[signal]\nhistory_path = "data/signals"\n',
        encoding="utf-8",
    )
    return config


def _generate(model_dir: Path, snap_dir: Path, config: Path, hist: Path, at: str = AT):
    return runner.invoke(
        app,
        [
            "signal",
            "generate",
            "--model",
            str(model_dir),
            "--snapshot",
            str(snap_dir),
            "--config",
            str(config),
            "--history-path",
            str(hist),
            "--at",
            at,
            "--now",
            NOW,
            "--fail-open",
        ],
    )


def test_replay_identical_id_and_record(tmp_path: Path) -> None:
    snap_dir, _ = make_snapshot_dir(tmp_path / "storage", n=5)
    model_dir, _, _ = make_registry_model_dir(tmp_path / "registry")
    config = _config(tmp_path)
    first = _generate(model_dir, snap_dir, config, tmp_path / "h1")
    assert first.exit_code == 0, first.output
    second = _generate(model_dir, snap_dir, config, tmp_path / "h2")
    assert second.exit_code == 0, second.output
    a, b = json.loads(first.output), json.loads(second.output)
    assert a["signal_id"] == b["signal_id"]
    assert a == b


def test_tail_perturbation_changes_id(tmp_path: Path) -> None:
    from crypto_engine.data.validation import normalize_candles
    from crypto_engine.datasets.snapshots import save_snapshot

    snap_dir, _ = make_snapshot_dir(tmp_path / "storage", n=5)
    model_dir, _, _ = make_registry_model_dir(tmp_path / "registry")
    config = _config(tmp_path)
    base = _generate(model_dir, snap_dir, config, tmp_path / "h1")
    assert base.exit_code == 0, base.output
    base_id = json.loads(base.output)["signal_id"]

    rows: list[list] = []
    for i in range(5):
        o = 40_000.0 + i * 10.0
        rows.append([1_700_000_000_000 + i * 3_600_000, o, o + 50.0, o - 40.0, o + 5.0, 10.0 + i])
    rows[-1][4] = rows[-1][4] * 1.10  # perturb ONLY the tail close
    rows[-1][2] = rows[-1][4] + 50.0  # keep high >= close (valid candle)
    perturbed = normalize_candles(rows)
    snap2 = save_snapshot(
        perturbed,
        {"requested_start": None, "requested_end": None, "warnings": [], "missing_count": 0},
        tmp_path / "storage2",
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="1h",
    )
    alt = _generate(model_dir, snap2, config, tmp_path / "h2")
    assert alt.exit_code == 0, alt.output
    assert json.loads(alt.output)["signal_id"] != base_id


def test_decision_uses_tail_row_only(tmp_path: Path) -> None:
    snap_dir, _ = make_snapshot_dir(tmp_path / "storage", n=12)
    frame, _ = load_snapshot(snap_dir)
    frame = frame.set_index(pd.to_datetime(frame["timestamp"], utc=True)).sort_index()
    full = compute_features(frame)
    tail = compute_features(frame.tail(10))
    # Window-free columns are prefix-stable: tail slicing never reads the future.
    for col in ("ret_1", "ret_4"):
        assert full[col].iloc[-1] == tail[col].iloc[-1]
    # Recursive EWM state is causal-but-prefix-dependent (documented): the CLI
    # always slices the OHLCV tail first, then computes, so replay on the same
    # snapshot is deterministic while never touching future rows.
    row = select_decision_row(full)
    assert set(row) == set(full.columns)
    import math as _math

    for col in full.columns:
        got, exp = row[col], float(full[col].iloc[-1])
        assert (got == exp) or (_math.isnan(got) and _math.isnan(exp)), col
    with pytest.raises(ValueError):
        select_decision_row(full.iloc[0:0])


def test_multi_row_input_rejected_explicitly() -> None:
    import pandas as pd

    assert require_single_row_inputs({"a": 1.0}) == {"a": 1.0}
    with pytest.raises(TypeError, match="exactly one row"):
        require_single_row_inputs(pd.DataFrame([{"a": 1.0}, {"a": 2.0}]))
    with pytest.raises(TypeError, match="exactly one row"):
        require_single_row_inputs([{"a": 1.0}])
