"""Statistical robustness analysis for Q6 frozen candidate evaluation.

Block-bootstrap CIs, permutation tests, regime analysis, cost sensitivity
(1x/1.5x/2x), and buy-and-hold benchmark comparison. Skips
TEMPORAL_PROVENANCE_UNVERIFIED candidates.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT / "src"))

DEFAULT_12M_STORAGE_ROOT = Path("data/historical_accelerated_12m")
DEFAULT_RESULTS_ROOT = Path("docs/validation/results")
SCHEMA_VERSION = "statistical_robustness.v1"
BOOTSTRAP_SEED = 42
PERMUTATION_SEED = 42
COST_MULTIPLIERS = (1.0, 1.5, 2.0)
BLOCK_SIZE = 24
N_BOOTSTRAP_REPLICATES = 1000
N_PERMUTATION_REPLICATES = 1000
PT_MULT = 2.0
SL_MULT = 1.0
MAX_HORIZON_BARS = 24
BASE_FEE_BP = 10.0
BASE_SPREAD_BP = 5.0
BASE_SLIPPAGE_BP = 5.0
VOL_WINDOW = 20
REGIME_BULL_THRESHOLD = 0.005
REGIME_BEAR_THRESHOLD = -0.005


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class BootstrapCIResult:
    mean_return_bp: float
    ci_lower_bp: float
    ci_upper_bp: float
    n_replicates: int
    block_size: int
    seed: int
    p_value_zero: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_return_bp": self.mean_return_bp,
            "ci_lower_bp": self.ci_lower_bp,
            "ci_upper_bp": self.ci_upper_bp,
            "n_replicates": self.n_replicates,
            "block_size": self.block_size,
            "seed": self.seed,
            "p_value_zero": self.p_value_zero,
        }


@dataclass(frozen=True, slots=True)
class PermutationTestResult:
    observed_mean_bp: float
    p_value: float
    n_replicates: int
    seed: int
    null_mean_bp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_mean_bp": self.observed_mean_bp,
            "p_value": self.p_value,
            "n_replicates": self.n_replicates,
            "seed": self.seed,
            "null_mean_bp": self.null_mean_bp,
        }


@dataclass(frozen=True, slots=True)
class RegimePeriodResult:
    regime_type: str
    start_utc: str
    end_utc: str
    n_bars: int
    candidate_return_bp: float | None
    buy_hold_return_bp: float | None
    candidate_outperformed: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime_type": self.regime_type,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
            "n_bars": self.n_bars,
            "candidate_return_bp": self.candidate_return_bp,
            "buy_hold_return_bp": self.buy_hold_return_bp,
            "candidate_outperformed": self.candidate_outperformed,
        }


@dataclass(frozen=True, slots=True)
class RegimeAnalysisResult:
    regime_periods: list[RegimePeriodResult]
    regime_robustness: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime_periods": [p.to_dict() for p in self.regime_periods],
            "regime_robustness": self.regime_robustness,
        }


@dataclass(frozen=True, slots=True)
class CostSensitivityResult:
    cost_multiplier: float
    net_return_bp: float
    trade_count: int
    profitable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_multiplier": self.cost_multiplier,
            "net_return_bp": self.net_return_bp,
            "trade_count": self.trade_count,
            "profitable": self.profitable,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkComparisonResult:
    candidate_net_return_bp: float
    buy_hold_net_return_bp: float
    outperforms_buy_hold: bool
    outperforms_cash: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_net_return_bp": self.candidate_net_return_bp,
            "buy_hold_net_return_bp": self.buy_hold_net_return_bp,
            "outperforms_buy_hold": self.outperforms_buy_hold,
            "outperforms_cash": self.outperforms_cash,
        }


@dataclass(frozen=True, slots=True)
class StatisticalRobustnessResult:
    candidate_id: str
    model_name: str
    temporal_provenance_status: str
    status: str
    bootstrap_ci: BootstrapCIResult | None
    permutation_test: PermutationTestResult | None
    regime_analysis: RegimeAnalysisResult | None
    cost_sensitivity: list[CostSensitivityResult]
    benchmark_comparison: BenchmarkComparisonResult | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "model_name": self.model_name,
            "temporal_provenance_status": self.temporal_provenance_status,
            "status": self.status,
            "bootstrap_ci": self.bootstrap_ci.to_dict() if self.bootstrap_ci else None,
            "permutation_test": self.permutation_test.to_dict() if self.permutation_test else None,
            "regime_analysis": self.regime_analysis.to_dict() if self.regime_analysis else None,
            "cost_sensitivity": [c.to_dict() for c in self.cost_sensitivity],
            "benchmark_comparison": self.benchmark_comparison.to_dict()
            if self.benchmark_comparison
            else None,
        }


def _load_12m_candles() -> list[dict[str, Any]]:
    storage_root = DEFAULT_12M_STORAGE_ROOT
    normalized_path = storage_root / "normalized_candles.jsonl"
    if not normalized_path.is_file():
        return []
    candles: list[dict[str, Any]] = []
    with normalized_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                candles.append(json.loads(line))
    return candles


def _load_prediction_audit(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        return json.loads(f.read())


def _compute_realized_vol(closes: np.ndarray, idx: int, window: int = VOL_WINDOW) -> float:
    if idx < window:
        return 1e-8
    rets = np.diff(np.log(closes[idx - window : idx + 1]))
    return float(np.std(rets)) if len(rets) > 0 else 1e-8


def _compute_trade_returns(
    candles: list[dict[str, Any]],
    predictions: list[int],
    decision_mask: list[bool],
    fee_bp: float = BASE_FEE_BP,
    spread_bp: float = BASE_SPREAD_BP,
    slippage_bp: float = BASE_SLIPPAGE_BP,
) -> list[float]:
    n = len(candles)
    closes = np.array([float(c["close"]) for c in candles], dtype=float)
    highs = np.array([float(c["high"]) for c in candles], dtype=float)
    lows = np.array([float(c["low"]) for c in candles], dtype=float)
    returns = np.diff(np.log(closes))
    returns = np.concatenate([[0.0], returns])

    trade_returns: list[float] = []
    total_cost_bp = fee_bp + spread_bp + slippage_bp

    for i in range(n):
        if not decision_mask[i]:
            continue
        pred = predictions[i]
        if pred == 0:
            continue
        entry_price = closes[i]
        vol = _compute_realized_vol(closes, i)
        if pred == 1:  # long
            pt = entry_price * (1.0 + PT_MULT * vol)
            sl = entry_price * (1.0 - SL_MULT * vol)
        else:  # short
            pt = entry_price * (1.0 - PT_MULT * vol)
            sl = entry_price * (1.0 + SL_MULT * vol)

        exit_price = closes[min(i + MAX_HORIZON_BARS, n - 1)]
        for j in range(i + 1, min(i + MAX_HORIZON_BARS + 1, n)):
            if pred == 1:
                if highs[j] >= pt:
                    exit_price = pt
                    break
                if lows[j] <= sl:
                    exit_price = sl
                    break
            else:
                if lows[j] <= sl:
                    exit_price = sl
                    break
                if highs[j] >= pt:
                    exit_price = pt
                    break

        gross_ret_bp = (exit_price / entry_price - 1.0) * 10000.0
        if pred == -1:
            gross_ret_bp = -gross_ret_bp
        net_ret_bp = gross_ret_bp - total_cost_bp
        trade_returns.append(net_ret_bp)

    return trade_returns


def _block_bootstrap_ci(
    trade_returns: list[float],
    seed: int = BOOTSTRAP_SEED,
    n_replicates: int = N_BOOTSTRAP_REPLICATES,
    block_size: int = BLOCK_SIZE,
) -> BootstrapCIResult:
    rng = np.random.RandomState(seed)
    n = len(trade_returns)
    if n == 0:
        return BootstrapCIResult(
            mean_return_bp=0.0,
            ci_lower_bp=0.0,
            ci_upper_bp=0.0,
            n_replicates=n_replicates,
            block_size=block_size,
            seed=seed,
            p_value_zero=1.0,
        )
    arr = np.array(trade_returns, dtype=float)
    replicate_means = np.empty(n_replicates, dtype=float)
    for r in range(n_replicates):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randint(0, max(1, n - block_size + 1))
            end = min(start + block_size, n)
            sample.extend(arr[start:end].tolist())
        sample = sample[:n]
        replicate_means[r] = float(np.mean(sample))
    mean_return_bp = float(np.mean(arr))
    ci_lower = float(np.percentile(replicate_means, 2.5))
    ci_upper = float(np.percentile(replicate_means, 97.5))
    p_value_zero = float(np.mean(replicate_means <= 0.0))
    return BootstrapCIResult(
        mean_return_bp=mean_return_bp,
        ci_lower_bp=ci_lower,
        ci_upper_bp=ci_upper,
        n_replicates=n_replicates,
        block_size=block_size,
        seed=seed,
        p_value_zero=p_value_zero,
    )


def _permutation_test(
    trade_returns: list[float],
    seed: int = PERMUTATION_SEED,
    n_replicates: int = N_PERMUTATION_REPLICATES,
) -> PermutationTestResult:
    rng = np.random.RandomState(seed)
    arr = np.array(trade_returns, dtype=float)
    n = len(arr)
    if n == 0:
        return PermutationTestResult(
            observed_mean_bp=0.0,
            p_value=1.0,
            n_replicates=n_replicates,
            seed=seed,
            null_mean_bp=0.0,
        )
    observed_mean = float(np.mean(arr))
    replicate_means = np.empty(n_replicates, dtype=float)
    for r in range(n_replicates):
        signs = rng.choice([-1.0, 1.0], size=n)
        replicate_means[r] = float(np.mean(arr * signs))
    null_mean = float(np.mean(replicate_means))
    p_value = float(np.mean(np.abs(replicate_means) >= abs(observed_mean)))
    return PermutationTestResult(
        observed_mean_bp=observed_mean,
        p_value=p_value,
        n_replicates=n_replicates,
        seed=seed,
        null_mean_bp=null_mean,
    )


def _regime_analysis(
    candles: list[dict[str, Any]],
    predictions: list[int],
    decision_mask: list[bool],
    trade_returns: list[float],
) -> RegimeAnalysisResult:
    n = len(candles)
    closes = np.array([float(c["close"]) for c in candles], dtype=float)
    timestamps = [c.get("open_time", "") for c in candles]

    # Classify regimes by 20-bar return
    regimes = ["sideways"] * n
    for i in range(VOL_WINDOW, n):
        ret_20 = (closes[i] / closes[i - VOL_WINDOW]) - 1.0
        if ret_20 > REGIME_BULL_THRESHOLD:
            regimes[i] = "bull"
        elif ret_20 < REGIME_BEAR_THRESHOLD:
            regimes[i] = "bear"

    # Group consecutive same-regime bars into periods
    periods: list[RegimePeriodResult] = []
    current_regime = regimes[0] if n > 0 else "sideways"
    period_start = 0
    for i in range(1, n):
        if regimes[i] != current_regime:
            periods.append(
                RegimePeriodResult(
                    regime_type=current_regime,
                    start_utc=timestamps[period_start],
                    end_utc=timestamps[i - 1],
                    n_bars=i - period_start,
                    candidate_return_bp=None,
                    buy_hold_return_bp=None,
                    candidate_outperformed=None,
                )
            )
            current_regime = regimes[i]
            period_start = i
    if n > 0:
        periods.append(
            RegimePeriodResult(
                regime_type=current_regime,
                start_utc=timestamps[period_start],
                end_utc=timestamps[n - 1],
                n_bars=n - period_start,
                candidate_return_bp=None,
                buy_hold_return_bp=None,
                candidate_outperformed=None,
            )
        )

    # Compute returns per period
    for period in periods:
        start_idx = timestamps.index(period.start_utc) if period.start_utc in timestamps else 0
        end_idx = timestamps.index(period.end_utc) if period.end_utc in timestamps else n - 1
        if end_idx >= n:
            end_idx = n - 1
        bh_ret_bp = (closes[end_idx] / closes[start_idx] - 1.0) * 10000.0
        # Candidate returns within this period
        period_trades = [tr for idx, tr in enumerate(trade_returns) if start_idx <= idx <= end_idx]
        cand_ret_bp = float(np.sum(period_trades)) if period_trades else 0.0
        # Replace the period with updated values
        periods[periods.index(period)] = RegimePeriodResult(
            regime_type=period.regime_type,
            start_utc=period.start_utc,
            end_utc=period.end_utc,
            n_bars=period.n_bars,
            candidate_return_bp=cand_ret_bp,
            buy_hold_return_bp=bh_ret_bp,
            candidate_outperformed=(cand_ret_bp > bh_ret_bp) if period_trades else None,
        )

    n_outperformed = sum(1 for p in periods if p.candidate_outperformed is True)
    n_with_trades = sum(1 for p in periods if p.candidate_outperformed is not None)
    regime_robustness = (n_outperformed / n_with_trades) if n_with_trades > 0 else 0.0

    return RegimeAnalysisResult(regime_periods=periods, regime_robustness=regime_robustness)


def _cost_sensitivity(
    candles: list[dict[str, Any]],
    predictions: list[int],
    decision_mask: list[bool],
) -> list[CostSensitivityResult]:
    results: list[CostSensitivityResult] = []
    for mult in COST_MULTIPLIERS:
        fee_bp = BASE_FEE_BP * mult
        spread_bp = BASE_SPREAD_BP * mult
        slippage_bp = BASE_SLIPPAGE_BP * mult
        trade_returns = _compute_trade_returns(
            candles,
            predictions,
            decision_mask,
            fee_bp=fee_bp,
            spread_bp=spread_bp,
            slippage_bp=slippage_bp,
        )
        net_return_bp = float(np.sum(trade_returns)) if trade_returns else 0.0
        results.append(
            CostSensitivityResult(
                cost_multiplier=mult,
                net_return_bp=net_return_bp,
                trade_count=len(trade_returns),
                profitable=net_return_bp > 0.0,
            )
        )
    return results


def _buy_and_hold_return(candles: list[dict[str, Any]]) -> float:
    if len(candles) < 2:
        return 0.0
    first_close = float(candles[0]["close"])
    last_close = float(candles[-1]["close"])
    return (last_close / first_close - 1.0) * 10000.0


class StatisticalRobustnessAnalyzer:
    def __init__(
        self,
        *,
        storage_root: Path | str | None = None,
        results_root: Path | str | None = None,
        seed: int = BOOTSTRAP_SEED,
    ) -> None:
        self.storage_root = Path(storage_root or DEFAULT_12M_STORAGE_ROOT)
        self.results_root = Path(results_root or DEFAULT_RESULTS_ROOT)
        self.seed = seed

    def analyze_candidate(
        self,
        *,
        candidate_id: str,
        model_name: str,
        predictions: list[int],
        decision_mask: list[bool],
        candles: list[dict[str, Any]],
        temporal_provenance_status: str,
    ) -> StatisticalRobustnessResult:
        if temporal_provenance_status != "TEMPORAL_PROVENANCE_VERIFIED":
            return StatisticalRobustnessResult(
                candidate_id=candidate_id,
                model_name=model_name,
                temporal_provenance_status=temporal_provenance_status,
                status="SKIPPED_UNVERIFIED",
                bootstrap_ci=None,
                permutation_test=None,
                regime_analysis=None,
                cost_sensitivity=[],
                benchmark_comparison=None,
            )

        trade_returns = _compute_trade_returns(candles, predictions, decision_mask)
        net_return_bp = float(np.sum(trade_returns)) if trade_returns else 0.0

        bootstrap_ci = _block_bootstrap_ci(
            trade_returns,
            seed=self.seed,
            n_replicates=N_BOOTSTRAP_REPLICATES,
            block_size=BLOCK_SIZE,
        )
        permutation_test = _permutation_test(
            trade_returns,
            seed=self.seed,
            n_replicates=N_PERMUTATION_REPLICATES,
        )
        regime_analysis = _regime_analysis(candles, predictions, decision_mask, trade_returns)
        cost_sensitivity = _cost_sensitivity(candles, predictions, decision_mask)
        bh_return_bp = _buy_and_hold_return(candles)
        benchmark = BenchmarkComparisonResult(
            candidate_net_return_bp=net_return_bp,
            buy_hold_net_return_bp=bh_return_bp,
            outperforms_buy_hold=net_return_bp > bh_return_bp,
            outperforms_cash=net_return_bp > 0.0,
        )

        return StatisticalRobustnessResult(
            candidate_id=candidate_id,
            model_name=model_name,
            temporal_provenance_status=temporal_provenance_status,
            status="EVALUATED",
            bootstrap_ci=bootstrap_ci,
            permutation_test=permutation_test,
            regime_analysis=regime_analysis,
            cost_sensitivity=cost_sensitivity,
            benchmark_comparison=benchmark,
        )

    def analyze_all(
        self,
        *,
        prediction_audits: dict[str, dict[str, Any]],
        candles: list[dict[str, Any]],
        provenance_statuses: dict[str, str],
    ) -> dict[str, StatisticalRobustnessResult]:
        results: dict[str, StatisticalRobustnessResult] = {}
        for candidate_id, audit in prediction_audits.items():
            model_name = audit.get("model_name", candidate_id)
            predictions = audit.get("predictions", [])
            decision_mask = audit.get("decision_mask", [])
            prov_status = provenance_statuses.get(candidate_id, "TEMPORAL_PROVENANCE_UNVERIFIED")
            results[candidate_id] = self.analyze_candidate(
                candidate_id=candidate_id,
                model_name=model_name,
                predictions=predictions,
                decision_mask=decision_mask,
                candles=candles,
                temporal_provenance_status=prov_status,
            )
        return results


def run_statistical_robustness_analysis(
    *,
    prediction_audit_12m_path: Path | str,
    provenance_audit_path: Path | str | None = None,
    output_path: Path | str | None = None,
) -> dict[str, Any]:
    candles = _load_12m_candles()
    audit = _load_prediction_audit(prediction_audit_12m_path)

    provenance_statuses: dict[str, str] = {}
    if provenance_audit_path:
        prov = _load_prediction_audit(provenance_audit_path)
        for cid, cres in prov.get("candidate_results", {}).items():
            provenance_statuses[cid] = cres.get(
                "temporal_provenance_status", "TEMPORAL_PROVENANCE_UNVERIFIED"
            )

    analyzer = StatisticalRobustnessAnalyzer()
    candidate_results: dict[str, Any] = {}
    for cand_name, cand_audit in audit.get("candidates", {}).items():
        result = analyzer.analyze_candidate(
            candidate_id=cand_audit.get("candidate_id", cand_name),
            model_name=cand_audit.get("model_name", cand_name),
            predictions=cand_audit.get("predictions", []),
            decision_mask=cand_audit.get("decision_mask", []),
            candles=candles,
            temporal_provenance_status=provenance_statuses.get(
                cand_name, "TEMPORAL_PROVENANCE_UNVERIFIED"
            ),
        )
        candidate_results[cand_name] = result.to_dict()

    bh_return_bp = _buy_and_hold_return(candles)

    output: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "dataset_type": "HISTORICAL_ACCELERATED_VALIDATION_12M",
        "official_prospective_evidence": False,
        "q5_2_eligible": False,
        "promotion_eligible": False,
        "q4_decision_preserved": True,
        "q4_decision": "NO_STRATEGY_ELIGIBLE",
        "promotion_gate": "BLOCKED",
        "storage_root": str(DEFAULT_12M_STORAGE_ROOT),
        "n_candles": len(candles),
        "buy_and_hold_return_bp": bh_return_bp,
        "candidates": candidate_results,
    }

    if output_path:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(output, indent=2, sort_keys=True, default=str), encoding="utf-8")

    return output


def write_robustness_report(results: dict[str, Any], output_path: Path | str) -> Path:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(results, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return p


__all__ = [
    "BASE_FEE_BP",
    "BASE_SLIPPAGE_BP",
    "BASE_SPREAD_BP",
    "BLOCK_SIZE",
    "BOOTSTRAP_SEED",
    "COST_MULTIPLIERS",
    "DEFAULT_12M_STORAGE_ROOT",
    "DEFAULT_RESULTS_ROOT",
    "MAX_HORIZON_BARS",
    "N_BOOTSTRAP_REPLICATES",
    "N_PERMUTATION_REPLICATES",
    "PERMUTATION_SEED",
    "PT_MULT",
    "SCHEMA_VERSION",
    "SL_MULT",
    "BenchmarkComparisonResult",
    "CostSensitivityResult",
    "RegimeAnalysisResult",
    "RegimePeriodResult",
    "StatisticalRobustnessAnalyzer",
    "StatisticalRobustnessResult",
    "_block_bootstrap_ci",
    "_buy_and_hold_return",
    "_compute_realized_vol",
    "_compute_trade_returns",
    "_cost_sensitivity",
    "_load_12m_candles",
    "_load_prediction_audit",
    "_permutation_test",
    "_regime_analysis",
    "_sha256_json",
    "_utc_now_iso",
    "run_statistical_robustness_analysis",
    "write_robustness_report",
]
