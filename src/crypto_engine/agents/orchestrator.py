"""Deterministic analyst orchestration (Phase 4 Track B).

Sequential loop only (``parallel`` defaults to ``False``; requesting
parallelism logs a warning and still runs sequentially on this 4 GB
machine). Merge is a template, never an LLM call, and never an LLM vote:
outputs are collected in configured order and concatenated. AI-disabled
(``provider=None``) returns ``disabled`` outputs plus a quant-only note with
exit-0 semantics — reduced functionality, never silent success.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from crypto_engine.agents.analysts import ANALYST_IDS, get_analyst
from crypto_engine.agents.schemas import AnalystOutput, EvidenceItem
from crypto_engine.ai_providers.base import AiProvider
from crypto_engine.ai_providers.budget import BudgetTracker

logger = logging.getLogger(__name__)


def _quant_note(quant_result: dict[str, Any] | None) -> str:
    if not quant_result:
        return "quant-only note: no quant result supplied."
    keys = ", ".join(sorted(str(k) for k in quant_result)[:12])
    return f"quant-only note (deterministic): keys {{{keys}}}."


def run_analysis(
    evidence: list[EvidenceItem] | list[dict[str, Any]],
    quant_result: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    provider: AiProvider | None = None,
    fallbacks: list[AiProvider] | None = None,
    budget: BudgetTracker | None = None,
) -> dict[str, Any]:
    """Run analysts sequentially and merge deterministically.

    Args:
        evidence: Supplied evidence items (models or raw dicts).
        quant_result: Canonical quant result dict (template-quoted, never sent
            to a model as instructions).
        config: ``{"analysts": [...], "parallel": False, "budget": {...}}``.
        provider: Primary provider, or ``None`` for AI-disabled mode.
        fallbacks: Accepted for interface parity; analysts call the primary
            only (fallback routing lives in Track A ``run_with_fallback``).
        budget: Optional :class:`BudgetTracker` snapshot for the report.

    Returns:
        Dict with ``outputs``, ``combined_summary``, ``fallback_chain``,
        ``correlation_id``, ``budget`` snapshot, and ``quant_note``.
    """
    cfg = dict(config or {})
    analyst_ids: list[str] = list(cfg.get("analysts", list(ANALYST_IDS)))
    unknown = [a for a in analyst_ids if a not in ANALYST_IDS]
    if unknown:
        raise ValueError(f"unknown analyst ids {unknown}; expected {ANALYST_IDS}.")
    parallel = bool(cfg.get("parallel", False))
    parallel_warned = False
    if parallel:
        logger.warning(
            "run_analysis: parallel=True requested but sequential execution "
            "enforced (4 GB machine, concurrency 1)."
        )
        parallel_warned = True
    correlation_id = str(cfg.get("correlation_id", f"ai-{uuid.uuid4().hex[:12]}"))
    source_ts = str(cfg.get("source_ts", datetime.now(UTC).isoformat()))

    items: list[EvidenceItem] = [
        e if isinstance(e, EvidenceItem) else EvidenceItem(**e) for e in evidence
    ]
    context: dict[str, Any] = {
        "correlation_id": correlation_id,
        "source_ts": source_ts,
        "quant_result": dict(quant_result or {}),
        "evidence_ids": [e.id for e in items],
    }
    outputs: list[AnalystOutput] = []
    for analyst_id in analyst_ids:  # sequential loop; concurrency is always 1
        analyst = get_analyst(analyst_id)
        if analyst_id == "judge":
            by_id = {o.analyst_id: o for o in outputs}
            context["bull"] = by_id.get("bull")
            context["bear"] = by_id.get("bear")
        outputs.append(analyst.run(items, provider, context))
        by_id = {o.analyst_id: o for o in outputs}
        context["bull"] = by_id.get("bull")
        context["bear"] = by_id.get("bear")

    lines = ["combined analysis (template merge, NOT an LLM call; order = configured order):"]
    for output in outputs:
        lines.append(f"- [{output.analyst_id}/{output.status}] {output.summary[:300]}")
    lines.append(_quant_note(quant_result))
    if provider is None:
        lines.append("AI disabled: all analyst outputs are 'disabled'; quant-only.")

    chain = [provider.provider_id] if provider is not None else []
    for fallback in fallbacks or []:
        chain.append(fallback.provider_id)
    return {
        "outputs": outputs,
        "combined_summary": "\n".join(lines),
        "fallback_chain": chain,
        "correlation_id": correlation_id,
        "parallel_warned": parallel_warned,
        "budget": budget.snapshot() if budget is not None else None,
        "quant_note": _quant_note(quant_result),
        "ai_disabled": provider is None,
    }


__all__ = ["run_analysis"]
