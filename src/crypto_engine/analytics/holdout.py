"""Holdout validation (Stage 5).

Returns pristine/used status and adequacy verdict from a HoldoutLedger.
Never exposes holdout data itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from crypto_engine.validation.holdout import HoldoutLedger, adequacy_report


def validate_holdout(ledger_path: str | Path, min_n: int = 100) -> dict[str, Any]:
    """Validate holdout ledger state without exposing holdout data.

    Args:
        ledger_path: Path to the HoldoutLedger JSON file.
        min_n: Adequacy bar for a directional read (default 100).

    Returns a dict with:
    - ``available``: bool — ledger exists and is readable
    - ``pristine``: bool — True if holdout never evaluated
    - ``access_count``: int — number of recorded accesses
    - ``adequacy``: dict — adequacy_report result
    - ``limitation``: str — citable caveat

    Never returns holdout rows, timestamps, or computed metrics from the
    holdout itself.
    """
    path = Path(ledger_path)
    if not path.is_file():
        return {
            "available": False,
            "pristine": None,
            "access_count": None,
            "adequacy": None,
            "limitation": "holdout ledger not found",
        }

    try:
        ledger = HoldoutLedger.load(path)
    except Exception as exc:
        return {
            "available": False,
            "pristine": None,
            "access_count": None,
            "adequacy": None,
            "limitation": f"holdout ledger unreadable: {type(exc).__name__}",
        }

    adequacy = adequacy_report(ledger.n_total, min_n=min_n)

    return {
        "available": True,
        "pristine": ledger.is_pristine,
        "access_count": ledger.access_count,
        "adequacy": adequacy,
        "limitation": (
            "Holdout validation reports ledger state and sample adequacy only; "
            "it does not validate any metric computed on the holdout data."
        ),
    }


__all__ = ["validate_holdout"]
