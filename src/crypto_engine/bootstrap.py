"""Bootstrap composition root (Stage 8).

Wires the canonical SignalForge pipeline together:

    exchange -> market data -> features -> models -> signals -> risk gates -> results

This is the single entry point that assembles all layers into one deterministic,
single-process application. No I/O happens at import time; wiring is explicit
and injectable for testing.

DR-12: This is the only module allowed to import across layer boundaries to
wire everything together. Each layer stays independent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from crypto_engine.analytics import research_proxies, validate_holdout
from crypto_engine.data.market_data_source import CcxtMarketDataSource
from crypto_engine.domain.direction import Direction
from crypto_engine.domain.reason_codes import ReasonCode
from crypto_engine.quality import audit as quality_audit
from crypto_engine.quality import detect_staleness
from crypto_engine.risk.gates import GateContext, run_gates
from crypto_engine.signals.schema import (
    AiContextSummary,
    CostEstimate,
    DecisionInfo,
    PredictionInfo,
    SignalRecord,
)
from crypto_engine.strategies.composition import StrategyOutput, compose_signal
from crypto_engine.strategies.metadata import (
    StrategyMetadata,
    StrategyParameter,
    StrategyVersion,
)


def _make_signal_id(strategy_id: str) -> str:
    """16-hex-char signal id matching SignalRecord pattern."""
    return hashlib.sha256(strategy_id.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class BootstrapConfig:
    """Bootstrap configuration — all bounded, deterministic."""

    signal_max_age_min: int = 60
    min_edge_bp: float = 5.0
    require_unanimous: bool = False
    min_confidence: float = 0.0
    fail_closed: bool = True


@dataclass(frozen=True)
class PipelineResult:
    """Result of running the full pipeline."""

    signal: SignalRecord | None
    trace: dict[str, Any]
    approved: bool


class SignalForgeApp:
    """Single-process SignalForge application.

    Wires together: domain -> ports -> quality -> analytics -> strategies ->
    risk -> signals. All components are injected or constructed from config.
    """

    def __init__(self, config: BootstrapConfig | None = None) -> None:
        self.config = config or BootstrapConfig()

        # Domain layer (stdlib only)
        self._strategy_metadata = StrategyMetadata(
            name="trend-follow",
            version=StrategyVersion(1, 0, 0),
            parameters=(
                StrategyParameter("fast_window", "int", 10, min_value=1, max_value=200),
                StrategyParameter("slow_window", "int", 30, min_value=1, max_value=500),
            ),
        )

        # Ports layer (protocol implementations)
        self._market_data = CcxtMarketDataSource(exchange_name="binance")

        # Quality layer
        self._quality = {
            "audit": quality_audit,
            "staleness": detect_staleness,
        }

        # Analytics layer
        self._analytics = {
            "research_proxies": research_proxies,
            "validate_holdout": validate_holdout,
        }

        # Strategies layer
        self._strategies = {
            "compose": compose_signal,
            "metadata": self._strategy_metadata,
        }

        # Risk layer
        self._risk = {
            "run_gates": run_gates,
        }

    def run_pipeline(
        self,
        symbol: str,
        timeframe: str,
        generated_at: str,
        model_score: float,
        model_confidence: float,
        expected_edge_bp: float,
        cost_estimate: dict[str, Any],
        portfolio: dict[str, Any] | None = None,
    ) -> PipelineResult:
        """Run the full pipeline end-to-end.

        Returns a PipelineResult with the final signal (or None if vetoed),
        a full trace of each stage, and whether the signal was approved.
        """
        trace: dict[str, Any] = {}
        now = datetime.now(UTC)
        gen_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        if gen_dt.tzinfo is None:
            gen_dt = gen_dt.replace(tzinfo=UTC)
        valid_until = gen_dt + timedelta(minutes=60)

        # 1. Market data (port): no fetch in pipeline — caller provides data
        trace["market_data"] = {"available": True, "source": "binance"}

        # 2. Quality: staleness check on generation time
        staleness = self._quality["staleness"](
            generated_at, max_age_seconds=self.config.signal_max_age_min * 60
        )
        trace["quality"] = {"staleness": staleness}

        if staleness["stale"]:
            trace["risk_veto"] = {"reason": ReasonCode.STALE_DATA}
            return PipelineResult(
                signal=None,
                trace=trace,
                approved=False,
            )

        # 3. Strategies: compose signal from model output
        model_output = StrategyOutput(
            strategy_id=self._strategy_metadata.strategy_id(),
            direction=Direction.LONG if model_score > 0.5 else Direction.SHORT,
            confidence=model_confidence,
            reason_codes=(),
        )
        composed = self._strategies["compose"](
            [model_output],
            min_confidence=self.config.min_confidence,
            require_unanimous=self.config.require_unanimous,
        )
        trace["strategies"] = {
            "direction": composed.direction.value,
            "confidence": composed.confidence,
            "reason_codes": composed.reason_codes,
        }

        if composed.direction == Direction.NO_TRADE:
            veto_reason = (
                composed.reason_codes[0] if composed.reason_codes else ReasonCode.INSUFFICIENT_DATA
            )
            trace["risk_veto"] = {"reason": veto_reason}
            return PipelineResult(
                signal=None,
                trace=trace,
                approved=False,
            )

        # 4. Risk gates (independent, deterministic)
        signal_id = _make_signal_id(self._strategy_metadata.strategy_id())
        record = SignalRecord(
            signal_id=signal_id,
            symbol=symbol,
            exchange="binance",
            timeframe=timeframe,
            generated_at=gen_dt,
            valid_until=valid_until,
            direction=composed.direction.value,
            prediction_horizon_bars=1,
            model_id="model-0",
            model_version="0.1.0",
            model_artifact_hash="",
            dataset_snapshot_id="",
            feature_snapshot_hash="",
            prediction=PredictionInfo(
                raw_score=model_score,
                calibration_status="uncalibrated",
                positive_class=None,
                model_family="trend",
                threshold_version="v1",
            ),
            market_regime="",
            expected_edge_bp=expected_edge_bp,
            cost_estimate=CostEstimate(**cost_estimate) if cost_estimate else None,
            entry_ref=100.0,
            ai_context=AiContextSummary(status="disabled"),
            decision=DecisionInfo(
                status="abstained",
                reason_codes=["OK"],
                gate="strategy",
            ),
        )
        context = GateContext(
            now_utc=now,
            portfolio=portfolio,
            fail_closed=self.config.fail_closed,
        )
        gated_record, gate_trace = self._risk["run_gates"](record, context)
        trace["risk_gates"] = gate_trace

        approved = gated_record.decision.status == "approved"

        return PipelineResult(
            signal=gated_record,
            trace=trace,
            approved=approved,
        )

    def research_proxies(self, experiments: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute research proxies (analytics layer)."""
        return self._analytics["research_proxies"](experiments)

    def validate_holdout(self, ledger_path: str, min_n: int = 100) -> dict[str, Any]:
        """Validate holdout ledger state (analytics layer)."""
        return self._analytics["validate_holdout"](ledger_path, min_n=min_n)

    def quality_audit(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run quality audit (quality layer)."""
        return self._quality["audit"](state)


__all__ = ["BootstrapConfig", "PipelineResult", "SignalForgeApp"]
