"""Research proxy computations (Stage 5).

Directional proxies computed from experiment records. Never combines
directional, trade-level, and portfolio-level metrics into one score.
Never claims profitability.
"""

from __future__ import annotations

from typing import Any


def research_proxies(experiments: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute research proxies from recorded experiment entries.

    Args:
        experiments: List of experiment dicts from ExperimentRegistry.list().

    Returns a dict with:
    - ``experiment_count``: int
    - ``by_status``: dict[str, int] — counts per status
    - ``avg_trials``: float | None — average trials across successes
    - ``directional_read``: str — "positive", "negative", or "neutral"
    - ``limitation``: str — citable caveat about proxy nature

    Never returns a single combined score. Directional read is based only on
    success/failure counts, not on P&L or Sharpe.
    """
    if not isinstance(experiments, list):
        return {
            "experiment_count": 0,
            "by_status": {},
            "avg_trials": None,
            "directional_read": "neutral",
            "limitation": "input must be a list of experiment records",
        }

    by_status: dict[str, int] = {}
    total_trials = 0
    trial_count = 0

    for entry in experiments:
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status", "unknown"))
        by_status[status] = by_status.get(status, 0) + 1
        try:
            trials = int(entry.get("n_trials", 0) or 0)
        except (TypeError, ValueError):
            continue
        if trials > 0:
            total_trials += trials
            trial_count += 1

    successes = by_status.get("success", 0)
    failures = by_status.get("failed", 0)

    if successes > failures:
        directional_read = "positive"
    elif failures > successes:
        directional_read = "negative"
    else:
        directional_read = "neutral"

    avg_trials = (total_trials / trial_count) if trial_count > 0 else None

    return {
        "experiment_count": len(experiments),
        "by_status": by_status,
        "avg_trials": avg_trials,
        "directional_read": directional_read,
        "limitation": (
            "Research proxies are directional indicators only; they do not "
            "constitute a profitability claim. Combine with holdout validation "
            "and cost-aware backtesting before any decision."
        ),
    }


__all__ = ["research_proxies"]
