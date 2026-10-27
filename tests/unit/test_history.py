"""Unit tests for the hash-chained signal audit history (Phase 5 Track B)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from crypto_engine.signals.history import GENESIS_HASH, PersistenceError, SignalHistory
from crypto_engine.signals.schema import SignalRecord

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _record(signal_id: str = "b" * 16, direction: str = "NO_TRADE", **over) -> SignalRecord:
    flat = direction in ("NEUTRAL", "NO_TRADE")
    params: dict = {
        "signal_id": signal_id,
        "symbol": "BTC/USDT",
        "exchange": "binance",
        "timeframe": "1h",
        "generated_at": NOW - timedelta(minutes=5),
        "valid_until": NOW + timedelta(minutes=55),
        "direction": direction,
        "prediction_horizon_bars": 24,
        "model_id": "m",
        "model_version": "0.1.0",
        "model_artifact_hash": "abc123",
        "dataset_snapshot_id": "snap",
        "feature_snapshot_hash": "f" * 64,
        "prediction": {
            "raw_score": 0.5 if flat else 0.9,
            "calibration_status": "unavailable",
            "positive_class": None,
            "model_family": "trend",
            "threshold_version": "v1",
        },
        "market_regime": "",
        "expected_edge_bp": None,
        "entry_ref": None,
        "stop_loss": None,
        "take_profit": None,
        "risk_reward": None,
        "data_quality": "validated",
        "ai_context": {"status": "disabled"},
        "decision": {
            "status": "abstained" if flat else "approved",
            "reason_codes": ["THRESHOLD_NOT_MET"] if flat else ["OK"],
            "gate": "policy",
        },
        "software_version": "0.1.0-test",
    }
    params.update(over)
    return SignalRecord.model_validate(params)


def test_append_get_list_roundtrip(tmp_path) -> None:
    hist = SignalHistory(tmp_path / "hist")
    rec = _record()
    assert hist.append(rec) == rec.signal_id
    assert hist.get(rec.signal_id) is not None
    assert hist.get(rec.signal_id).signal_id == rec.signal_id
    assert [r.signal_id for r in hist.list()] == [rec.signal_id]
    assert hist.list(symbol="BTC/USDT")
    assert hist.list(symbol="ETH/USDT") == []
    ok, errors = hist.verify()
    assert ok and errors == []


def test_append_idempotent(tmp_path) -> None:
    hist = SignalHistory(tmp_path / "hist")
    rec = _record()
    hist.append(rec)
    hist.append(rec)
    assert len(hist.list()) == 1
    ok, _ = hist.verify()
    assert ok


def test_chain_links_records(tmp_path) -> None:
    import json

    hist = SignalHistory(tmp_path / "hist")
    hist.append(_record("0" * 16))
    hist.append(_record("1" * 16))
    lines = (tmp_path / "hist" / "signals.jsonl").read_text(encoding="utf-8").splitlines()
    first, second = (json.loads(line) for line in lines)
    assert first["prev_hash"] == GENESIS_HASH
    assert second["prev_hash"] == first["record_hash"]
    ok, errors = hist.verify()
    assert ok and errors == []


def test_tamper_detection(tmp_path) -> None:
    hist = SignalHistory(tmp_path / "hist")
    hist.append(_record("0" * 16))
    hist.append(_record("1" * 16))
    path = tmp_path / "hist" / "signals.jsonl"
    raw = path.read_text(encoding="utf-8")
    tampered = raw.replace("BTC/USDT", "BTC/USDX", 1)
    assert tampered != raw
    path.write_text(tampered, encoding="utf-8")
    ok, errors = hist.verify()
    assert not ok and errors


def test_corrupt_line_handling(tmp_path) -> None:
    hist = SignalHistory(tmp_path / "hist")
    hist.append(_record())
    with open(tmp_path / "hist" / "signals.jsonl", "a", encoding="utf-8") as fh:
        fh.write("{not-json\n")
    ok, errors = hist.verify()
    assert not ok
    assert any("corrupt" in e for e in errors)
    with pytest.raises(PersistenceError):
        hist.append(_record("2" * 16))


def test_persistence_error_on_unwritable(tmp_path) -> None:
    hist = SignalHistory(tmp_path / "hist")
    hist.append(_record())
    path = tmp_path / "hist" / "signals.jsonl"
    path.chmod(0o444)
    try:
        with pytest.raises(PersistenceError):
            hist.append(_record("2" * 16))
    finally:
        path.chmod(0o666)


def test_no_secret_like_keys_stored(tmp_path, monkeypatch) -> None:
    hist = SignalHistory(tmp_path / "hist")
    rec = _record()
    assert hist.append(rec) == rec.signal_id
    sneaky = _record("3" * 16)
    real_dump = SignalRecord.model_dump

    def _dump_with_secret(self, *args, **kwargs):
        payload = real_dump(self, *args, **kwargs)
        payload["api_key"] = "sk-test"
        return payload

    monkeypatch.setattr(SignalRecord, "model_dump", _dump_with_secret)
    try:
        with pytest.raises(ValueError, match="secret-like"):
            hist.append(sneaky)
    finally:
        monkeypatch.undo()
    assert hist.get("3" * 16) is None
    assert len(hist.list()) == 1


def test_rejects_secret_keys_directly(tmp_path) -> None:
    import json as _json

    hist = SignalHistory(tmp_path / "hist")
    rec = _record()
    payload = rec.model_dump(mode="json")
    payload["note_secret"] = "hidden"
    tampered_record = SignalRecord.model_validate(
        {k: v for k, v in payload.items() if k != "note_secret"}
    )
    assert hist.append(tampered_record) == tampered_record.signal_id
    bad_line = _json.dumps(
        {
            "signal_id": "f" * 16,
            "prev_hash": GENESIS_HASH,
            "record_hash": "0" * 64,
            "record": {**payload, "api_key": "sk-test"},
        }
    )
    with open(tmp_path / "hist" / "signals.jsonl", "a", encoding="utf-8") as fh:
        fh.write(bad_line + "\n")
    ok, errors = hist.verify()
    assert not ok
    assert any("schema" in e or "secret" in e or "record_hash" in e for e in errors)
