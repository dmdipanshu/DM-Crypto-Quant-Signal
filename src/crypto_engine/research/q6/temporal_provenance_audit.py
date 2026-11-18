"""Temporal provenance and data integrity audit for Q6 frozen candidates.

Audits frozen model artifacts against historical evaluation datasets to
establish artifact existence/hash integrity, training timestamp integrity
(no leakage), earliest permissible evaluation timestamp, feature schema and
config hash consistency, and assigns TEMPORAL_PROVENANCE_VERIFIED or
TEMPORAL_PROVENANCE_UNVERIFIED status.

All operations are read-only: no retraining, refitting, calibration, or
tuning. Artifact hashes are verified against record.json without loading
pickle callables.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

ARTIFACTS_ROOT = ROOT.parent / "models" / "artifacts"
DEFAULT_HISTORICAL_STORAGE_ROOT = Path("data/historical_accelerated")
DEFAULT_PROSPECTIVE_STORAGE_ROOT = Path("data/prospective")

SCHEMA_VERSION = "temporal_provenance_audit.v1"

TEMPORAL_PROVENANCE_VERIFIED = "TEMPORAL_PROVENANCE_VERIFIED"
TEMPORAL_PROVENANCE_UNVERIFIED = "TEMPORAL_PROVENANCE_UNVERIFIED"

ARTIFACT_FILENAME = "artifact.pkl"
RECORD_FILENAME = "record.json"


def _utc_now_iso() -> str:
    """Return current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _sha256_json(value: Any) -> str:
    """Deterministic sha256 over a JSON-serialisable value."""
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    """Streaming sha256 of a file (bounded memory for large artifacts)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None when unparseable."""
    if ts is None:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class ArtifactAuditResult:
    """Read-only audit of a single frozen model artifact directory."""

    artifact_id: str
    artifact_dir: Path
    artifact_exists: bool
    artifact_file: Path | None
    record_file: Path | None
    artifact_sha256_expected: str
    artifact_sha256_actual: str
    artifact_hash_match: bool
    record_exists: bool
    record_parsed: bool
    record: dict[str, Any] | None
    train_start: str | None
    train_end: str | None
    feature_names: list[str]
    feature_schema_hash: str
    label_hash: str
    dataset_snapshot_id: str
    dataset_checksum: str
    created_at: str | None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_dir": str(self.artifact_dir),
            "artifact_exists": self.artifact_exists,
            "artifact_file": str(self.artifact_file) if self.artifact_file else None,
            "record_file": str(self.record_file) if self.record_file else None,
            "artifact_sha256_expected": self.artifact_sha256_expected,
            "artifact_sha256_actual": self.artifact_sha256_actual,
            "artifact_hash_match": self.artifact_hash_match,
            "record_exists": self.record_exists,
            "record_parsed": self.record_parsed,
            "record": self.record,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "feature_names": list(self.feature_names),
            "feature_schema_hash": self.feature_schema_hash,
            "label_hash": self.label_hash,
            "dataset_snapshot_id": self.dataset_snapshot_id,
            "dataset_checksum": self.dataset_checksum,
            "created_at": self.created_at,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class CandidateProvenanceResult:
    """Temporal provenance verdict for one Q6 candidate."""

    candidate_id: str
    model_name: str
    artifact_audit: ArtifactAuditResult
    artifact_recoverable: bool
    training_end_before_eval_start: bool
    training_start_before_eval_start: bool
    eval_dataset_start: str | None
    earliest_permissible_eval_ts: str | None
    temporal_provenance_status: str
    provenance_reasons: list[str] = field(default_factory=list)
    config_hash_consistent: bool = False
    feature_schema_consistent: bool = False
    leakage_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "model_name": self.model_name,
            "artifact_audit": self.artifact_audit.to_dict(),
            "artifact_recoverable": self.artifact_recoverable,
            "training_end_before_eval_start": self.training_end_before_eval_start,
            "training_start_before_eval_start": self.training_start_before_eval_start,
            "eval_dataset_start": self.eval_dataset_start,
            "earliest_permissible_eval_ts": self.earliest_permissible_eval_ts,
            "temporal_provenance_status": self.temporal_provenance_status,
            "provenance_reasons": list(self.provenance_reasons),
            "config_hash_consistent": self.config_hash_consistent,
            "feature_schema_consistent": self.feature_schema_consistent,
            "leakage_detected": self.leakage_detected,
        }


@dataclass(frozen=True, slots=True)
class TemporalProvenanceAuditReport:
    """Complete temporal provenance audit report for a candidate set."""

    schema_version: str
    generated_at_utc: str
    audit_id: str
    storage_root: str
    artifacts_root: str
    n_candidates: int
    candidate_results: dict[str, CandidateProvenanceResult] = field(default_factory=dict)
    dataset_info: dict[str, Any] = field(default_factory=dict)
    overall_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at_utc": self.generated_at_utc,
            "audit_id": self.audit_id,
            "storage_root": self.storage_root,
            "artifacts_root": self.artifacts_root,
            "n_candidates": self.n_candidates,
            "candidate_results": {
                cid: result.to_dict() for cid, result in self.candidate_results.items()
            },
            "dataset_info": dict(self.dataset_info),
            "overall_summary": dict(self.overall_summary),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, default=str)


def _audit_artifact(artifact_dir: Path) -> ArtifactAuditResult:
    """Audit a single model artifact directory (artifact.pkl + record.json)."""
    artifact_dir = Path(artifact_dir)
    artifact_file = artifact_dir / ARTIFACT_FILENAME
    record_file = artifact_dir / RECORD_FILENAME
    errors: list[str] = []

    if not artifact_dir.is_dir():
        errors.append(f"artifact directory does not exist: {artifact_dir}")
        return ArtifactAuditResult(
            artifact_id=artifact_dir.name,
            artifact_dir=artifact_dir,
            artifact_exists=False,
            artifact_file=artifact_file,
            record_file=record_file,
            artifact_sha256_expected="",
            artifact_sha256_actual="",
            artifact_hash_match=False,
            record_exists=False,
            record_parsed=False,
            record=None,
            train_start=None,
            train_end=None,
            feature_names=[],
            feature_schema_hash="",
            label_hash="",
            dataset_snapshot_id="",
            dataset_checksum="",
            created_at=None,
            errors=errors,
        )

    record: dict[str, Any] | None = None
    record_exists = record_file.is_file()
    record_parsed = False

    if record_exists:
        try:
            parsed = json.loads(record_file.read_text(encoding="utf-8"))
            record = parsed if isinstance(parsed, dict) else None
            if record is None:
                errors.append(f"record.json is not a JSON object: {record_file}")
            else:
                record_parsed = True
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"failed to parse record.json {record_file}: {exc}")

    artifact_exists = artifact_file.is_file()
    artifact_sha256_expected = str(record.get("artifact_sha256", "")) if record_parsed else ""
    artifact_sha256_actual = ""
    artifact_hash_match = False

    if artifact_exists:
        try:
            artifact_sha256_actual = _sha256_file(artifact_file)
            if artifact_sha256_expected:
                artifact_hash_match = artifact_sha256_actual == artifact_sha256_expected
        except OSError as exc:
            errors.append(f"failed to hash artifact.pkl {artifact_file}: {exc}")

    artifact_id = (
        str(record.get("model_id", artifact_dir.name)) if record_parsed else artifact_dir.name
    )

    return ArtifactAuditResult(
        artifact_id=artifact_id,
        artifact_dir=artifact_dir,
        artifact_exists=artifact_exists,
        artifact_file=artifact_file,
        record_file=record_file,
        artifact_sha256_expected=artifact_sha256_expected,
        artifact_sha256_actual=artifact_sha256_actual,
        artifact_hash_match=artifact_hash_match,
        record_exists=record_exists,
        record_parsed=record_parsed,
        record=record,
        train_start=record.get("train_start") if record_parsed else None,
        train_end=record.get("train_end") if record_parsed else None,
        feature_names=list(record.get("feature_names", [])) if record_parsed else [],
        feature_schema_hash=str(record.get("feature_schema_hash", "")) if record_parsed else "",
        label_hash=str(record.get("label_hash", "")) if record_parsed else "",
        dataset_snapshot_id=str(record.get("dataset_snapshot_id", "")) if record_parsed else "",
        dataset_checksum=str(record.get("dataset_checksum", "")) if record_parsed else "",
        created_at=record.get("created_at") if record_parsed else None,
        errors=errors,
    )


def _discover_artifact_dirs() -> list[Path]:
    """Discover all artifact directories under models/artifacts/."""
    if not ARTIFACTS_ROOT.is_dir():
        return []
    return sorted(path for path in ARTIFACTS_ROOT.iterdir() if path.is_dir())


def audit_all_artifacts() -> dict[str, ArtifactAuditResult]:
    """Audit every discovered artifact directory, keyed by artifact id."""
    results: dict[str, ArtifactAuditResult] = {}
    for artifact_dir in _discover_artifact_dirs():
        result = _audit_artifact(artifact_dir)
        results[result.artifact_id] = result
    return results


def audit_candidate_provenance(
    *,
    candidate_id: str,
    model_name: str,
    spec: dict[str, Any],
    artifact_audit: ArtifactAuditResult,
    eval_dataset_start_utc: str | None,
    eval_dataset_end_utc: str | None,
) -> CandidateProvenanceResult:
    """Determine temporal provenance status for one candidate.

    Status rules:
      - artifact not recoverable -> TEMPORAL_PROVENANCE_UNVERIFIED
      - leakage detected (training_end not before eval start) -> UNVERIFIED
      - config/schema hash inconsistent -> UNVERIFIED
      - otherwise -> TEMPORAL_PROVENANCE_VERIFIED
    """
    reasons: list[str] = []

    artifact_recoverable = bool(
        artifact_audit.artifact_exists
        and artifact_audit.record_exists
        and artifact_audit.record_parsed
        and artifact_audit.artifact_hash_match
    )

    eval_start = _parse_iso(eval_dataset_start_utc)
    train_start_dt = _parse_iso(artifact_audit.train_start)
    train_end_dt = _parse_iso(artifact_audit.train_end)

    training_end_before_eval_start = False
    training_start_before_eval_start = False

    if train_end_dt is not None and eval_start is not None:
        training_end_before_eval_start = train_end_dt < eval_start
    if train_start_dt is not None and eval_start is not None:
        training_start_before_eval_start = train_start_dt < eval_start

    leakage_detected = bool(
        artifact_recoverable
        and train_end_dt is not None
        and eval_start is not None
        and not training_end_before_eval_start
    )
    if artifact_recoverable and (train_end_dt is None or eval_start is None):
        leakage_detected = True
        reasons.append("training/eval timestamps unavailable to rule out leakage")
    if leakage_detected and train_end_dt is not None and eval_start is not None:
        reasons.append("training leakage detected: training_end is not before eval dataset start")

    earliest_permissible_eval_ts = artifact_audit.train_end

    feature_schema_consistent = False
    config_hash_consistent = False
    if artifact_audit.record_parsed:
        feature_schema_consistent = (
            str(spec.get("feature_config_hash", "")) == artifact_audit.feature_schema_hash
        )
        config_hash_consistent = (
            str(spec.get("target_config_hash", "")) == artifact_audit.label_hash
        )

    if not artifact_recoverable:
        reasons.append("artifact not recoverable (missing file, record, or hash mismatch)")
    if not feature_schema_consistent:
        reasons.append("feature schema hash mismatch between spec and artifact record")
    if not config_hash_consistent:
        reasons.append("target/config hash mismatch between spec and artifact record")

    status = (
        TEMPORAL_PROVENANCE_VERIFIED
        if (
            artifact_recoverable
            and not leakage_detected
            and config_hash_consistent
            and feature_schema_consistent
        )
        else TEMPORAL_PROVENANCE_UNVERIFIED
    )

    return CandidateProvenanceResult(
        candidate_id=candidate_id,
        model_name=model_name,
        artifact_audit=artifact_audit,
        artifact_recoverable=artifact_recoverable,
        training_end_before_eval_start=training_end_before_eval_start,
        training_start_before_eval_start=training_start_before_eval_start,
        eval_dataset_start=eval_dataset_start_utc,
        earliest_permissible_eval_ts=earliest_permissible_eval_ts,
        temporal_provenance_status=status,
        provenance_reasons=reasons,
        config_hash_consistent=config_hash_consistent,
        feature_schema_consistent=feature_schema_consistent,
        leakage_detected=leakage_detected,
    )


def run_temporal_provenance_audit(
    *,
    candidates: list[dict[str, Any]] | dict[str, dict[str, Any]],
    storage_root: Path | str | None = None,
    artifacts_root: Path | str | None = None,
    eval_dataset_start_utc: str | None = None,
    eval_dataset_end_utc: str | None = None,
    eval_dataset_hash: str | None = None,
    eval_observation_count: int | None = None,
    eval_expected_intervals: int | None = None,
    eval_coverage_fraction: float | None = None,
) -> TemporalProvenanceAuditReport:
    """Run the complete temporal provenance audit for all candidates."""
    generated_at = _utc_now_iso()
    root = Path(storage_root or DEFAULT_HISTORICAL_STORAGE_ROOT)
    arts = Path(artifacts_root or ARTIFACTS_ROOT)

    if isinstance(candidates, dict):
        candidate_items = sorted(candidates.items())
    else:
        candidate_items = [
            (str(spec.get("candidate_id", f"candidate-{index}")), spec)
            for index, spec in enumerate(candidates)
        ]

    candidate_results: dict[str, CandidateProvenanceResult] = {}
    for candidate_id, spec in candidate_items:
        model_name = str(spec.get("model_name", candidate_id))
        if spec.get("artifact_dir"):
            artifact_dir = Path(spec["artifact_dir"])
        else:
            artifact_dir = arts / str(spec.get("model_artifact_hash", candidate_id))
        artifact_audit = _audit_artifact(artifact_dir)
        result = audit_candidate_provenance(
            candidate_id=candidate_id,
            model_name=model_name,
            spec=spec,
            artifact_audit=artifact_audit,
            eval_dataset_start_utc=eval_dataset_start_utc,
            eval_dataset_end_utc=eval_dataset_end_utc,
        )
        candidate_results[candidate_id] = result

    verified = sorted(
        cid
        for cid, r in candidate_results.items()
        if r.temporal_provenance_status == TEMPORAL_PROVENANCE_VERIFIED
    )
    unverified = sorted(
        cid
        for cid, r in candidate_results.items()
        if r.temporal_provenance_status == TEMPORAL_PROVENANCE_UNVERIFIED
    )
    recoverable = sorted(cid for cid, r in candidate_results.items() if r.artifact_recoverable)
    leakage = sorted(cid for cid, r in candidate_results.items() if r.leakage_detected)

    audit_id = f"tpa-{
        _sha256_json(
            {
                'candidate_ids': [cid for cid, _ in candidate_items],
                'eval_dataset_start_utc': eval_dataset_start_utc,
                'eval_dataset_end_utc': eval_dataset_end_utc,
                'eval_dataset_hash': eval_dataset_hash,
            }
        )[:16]
    }"

    overall_summary = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "audit_id": audit_id,
        "n_candidates": len(candidate_results),
        "n_verified": len(verified),
        "n_unverified": len(unverified),
        "n_artifact_recoverable": len(recoverable),
        "n_leakage_detected": len(leakage),
        "verified_candidates": verified,
        "unverified_candidates": unverified,
        "recoverable_candidates": recoverable,
        "leakage_candidates": leakage,
        "all_verified": len(unverified) == 0 and len(candidate_results) > 0,
    }

    dataset_info = {
        "storage_root": str(root),
        "artifacts_root": str(arts),
        "eval_dataset_start_utc": eval_dataset_start_utc,
        "eval_dataset_end_utc": eval_dataset_end_utc,
        "eval_dataset_hash": eval_dataset_hash,
        "eval_observation_count": eval_observation_count,
        "eval_expected_intervals": eval_expected_intervals,
        "eval_coverage_fraction": eval_coverage_fraction,
    }

    return TemporalProvenanceAuditReport(
        schema_version=SCHEMA_VERSION,
        generated_at_utc=generated_at,
        audit_id=audit_id,
        storage_root=str(root),
        artifacts_root=str(arts),
        n_candidates=len(candidate_results),
        candidate_results=candidate_results,
        dataset_info=dataset_info,
        overall_summary=overall_summary,
    )


def write_audit_report(report: TemporalProvenanceAuditReport, output_path: Path | str) -> Path:
    """Write the audit report as JSON and return the output path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.to_json(indent=2), encoding="utf-8")
    return output_path


__all__ = [
    "ARTIFACTS_ROOT",
    "DEFAULT_HISTORICAL_STORAGE_ROOT",
    "DEFAULT_PROSPECTIVE_STORAGE_ROOT",
    "SCHEMA_VERSION",
    "TEMPORAL_PROVENANCE_UNVERIFIED",
    "TEMPORAL_PROVENANCE_VERIFIED",
    "ArtifactAuditResult",
    "CandidateProvenanceResult",
    "TemporalProvenanceAuditReport",
    "audit_all_artifacts",
    "audit_candidate_provenance",
    "run_temporal_provenance_audit",
    "write_audit_report",
]
