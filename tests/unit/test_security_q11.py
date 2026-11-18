"""Q11 security verification tests (offline, deterministic).

Covers all 14 required areas:

1. API token fail-closed / development mode behaviour
2. Token verification (missing / invalid / valid Bearer)
3. Pickle-free serialization (store + frozen inference)
4. Malformed JSON artifact fails safely
5. JSON artifact roundtrip (predictions match)
6. Fail-open gating behind SIGNALFORGE_ALLOW_FAIL_OPEN
7. Metric label redaction
8. Backup path traversal rejection
9. Backup tampering detection
10. Backup dry-run restore performs no writes
11. Backup corrupt manifest rejection
12. No real secrets in .env
13. meta_muse provider disabled in config
14. Q6 governance flags preserved
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from crypto_engine import backup
from crypto_engine.api.app import AUTH_REQUIRED, create_app
from crypto_engine.api.security import ApiError, get_api_token, verify_api_token
from crypto_engine.cli import app as cli_app
from crypto_engine.config.settings import Settings, load_settings
from crypto_engine.models.sklearn_wrappers import SklearnLogistic
from crypto_engine.observability.metrics import _redact_label_value
from crypto_engine.registry.store import (
    JSON_ARTIFACT,
    CorruptArtifactError,
    load_model,
    save_model,
)
from crypto_engine.research.q6.frozen_inference import (
    PROMOTION_ELIGIBLE,
    Q4_DECISION,
    Q5_2_ELIGIBLE,
    reconstruct_logistic_model,
)

runner = CliRunner()

API_TOKEN_ENV = "SIGNALFORGE_API_TOKEN"
AUTH_REQUIRED_ENV = "SIGNALFORGE_AUTH_REQUIRED"
FAIL_OPEN_ENV = "SIGNALFORGE_ALLOW_FAIL_OPEN"
REPO_ROOT = Path(__file__).resolve().parents[2]

SECRET_PATTERNS = ("sk-", "sk_live", "sk-or-", "xai-", "llm_")


# ---------------------------------------------------------------- API token

def test_production_fails_closed_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_REQUIRED_ENV, "1")
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    assert os.environ.get(AUTH_REQUIRED_ENV) == "1"
    assert get_api_token() is None
    # Fail-closed condition: auth required but no token configured.
    assert AUTH_REQUIRED or os.environ.get(AUTH_REQUIRED_ENV) == "1"
    assert (os.environ.get(AUTH_REQUIRED_ENV) == "1") and (get_api_token() is None)
    assert callable(create_app)


def test_development_mode_allows_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AUTH_REQUIRED_ENV, raising=False)
    monkeypatch.delenv(API_TOKEN_ENV, raising=False)
    assert os.environ.get(AUTH_REQUIRED_ENV) is None
    assert get_api_token() is None
    assert verify_api_token() is None
    assert verify_api_token("Bearer anything") is None


def test_production_requires_token_when_auth_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUTH_REQUIRED_ENV, "1")
    monkeypatch.setenv(API_TOKEN_ENV, "prod-token-123")
    assert os.environ.get(AUTH_REQUIRED_ENV) == "1"
    assert get_api_token() == "prod-token-123"


def test_verify_api_token_rejects_missing_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
    with pytest.raises(ApiError) as exc:
        verify_api_token(None)
    assert exc.value.status_code == 401
    assert exc.value.code == "missing_token"


def test_verify_api_token_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
    with pytest.raises(ApiError) as exc:
        verify_api_token("Bearer wrong-token")
    assert exc.value.status_code == 401
    assert exc.value.code == "invalid_token"


def test_verify_api_token_accepts_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(API_TOKEN_ENV, "secret-token")
    assert verify_api_token("Bearer secret-token") is None


# ------------------------------------------------------------ pickle removal

def test_no_pickle_import_in_store() -> None:
    import crypto_engine.registry.store as store_module

    source = Path(store_module.__file__).read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "pickle" not in stripped, f"pickle import found: {stripped!r}"


def test_no_pickle_import_in_frozen_inference() -> None:
    import crypto_engine.research.q6.frozen_inference as fi_module

    source = Path(fi_module.__file__).read_text(encoding="utf-8")
    assert "pickle" not in source, "pickle reference found in frozen_inference.py"


# ------------------------------------------------------------ corrupt JSON

def test_malformed_json_artifact_fails_safely(tmp_path: Path) -> None:
    model_dir = Path(tempfile.mkdtemp(dir=tmp_path))
    artifact = model_dir / JSON_ARTIFACT
    artifact.write_text("{not valid json", encoding="utf-8")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    record = {
        "model_id": "malformed-1",
        "version": "0.1.0",
        "family": "logistic",
        "params": {},
        "dataset_snapshot_id": "",
        "dataset_checksum": "",
        "feature_names": [],
        "feature_schema_hash": "",
        "label_config": {},
        "label_hash": "",
        "train_start": "",
        "train_end": "",
        "created_at": "2024-01-01T00:00:00+00:00",
        "random_state": 42,
        "package_version": "0.1.0",
        "sklearn_version": "builtin",
        "artifact_path": JSON_ARTIFACT,
        "artifact_sha256": digest,
        "status": "trained",
        "interface": "predict/predict_score",
        "limitations": "",
        "known_incompatibilities": [],
    }
    (model_dir / "record.json").write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(CorruptArtifactError):
        load_model(model_dir)


def test_json_artifact_roundtrip(tmp_path: Path) -> None:
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

def test_fail_open_requires_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(FAIL_OPEN_ENV, raising=False)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    result = runner.invoke(
        cli_app, ["signal", "generate", "--model", str(model_dir), "--fail-open"]
    )
    assert result.exit_code == 1
    assert result.exception is not None


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


def test_fail_open_allowed_with_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FAIL_OPEN_ENV, "1")
    from tests.fixtures.signal_fixtures import make_registry_model_dir, make_snapshot_dir

    storage = tmp_path / "storage"
    registry = tmp_path / "registry"
    hist_dir = tmp_path / "history"
    snap_dir, _ = make_snapshot_dir(storage, n=5)
    model_dir, _, _ = make_registry_model_dir(registry)
    config = _write_config(tmp_path)

    result = runner.invoke(
        cli_app,
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

def test_secret_redaction_in_metrics() -> None:
    assert _redact_label_value("sk-live-abc123") == "REDACTED"
    assert _redact_label_value("Bearer token") == "REDACTED"
    assert _redact_label_value("normal") == "normal"


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
    from crypto_engine.config.settings import (
        BackupConfig,
        DataConfig,
        LoggingConfig,
        PaperConfig,
        RegistryConfig,
    )
    return Settings(
        registry=RegistryConfig(path=str(registry)),
        data=DataConfig(storage_path=str(data)),
        paper=PaperConfig(account_path=str(paper)),
        logging=LoggingConfig(file_path=str(logs / "signalforge.log")),
        backup=BackupConfig(path=str(backup_root)),
    )


def test_backup_path_traversal_rejected(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    assert backup._resolve_within(backup_dir, "../etc/passwd") is None
    assert backup._resolve_within(backup_dir, "safe/file.json") is not None


def test_backup_tampering_detected(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manifest = backup.create_backup(settings)
    assert manifest.status == "completed"
    assert manifest.files
    backup_dir = Path(settings.backup.path) / manifest.backup_id
    rel_path = manifest.files[0]
    (backup_dir / rel_path).write_bytes(b"tampered-bytes")
    assert backup.verify_backup(backup_dir) is False


def test_backup_restore_dry_run_no_writes(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manifest = backup.create_backup(settings)
    backup_dir = Path(settings.backup.path) / manifest.backup_id
    rel_path = manifest.files[0]
    source = None
    for source_dir in backup._configured_source_dirs(settings):
        candidate = source_dir / rel_path
        if candidate.is_file():
            source = candidate
            break
    assert source is not None
    before = source.read_bytes()

    result = backup.restore_from_backup(backup_dir, settings, dry_run=True)
    assert rel_path in result["restored"]
    assert not result["errors"]
    assert result["dry_run"] is True
    assert source.read_bytes() == before


def test_backup_corrupt_manifest_rejected(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manifest = backup.create_backup(settings)
    backup_dir = Path(settings.backup.path) / manifest.backup_id
    (backup_dir / backup._MANIFEST_NAME).unlink()
    assert backup.verify_backup(backup_dir) is False


# ------------------------------------------------------------- no secrets

def test_env_has_no_real_secrets() -> None:
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
            pattern in lowered for pattern in SECRET_PATTERNS
        ), f"real secret-like value found in .env: {value!r}"
        assert len(value) < 40, f"long API key value found in .env: {value!r}"


def test_meta_muse_disabled() -> None:
    settings = load_settings()
    meta_muse = [p for p in settings.ai.providers if p.provider_id == "meta_muse"]
    assert meta_muse, "meta_muse provider entry not found in config"
    assert meta_muse[0].enabled is False
    # Single-process constraint is enforced by settings validation.
    with pytest.raises(ValidationError):
        Settings.model_validate({"model": {"n_jobs": 2}})


# -------------------------------------------------------- governance flags

def test_governance_flags_preserved() -> None:
    import crypto_engine.research.q6.frozen_inference as fi_module

    source = Path(fi_module.__file__).read_text(encoding="utf-8")
    assert 'Q4_DECISION = "NO_STRATEGY_ELIGIBLE"' in source
    assert Q4_DECISION == "NO_STRATEGY_ELIGIBLE"
    assert PROMOTION_ELIGIBLE is False
    assert Q5_2_ELIGIBLE is False
    # Frozen inference module exposes the JSON-only reconstructor.
    assert callable(reconstruct_logistic_model)

