"""Locked final-holdout protocol: chronological tail split + access ledger.

Protocol (explicit, versioned, auditable; no OS enforcement)
------------------------------------------------------------
1. :func:`split_holdout` carves the final ``holdout_frac`` tail off the
   series. All research — walk-forward folds, calibration, model selection,
   threshold tuning — runs on the research head only.
2. A :class:`HoldoutLedger` JSON file (schema ``holdout-ledger-v1``) records
   the holdout boundary, creation time, and every evaluation access with a
   stated purpose. The ledger is honesty-based: nothing in code prevents
   reading the holdout rows, so discipline comes from the audit trail, not
   from file locks.
3. The first call to :meth:`HoldoutLedger.mark_accessed` flips
   :attr:`HoldoutLedger.is_pristine` from ``True`` to ``False`` permanently.
   A pristine ledger means "holdout never evaluated"; any published number
   computed on the holdout must cite the ledger's access log.
4. :func:`adequacy_report` judges whether the holdout is large enough for
   even a directional read. Never claim a definitive result on an
   ``insufficient`` holdout — collect more data or report the research
   split only, with the limitation stated.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from crypto_engine.models.base import InformativeError

#: Ledger schema version (bumped if fields change; old ledgers stay readable).
LEDGER_SCHEMA = "holdout-ledger-v1"

#: Absolute floor: fewer rows cannot support even a directional statement.
ABSOLUTE_MIN_HOLDOUT = 30


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def split_holdout(n: int, holdout_frac: float) -> tuple[np.ndarray, np.ndarray]:
    """Split ``n`` rows into ``(research_idx, holdout_idx)``.

    The holdout is the chronological tail: ``n_hold = floor(n *
    holdout_frac)`` rows ``[n - n_hold, n)``; research is ``[0, n -
    n_hold)``. ``holdout_frac=0`` yields an empty holdout.

    Raises:
        InformativeError: ``n <= 0`` or ``holdout_frac`` outside
            ``[0, 0.4]`` (caps the tail at 40% so research keeps a majority).
    """
    n = int(n)
    if n <= 0:
        raise InformativeError(f"holdout: n must be > 0 (got {n!r}).")
    frac = float(holdout_frac)
    if not 0.0 <= frac <= 0.4:
        raise InformativeError(
            f"holdout: holdout_frac must lie in [0, 0.4] (got {holdout_frac!r})."
        )
    n_hold = int(n * frac)  # floor: never over-allocates the tail
    cut = n - n_hold
    return np.arange(cut, dtype=np.int64), np.arange(cut, n, dtype=np.int64)


class HoldoutLedger:
    """Tiny JSON audit log for final-holdout access.

    Attributes mirror the file schema; use :meth:`create` for a new ledger
    and :meth:`load` to reopen one. Every mutation persists atomically
    (tmp file + ``os.replace``).
    """

    def __init__(
        self,
        path: Path | str,
        holdout_start: int,
        n_total: int,
        holdout_frac: float,
        created_at: str = "",
        access_count: int = 0,
        log: list[dict[str, str]] | None = None,
    ) -> None:
        self.path = Path(path)
        self.holdout_start = int(holdout_start)
        self.n_total = int(n_total)
        self.holdout_frac = float(holdout_frac)
        self.created_at = created_at or _utc_now_iso()
        self.access_count = int(access_count)
        self.log: list[dict[str, str]] = list(log or [])

    @property
    def is_pristine(self) -> bool:
        """``True`` until the first :meth:`mark_accessed` call."""
        return self.access_count == 0

    @classmethod
    def create(
        cls, path: Path | str, holdout_start: int, n_total: int, holdout_frac: float
    ) -> HoldoutLedger:
        """Create a new pristine ledger; refuses to overwrite an existing one."""
        target = Path(path)
        if target.exists():
            raise FileExistsError(f"holdout: ledger already exists (no overwrite): {target}")
        ledger = cls(target, holdout_start, n_total, holdout_frac)
        ledger._persist()
        return ledger

    @classmethod
    def load(cls, path: Path | str) -> HoldoutLedger:
        """Reload a ledger written by :meth:`create`/earlier accesses."""
        target = Path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise InformativeError(f"holdout: cannot read ledger {target}: {exc}") from exc
        if payload.get("schema") != LEDGER_SCHEMA:
            raise InformativeError(
                f"holdout: ledger schema {payload.get('schema')!r} != {LEDGER_SCHEMA!r}."
            )
        return cls(
            target,
            holdout_start=int(payload["holdout_start"]),
            n_total=int(payload["n_total"]),
            holdout_frac=float(payload["holdout_frac"]),
            created_at=str(payload.get("created_at", "")),
            access_count=int(payload.get("access_count", 0)),
            log=[dict(entry) for entry in payload.get("log", [])],
        )

    def mark_accessed(self, purpose: str) -> HoldoutLedger:
        """Record one holdout evaluation; ends pristineness permanently.

        Args:
            purpose: Non-empty statement of why the holdout is touched
                (e.g. ``"final unbiased accuracy read, exp abc123"``).

        Raises:
            InformativeError: Empty purpose.
        """
        if not str(purpose).strip():
            raise InformativeError("holdout: purpose must be a non-empty string.")
        self.access_count += 1
        self.log.append({"purpose": str(purpose), "at": _utc_now_iso()})
        self._persist()
        return self

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe ledger snapshot."""
        return {
            "schema": LEDGER_SCHEMA,
            "holdout_start": self.holdout_start,
            "n_total": self.n_total,
            "holdout_frac": self.holdout_frac,
            "created_at": self.created_at,
            "access_count": self.access_count,
            "is_pristine": self.is_pristine,
            "log": [dict(entry) for entry in self.log],
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2).encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            raise


def adequacy_report(n_holdout: int, min_n: int = 100) -> dict[str, Any]:
    """Judge whether the holdout supports even a directional read.

    Verdicts: ``sufficient`` (``n >= min_n``), ``marginal`` (``min_n / 2
    <= n < min_n`` — report with wide uncertainty), ``insufficient``
    (``n < min_n / 2`` or below the absolute floor of 30 rows — do not
    report definitive conclusions).

    Args:
        n_holdout: Holdout row count.
        min_n: Adequacy bar for a directional read (default 100).

    Returns:
        Dict with ``verdict``, ``n_holdout``, ``min_n``, ``reasons``, and
        ``limitation`` (a citable caveat string).
    """
    n_holdout = int(n_holdout)
    min_n = int(min_n)
    if min_n < 1:
        raise InformativeError(f"holdout: min_n must be >= 1 (got {min_n!r}).")
    if n_holdout < 0:
        raise InformativeError(f"holdout: n_holdout must be >= 0 (got {n_holdout!r}).")
    if n_holdout >= min_n:
        verdict = "sufficient"
        reasons = [
            f"n_holdout={n_holdout} meets min_n={min_n} for a directional read.",
            "Sufficiency is about sample size only; regime shift can still invalidate reads.",
        ]
    elif n_holdout >= max(ABSOLUTE_MIN_HOLDOUT, min_n // 2):
        verdict = "marginal"
        reasons = [
            f"n_holdout={n_holdout} is below min_n={min_n}; treat estimates as noisy.",
            "Report intervals, not point claims; note the short-holdout limitation.",
        ]
    else:
        verdict = "insufficient"
        reasons = [
            f"n_holdout={n_holdout} is below min_n={min_n} (floor {ABSOLUTE_MIN_HOLDOUT}).",
            "Never claim a definitive result on this holdout; report research-split "
            "diagnostics only, or collect more data.",
        ]
    return {
        "verdict": verdict,
        "n_holdout": n_holdout,
        "min_n": min_n,
        "reasons": reasons,
        "limitation": (
            "Holdout reads are unbiased only if the holdout was never used for "
            "selection (see HoldoutLedger); small holdouts remain noisy even when unbiased."
        ),
    }


__all__ = ["HoldoutLedger", "adequacy_report", "split_holdout"]
