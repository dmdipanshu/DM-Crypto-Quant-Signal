"""Append-only project progress ledger (read-only dashboard source).

This package is intentionally dependency-free beyond the Python standard
library: it validates JSONL events, maintains an atomically replaced derived
manifest, and exposes bounded readers for the GET-only progress endpoints.
No trading, AI provider, network, or execution code is imported here.
"""

from __future__ import annotations

from crypto_engine.progress.ledger import (
    EVENTS_FILENAME,
    MANIFEST_FILENAME,
    SCHEMA_FILENAME,
    ProgressLedger,
    resolve_progress_root,
)
from crypto_engine.progress.schema import (
    LEVELS,
    STATUSES,
    ProgressValidationError,
    check_transition,
    normalize_event,
    schema_document,
)

__all__ = [
    "EVENTS_FILENAME",
    "LEVELS",
    "MANIFEST_FILENAME",
    "SCHEMA_FILENAME",
    "STATUSES",
    "ProgressLedger",
    "ProgressValidationError",
    "check_transition",
    "normalize_event",
    "resolve_progress_root",
    "schema_document",
]
