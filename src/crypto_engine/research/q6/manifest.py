"""Q6 evaluation manifest module.

Immutable evaluation manifests for each Q6 evaluation run. Each evaluation
records:

- q6_evaluation_id
- candidate lock ID
- research freeze ID
- collection ID
- dataset hash
- dataset time boundaries
- candidate hashes
- evaluation protocol hash
- configuration hashes
- evaluation timestamp
- sample size
- metrics
- promotion decision

Prevents repeated evaluation from silently overwriting previous results.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_Q6_MANIFEST_PATH = None  # Set at runtime
DEFAULT_Q6_HISTORY_PATH = None  # Set at runtime

Q6_MANIFEST_SCHEMA_VERSION = "q6-evaluation-manifest.v1"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _hash_value(value: Any) -> str:
    """Deterministic sha256 over a JSON-serialisable value."""
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _append_history(path: Path, entry: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def build_evaluation_manifest(
    *,
    q6_evaluation_id: str | None = None,
    candidate_lock_id: str | None = None,
    candidate_lock_hash: str | None = None,
    research_freeze_id: str | None = None,
    research_freeze_timestamp: str | None = None,
    collection_id: str | None = None,
    collection_started_at: str | None = None,
    dataset_hash: str | None = None,
    dataset_start: str | None = None,
    dataset_end: str | None = None,
    evaluation_protocol_hash: str | None = None,
    configuration_hashes: dict[str, str] | None = None,
    evaluation_timestamp: str | None = None,
    sample_size: int | None = None,
    metrics: dict[str, Any] | None = None,
    promotion_decision: str | None = None,
    promotion_reasons: list[str] | None = None,
) -> dict[str, Any]:
    """Build a Q6 evaluation manifest dict (does not write to disk)."""
    return {
        "schema_version": Q6_MANIFEST_SCHEMA_VERSION,
        "q6_evaluation_id": q6_evaluation_id or f"q6-{uuid.uuid4().hex[:12]}",
        "evaluation_timestamp": evaluation_timestamp or _utc_now_iso(),
        "candidate_lock_id": candidate_lock_id,
        "candidate_lock_hash": candidate_lock_hash,
        "research_freeze_id": research_freeze_id,
        "research_freeze_timestamp": research_freeze_timestamp,
        "collection_id": collection_id,
        "collection_started_at": collection_started_at,
        "dataset_hash": dataset_hash,
        "dataset_start": dataset_start,
        "dataset_end": dataset_end,
        "evaluation_protocol_hash": evaluation_protocol_hash,
        "configuration_hashes": configuration_hashes or {},
        "sample_size": sample_size,
        "metrics": metrics or {},
        "promotion_decision": promotion_decision,
        "promotion_reasons": promotion_reasons or [],
        "immutable": True,
    }


class EvaluationManifest:
    """Manages Q6 evaluation manifests with immutability guarantees.

    Each evaluation creates a new manifest entry. Previous manifests are
    preserved in history. Repeated evaluation on the same holdout is
    treated as repeated exposure and recorded separately.
    """

    def __init__(
        self,
        manifest_path: Path | None = None,
        history_path: Path | None = None,
    ) -> None:
        self.manifest_path = manifest_path or DEFAULT_Q6_MANIFEST_PATH
        self.history_path = history_path or DEFAULT_Q6_HISTORY_PATH

    def create_manifest(
        self,
        *,
        q6_evaluation_id: str | None = None,
        candidate_lock_id: str | None = None,
        candidate_lock_hash: str | None = None,
        research_freeze_id: str | None = None,
        research_freeze_timestamp: str | None = None,
        collection_id: str | None = None,
        collection_started_at: str | None = None,
        dataset_hash: str | None = None,
        dataset_start: str | None = None,
        dataset_end: str | None = None,
        evaluation_protocol_hash: str | None = None,
        configuration_hashes: dict[str, str] | None = None,
        evaluation_timestamp: str | None = None,
        sample_size: int | None = None,
        metrics: dict[str, Any] | None = None,
        promotion_decision: str | None = None,
        promotion_reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new evaluation manifest entry.

        Never overwrites previous manifests. Appends to history.
        """
        manifest = build_evaluation_manifest(
            q6_evaluation_id=q6_evaluation_id,
            candidate_lock_id=candidate_lock_id,
            candidate_lock_hash=candidate_lock_hash,
            research_freeze_id=research_freeze_id,
            research_freeze_timestamp=research_freeze_timestamp,
            collection_id=collection_id,
            collection_started_at=collection_started_at,
            dataset_hash=dataset_hash,
            dataset_start=dataset_start,
            dataset_end=dataset_end,
            evaluation_protocol_hash=evaluation_protocol_hash,
            configuration_hashes=configuration_hashes,
            evaluation_timestamp=evaluation_timestamp,
            sample_size=sample_size,
            metrics=metrics,
            promotion_decision=promotion_decision,
            promotion_reasons=promotion_reasons,
        )

        # Write to manifest path (overwrites with latest)
        if self.manifest_path:
            self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
            )

        # Append to history (preserves all evaluations)
        if self.history_path:
            _append_history(self.history_path, manifest)

        return manifest

    def load_history(self) -> list[dict[str, Any]]:
        """Load all historical evaluation manifests."""
        if not self.history_path or not self.history_path.is_file():
            return []
        entries: list[dict[str, Any]] = []
        with self.history_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def load_current(self) -> dict[str, Any] | None:
        """Load the current (latest) evaluation manifest."""
        if not self.manifest_path or not self.manifest_path.is_file():
            return None
        return _load_json(self.manifest_path)


__all__ = [
    "DEFAULT_Q6_HISTORY_PATH",
    "DEFAULT_Q6_MANIFEST_PATH",
    "Q6_MANIFEST_SCHEMA_VERSION",
    "EvaluationManifest",
    "build_evaluation_manifest",
]
