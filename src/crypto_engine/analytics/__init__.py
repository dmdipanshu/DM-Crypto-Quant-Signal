"""Analytics extraction — research-proxy wrappers (Stage 5).

Thin wrappers over existing validation code. Return directional/profitability
proxies only; never combine metrics into a single score. Never claim
profitability without valid out-of-sample support net of costs.
"""

from __future__ import annotations

from .holdout import validate_holdout
from .proxies import research_proxies

__all__ = ["research_proxies", "validate_holdout"]
