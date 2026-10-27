"""Triple-barrier labels (Phase 2, Track A)."""

from crypto_engine.labels.triple_barrier import (
    LABEL_COLUMNS,
    TripleBarrierConfig,
    assert_no_leakage,
    label_config_hash,
    label_triple_barrier,
    load_labels,
    save_labels,
    to_label_frame,
    valid_mask,
)

__all__ = [
    "LABEL_COLUMNS",
    "TripleBarrierConfig",
    "assert_no_leakage",
    "label_config_hash",
    "label_triple_barrier",
    "load_labels",
    "save_labels",
    "to_label_frame",
    "valid_mask",
]
