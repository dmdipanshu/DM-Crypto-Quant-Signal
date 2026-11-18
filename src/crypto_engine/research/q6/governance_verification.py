"""Q6 governance verification and namespace isolation.

Read-only verification that Q6 historical validation respects governance
constraints:

- JSON result files under docs/validation/results/ that carry governance
  fields must reflect the frozen Q4 decision (NO_STRATEGY_ELIGIBLE), no
  prospective evidence, no Q5.2 eligibility, no promotion eligibility, and
  a BLOCKED promotion gate.
- The 1-week dataset (data/historical_accelerated) must be unmodified
  (manifest retrieval_timestamp present, ledger hash chain intact).
- The 12-month dataset (data/historical_accelerated_12m) must be separate
  from the 1-week dataset.
- Prospective data (data/prospective) must be isolated from historical data.
- Ledger hash chains must be valid (previous_hash links).

All operations are read-only: no dataset or result file is modified.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GOVERNANCE_SCHEMA_VERSION = "governance_verification.v1"

REQUIRED_GOVERNANCE_FIELDS: dict[str, Any] = {
    "official_prospective_evidence": False,
    "q5_2_eligible": False,
    "promotion_eligible": False,
    "q4_decision_preserved": True,
    "q4_decision": "NO_STRATEGY_ELIGIBLE",
    "promotion_gate": "BLOCKED",
}

HISTORICAL_STORAGE_ROOT = Path("data/historical_accelerated")
HISTORICAL_12M_STORAGE_ROOT = Path("data/historical_accelerated_12m")
PROSPECTIVE_STORAGE_ROOT = Path("data/prospective")
APPROVALS_ROOT = Path("docs/validation/approvals")
RESULTS_ROOT = Path("docs/validation/results")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_json(value: Any) -> str:
    """Deterministic sha256 over a JSON-serialisable value."""
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file as a dict, returning {} on any failure."""
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _check_governance_metadata(data: dict[str, Any], file_path: Path) -> GovernanceCheckResult:
    required = REQUIRED_GOVERNANCE_FIELDS
    missing_fields: list[str] = [field for field in sorted(required) if field not in data]
    mismatched_fields: dict[str, dict[str, Any]] = {}
    for field in sorted(required):
        if field in data and data[field] != required[field]:
            mismatched_fields[field] = {
                "expected": required[field],
                "actual": data[field],
            }
    extra_fields: list[str] = sorted(key for key in data if key not in required)
    compliant = not missing_fields and not mismatched_fields
    details: dict[str, Any] = {
        "required_fields": dict(required),
        "present_governance_fields": [field for field in sorted(required) if field in data],
    }
    return GovernanceCheckResult(
        file_path=str(file_path),
        compliant=compliant,
        missing_fields=missing_fields,
        mismatched_fields=mismatched_fields,
        extra_fields=extra_fields,
        details=details,
    )


def _check_1week_namespace() -> NamespaceCheckResult:
    root = HISTORICAL_STORAGE_ROOT
    manifest = _verify_dataset_manifest(root)
    violations: list[str] = []
    retrieval_timestamp: Any = None
    dataset_id: Any = None
    if manifest:
        retrieval_timestamp = manifest.get("retrieval_timestamp")
        dataset_id = manifest.get("dataset_id")
        if not retrieval_timestamp:
            violations.append("retrieval_timestamp missing in dataset manifest")
    else:
        violations.append("dataset_manifest missing")
    ledger = _verify_ledger_integrity(root)
    if not ledger.hash_chain_valid:
        violations.append("ingestion ledger hash chain invalid")
    isolated = not violations
    details: dict[str, Any] = {
        "root": str(root),
        "retrieval_timestamp": retrieval_timestamp,
        "dataset_id": dataset_id,
        "manifest_present": bool(manifest),
        "ledger_entries": ledger.ledger_entries,
        "hash_chain_valid": ledger.hash_chain_valid,
        "missing_hashes": ledger.missing_hashes,
        "orphaned_entries": ledger.orphaned_entries,
    }
    return NamespaceCheckResult(
        namespace="historical_accelerated",
        isolated=isolated,
        violations=violations,
        details=details,
    )


def _check_12m_namespace() -> NamespaceCheckResult:
    root = HISTORICAL_12M_STORAGE_ROOT
    manifest = _verify_dataset_manifest(root)
    manifest_1w = _verify_dataset_manifest(HISTORICAL_STORAGE_ROOT)
    violations: list[str] = []
    retrieval_timestamp: Any = None
    dataset_id_12m: Any = None
    dataset_id_1w: Any = None
    if manifest:
        retrieval_timestamp = manifest.get("retrieval_timestamp")
        dataset_id_12m = manifest.get("dataset_id")
        if not retrieval_timestamp:
            violations.append("retrieval_timestamp missing in dataset manifest")
    else:
        violations.append("dataset_manifest missing")
    if manifest and manifest_1w and dataset_id_12m == dataset_id_1w:
        violations.append("12-month dataset is not separate from 1-week dataset (same dataset_id)")
    if HISTORICAL_12M_STORAGE_ROOT == HISTORICAL_STORAGE_ROOT:
        violations.append("12-month storage root equals 1-week storage root")
    isolated = not violations
    details: dict[str, Any] = {
        "root": str(root),
        "retrieval_timestamp": retrieval_timestamp,
        "dataset_id_12m": dataset_id_12m,
        "dataset_id_1week": dataset_id_1w,
        "separate_from_1week": (
            dataset_id_12m != dataset_id_1w if (dataset_id_12m and dataset_id_1w) else None
        ),
        "manifest_present": bool(manifest),
    }
    return NamespaceCheckResult(
        namespace="historical_accelerated_12m",
        isolated=isolated,
        violations=violations,
        details=details,
    )


def _check_prospective_namespace() -> NamespaceCheckResult:
    root = PROSPECTIVE_STORAGE_ROOT
    violations: list[str] = []
    exists = root.is_dir()
    if not exists:
        violations.append("prospective storage root missing")
    same_1w = PROSPECTIVE_STORAGE_ROOT == HISTORICAL_STORAGE_ROOT
    same_12m = PROSPECTIVE_STORAGE_ROOT == HISTORICAL_12M_STORAGE_ROOT
    if same_1w or same_12m:
        violations.append("prospective storage root overlaps a historical storage root")
    isolated = not violations
    details: dict[str, Any] = {
        "root": str(root),
        "exists": exists,
        "separate_from_historical_1week": not same_1w,
        "separate_from_historical_12m": not same_12m,
    }
    return NamespaceCheckResult(
        namespace="prospective",
        isolated=isolated,
        violations=violations,
        details=details,
    )


def _check_namespace_isolation() -> list[NamespaceCheckResult]:
    return [
        _check_1week_namespace(),
        _check_12m_namespace(),
        _check_prospective_namespace(),
    ]


def _read_ledger_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not path.is_file():
        return entries
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _verify_ledger_integrity(storage_root: Path) -> LedgerIntegrityResult:
    ledger_path = storage_root / "ingestion_ledger.jsonl"
    entries = _read_ledger_entries(ledger_path)
    missing_hashes: list[str] = []
    orphaned_entries: list[str] = []
    hash_chain_valid = True
    previous_hash: str | None = None
    for entry in entries:
        event_id = str(entry.get("event_id", "<unknown>"))
        current_hash = entry.get("current_event_hash")
        prev_hash = entry.get("previous_event_hash")
        # A genesis entry (first) legitimately has previous_event_hash None;
        # every entry must carry a verifiable current_event_hash.
        if current_hash is None:
            missing_hashes.append(event_id)
            hash_chain_valid = False
        else:
            content_for_hash = {k: v for k, v in entry.items() if k != "current_event_hash"}
            expected_hash = _sha256_json(content_for_hash)
            if current_hash != expected_hash:
                missing_hashes.append(event_id)
                hash_chain_valid = False
        # Chain link: previous_event_hash must equal the prior entry's hash.
        if prev_hash != previous_hash:
            orphaned_entries.append(event_id)
            hash_chain_valid = False
        if current_hash is not None:
            previous_hash = current_hash
    return LedgerIntegrityResult(
        storage_root=str(storage_root),
        ledger_entries=len(entries),
        hash_chain_valid=hash_chain_valid,
        missing_hashes=missing_hashes,
        orphaned_entries=orphaned_entries,
    )


def _verify_dataset_manifest(storage_root: Path) -> dict[str, Any]:
    return _load_json(storage_root / "dataset_manifest.json")


@dataclass(frozen=True, slots=True)
class NamespaceCheckResult:
    namespace: str
    isolated: bool
    violations: list[str]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "isolated": self.isolated,
            "violations": list(self.violations),
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class GovernanceCheckResult:
    file_path: str
    compliant: bool
    missing_fields: list[str]
    mismatched_fields: dict[str, dict[str, Any]]
    extra_fields: list[str]
    details: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "compliant": self.compliant,
            "missing_fields": list(self.missing_fields),
            "mismatched_fields": dict(self.mismatched_fields),
            "extra_fields": list(self.extra_fields),
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class LedgerIntegrityResult:
    storage_root: str
    ledger_entries: int
    hash_chain_valid: bool
    missing_hashes: list[str]
    orphaned_entries: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "storage_root": self.storage_root,
            "ledger_entries": self.ledger_entries,
            "hash_chain_valid": self.hash_chain_valid,
            "missing_hashes": list(self.missing_hashes),
            "orphaned_entries": list(self.orphaned_entries),
        }


@dataclass(frozen=True, slots=True)
class CollectionAuthorizationResult:
    authorized: bool
    collection_id: str
    freeze_id: str
    reason: str
    approval_record: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorized": self.authorized,
            "collection_id": self.collection_id,
            "freeze_id": self.freeze_id,
            "reason": self.reason,
            "approval_record": dict(self.approval_record) if self.approval_record else None,
        }


@dataclass(frozen=True, slots=True)
class GovernanceVerificationReport:
    schema_version: str
    generated_at_utc: str
    namespace_checks: list[NamespaceCheckResult]
    governance_checks: list[GovernanceCheckResult]
    ledger_checks: list[LedgerIntegrityResult]
    overall_compliant: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at_utc": self.generated_at_utc,
            "namespace_checks": [nc.to_dict() for nc in self.namespace_checks],
            "governance_checks": [gc.to_dict() for gc in self.governance_checks],
            "ledger_checks": [lc.to_dict() for lc in self.ledger_checks],
            "overall_compliant": self.overall_compliant,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str)


class GovernanceVerifier:
    def __init__(self, *, results_root: Path | str | None = None) -> None:
        self.results_root = Path(results_root) if results_root is not None else RESULTS_ROOT

    def verify_all(self) -> GovernanceVerificationReport:
        namespace_checks = _check_namespace_isolation()
        governance_checks = self._collect_governance_checks()
        ledger_checks = [
            _verify_ledger_integrity(HISTORICAL_STORAGE_ROOT),
            _verify_ledger_integrity(HISTORICAL_12M_STORAGE_ROOT),
        ]
        overall_compliant = (
            all(nc.isolated for nc in namespace_checks)
            and all(gc.compliant for gc in governance_checks)
            and all(
                lc.hash_chain_valid and not lc.missing_hashes and not lc.orphaned_entries
                for lc in ledger_checks
            )
        )
        return GovernanceVerificationReport(
            schema_version=GOVERNANCE_SCHEMA_VERSION,
            generated_at_utc=_utc_now_iso(),
            namespace_checks=namespace_checks,
            governance_checks=governance_checks,
            ledger_checks=ledger_checks,
            overall_compliant=overall_compliant,
        )

    def _collect_governance_checks(self) -> list[GovernanceCheckResult]:
        checks: list[GovernanceCheckResult] = []
        if not self.results_root.is_dir():
            return checks
        for path in sorted(self.results_root.glob("*.json")):
            data = _load_json(path)
            if not data:
                continue
            if any(field in data for field in REQUIRED_GOVERNANCE_FIELDS):
                checks.append(_check_governance_metadata(data, path))
        return checks

    def verify_governance_file(self, path: Path | str) -> GovernanceCheckResult:
        data = _load_json(Path(path))
        return _check_governance_metadata(data, Path(path))

    def verify_namespace_isolation(self) -> list[NamespaceCheckResult]:
        return _check_namespace_isolation()

    def verify_ledger_integrity(self, storage_root: Path | str) -> LedgerIntegrityResult:
        return _verify_ledger_integrity(Path(storage_root))


def _load_approval_records(approvals_root: Path) -> list[dict[str, Any]]:
    """Load all approval records from the approvals directory."""
    records: list[dict[str, Any]] = []
    if not approvals_root.is_dir():
        return records
    for path in sorted(approvals_root.glob("*.json")):
        data = _load_json(path)
        if data and isinstance(data, dict):
            records.append(data)
    return records


def check_collection_authorization(
    collection_id: str,
    freeze_id: str,
    approvals_root: Path | str | None = None,
) -> CollectionAuthorizationResult:
    """Check if collection is authorized by an active, non-revoked approval record.
    
    Authorization requires an approval record that:
    - Exists in docs/validation/approvals/
    - Has status == "active"
    - Matches the exact collection_id and freeze_id
    - Is not revoked (revoked_at_utc is null)
    - Is valid (valid_from_utc <= now, expires_at_utc is null or > now)
    
    Revocation (status != "active" or revoked_at_utc set) immediately prevents
    future collection execution.
    """
    root = Path(approvals_root) if approvals_root is not None else APPROVALS_ROOT
    now = datetime.now(UTC)
    
    records = _load_approval_records(root)
    
    for record in records:
        if record.get("collection_id") != collection_id:
            continue
        if record.get("freeze_id") != freeze_id:
            continue
        
        status = record.get("status")
        revoked_at = record.get("revoked_at_utc")
        valid_from = record.get("valid_from_utc")
        expires_at = record.get("expires_at_utc")
        
        # Revocation check: status must be "active" and not revoked
        if status != "active":
            return CollectionAuthorizationResult(
                authorized=False,
                collection_id=collection_id,
                freeze_id=freeze_id,
                reason=f"approval record status is '{status}', not 'active'",
                approval_record=record,
            )
        if revoked_at is not None:
            return CollectionAuthorizationResult(
                authorized=False,
                collection_id=collection_id,
                freeze_id=freeze_id,
                reason=f"approval record revoked at {revoked_at}",
                approval_record=record,
            )
        
        # Validity window check
        if valid_from is not None:
            try:
                valid_from_dt = datetime.fromisoformat(valid_from)
                if now < valid_from_dt:
                    return CollectionAuthorizationResult(
                        authorized=False,
                        collection_id=collection_id,
                        freeze_id=freeze_id,
                        reason=f"approval record not yet valid (valid_from={valid_from})",
                        approval_record=record,
                    )
            except (ValueError, TypeError):
                pass
        
        if expires_at is not None:
            try:
                expires_at_dt = datetime.fromisoformat(expires_at)
                if now >= expires_at_dt:
                    return CollectionAuthorizationResult(
                        authorized=False,
                        collection_id=collection_id,
                        freeze_id=freeze_id,
                        reason=f"approval record expired at {expires_at}",
                        approval_record=record,
                    )
            except (ValueError, TypeError):
                pass
        
        # All checks passed
        return CollectionAuthorizationResult(
            authorized=True,
            collection_id=collection_id,
            freeze_id=freeze_id,
            reason="collection authorized by active, non-revoked approval record",
            approval_record=record,
        )
    
    # No matching approval record found
    return CollectionAuthorizationResult(
        authorized=False,
        collection_id=collection_id,
        freeze_id=freeze_id,
        reason="no active approval record found for this collection and freeze",
        approval_record=None,
    )


def run_governance_verification(
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    verifier = GovernanceVerifier()
    report = verifier.verify_all()
    if output_path is not None:
        write_governance_report(report, output_path)
    return report.to_dict()


def write_governance_report(
    report: GovernanceVerificationReport,
    output_path: Path | str | None = None,
) -> Path:
    if output_path is None:
        output_path = RESULTS_ROOT / "governance_verification_report.json"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.to_json(), encoding="utf-8")
    return path



__all__ = [
    "APPROVALS_ROOT",
    "GOVERNANCE_SCHEMA_VERSION",
    "HISTORICAL_12M_STORAGE_ROOT",
    "HISTORICAL_STORAGE_ROOT",
    "PROSPECTIVE_STORAGE_ROOT",
    "REQUIRED_GOVERNANCE_FIELDS",
    "RESULTS_ROOT",
    "CollectionAuthorizationResult",
    "GovernanceCheckResult",
    "GovernanceVerificationReport",
    "GovernanceVerifier",
    "LedgerIntegrityResult",
    "NamespaceCheckResult",
    "_check_governance_metadata",
    "_check_namespace_isolation",
    "_load_approval_records",
    "_load_json",
    "_sha256_json",
    "_utc_now_iso",
    "_verify_dataset_manifest",
    "_verify_ledger_integrity",
    "check_collection_authorization",
    "run_governance_verification",
    "write_governance_report",
]
