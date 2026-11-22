"""Deterministic model-to-decision policy (Phase 5 Track A).

Label semantics: ``label_convention="long_up"`` is the only supported
convention — ``+1`` means favorable-up (LONG), ``-1`` favorable-down
(SHORT), ``0`` neutral (timeout/ambiguous/incomplete per
labels/triple_barrier.py). SHORT requires ``-1`` in the model's label
set, otherwise the SHORT path is disabled (DIRECTION_DISABLED).

Fixed evaluation order (auditable; first failure wins, NO_TRADE veto):

1. timestamps/horizon contract → model fitted? → feature schema match?
2. finite features? → model predict/predict_score finite?
3. calibration branch (match → calibrated p; mismatch → uncalibrated;
   rule-based trend/majority → RULE_BASED_NO_PROBABILITY, never a
   calibrated probability)
4. calibration_required and uncalibrated → NO_TRADE(UNCALIBRATED)
5. AI-required check (advisory only; never changes direction)
6. direction from p vs thresholds (binary) or argmax label (multiclass)
7. enabled_directions check → edge check (LONG/SHORT only)
8. levels hook (validated via levels.validate_levels)

Probability is NOT profitability: ``p`` ranks outcomes; edge
(expected bp net of costs) is an explicitly provided estimate, never
derived from confidence. Deterministic: same inputs → same record
including signal_id. No network, no randomness, no global state.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from crypto_engine import __version__ as _package_version
from crypto_engine.models.base import NotFittedError, SchemaMismatchError
from crypto_engine.signals.levels import validate_levels
from crypto_engine.signals.schema import (
    AI_REQUIRED_UNAVAILABLE,
    CALIBRATION_MISMATCH,
    COST_INPUTS_MISSING,
    DIRECTION_DISABLED,
    EDGE_UNAVAILABLE,
    INCOMPLETE_DATA,
    INSUFFICIENT_EDGE,
    INVALID_OUTPUT,
    INVALID_TIMESTAMPS,
    MODEL_UNFITTED,
    NON_FINITE,
    OK,
    RULE_BASED_FAMILIES,
    RULE_BASED_NO_PROBABILITY,
    SCHEMA_MISMATCH,
    STALE_DATA,
    THRESHOLD_NOT_MET,
    UNCALIBRATED,
    AiContextSummary,
    CostEstimate,
    SignalLevels,
    SignalRecord,
)

GateTrace = list[dict[str, str]]


def _parse_ts(value: str, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"PolicyInputs: {name} must be an ISO string, got {value!r}.")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"PolicyInputs: {name} invalid ISO {value!r}: {exc}") from exc
    if dt.tzinfo is None:
        raise ValueError(f"PolicyInputs: {name} must be timezone-aware (UTC), got {value!r}.")
    return dt


@dataclass
class PolicyInputs:
    """Single-row decision inputs (already-validated snapshot row)."""

    feature_row: dict[str, float]
    feature_names: list[str]
    model: Any
    model_id: str = ""
    model_version: str = ""
    model_artifact_hash: str = ""
    label_convention: str = "long_up"
    calibrator: Any | None = None
    calibrator_meta: dict[str, Any] = field(default_factory=dict)
    calibration_required: bool = False
    threshold_version: str = "v1"
    t_long: float = 0.6
    t_short: float = 0.4
    require_edge: bool = True
    expected_edge_bp: float | None = None
    cost: CostEstimate | None = None
    enabled_directions: set[str] = field(
        default_factory=lambda: {"LONG", "SHORT", "NEUTRAL", "NO_TRADE"}
    )
    data_quality: str = "validated"
    generated_at: str = ""
    valid_until: str = ""
    symbol: str = ""
    exchange: str = ""
    timeframe: str = ""
    horizon_bars: int = 24
    regime: str = ""
    blocked_regimes: set[str] = field(default_factory=set)
    ai: AiContextSummary | None = None
    ai_required: bool = False
    dataset_snapshot_id: str = ""
    levels: SignalLevels | None = None

    def __post_init__(self) -> None:
        if self.label_convention != "long_up":
            raise ValueError(
                f"PolicyInputs: unknown label_convention {self.label_convention!r} "
                "(only 'long_up' supported: +1 favorable-up, -1 favorable-down)."
            )
        for name, t in (("t_long", self.t_long), ("t_short", self.t_short)):
            if not isinstance(t, (int, float)) or not math.isfinite(float(t)):
                raise ValueError(f"PolicyInputs: {name} must be finite, got {t!r}.")
        if not (float(self.t_short) < 0.5 < float(self.t_long)):
            raise ValueError(
                f"PolicyInputs: need t_short < 0.5 < t_long "
                f"(got t_short={self.t_short!r}, t_long={self.t_long!r})."
            )
        if self.data_quality not in ("validated", "stale", "incomplete", "invalid"):
            raise ValueError(f"PolicyInputs: unknown data_quality {self.data_quality!r}.")
        unknown = set(self.enabled_directions) - {"LONG", "SHORT", "NEUTRAL", "NO_TRADE"}
        if unknown:
            raise ValueError(f"PolicyInputs: unknown enabled_directions {sorted(unknown)!r}.")
        if not isinstance(self.horizon_bars, int) or self.horizon_bars < 1:
            raise ValueError(
                f"PolicyInputs: horizon_bars must be int >= 1, got {self.horizon_bars!r}."
            )


def _feature_hash(feature_row: dict[str, float]) -> str:
    payload = json.dumps([[k, feature_row[k]] for k in sorted(feature_row)], default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _signal_id(
    symbol: str,
    timeframe: str,
    ts_iso: str,
    model_id: str,
    threshold_version: str,
    feature_hash: str,
) -> str:
    raw = "|".join([symbol, timeframe, ts_iso, model_id, threshold_version, feature_hash])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _model_classes(model: Any) -> list[int]:
    try:
        classes = model.to_dict().get("classes", [])
        return [int(c) for c in classes]
    except Exception:
        return []


def _model_family(model: Any) -> str:
    try:
        fam = model.to_dict().get("family", "")
        if fam:
            return str(fam)
    except Exception:
        pass
    return str(getattr(model, "family", type(model).__name__))


def _calibrator_status(inputs: PolicyInputs) -> tuple[str, float | None, int | None, list[str]]:
    """Resolve (status, p_event, positive_class, extra_reasons) for the raw score hook.

    Caller supplies the raw score separately; this helper only decides
    whether a calibrator may be applied. Returns status in
    calibrated/uncalibrated/unavailable/mismatch.
    """
    cal = inputs.calibrator
    if cal is None:
        return "unavailable", None, None, []
    try:
        fitted = bool(getattr(cal, "is_fitted", False))
    except Exception:
        fitted = False
    if not fitted:
        return "mismatch", None, None, [CALIBRATION_MISMATCH]
    try:
        payload = cal.to_dict()
    except Exception:
        return "mismatch", None, None, [CALIBRATION_MISMATCH]
    meta = inputs.calibrator_meta
    checks = (
        str(meta.get("model_family", "")) == str(payload.get("model_family", "")),
        str(meta.get("version", "")) == str(payload.get("model_version", "")),
        str(meta.get("feature_hash", "")) == str(payload.get("feature_schema_hash", "")),
        (None if meta.get("positive_class") is None else int(meta["positive_class"]))
        == int(payload.get("positive_class")),
    )
    if not all(checks):
        return "mismatch", None, None, [CALIBRATION_MISMATCH]
    return "calibrated", None, int(payload.get("positive_class")), []


def _no_trade(
    inputs: PolicyInputs,
    reasons: list[str],
    gate: str,
    trace: GateTrace,
    raw_score: float | None,
    cal_status: str,
    cal_p: float | None,
    positive: int | None,
    family: str,
    feature_hash: str,
    gen_dt: datetime,
    val_dt: datetime,
    edge: float | None,
) -> tuple[SignalRecord, GateTrace]:
    trace.append({"gate": gate, "outcome": "veto:" + ",".join(reasons)})
    levels = SignalLevels(direction="NO_TRADE")
    record = SignalRecord(
        signal_id=_signal_id(
            inputs.symbol,
            inputs.timeframe,
            gen_dt.isoformat(),
            inputs.model_id,
            inputs.threshold_version,
            feature_hash,
        ),
        symbol=inputs.symbol,
        exchange=inputs.exchange,
        timeframe=inputs.timeframe,
        generated_at=gen_dt,
        valid_until=val_dt,
        direction="NO_TRADE",
        prediction_horizon_bars=inputs.horizon_bars,
        model_id=inputs.model_id,
        model_version=inputs.model_version,
        model_artifact_hash=inputs.model_artifact_hash,
        dataset_snapshot_id=inputs.dataset_snapshot_id,
        feature_snapshot_hash=feature_hash,
        prediction={
            "raw_score": raw_score,
            "calibrated_probability": cal_p if cal_status == "calibrated" else None,
            "calibration_status": cal_status,  # type: ignore[typeddict-item]
            "positive_class": positive,
            "model_family": family,
            "threshold_version": inputs.threshold_version,
        },
        market_regime=inputs.regime,
        expected_edge_bp=edge,
        cost_estimate=inputs.cost,
        entry_ref=None,
        stop_loss=None,
        take_profit=None,
        risk_reward=None,
        data_quality=inputs.data_quality,  # type: ignore[arg-type]
        ai_context=inputs.ai if inputs.ai is not None else AiContextSummary(status="disabled"),
        decision={"status": "abstained", "reason_codes": reasons, "gate": gate},
        software_version=_package_version,
    )
    _ = levels
    return record, trace


def decide(inputs: PolicyInputs) -> tuple[SignalRecord, GateTrace]:
    """Run the fixed veto pipeline; always returns (record, gate_trace)."""
    trace: GateTrace = []
    gen_dt = _parse_ts(inputs.generated_at, "generated_at")
    val_dt = _parse_ts(inputs.valid_until, "valid_until")
    if val_dt <= gen_dt:
        val_dt = gen_dt + timedelta(hours=1)
        trace.append({"gate": "policy:timestamps", "outcome": "veto:" + INVALID_TIMESTAMPS})
        ts_invalid = True
    else:
        trace.append({"gate": "policy:timestamps", "outcome": "pass"})
        ts_invalid = False

    feature_hash = _feature_hash(inputs.feature_row)
    family = _model_family(inputs.model)
    rule_based = family in RULE_BASED_FAMILIES
    raw_score: float | None = None
    cal_p: float | None = None
    cal_status = "unavailable"
    positive: int | None = None
    edge = inputs.expected_edge_bp
    if edge is None and inputs.cost is not None:
        edge = inputs.cost.expected_edge_bp

    def veto(reasons: list[str], gate: str) -> tuple[SignalRecord, GateTrace]:
        return _no_trade(
            inputs,
            reasons,
            gate,
            trace,
            raw_score,
            cal_status,
            cal_p,
            positive,
            family,
            feature_hash,
            gen_dt,
            val_dt,
            edge,
        )

    if ts_invalid:
        return veto([INVALID_TIMESTAMPS], "policy:timestamps")
    if inputs.data_quality == "stale":
        trace.append({"gate": "policy:data", "outcome": "veto:" + STALE_DATA})
        return veto([STALE_DATA], "policy:data")
    if inputs.data_quality in ("incomplete", "invalid"):
        trace.append({"gate": "policy:data", "outcome": "veto:" + INCOMPLETE_DATA})
        return veto([INCOMPLETE_DATA], "policy:data")
    trace.append({"gate": "policy:data", "outcome": "pass"})

    if inputs.regime and inputs.regime in inputs.blocked_regimes:
        from crypto_engine.signals.schema import REGIME_BLOCKED

        return veto([REGIME_BLOCKED], "policy:regime")
    trace.append({"gate": "policy:regime", "outcome": "pass"})

    if inputs.ai_required:
        ai_ok = inputs.ai is not None and inputs.ai.status == "ok"
        if not ai_ok:
            return veto([AI_REQUIRED_UNAVAILABLE], "policy:ai")
    trace.append({"gate": "policy:ai", "outcome": "pass"})

    if not bool(getattr(inputs.model, "is_fitted", False)):
        return veto([MODEL_UNFITTED], "policy:model")
    trace.append({"gate": "policy:model", "outcome": "pass:fitted"})

    expected_schema = list(getattr(inputs.model, "feature_names_", None) or [])
    if not expected_schema:
        try:
            expected_schema = [str(c) for c in inputs.model.to_dict().get("feature_names", [])]
        except Exception:
            expected_schema = []
    if list(inputs.feature_names) != list(expected_schema):
        return veto([SCHEMA_MISMATCH], "policy:schema")
    missing = [c for c in inputs.feature_names if c not in inputs.feature_row]
    if missing:
        return veto([SCHEMA_MISMATCH], "policy:schema")
    trace.append({"gate": "policy:schema", "outcome": "pass"})

    for col in inputs.feature_names:
        try:
            v = float(inputs.feature_row[col])
        except (TypeError, ValueError):
            return veto([NON_FINITE], "policy:finite")
        if not math.isfinite(v):
            return veto([NON_FINITE], "policy:finite")
    trace.append({"gate": "policy:finite", "outcome": "pass"})

    frame = pd.DataFrame(
        [{c: float(inputs.feature_row[c]) for c in inputs.feature_names}],
        columns=list(inputs.feature_names),
    )
    try:
        labels = inputs.model.predict(frame)
        scores = inputs.model.predict_score(frame)
    except NotFittedError:
        return veto([MODEL_UNFITTED], "policy:model")
    except SchemaMismatchError:
        return veto([SCHEMA_MISMATCH], "policy:schema")
    except Exception:
        return veto([INVALID_OUTPUT], "policy:model")
    try:
        pred_label = int(labels.ravel()[0])
        raw = float(scores.ravel()[0])
    except Exception:
        return veto([INVALID_OUTPUT], "policy:model")
    if not math.isfinite(raw):
        return veto([NON_FINITE], "policy:model")
    if not 0.0 <= raw <= 1.0:
        return veto([INVALID_OUTPUT], "policy:model")
    raw_score = raw
    trace.append({"gate": "policy:model", "outcome": "pass:outputs-finite"})

    classes = _model_classes(inputs.model)
    extra_reasons: list[str] = []
    direction: str | None = None

    if rule_based:
        cal_status = "unavailable"
        cal_p = None
        positive = None
        extra_reasons = [RULE_BASED_NO_PROBABILITY]
        trace.append({"gate": "policy:calibration", "outcome": "rule-based-no-probability"})
        if inputs.calibration_required:
            return veto([UNCALIBRATED, RULE_BASED_NO_PROBABILITY], "policy:calibration")
        direction = "LONG" if pred_label == 1 else ("SHORT" if pred_label == -1 else "NEUTRAL")
    elif len(classes) > 2:
        cal_status = "unavailable"
        if inputs.calibrator is not None:
            extra_reasons = [CALIBRATION_MISMATCH]
            cal_status = "mismatch"
            trace.append({"gate": "policy:calibration", "outcome": "veto:" + CALIBRATION_MISMATCH})
        else:
            trace.append({"gate": "policy:calibration", "outcome": "multiclass-uncalibrated"})
        if inputs.calibration_required:
            codes = [UNCALIBRATED, *extra_reasons]
            return veto(codes, "policy:calibration")
        if pred_label not in classes:
            return veto([INVALID_OUTPUT], "policy:model")
        direction = "LONG" if pred_label == 1 else ("SHORT" if pred_label == -1 else "NEUTRAL")
    else:
        if not classes:
            return veto([INVALID_OUTPUT], "policy:model")
        if pred_label not in classes and len(classes) == 2:
            return veto([INVALID_OUTPUT], "policy:model")
        status, _, positive_class, mismatch_reasons = _calibrator_status(inputs)
        if status == "calibrated":
            assert inputs.calibrator is not None
            try:
                import numpy as np

                cal_p = float(inputs.calibrator.predict_proba(np.asarray([raw_score]))[0])
            except Exception:
                return veto([INVALID_OUTPUT], "policy:calibration")
            if not math.isfinite(cal_p) or not 0.0 <= cal_p <= 1.0:
                return veto([INVALID_OUTPUT], "policy:calibration")
            cal_status = "calibrated"
            positive = positive_class
            trace.append({"gate": "policy:calibration", "outcome": "pass:calibrated"})
        elif status == "mismatch":
            cal_status = "mismatch"
            extra_reasons = mismatch_reasons
            trace.append({"gate": "policy:calibration", "outcome": "veto:" + CALIBRATION_MISMATCH})
            if inputs.calibration_required:
                return veto([CALIBRATION_MISMATCH, UNCALIBRATED], "policy:calibration")
        else:
            cal_status = "uncalibrated"
            trace.append({"gate": "policy:calibration", "outcome": "uncalibrated"})
            if inputs.calibration_required:
                return veto([UNCALIBRATED], "policy:calibration")

        p_event = cal_p if cal_status == "calibrated" else raw_score
        assert p_event is not None
        if cal_status == "calibrated":
            assert positive is not None
            if positive == 1:
                p_up: float | None = float(p_event)
            elif 1 in classes:
                p_up = 1.0 - float(p_event)
            else:
                p_up = None
        else:
            if classes[1] == 1 if len(classes) == 2 else (1 in classes):
                p_up = float(p_event)
            elif 1 in classes:
                p_up = 1.0 - float(p_event)
            else:
                p_up = None
        if p_up is not None:
            if p_up >= float(inputs.t_long):
                direction = "LONG"
            elif p_up <= float(inputs.t_short):
                if -1 not in classes:
                    return veto([DIRECTION_DISABLED], "policy:direction")
                direction = "SHORT"
            else:
                direction = "NEUTRAL"
        else:
            other = classes[0] if (positive == classes[1] if len(classes) == 2 else True) else None
            implied = positive if float(p_event) >= 0.5 else other
            direction = "SHORT" if implied == -1 else "NEUTRAL"
            if direction == "SHORT" and -1 not in classes:
                return veto([DIRECTION_DISABLED], "policy:direction")
    assert direction is not None
    trace.append({"gate": "policy:direction", "outcome": f"pass:{direction}"})

    if direction not in inputs.enabled_directions:
        return veto([DIRECTION_DISABLED], "policy:enabled")
    trace.append({"gate": "policy:enabled", "outcome": "pass"})

    if direction == "NEUTRAL":
        trace.append({"gate": "policy:thresholds", "outcome": "in-band:neutral"})
        record_reasons = [THRESHOLD_NOT_MET, *extra_reasons]
        trace.append({"gate": "policy:edge", "outcome": "skip:neutral-flat"})
        entry = stop = take = rr = None
        if inputs.levels is not None:
            lv, err = validate_levels(
                direction,
                inputs.levels.entry_ref,
                inputs.levels.stop_loss,
                inputs.levels.take_profit,
            )
            if err is not None or lv is None:
                return veto([INVALID_OUTPUT], "policy:levels")
        return _build_approved(
            inputs,
            trace,
            raw_score,
            cal_status,
            cal_p,
            positive,
            family,
            feature_hash,
            gen_dt,
            val_dt,
            edge,
            direction,
            record_reasons,
            entry,
            stop,
            take,
            rr,
        )

    if inputs.require_edge:
        if inputs.cost is None:
            return veto([COST_INPUTS_MISSING], "policy:edge")
        if edge is None or not math.isfinite(float(edge)):
            return veto([EDGE_UNAVAILABLE], "policy:edge")
        total = float(inputs.cost.cost_bp_total)
        margin = float(inputs.cost.edge_margin_bp)
        if float(edge) < total + margin:
            return veto([INSUFFICIENT_EDGE], "policy:edge")
    trace.append({"gate": "policy:edge", "outcome": "pass"})

    entry = stop = take = rr = None
    if inputs.levels is not None:
        lv, err = validate_levels(
            direction, inputs.levels.entry_ref, inputs.levels.stop_loss, inputs.levels.take_profit
        )
        if err is not None or lv is None:
            return veto([INVALID_OUTPUT], "policy:levels")
        entry, stop, take = lv.entry_ref, lv.stop_loss, lv.take_profit
        if entry is not None and stop is not None and take is not None:
            risk = abs(entry - stop)
            rr = (abs(take - entry) / risk) if risk > 0 else 0.0
        elif inputs.levels.risk_reward is not None:
            rr = inputs.levels.risk_reward
        trace.append({"gate": "policy:levels", "outcome": "pass:validated"})
    else:
        trace.append({"gate": "policy:levels", "outcome": "skip:none"})

    record_reasons = [*extra_reasons] if extra_reasons else [OK]
    return _build_approved(
        inputs,
        trace,
        raw_score,
        cal_status,
        cal_p,
        positive,
        family,
        feature_hash,
        gen_dt,
        val_dt,
        edge,
        direction,
        record_reasons,
        entry,
        stop,
        take,
        rr,
    )


def _build_approved(
    inputs: PolicyInputs,
    trace: GateTrace,
    raw_score: float | None,
    cal_status: str,
    cal_p: float | None,
    positive: int | None,
    family: str,
    feature_hash: str,
    gen_dt: datetime,
    val_dt: datetime,
    edge: float | None,
    direction: str,
    reasons: list[str],
    entry: float | None,
    stop: float | None,
    take: float | None,
    rr: float | None,
) -> tuple[SignalRecord, GateTrace]:
    status = "approved" if direction in ("LONG", "SHORT") else "abstained"
    trace.append({"gate": "policy:decision", "outcome": f"{status}:" + ",".join(reasons)})
    record = SignalRecord(
        signal_id=_signal_id(
            inputs.symbol,
            inputs.timeframe,
            gen_dt.isoformat(),
            inputs.model_id,
            inputs.threshold_version,
            feature_hash,
        ),
        symbol=inputs.symbol,
        exchange=inputs.exchange,
        timeframe=inputs.timeframe,
        generated_at=gen_dt,
        valid_until=val_dt,
        direction=direction,  # type: ignore[arg-type]
        prediction_horizon_bars=inputs.horizon_bars,
        model_id=inputs.model_id,
        model_version=inputs.model_version,
        model_artifact_hash=inputs.model_artifact_hash,
        dataset_snapshot_id=inputs.dataset_snapshot_id,
        feature_snapshot_hash=feature_hash,
        prediction={
            "raw_score": raw_score,
            "calibrated_probability": cal_p if cal_status == "calibrated" else None,
            "calibration_status": cal_status,  # type: ignore[typeddict-item]
            "positive_class": positive,
            "model_family": family,
            "threshold_version": inputs.threshold_version,
        },
        market_regime=inputs.regime,
        expected_edge_bp=edge,
        cost_estimate=inputs.cost,
        entry_ref=entry,
        stop_loss=stop,
        take_profit=take,
        risk_reward=rr,
        data_quality=inputs.data_quality,  # type: ignore[arg-type]
        ai_context=inputs.ai if inputs.ai is not None else AiContextSummary(status="disabled"),
        decision={"status": status, "reason_codes": reasons, "gate": "policy"},  # type: ignore[typeddict-item]
        software_version=_package_version,
    )
    return record, trace


def apply_gate_veto(record: SignalRecord, gate: str, reason: str) -> SignalRecord:
    """Apply a downstream (Track B risk-gate) veto to an approved record.

    Returns a NEW record with decision ``rejected``, the gate name, and
    ``reason`` appended (``RISK_GATE_*`` convention). Never mutates.
    """
    import re as _re

    if not isinstance(reason, str) or not _re.match(r"^[A-Z][A-Z0-9_]+$", reason):
        raise ValueError(f"apply_gate_veto: reason must match ^[A-Z][A-Z0-9_]+$, got {reason!r}.")
    if not isinstance(gate, str) or not gate:
        raise ValueError("apply_gate_veto: gate must be a non-empty name.")
    data = record.model_dump(mode="json")
    codes = list(data.get("decision", {}).get("reason_codes", []))
    if reason not in codes:
        codes.append(reason)
    # Preserve the original veto gate (first rejection) rather than
    # overwriting with the most recent gate, so the decision trace
    # accurately represents execution order. A record that is already
    # vetoed (abstained or rejected) keeps its first gate.
    existing_gate = data.get("decision", {}).get("gate", "policy")
    existing_status = data.get("decision", {}).get("status", "abstained")
    final_gate = existing_gate if existing_status != "approved" else gate
    data["decision"] = {"status": "rejected", "reason_codes": codes, "gate": final_gate}
    return SignalRecord.model_validate(data)


__all__ = ["GateTrace", "PolicyInputs", "apply_gate_veto", "decide"]
