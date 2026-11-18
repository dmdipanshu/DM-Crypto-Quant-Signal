"""Q5 prospective data collection package.

Provides Binance prospective market-data collection, append-only storage,
tamper-evident hash-chain ledger, collection manifest governance, and
prospective holdout lifecycle management.

Read-only public market data only. No trading, no orders, no execution.
"""

from crypto_engine.research.prospective.collector import (
    CollectionStatus,
    ProspectiveCollector,
)
from crypto_engine.research.prospective.config import (
    COLLECTION_SCHEMA_VERSION,
    COLLECTION_STORAGE_FORMAT,
    DEFAULT_COLLECTION_HISTORY_PATH,
    DEFAULT_COLLECTION_MANIFEST_PATH,
    DEFAULT_EXCHANGE,
    DEFAULT_MARKET_TYPE,
    DEFAULT_PROSPECTIVE_STORAGE_ROOT,
    DEFAULT_SYMBOLS,
    DEFAULT_TIMEFRAMES,
    ProspectiveCollectionPolicy,
    hash_collection_config,
)
from crypto_engine.research.prospective.governance import (
    PROSPECTIVE_STATUSES,
    assess_prospective_holdout_readiness,
    historical_holdout_is_contaminated,
)
from crypto_engine.research.prospective.manifest import (
    SCHEMA_VERSION as COLLECTION_MANIFEST_SCHEMA_VERSION,
)
from crypto_engine.research.prospective.manifest import (
    CollectionConfigMismatch,
    build_collection_manifest,
    create_or_reuse_collection,
    load_collection_history,
    verify_collection_integrity,
)
from crypto_engine.research.prospective.quality import (
    compute_quality_report,
    is_quality_state_healthy,
)
from crypto_engine.research.prospective.storage import (
    DATASET_SCHEMA_VERSION,
    LEDGER_SCHEMA_VERSION,
    ProspectiveStorage,
)

__all__ = [
    "COLLECTION_MANIFEST_SCHEMA_VERSION",
    "COLLECTION_SCHEMA_VERSION",
    "COLLECTION_STORAGE_FORMAT",
    "DATASET_SCHEMA_VERSION",
    "DEFAULT_COLLECTION_HISTORY_PATH",
    "DEFAULT_COLLECTION_MANIFEST_PATH",
    "DEFAULT_EXCHANGE",
    "DEFAULT_MARKET_TYPE",
    "DEFAULT_PROSPECTIVE_STORAGE_ROOT",
    "DEFAULT_SYMBOLS",
    "DEFAULT_TIMEFRAMES",
    "LEDGER_SCHEMA_VERSION",
    "PROSPECTIVE_STATUSES",
    "CollectionConfigMismatch",
    "CollectionStatus",
    "ProspectiveCollectionPolicy",
    "ProspectiveCollector",
    "ProspectiveStorage",
    "assess_prospective_holdout_readiness",
    "build_collection_manifest",
    "compute_quality_report",
    "create_or_reuse_collection",
    "hash_collection_config",
    "historical_holdout_is_contaminated",
    "is_quality_state_healthy",
    "load_collection_history",
    "verify_collection_integrity",
]
