"""Deterministic risk gates (Phase 5 Track B).

Gates run in a fixed order over a decided :class:`SignalRecord` draft plus a
caller-supplied :class:`GateContext`. The first failing gate flips the record
via :func:`apply_gate_veto` with a ``RISK_GATE_<NAME>`` reason code
(schema-valid per ``REASON_PATTERN``). Passing gates never change the record:
an ``approved`` record stays approved, and an ``abstained``/``rejected``
record is never upgraded.

Gate order (fixed, auditable):

A. data: FRESHNESS -> COMPLETENESS -> CONSISTENCY
B. model: ARTIFACT -> SCHEMA -> OUTPUT -> CALIBRATION
C. decision: DIRECTIONS -> DISAGREEMENT -> EDGE -> AGE -> HORIZON -> REGIME -> AI_VETO
D. exposure: TRADE_RISK -> EXPOSURE -> DRAWDOWN -> STRATEGY -> CIRCUIT

Exposure inputs are caller-supplied only and NEVER invented: when no
portfolio snapshot is supplied and risk gates are enabled, the exposure block
is reported as unavailable (``EXPOSURE_UNKNOWN``) and fails closed
(veto) unless ``fail_closed`` is false (pass with a warning). ``NO_TRADE``
is a valid output; flat directions (NEUTRAL/NO_TRADE) skip the EDGE and
TRADE_RISK checks (no position, no edge required -- mirrors policy).

AI is advisory-only: gates ignore the AI payload except for the AI_VETO
presence check. AI can never change a direction.

4 GB laptop: pure math, single-process, no network, no randomness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crypto_engine.config.settings import RiskConfig
from crypto_engine.signals.policy import apply_gate_veto
from crypto_engine.signals.schema import SignalRecord

GateTrace = list[dict[str, str]]

GateResult = tuple[SignalRecord, GateTrace]

_REQUIRED_PORTFOLIO_KEYS = (
    "exposure_pct",
    "symbol_exposure_pct",
    "day_start_equity",
    "current_equity",
    "strategy_enabled",
    "circuit_open",
)

#: Fixed evaluation order; each entry is (gate_tag, reason_code).
GATE_ORDER: tuple[tuple[str, str], ...] = (
    ("risk:freshness", "RISK_GATE_FRESHNESS"),
    ("risk:completeness", "RISK_GATE_COMPLETENESS"),
    ("risk:consistency", "RISK_GATE_CONSISTENCY"),
    ("risk:artifact", "RISK_GATE_ARTIFACT"),
    ("risk:schema", "RISK_GATE_SCHEMA"),
    ("risk:output", "RISK_GATE_OUTPUT"),
    ("risk:calibration", "RISK_GATE_CALIBRATION"),
    ("risk:directions", "RISK_GATE_DIRECTIONS"),
    ("risk:disagreement", "RISK_GATE_DISAGREEMENT"),
    ("risk:edge", "RISK_GATE_EDGE"),
    ("risk:age", "RISK_GATE_AGE"),
    ("risk:horizon", "RISK_GATE_HORIZON"),
    ("risk:regime", "RISK_GATE_REGIME"),
    ("risk:ai", "RISK_GATE_AI"),
    ("risk:trade-risk", "RISK_GATE_TRADE_RISK"),
    ("risk:exposure", "RISK_GATE_EXPOSURE"),
    ("risk:drawdown", "RISK_GATE_DRAWDOWN"),
    ("risk:strategy", "RISK_GATE_STRATEGY"),
    ("risk:circuit", "RISK_GATE_CIRCUIT"),
)


def _validate_portfolio(portfolio: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate a caller-supplied portfolio snapshot (never invent values)."""
    if portfolio is None:
        return None
    if not isinstance(portfolio, dict):
        raise ValueError(f"GateContext: portfolio must be a dict or None, got {type(portfolio)}.")
    missing = [k for k in _REQUIRED_PORTFOLIO_KEYS if k not in portfolio]
    if missing:
        raise ValueError(f"GateContext: portfolio missing keys {missing} (never invented).")
    for key in ("exposure_pct", "symbol_exposure_pct", "day_start_equity", "current_equity"):
        val = portfolio[key]
        if not isinstance(val, (int, float)) or not math.isfinite(float(val)):
            raise ValueError(f"GateContext: portfolio[{key!r}] must be finite, got {val!r}.")
        if float(val) < 0:
            raise ValueError(f"GateContext: portfolio[{key!r}] must be >= 0, got {val!r}.")
    for key in ("day_start_equity",):
        if float(portfolio[key]) <= 0:
            raise ValueError(
                f"GateContext: portfolio[{key!r}] must be > 0, got {portfolio[key]!r}."
            )
    if not isinstance(portfolio["strategy_enabled"], bool):
        raise ValueError("GateContext: portfolio['strategy_enabled'] must be bool.")
    if not isinstance(portfolio["circuit_open"], bool):
        raise ValueError("GateContext: portfolio['circuit_open'] must be bool.")
    return dict(portfolio)


@dataclass
class GateContext:
    """Caller-supplied inputs for risk-gate evaluation.

    The portfolio snapshot is supplied by the caller (strategy/risk layer)
    and validated strictly; missing snapshots are reported as
    ``EXPOSURE_UNKNOWN``, never zero-filled.
    """

    now_utc: datetime
    risk: RiskConfig = field(default_factory=RiskConfig)
    max_age_min: float = 60.0
    staleness_limit_min: float = 90.0
    feature_timestamp: datetime | None = None
    enabled_directions: set[str] = field(
        default_factory=lambda: {"LONG", "SHORT", "NEUTRAL", "NO_TRADE"}
    )
    blocked_regimes: set[str] = field(default_factory=set)
    require_edge: bool = True
    edge_margin_bp: float = 0.0
    ai_required: bool = False
    model_disagreement: bool = False
    label_set: list[int] | None = None
    threshold_version: str | None = None
    feature_names: list[str] | None = None
    proposed_risk_pct: float | None = None
    portfolio: dict[str, Any] | None = None
    fail_closed: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.now_utc, datetime) or self.now_utc.tzinfo is None:
            raise ValueError("GateContext: now_utc must be timezone-aware (UTC).")
        for name in ("max_age_min", "staleness_limit_min"):
            val = float(getattr(self, name))
            if not math.isfinite(val) or val < 0:
                raise ValueError(f"GateContext: {name} must be finite >= 0, got {val!r}.")
        if self.feature_timestamp is not None and (
            not isinstance(self.feature_timestamp, datetime)
            or self.feature_timestamp.tzinfo is None
        ):
            raise ValueError("GateContext: feature_timestamp must be timezone-aware or None.")
        unknown = set(self.enabled_directions) - {"LONG", "SHORT", "NEUTRAL", "NO_TRADE"}
        if unknown:
            raise ValueError(f"GateContext: unknown enabled_directions {sorted(unknown)!r}.")
        if self.proposed_risk_pct is not None:
            val = float(self.proposed_risk_pct)
            if not math.isfinite(val) or val < 0:
                raise ValueError(
                    f"GateContext: proposed_risk_pct must be finite >= 0, got {val!r}."
                )
        if not math.isfinite(float(self.edge_margin_bp)) or float(self.edge_margin_bp) < 0:
            raise ValueError("GateContext: edge_margin_bp must be finite >= 0.")
        object.__setattr__(self, "portfolio", _validate_portfolio(self.portfolio))

    @property
    def effective_fail_closed(self) -> bool:
        """Explicit override wins; otherwise the RiskConfig default."""
        if self.fail_closed is not None:
            return bool(self.fail_closed)
        return bool(self.risk.fail_closed)


def _age_seconds(now: datetime, ts: datetime) -> float:
    return max(0.0, (now - ts).total_seconds())


def run_gates(
    record: SignalRecord,
    context: GateContext,
    artifact_hash: str | None = None,
    feature_schema: list[str] | None = None,
) -> GateResult:
    """Evaluate gates in fixed order; first failure vetoes.

    Args:
        record: Decided signal draft (usually from :func:`decide`).
        context: Caller-supplied gate inputs (time, risk config, portfolio).
        artifact_hash: Loaded artifact sha256 when available (ARTIFACT check).
        feature_schema: Expected feature names when available (SCHEMA check).

    Returns:
        ``(record, gate_trace)`` where ``record`` is unchanged when all
        gates pass, else vetoed (``rejected`` + ``RISK_GATE_*`` code, or
        abstained ``NEUTRAL`` for MODEL_DISAGREEMENT).
    """
    trace: GateTrace = []
    now = context.now_utc
    direction = record.direction

    def _veto(gate: str, code: str, detail: str, observed: str = "", limit: str = "") -> GateResult:
        trace.append(
            {
                "gate": gate,
                "outcome": f"veto:{detail}",
                "observed": observed,
                "limit": limit,
            }
        )
        return apply_gate_veto(record, gate, code), trace

    def _pass(gate: str, note: str = "pass", observed: str = "", limit: str = "") -> None:
        trace.append({"gate": gate, "outcome": note, "observed": observed, "limit": limit})

    # -- A. data gates ----------------------------------------------------
    age_s = _age_seconds(now, record.generated_at)
    if age_s <= float(context.max_age_min) * 60.0:
        _pass("risk:freshness", observed=f"{age_s:.1f}s", limit=f"{context.max_age_min}min")
    else:
        return _veto(
            "risk:freshness",
            "RISK_GATE_FRESHNESS",
            "STALE_DATA",
            observed=f"{age_s:.1f}s",
            limit=f"{context.max_age_min}min",
        )
    if context.feature_timestamp is not None:
        feat_age_s = _age_seconds(now, context.feature_timestamp)
        if feat_age_s > float(context.staleness_limit_min) * 60.0:
            return _veto(
                "risk:freshness",
                "RISK_GATE_FRESHNESS",
                "STALE_DATA",
                observed=f"feature_age={feat_age_s:.1f}s",
                limit=f"{context.staleness_limit_min}min",
            )
        _pass(
            "risk:freshness:features",
            observed=f"{feat_age_s:.1f}s",
            limit=f"{context.staleness_limit_min}min",
        )

    if record.data_quality == "validated":
        _pass("risk:completeness", observed=str(record.data_quality))
    else:
        detail = {"stale": "STALE_DATA", "incomplete": "INCOMPLETE_DATA"}.get(
            str(record.data_quality), "INVALID"
        )
        return _veto(
            "risk:completeness", "RISK_GATE_COMPLETENESS", detail, observed=str(record.data_quality)
        )

    if record.valid_until > record.generated_at:
        _pass("risk:consistency")
    else:  # pragma: no cover - schema already enforces; defense in depth
        return _veto("risk:consistency", "RISK_GATE_CONSISTENCY", "INVALID_TIMESTAMPS")

    # -- B. model gates ---------------------------------------------------
    if not record.model_artifact_hash:
        return _veto("risk:artifact", "RISK_GATE_ARTIFACT", "ARTIFACT_MISSING", observed="empty")
    if artifact_hash is not None and artifact_hash != record.model_artifact_hash:
        return _veto("risk:artifact", "RISK_GATE_ARTIFACT", "ARTIFACT_CORRUPT")
    _pass("risk:artifact", observed="hash-match" if artifact_hash is not None else "present")

    if (
        feature_schema is not None
        and context.feature_names is not None
        and list(context.feature_names) != list(feature_schema)
    ):
        return _veto(
            "risk:schema",
            "RISK_GATE_SCHEMA",
            "SCHEMA_MISMATCH",
            observed=str(list(context.feature_names)),
            limit=str(list(feature_schema)),
        )
    _pass("risk:schema", observed="match" if feature_schema is not None else "unchecked")

    raw = record.prediction.raw_score
    if raw is None:
        return _veto("risk:output", "RISK_GATE_OUTPUT", "INVALID_OUTPUT", observed="none")
    try:
        raw_f = float(raw)
    except (TypeError, ValueError):
        return _veto("risk:output", "RISK_GATE_OUTPUT", "INVALID_OUTPUT", observed=str(raw))
    if not math.isfinite(raw_f):
        return _veto("risk:output", "RISK_GATE_OUTPUT", "NON_FINITE")
    if not 0.0 <= raw_f <= 1.0:
        return _veto(
            "risk:output", "RISK_GATE_OUTPUT", "INVALID_OUTPUT", observed=str(raw_f), limit="[0,1]"
        )
    _pass("risk:output", observed=str(raw_f))

    cal_status = record.prediction.calibration_status
    if cal_status == "mismatch":
        return _veto("risk:calibration", "RISK_GATE_CALIBRATION", "CALIBRATION_MISMATCH")
    if cal_status == "calibrated":
        problems: list[str] = []
        if record.prediction.positive_class is None:
            problems.append("positive_class-none")
        elif context.label_set is not None and int(record.prediction.positive_class) not in [
            int(c) for c in context.label_set
        ]:
            problems.append("positive_class-not-in-label-set")
        if (
            context.threshold_version is not None
            and record.prediction.threshold_version != context.threshold_version
        ):
            problems.append("threshold-version-mismatch")
        if problems:
            return _veto(
                "risk:calibration",
                "RISK_GATE_CALIBRATION",
                "CALIBRATION_MISMATCH",
                observed=",".join(problems),
            )
    _pass("risk:calibration", observed=str(cal_status))

    # -- C. decision gates ------------------------------------------------
    if direction not in context.enabled_directions:
        return _veto(
            "risk:directions",
            "RISK_GATE_DIRECTIONS",
            "DIRECTION_DISABLED",
            observed=str(direction),
            limit=str(sorted(context.enabled_directions)),
        )
    _pass("risk:directions", observed=str(direction))

    if context.model_disagreement and direction in ("LONG", "SHORT"):
        trace.append(
            {
                "gate": "risk:disagreement",
                "outcome": "veto:MODEL_DISAGREEMENT",
                "observed": str(direction),
                "limit": "abstain-neutral",
            }
        )
        data = record.model_dump(mode="json")
        data["direction"] = "NEUTRAL"
        data["entry_ref"] = None
        data["stop_loss"] = None
        data["take_profit"] = None
        data["risk_reward"] = None
        codes = list(data.get("decision", {}).get("reason_codes", []))
        if "MODEL_DISAGREEMENT" not in codes:
            codes.append("MODEL_DISAGREEMENT")
        data["decision"] = {
            "status": "abstained",
            "reason_codes": codes,
            "gate": "risk:disagreement",
        }
        return SignalRecord.model_validate(data), trace
    _pass("risk:disagreement", observed="agree" if not context.model_disagreement else "flat")

    if direction in ("NEUTRAL", "NO_TRADE"):
        _pass("risk:edge", "skip:flat-no-edge-required")
    elif not context.require_edge:
        _pass("risk:edge", "skip:edge-not-required")
    else:
        edge = record.expected_edge_bp
        cost = record.cost_estimate
        if edge is None or not math.isfinite(float(edge)):
            return _veto("risk:edge", "RISK_GATE_EDGE", "EDGE_UNAVAILABLE")
        if cost is None:
            return _veto("risk:edge", "RISK_GATE_EDGE", "EDGE_UNAVAILABLE", observed="cost-missing")
        total = float(cost.cost_bp_total)
        margin = float(cost.edge_margin_bp) + float(context.edge_margin_bp)
        if float(edge) < total + margin:
            return _veto(
                "risk:edge",
                "RISK_GATE_EDGE",
                "INSUFFICIENT_EDGE",
                observed=f"{float(edge)}bp",
                limit=f"{total + margin}bp",
            )
        _pass("risk:edge", observed=f"{float(edge)}bp", limit=f"{total + margin}bp")

    if now <= record.valid_until:
        _pass("risk:age")
    else:
        return _veto("risk:age", "RISK_GATE_AGE", "SIGNAL_EXPIRED")

    if int(record.prediction_horizon_bars) >= 1:
        _pass("risk:horizon", observed=str(record.prediction_horizon_bars))
    else:  # pragma: no cover - schema already enforces ge=1; defense in depth
        return _veto("risk:horizon", "RISK_GATE_HORIZON", "HORIZON_INVALID")

    if record.market_regime and record.market_regime in context.blocked_regimes:
        return _veto(
            "risk:regime",
            "RISK_GATE_REGIME",
            "REGIME_BLOCKED",
            observed=str(record.market_regime),
        )
    _pass("risk:regime", observed=str(record.market_regime or "none"))

    ai_status = record.ai_context.status if record.ai_context is not None else "disabled"
    if context.ai_required and ai_status != "ok":
        return _veto("risk:ai", "RISK_GATE_AI", "AI_REQUIRED_UNAVAILABLE", observed=str(ai_status))
    _pass("risk:ai", observed=str(ai_status))

    # -- D. exposure gates ------------------------------------------------
    if not context.risk.enabled:
        _pass("risk:exposure", "skip:risk-disabled")
        return record, trace

    portfolio = context.portfolio
    if portfolio is None:
        _pass_msg = "skip:unavailable-EXPOSURE_UNKNOWN"
        trace.append(
            {
                "gate": "risk:exposure",
                "outcome": _pass_msg,
                "observed": "no-portfolio",
                "limit": "",
            }
        )
        if context.effective_fail_closed:
            trace.append(
                {
                    "gate": "risk:exposure",
                    "outcome": "veto:EXPOSURE_UNKNOWN",
                    "observed": "no-portfolio",
                    "limit": "",
                }
            )
            return apply_gate_veto(record, "risk:exposure", "RISK_GATE_EXPOSURE"), trace
        return record, trace

    if direction in ("NEUTRAL", "NO_TRADE"):
        _pass("risk:trade-risk", "skip:flat-no-position")
    elif context.proposed_risk_pct is None:
        # No proposed size supplied (CLI v1 has no positions): nothing to
        # check, so skip. Callers sizing real trades must supply
        # proposed_risk_pct; absence is not evidence of a breach.
        _pass("risk:trade-risk", "skip:proposed-risk-missing")
    elif float(context.proposed_risk_pct) <= float(context.risk.max_trade_risk_pct):
        _pass(
            "risk:trade-risk",
            observed=f"{float(context.proposed_risk_pct)}pct",
            limit=f"{float(context.risk.max_trade_risk_pct)}pct",
        )
    else:
        return _veto(
            "risk:trade-risk",
            "RISK_GATE_TRADE_RISK",
            "TRADE_RISK_EXCEEDED",
            observed=f"{float(context.proposed_risk_pct)}pct",
            limit=f"{float(context.risk.max_trade_risk_pct)}pct",
        )

    if float(portfolio["symbol_exposure_pct"]) <= float(context.risk.max_position_pct):
        _pass(
            "risk:exposure",
            observed=f"{float(portfolio['symbol_exposure_pct'])}pct",
            limit=f"{float(context.risk.max_position_pct)}pct",
        )
    else:
        return _veto(
            "risk:exposure",
            "RISK_GATE_EXPOSURE",
            "POSITION_LIMIT_EXCEEDED",
            observed=f"{float(portfolio['symbol_exposure_pct'])}pct",
            limit=f"{float(context.risk.max_position_pct)}pct",
        )

    start_eq = float(portfolio["day_start_equity"])
    cur_eq = float(portfolio["current_equity"])
    drawdown_pct = (start_eq - cur_eq) / start_eq * 100.0
    if drawdown_pct <= float(context.risk.max_drawdown_pct):
        _pass(
            "risk:drawdown",
            observed=f"{drawdown_pct:.4f}pct",
            limit=f"{float(context.risk.max_drawdown_pct)}pct",
        )
    else:
        return _veto(
            "risk:drawdown",
            "RISK_GATE_DRAWDOWN",
            "DRAWDOWN_EXCEEDED",
            observed=f"{drawdown_pct:.4f}pct",
            limit=f"{float(context.risk.max_drawdown_pct)}pct",
        )

    if bool(portfolio["strategy_enabled"]):
        _pass("risk:strategy")
    else:
        return _veto("risk:strategy", "RISK_GATE_STRATEGY", "STRATEGY_DISABLED")

    if not bool(portfolio["circuit_open"]):
        _pass("risk:circuit")
    else:
        return _veto("risk:circuit", "RISK_GATE_CIRCUIT", "CIRCUIT_OPEN")

    return record, trace


__all__ = ["GATE_ORDER", "GateContext", "GateResult", "GateTrace", "run_gates"]
