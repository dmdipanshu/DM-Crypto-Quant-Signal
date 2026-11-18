"""Market regime coverage analysis for the canonical BTC/USDT 1h dataset.

Stage 5 of Q1.2: descriptive regime classification (not predictive).

Uses:
- Trend: log-return over a 168-hour (1-week) lookback
- Volatility: 24-hour rolling standard deviation of hourly log-returns

Classifies each candle into a regime bucket:
  Bull:   trend > +2% and vol < high_vol_threshold
  Bear:   trend < -2% and vol < high_vol_threshold
  Sideways: |trend| <= 2% and vol < high_vol_threshold
  HighVol: vol >= high_vol_threshold (regardless of trend)
  LowVol:  vol < low_vol_threshold and |trend| <= 2% (subset of sideways)

Read-only: reads the canonical dataset, writes only analysis results.

Run:
    uv run python scripts/analyze_market_regimes.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PATH = (
    REPO_ROOT / "data" / "canonical" / "binance" / "BTC-USDT" / "1h" / "candles.parquet"
)
REPORT_DIR = REPO_ROOT / "docs" / "validation"
RESULTS_DIR = REPORT_DIR / "results"


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def classify_regimes(df: pd.DataFrame) -> dict[str, Any]:
    """Classify each candle into a market regime bucket.

    Returns per-candle regime labels and aggregate coverage statistics.
    """
    close = df["close"].astype(float)
    ret = np.log(close / close.shift(1))

    # Trend: log-return over 168-hour (1-week) lookback
    trend = np.log(close / close.shift(168))

    # Volatility: 24-hour rolling std of hourly log-returns (annualized)
    vol_24h = ret.rolling(24, min_periods=1).std()
    vol_annualized = vol_24h * np.sqrt(8760)  # hours per year

    # Volatility thresholds based on distribution percentiles
    vol_median = float(vol_annualized.median())
    vol_p75 = float(vol_annualized.quantile(0.75))
    vol_p90 = float(vol_annualized.quantile(0.90))

    high_vol_threshold = vol_p90
    low_vol_threshold = vol_p75

    trend_bull_threshold = 0.02  # +2% over 1 week
    trend_bear_threshold = -0.02  # -2% over 1 week

    regimes = []
    for i in range(len(df)):
        t = float(trend.iloc[i]) if not pd.isna(trend.iloc[i]) else 0.0
        v = float(vol_annualized.iloc[i]) if not pd.isna(vol_annualized.iloc[i]) else vol_median

        if v >= high_vol_threshold:
            regime = "high_volatility"
        elif t >= trend_bull_threshold:
            regime = "bull"
        elif t <= trend_bear_threshold:
            regime = "bear"
        else:
            regime = "sideways"

        if regime == "sideways" and v < low_vol_threshold:
            regime = "low_volatility_sideways"

        regimes.append(regime)

    df = df.copy()
    df["regime"] = regimes

    # Aggregate coverage
    regime_counts = df["regime"].value_counts()
    total = len(df)
    regime_coverage = {}
    for r in ["bull", "bear", "sideways", "low_volatility_sideways", "high_volatility"]:
        n = int(regime_counts.get(r, 0))
        regime_coverage[r] = {
            "rows": n,
            "pct_of_total": round(100.0 * n / total, 2),
            "hours": n,  # 1h timeframe: 1 row = 1 hour
        }

    # First/last timestamp per regime
    regime_periods = {}
    for r in regime_coverage:
        subset = df[df["regime"] == r]
        if len(subset) > 0:
            regime_periods[r] = {
                "first_ts": str(subset.index[0]),
                "last_ts": str(subset.index[-1]),
                "contiguous_blocks": int(
                    (subset.index.to_series().diff() > pd.Timedelta(hours=1)).sum()
                )
                + 1,
            }

    return {
        "methodology": {
            "trend_lookback_hours": 168,
            "trend_bull_threshold": trend_bull_threshold,
            "trend_bear_threshold": trend_bear_threshold,
            "vol_lookback_hours": 24,
            "high_vol_threshold_annualized": round(high_vol_threshold, 4),
            "low_vol_threshold_annualized": round(low_vol_threshold, 4),
            "vol_median_annualized": round(vol_median, 4),
            "note": (
                "Descriptive heuristic classification only. Not predictive. "
                "Regimes are assigned per candle based on trailing trend and "
                "volatility; a candle's regime does not predict future regimes."
            ),
        },
        "total_rows": int(total),
        "regime_coverage": regime_coverage,
        "regime_periods": regime_periods,
        "volatility_stats": {
            "median_annualized": round(vol_median, 4),
            "p75_annualized": round(vol_p75, 4),
            "p90_annualized": round(vol_p90, 4),
            "min_annualized": round(float(vol_annualized.min()), 4),
            "max_annualized": round(float(vol_annualized.max()), 4),
        },
        "trend_stats": {
            "median_weekly_log_return": round(float(trend.median()), 6),
            "mean_weekly_log_return": round(float(trend.mean()), 6),
            "std_weekly_log_return": round(float(trend.std()), 6),
        },
    }


def main() -> int:
    print("=== SignalForge Q1.2 Stage 5: Market Regime Coverage ===")
    print(f"Generated: {utc_now_iso()}")
    print()

    if not CANONICAL_PATH.is_file():
        print(f"ERROR: canonical dataset not found at {CANONICAL_PATH}")
        return 1

    df = pd.read_parquet(CANONICAL_PATH)
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    elif "index" in df.columns:
        df = df.set_index("index")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.DatetimeIndex(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df = df.sort_index()
    print(f"Loaded {len(df)} rows from {df.index[0]} to {df.index[-1]}")
    print()

    result = classify_regimes(df)

    print("Regime coverage:")
    for r, info in result["regime_coverage"].items():
        print(f"  {r}: {info['rows']} rows ({info['pct_of_total']}%)")
    print()

    print("Volatility stats (annualized):")
    for k, v in result["volatility_stats"].items():
        print(f"  {k}: {v}")
    print()

    print("Trend stats (weekly log return):")
    for k, v in result["trend_stats"].items():
        print(f"  {k}: {v}")
    print()

    # ---- Write results ----
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / "quantitative_data_expansion_q1_2_regimes.json"
    result["generated_at"] = utc_now_iso()
    result["dataset_first_ts"] = str(df.index[0])
    result["dataset_last_ts"] = str(df.index[-1])
    result["dataset_rows"] = len(df)
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    print(f"[Results] {results_path}")
    print()
    print("=== Market regime analysis complete ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
