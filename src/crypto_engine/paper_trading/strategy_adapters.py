"""Q7 Phase 5: Strategy signal producer adapters for frozen candidates.

Each candidate (majority, trend, logistic, lightgbm) is isolated in its own
adapter instance with its own model and state. No retraining occurs.
NOT_EVALUABLE candidates remain NOT_EVALUABLE.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from crypto_engine.models.baselines import MajorityBaseline, TrendBaseline
from crypto_engine.models.sklearn_wrappers import SklearnLogistic
from crypto_engine.signals.policy import PolicyInputs, decide
from crypto_engine.signals.schema import (
    CostEstimate,
    SignalRecord,
)

CandidateStatus = Literal["ACTIVE", "NOT_EVALUABLE", "DISABLED"]

LOGISTIC_ARTIFACT_DIR = Path("models/artifacts/logistic-ee482c-3e3324f2-20261028T132236")


class SignalProducer(BaseModel):
    """Base class for strategy signal producers."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    candidate_id: str
    candidate_name: str
    status: CandidateStatus = "ACTIVE"
    model_id: str = ""
    feature_names: list[str] = Field(default_factory=list)
    horizon_bars: int = Field(default=24, ge=1)
    t_long: float = Field(default=0.6, gt=0.5, lt=1.0)
    t_short: float = Field(default=0.4, gt=0.0, lt=0.5)
    require_edge: bool = Field(default=True)
    edge_margin_bp: float = Field(default=0.0, ge=0.0)
    enabled_directions: list[str] = Field(
        default_factory=lambda: ["LONG", "SHORT", "NEUTRAL", "NO_TRADE"]
    )
    calibration_required: bool = Field(default=False)
    cost_estimate: dict[str, float] | None = None

    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    def produce(
        self,
        bar_idx: int,
        bar: dict[str, object],
        features_row: dict[str, float],
        generated_at: str,
    ) -> SignalRecord | None:
        """Produce a signal for this bar, or None if NOT_EVALUABLE/no signal."""
        raise NotImplementedError

    def _make_signal_id(
        self, symbol: str, timeframe: str, gen_iso: str, feature_hash: str
    ) -> str:
        raw = "|".join(["signal", symbol, timeframe, gen_iso, self.candidate_id, feature_hash])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class MajorityProducer(SignalProducer):
    """Majority-class baseline producer."""

    candidate_id: str = Field(default="majority")
    candidate_name: str = Field(default="majority")
    model: MajorityBaseline = Field(default_factory=MajorityBaseline, exclude=True)
    fitted: bool = Field(default=False, exclude=True)

    def fit(self, X: pd.DataFrame, y: np.ndarray, feature_names: list[str]) -> None:
        self.model.fit(X, y, feature_names=feature_names)
        self.feature_names = list(feature_names)
        object.__setattr__(self, "fitted", True)

    def produce(
        self,
        bar_idx: int,
        bar: dict[str, object],
        features_row: dict[str, float],
        generated_at: str,
    ) -> SignalRecord | None:
        if not self.fitted:
            return None
        cost = self._cost_estimate()
        edge = self.cost_estimate.get("expected_edge_bp") if self.cost_estimate else None
        inputs = PolicyInputs(
            feature_row=features_row,
            feature_names=self.feature_names,
            model=self.model,
            model_id=self.model_id,
            model_artifact_hash=self.artifact_sha256,
            label_convention="long_up",
            calibration_required=self.calibration_required,
            t_long=self.t_long,
            t_short=self.t_short,
            require_edge=self.require_edge,
            expected_edge_bp=edge,
            cost=cost,
            enabled_directions=set(self.enabled_directions),
            data_quality="validated",
            generated_at=generated_at,
            valid_until=(datetime.fromisoformat(generated_at.replace("Z", "+00:00")).replace(tzinfo=UTC) if "Z" in generated_at else datetime.fromisoformat(generated_at)).isoformat(),
            symbol="BTC/USDT",
            timeframe="1h",
            horizon_bars=self.horizon_bars,
        )
        record, _ = decide(inputs)
        return record

    def _cost_estimate(self) -> CostEstimate:
        legs = self.cost_estimate or {}
        return CostEstimate(
            fee_bp=float(legs.get("fee_bp", 0.0)),
            spread_bp=float(legs.get("spread_bp", 0.0)),
            slippage_bp=float(legs.get("slippage_bp", 0.0)),
            funding_bp=float(legs.get("funding_bp", 0.0)),
            source="configured",
        )


class TrendProducer(SignalProducer):
    """Trend-following baseline producer."""

    candidate_id: str = Field(default="trend")
    candidate_name: str = Field(default="trend")
    model: TrendBaseline = Field(default_factory=TrendBaseline, exclude=True)
    fitted: bool = Field(default=False, exclude=True)

    def fit(self, X: pd.DataFrame, y: np.ndarray, feature_names: list[str]) -> None:
        self.model.fit(X, y, feature_names=feature_names)
        self.feature_names = list(feature_names)
        object.__setattr__(self, "fitted", True)

    def produce(
        self,
        bar_idx: int,
        bar: dict[str, object],
        features_row: dict[str, float],
        generated_at: str,
    ) -> SignalRecord | None:
        if not self.fitted:
            return None
        cost = self._cost_estimate()
        edge = self.cost_estimate.get("expected_edge_bp") if self.cost_estimate else None
        inputs = PolicyInputs(
            feature_row=features_row,
            feature_names=self.feature_names,
            model=self.model,
            model_id=self.model_id,
            model_artifact_hash=self.artifact_sha256,
            label_convention="long_up",
            calibration_required=self.calibration_required,
            t_long=self.t_long,
            t_short=self.t_short,
            require_edge=self.require_edge,
            expected_edge_bp=edge,
            cost=cost,
            enabled_directions=set(self.enabled_directions),
            data_quality="validated",
            generated_at=generated_at,
            valid_until=(datetime.fromisoformat(generated_at.replace("Z", "+00:00")).replace(tzinfo=UTC) if "Z" in generated_at else datetime.fromisoformat(generated_at)).isoformat(),
            symbol="BTC/USDT",
            timeframe="1h",
            horizon_bars=self.horizon_bars,
        )
        record, _ = decide(inputs)
        return record

    def _cost_estimate(self) -> CostEstimate:
        legs = self.cost_estimate or {}
        return CostEstimate(
            fee_bp=float(legs.get("fee_bp", 0.0)),
            spread_bp=float(legs.get("spread_bp", 0.0)),
            slippage_bp=float(legs.get("slippage_bp", 0.0)),
            funding_bp=float(legs.get("funding_bp", 0.0)),
            source="configured",
        )


class LogisticProducer(SignalProducer):
    """Frozen logistic regression producer.

    Loads the frozen artifact from models/artifacts/logistic-ee482c-3e3324f2-20261028T132236/.
    Does NOT retrain. Does NOT modify frozen configuration.
    """

    candidate_id: str = Field(default="logistic")
    candidate_name: str = Field(default="logistic")
    artifact_dir: Path = Field(default=LOGISTIC_ARTIFACT_DIR)
    model: SklearnLogistic | None = Field(default=None, exclude=True)
    loaded: bool = Field(default=False, exclude=True)
    artifact_sha256: str = Field(default="")

    def load_artifact(self) -> bool:
        """Load the frozen logistic artifact. Returns True on success."""
        record_path = self.artifact_dir / "record.json"
        if not record_path.is_file():
            return False
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        try:
            model = SklearnLogistic.from_dict(payload)
            self.model = model
            self.model_id = str(payload.get("model_id", self.candidate_id))
            self.feature_names = list(payload.get("feature_names", []))
            self.artifact_sha256 = str(payload.get("artifact_sha256", ""))
            object.__setattr__(self, "loaded", True)
            return True
        except Exception:
            return False

    def produce(
        self,
        bar_idx: int,
        bar: dict[str, object],
        features_row: dict[str, float],
        generated_at: str,
    ) -> SignalRecord | None:
        if not self.loaded or self.model is None:
            return None
        cost = self._cost_estimate()
        edge = self.cost_estimate.get("expected_edge_bp") if self.cost_estimate else None
        inputs = PolicyInputs(
            feature_row=features_row,
            feature_names=self.feature_names,
            model=self.model,
            model_id=self.model_id,
            model_artifact_hash=self.artifact_sha256,
            label_convention="long_up",
            calibration_required=self.calibration_required,
            t_long=self.t_long,
            t_short=self.t_short,
            require_edge=self.require_edge,
            expected_edge_bp=edge,
            cost=cost,
            enabled_directions=set(self.enabled_directions),
            data_quality="validated",
            generated_at=generated_at,
            valid_until=(datetime.fromisoformat(generated_at.replace("Z", "+00:00")).replace(tzinfo=UTC) if "Z" in generated_at else datetime.fromisoformat(generated_at)).isoformat(),
            symbol="BTC/USDT",
            timeframe="1h",
            horizon_bars=self.horizon_bars,
        )
        record, _ = decide(inputs)
        return record

    def _cost_estimate(self) -> CostEstimate:
        legs = self.cost_estimate or {}
        return CostEstimate(
            fee_bp=float(legs.get("fee_bp", 0.0)),
            spread_bp=float(legs.get("spread_bp", 0.0)),
            slippage_bp=float(legs.get("slippage_bp", 0.0)),
            funding_bp=float(legs.get("funding_bp", 0.0)),
            source="configured",
        )


class LightGBMProducer(SignalProducer):
    """LightGBM producer - NOT_EVALUABLE (no artifact exists).

    produce() always returns None. Status is permanently NOT_EVALUABLE.
    """

    candidate_id: str = Field(default="lightgbm")
    candidate_name: str = Field(default="lightgbm")
    status: CandidateStatus = Field(default="NOT_EVALUABLE")

    def produce(
        self,
        bar_idx: int,
        bar: dict[str, object],
        features_row: dict[str, float],
        generated_at: str,
    ) -> SignalRecord | None:
        return None


def create_producers(
    cost_legs: dict[str, float] | None = None,
) -> dict[str, SignalProducer]:
    """Create all four candidate producers.

    Returns dict keyed by candidate_id: majority, trend, logistic, lightgbm.
    logistic loads the frozen artifact; lightgbm is NOT_EVALUABLE.
    """
    legs = cost_legs or {"fee_bp": 0.0, "spread_bp": 0.0, "slippage_bp": 0.0, "funding_bp": 0.0}

    majority = MajorityProducer(
        candidate_id="majority",
        candidate_name="majority",
        model_id="majority-baseline",
        feature_names=["trend_ratio_20_50"],
        cost_estimate={**legs, "expected_edge_bp": 25.0},
        model=MajorityBaseline(),
        fitted=False,
    )
    trend = TrendProducer(
        candidate_id="trend",
        candidate_name="trend",
        model_id="trend-baseline",
        feature_names=["trend_ratio_20_50"],
        cost_estimate={**legs, "expected_edge_bp": 25.0},
        model=TrendBaseline(),
        fitted=False,
    )
    logistic = LogisticProducer(
        candidate_id="logistic",
        candidate_name="logistic",
        model_id="logistic-ee482c-3e3324f2-20261028T132236",
        feature_names=[
            "ret_1", "ret_4", "ret_12", "sma_20", "sma_50", "sma_ratio_20",
            "trend_ratio_20_50", "ema_12", "ema_26", "macd", "macd_signal",
            "macd_hist", "trend_slope_5", "atr_14", "realized_vol_20", "rsi_14",
            "bb_mid_20", "bb_pos_20", "bb_width_20", "sma_vol_20", "norm_vol_20",
            "volume_z_20", "range", "body", "upper_wick", "lower_wick",
            "vol_regime_100", "trend_regime",
        ],
        cost_estimate={**legs, "expected_edge_bp": 25.0},
    )
    logistic.load_artifact()
    lightgbm = LightGBMProducer(
        candidate_id="lightgbm",
        candidate_name="lightgbm",
        status="NOT_EVALUABLE",
        model_id="lightgbm-not-available",
        cost_estimate={**legs},
    )
    return {
        "majority": majority,
        "trend": trend,
        "logistic": logistic,
        "lightgbm": lightgbm,
    }


class CandidateIsolationManager:
    """Manages candidate isolation: one SignalProducer per candidate."""

    def __init__(self, producers: dict[str, SignalProducer] | None = None) -> None:
        self._producers: dict[str, SignalProducer] = dict(producers or {})

    def get_producer(self, candidate_id: str) -> SignalProducer | None:
        return self._producers.get(candidate_id)

    def active_producers(self) -> list[SignalProducer]:
        return [p for p in self._producers.values() if p.is_active()]

    def produce_all(
        self,
        bar_idx: int,
        bar: dict[str, object],
        features_row: dict[str, float],
        generated_at: str,
    ) -> dict[str, SignalRecord | None]:
        """Produce signals from all producers. Returns dict candidate_id -> SignalRecord | None."""
        result: dict[str, SignalRecord | None] = {}
        for cid, producer in self._producers.items():
            result[cid] = producer.produce(bar_idx, bar, features_row, generated_at)
        return result

    def statuses(self) -> dict[str, CandidateStatus]:
        return {cid: p.status for cid, p in self._producers.items()}


__all__ = [
    "CandidateIsolationManager",
    "CandidateStatus",
    "LightGBMProducer",
    "LogisticProducer",
    "MajorityProducer",
    "SignalProducer",
    "TrendProducer",
    "create_producers",
]






