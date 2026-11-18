"""Q10 security tests (offline, deterministic).

Covers: API token auth, pickle-free registry JSON serialization,
fail-open gating, metric label redaction, backup/restore integrity,
and no secrets in config.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from crypto_engine import backup
from crypto_engine.api.security import ApiError, get_api_token, verify_api_token
from crypto_engine.cli import app
from crypto_engine.config.settings import (
    BackupConfig,
    DataConfig,
    LoggingConfig,
    PaperConfig,
    RegistryConfig,
    Settings,
    load_settings,
)
from crypto_engine.models.sklearn_wrappers import SklearnLogistic
from crypto_engine.observability.metrics import _redact_label_value
from crypto_engine.registry import store as store_module
from crypto_engine.registry.store import load_model, save_model
from tests.fixtures.signal_fixtures import make_registry_model_dir, make_snapshot_dir

runner = CliRunner()

API_TOKEN_ENV = "SIGNALFORGE_API_TOKEN"
FAIL_OPEN_ENV = "SIGNALFORGE_ALLOW_FAIL_OPEN"
SECRET_VALUE_PATTERNS = ("sk-", "sk_live", "sk-or-", "xai-", "llm_")

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- API token

def test_get_api_token_returns_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    assert get_api_token() is None


def test_verify_api_token_no_auth_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    assert verify_api_token() is None
    assert verify_api_token("Bearer anything") is None


def test_verify_api_token_missing_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
    with pytest.raises(ApiError) as exc:
        verify_api_token(None)
    assert exc.value.status_code == 401
    assert exc.value.code == "missing_token"


def test_verify_api_token_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
    with pytest.raises(ApiError) as exc:
        verify_api_token("Bearer wrong-token")
    assert exc.value.status_code == 401
    assert exc.value.code == "invalid_token"


def test_verify_api_token_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
    assert verify_api_token("Bearer secret-token") is None


# ------------------------------------------------------------ pickle removal

def test_store_has_no_pickle_import() -> None:
    source = Path(store_module.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "pickle" not in stripped, f"pickle import found: {stripped!r}"


def test_serialize_deserialize_roundtrip_json(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(60, 3)), columns=["a", "b", "c"])
    y = (X["a"] > 0).astype(int).to_numpy()
    model = SklearnLogistic(feature_names=["a", "b", "c"]).fit(X, y)

    meta = {
        "dataset_snapshot_id": "snap-1",
        "dataset_checksum": hashlib.sha256(b"fixture").hexdigest(),
        "label_config": {"pt_mult": 2.0, "sl_mult": 1.0},
        "label_hash": "lbl",
        "train_start": "2024-01-01T00:00:00+00:00",
        "train_end": "2024-01-05T00:00:00+00:00",
    }
    model_dir = save_model(model, meta, tmp_path / "registry")
    assert (model_dir / "artifact.json").is_file()
    assert not (model_dir / "artifact.pkl").exists()

    loaded, record = load_model(model_dir)
    assert record.family == "logistic"
    assert loaded.is_fitted
    assert loaded.to_dict()["feature_names"] == ["a", "b", "c"]
    np.testing.assert_array_equal(loaded.predict(X), model.predict(X))
    np.testing.assert_allclose(loaded.predict_score(X), model.predict_score(X))


# ----------------------------------------------------------------- fail-open

def _write_config(tmp_path: Path) -> Path:
    config = tmp_path / "sf.toml"
    config.write_text(
        '[exchange]\nname = "binance"\n'
        '[data]\nstorage_path = "data"\n'
        "[validation]\nfee_bp = 10.0\nslippage_bp = 5.0\n"
        '[signal]\nhistory_path = "data/signals"\n'
        "calibration_required = true\nrequire_edge = true\n",
        encoding="utf-8",
    )
    return config


def test_fail_open_requires_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FAIL_OPEN_ENV, raising=False)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    result = runner.invoke(app, ["signal", "generate", "--model", str(model_dir), "--fail-open"])
    assert result.exit_code == 1
    assert result.exception is not None
    assert getattr(result.exception, "code", None) in (1, "1")


def test_fail_open_allowed_with_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FAIL_OPEN_ENV, "1")
    storage = tmp_path / "storage"
    registry = tmp_path / "registry"
    hist_dir = tmp_path / "history"
    snap_dir, _ = make_snapshot_dir(storage, n=5)
    model_dir, _, _ = make_registry_model_dir(registry)
    config = _write_config(tmp_path)

    result = runner.invoke(
        app,
        [
            "signal", "generate",
            "--symbol", "BTC/USDT",
            "--timeframe", "1h",
            "--model", str(model_dir),
            "--snapshot", str(snap_dir),
            "--config", str(config),
            "--history-path", str(hist_dir),
            "--at", "2024-06-01T12:00:00+00:00",
            "--now", "2024-06-01T12:05:00+00:00",
            "--fail-open",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["symbol"] == "BTC/USDT"
    assert payload["direction"] in ("LONG", "SHORT", "NEUTRAL", "NO_TRADE")


# -------------------------------------------------------- metric label redaction

def test_redact_label_value_sk_key() -> None:
    assert _redact_label_value("sk-live-abc123") == "REDACTED"


def test_redact_label_value_bearer() -> None:
    assert _redact_label_value("Bearer token123") == "REDACTED"


def test_redact_label_value_api_key() -> None:
    assert _redact_label_value("api_key=secret123") == "REDACTED"


def test_redact_label_value_normal() -> None:
    assert _redact_label_value("normal_label") == "normal_label"


# ------------------------------------------------------------ backup / restore

def _make_settings(tmp_path: Path) -> Settings:
    registry = tmp_path / "registry"
    data = tmp_path / "data"
    paper = tmp_path / "paper"
    logs = tmp_path / "logs"
    backup_root = tmp_path / "backups"
    for directory in (registry, data, paper, logs):
        directory.mkdir(parents=True, exist_ok=True)
    (registry / "model.json").write_text("{}", encoding="utf-8")
    (data / "dataset.parquet").write_bytes(b"parquet-bytes")
    (paper / "account.json").write_text("{}", encoding="utf-8")
    return Settings(
        registry=RegistryConfig(path=str(registry)),
        data=DataConfig(storage_path=str(data)),
        paper=PaperConfig(account_path=str(paper)),
        logging=LoggingConfig(file_path=str(logs / "signalforge.log")),
        backup=BackupConfig(path=str(backup_root)),
    )


def _find_source_file(settings: Settings, rel_path: str) -> Path | None:
    for source_dir in backup._configured_source_dirs(settings):
        candidate = source_dir / rel_path
        if candidate.is_file():
            return candidate
    return None


def test_backup_create_and_verify(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manifest = backup.create_backup(settings)
    assert manifest.status == "completed"
    assert manifest.files
    assert backup.verify_backup(Path(settings.backup.path) / manifest.backup_id) is True


def test_backup_tamper_detection(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manifest = backup.create_backup(settings)
    backup_dir = Path(settings.backup.path) / manifest.backup_id
    rel_path = manifest.files[0]
    (backup_dir / rel_path).write_bytes(b"tampered-bytes")
    assert backup.verify_backup(backup_dir) is False


def test_backup_restore_dry_run(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manifest = backup.create_backup(settings)
    backup_dir = Path(settings.backup.path) / manifest.backup_id
    rel_path = manifest.files[0]
    source = _find_source_file(settings, rel_path)
    assert source is not None
    source.write_bytes(b"modified-content")

    result = backup.restore_from_backup(backup_dir, settings, dry_run=True)
    assert rel_path in result["restored"]
    assert not result["errors"]
    assert source.read_bytes() == b"modified-content"


def test_backup_restore_execute(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manifest = backup.create_backup(settings)
    backup_dir = Path(settings.backup.path) / manifest.backup_id
    rel_path = manifest.files[0]
    expected = (backup_dir / rel_path).read_bytes()
    source = _find_source_file(settings, rel_path)
    assert source is not None
    source.write_bytes(b"modified-content")

    result = backup.restore_from_backup(backup_dir, settings, dry_run=False)
    assert rel_path in result["restored"]
    assert not result["errors"]
    assert source.read_bytes() == expected


def test_backup_list_and_retention(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    ids: list[str] = []
    for _ in range(4):
        manifest = backup.create_backup(settings)
        ids.append(manifest.backup_id)

    listed = backup.list_backups(Path(settings.backup.path))
    assert len(listed) == 4

    deleted = backup.retention_cleanup(Path(settings.backup.path), keep=2)
    assert len(deleted) == 2
    remaining = backup.list_backups(Path(settings.backup.path))
    assert len(remaining) == 2
    assert {m.backup_id for m in remaining} == set(ids[-2:])


# ------------------------------------------------------------- no secrets

def test_no_real_api_key_in_env() -> None:
    env_path = REPO_ROOT / ".env"
    text = env_path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        _, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        lowered = value.lower()
        assert not any(
            pattern in lowered for pattern in SECRET_VALUE_PATTERNS
        ), f"real secret-like value found in .env: {value!r}"


def test_meta_muse_disabled_in_toml() -> None:
    settings = load_settings()
    meta_muse = [p for p in settings.ai.providers if p.provider_id == "meta_muse"]
    assert meta_muse, "meta_muse provider entry not found in config"
    assert meta_muse[0].enabled is False


__all__: list[str] = []
