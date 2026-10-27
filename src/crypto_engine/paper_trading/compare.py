"""Divergence checks: paper account vs frozen backtest ledger (Phase 6 Track B).

:func:`compare_to_backtest` verifies that stepping a paper account over the
identical bar + signal sequence reproduces :func:`run_backtest` fills:
trade-count match, per-trade entry_ts/side/qty (relative tolerance) and
net_pnl (absolute tolerance), missing/extra trades listed, equity-end diff,
and a ``match``/``diverged`` verdict with reasons.

Paper-only trade keys (``paper_reason``, e.g. ``MANUAL_FLATTEN``) are
stripped before ``Trade`` validation so strict engine ledgers still parse.
``exit_reason`` is intentionally NOT compared (paper flatten reasons have no
engine counterpart); entry_ts/side/qty/net_pnl carry the fill-equality
signal. Tolerances are explicit parameters (defaults: qty 1e-9 relative,
pnl/equity 1e-6 absolute).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from crypto_engine.backtesting.engine import BacktestResult
from crypto_engine.backtesting.types import Trade

_VERDICT = Literal["match", "diverged"]


class TradeDivergence(BaseModel):
    """One named per-trade mismatch."""

    model_config = ConfigDict(extra="forbid")

    trade_id: str = ""
    field: str = ""
    paper: Any = None
    backtest: Any = None


class DivergenceReport(BaseModel):
    """Machine-readable comparison verdict."""

    model_config = ConfigDict(extra="forbid")

    verdict: _VERDICT = "diverged"
    reasons: list[str] = Field(default_factory=list)
    n_paper: int = 0
    n_backtest: int = 0
    missing_in_paper: list[str] = Field(default_factory=list)
    extra_in_paper: list[str] = Field(default_factory=list)
    trade_divergences: list[TradeDivergence] = Field(default_factory=list)
    equity_end_paper: float = 0.0
    equity_end_backtest: float = 0.0
    equity_end_diff: float = 0.0


def _coerce_trades(raw: list[Any]) -> list[Trade]:
    out: list[Trade] = []
    for item in raw:
        if isinstance(item, Trade):
            out.append(item)
            continue
        if not isinstance(item, dict):
            raise TypeError(f"compare: trade must be a Trade or dict, got {type(item).__name__}.")
        payload = {k: v for k, v in item.items() if k != "paper_reason"}
        out.append(Trade.model_validate(payload))
    return out


def compare_to_backtest(
    paper_trades: list[Any],
    backtest: BacktestResult,
    *,
    qty_rel_tol: float = 1e-9,
    pnl_abs_tol: float = 1e-6,
    equity_abs_tol: float = 1e-6,
) -> DivergenceReport:
    """Compare paper closed trades against a frozen backtest ledger."""
    if not isinstance(backtest, BacktestResult):
        raise TypeError(
            f"compare: backtest must be a BacktestResult, got {type(backtest).__name__}."
        )
    import math as _math

    for name, val in (
        ("qty_rel_tol", qty_rel_tol),
        ("pnl_abs_tol", pnl_abs_tol),
        ("equity_abs_tol", equity_abs_tol),
    ):
        try:
            fval = float(val)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"compare: {name} must be numeric, got {val!r}.") from exc
        if not _math.isfinite(fval) or fval < 0:
            raise ValueError(f"compare: {name} must be finite >= 0, got {val!r}.")
    paper = _coerce_trades(list(paper_trades))
    reference = list(backtest.trades)
    reasons: list[str] = []
    divergences: list[TradeDivergence] = []

    paper_ids = [t.trade_id for t in paper]
    ref_ids = [t.trade_id for t in reference]
    missing = [tid for tid in ref_ids if tid not in set(paper_ids)]
    extra = [tid for tid in paper_ids if tid not in set(ref_ids)]
    if missing:
        reasons.append(f"missing_in_paper: {missing}")
    if extra:
        reasons.append(f"extra_in_paper: {extra}")
    if len(paper) != len(reference):
        reasons.append(f"trade_count: paper={len(paper)} backtest={len(reference)}")

    for idx, (pt, bt) in enumerate(zip(paper, reference, strict=False)):
        tag = pt.trade_id if pt.trade_id == bt.trade_id else f"index-{idx}"

        def _flag(field: str, p: Any, b: Any, tag: str = tag) -> None:
            divergences.append(TradeDivergence(trade_id=tag, field=field, paper=p, backtest=b))
            reasons.append(f"trade {tag}: {field} paper={p!r} backtest={b!r}")

        if pt.entry.ts != bt.entry.ts:
            _flag("entry_ts", pt.entry.ts, bt.entry.ts)
        if pt.side != bt.side:
            _flag("side", pt.side, bt.side)
        denom = max(abs(float(bt.entry.qty)), 1e-18)
        if abs(float(pt.entry.qty) - float(bt.entry.qty)) / denom > float(qty_rel_tol):
            _flag("qty", float(pt.entry.qty), float(bt.entry.qty))
        if abs(float(pt.net_pnl) - float(bt.net_pnl)) > float(pnl_abs_tol):
            _flag("net_pnl", float(pt.net_pnl), float(bt.net_pnl))

    eq_backtest = float(backtest.summary.get("final_equity", 0.0)) if backtest.summary else 0.0
    report = DivergenceReport(
        verdict="match" if not reasons else "diverged",
        reasons=reasons,
        n_paper=len(paper),
        n_backtest=len(reference),
        missing_in_paper=missing,
        extra_in_paper=extra,
        trade_divergences=divergences,
        equity_end_paper=0.0,
        equity_end_backtest=eq_backtest,
        equity_end_diff=0.0,
    )
    return report


def compare_account_to_backtest(
    account: Any,
    backtest: BacktestResult,
    *,
    qty_rel_tol: float = 1e-9,
    pnl_abs_tol: float = 1e-6,
    equity_abs_tol: float = 1e-6,
) -> DivergenceReport:
    """Compare a paper account's closed trades + end equity vs a backtest ledger."""
    trades = list(account.trades_closed)
    report = compare_to_backtest(
        trades,
        backtest,
        qty_rel_tol=qty_rel_tol,
        pnl_abs_tol=pnl_abs_tol,
        equity_abs_tol=equity_abs_tol,
    )
    curve = list(getattr(account, "equity_curve", []) or [])
    eq_paper = float(curve[-1][1]) if curve else float(getattr(account, "starting_equity", 0.0))
    diff = eq_paper - report.equity_end_backtest
    report.equity_end_paper = eq_paper
    report.equity_end_diff = diff
    if abs(diff) > float(equity_abs_tol):
        report.reasons.append(
            f"equity_end: paper={eq_paper!r} backtest={report.equity_end_backtest!r}"
        )
        report.verdict = "diverged"
    return report


__all__ = [
    "DivergenceReport",
    "TradeDivergence",
    "compare_account_to_backtest",
    "compare_to_backtest",
]
