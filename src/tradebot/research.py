"""Research protocol: fixed periods, a pre-declared score, and causality checks.

Periods (fixed before any strategy was designed):

* TRAIN    2018-01-01 .. 2023-12-31  - design and parameter tuning
* VALID    2024-01-01 .. 2024-12-31  - model selection
* HOLDOUT  2025-01-01 .. latest      - touched once, after selection, to report

Score (pre-declared): mean of log(1 + r) over rolling 30-day campaigns started
daily, each starting flat with fresh capital and ending flat - i.e. the
expected log-growth of exactly the run we deploy. Log-growth rewards making
money and penalises losses more than gains of equal size.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from .backtest import run_backtest, run_campaigns
from .config import Config, load_config
from .metrics import campaign_summary
from .strategies.base import Strategy

PERIODS = {
    "train": ("2018-01-01", "2023-12-31"),
    "valid": ("2024-01-01", "2024-12-31"),
    "holdout": ("2025-01-01", None),
    # TRAIN + VALID together, used for the final model-selection ranking.
    "dev": ("2018-01-01", "2024-12-31"),
}
TRAIN_YEARS = [2018, 2019, 2020, 2021, 2022, 2023]


def campaign_score(campaigns: pd.DataFrame) -> float:
    if campaigns.empty:
        return float("nan")
    return float(np.log1p(campaigns["return"].to_numpy()).mean())


def evaluate_period(candles: pd.DataFrame, strategy: Strategy, cfg: Config | None = None,
                    period: str = "train", signals: pd.DataFrame | None = None,
                    per_year: bool = True) -> dict[str, Any]:
    """Score + campaign distribution + continuous metrics (+ per-year) for a period."""
    cfg = cfg or load_config()
    start, end = PERIODS[period]
    sig = signals if signals is not None else strategy.signals(candles)
    camp = run_campaigns(candles, strategy, cfg, start, end, signals=sig)
    bt = run_backtest(candles, strategy, cfg, start, end, signals=sig)
    out: dict[str, Any] = {
        "period": period,
        "score": campaign_score(camp),
        "campaigns": campaign_summary(camp),
        "continuous": {k: bt.metrics.get(k) for k in (
            "total_return", "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "n_trades",
            "round_trips", "win_rate", "profit_factor", "avg_exposure", "time_in_market",
            "fees_paid", "avg_hold_hours")},
    }
    if not camp.empty:
        out["campaigns"]["bh_pct_positive"] = float((camp["bh_return"] > 0).mean())
        out["campaigns"]["bh_mean"] = float(camp["bh_return"].mean())
    if per_year and not camp.empty:
        years = camp.groupby(camp["start"].dt.year)
        out["per_year"] = {
            int(y): {"score": campaign_score(g), "pct_positive": float((g["return"] > 0).mean()),
                     "mean": float(g["return"].mean()), "n": int(len(g))}
            for y, g in years
        }
    return out


def check_causal(strategy: Strategy, candles: pd.DataFrame, n_checks: int = 25,
                 min_index: int | None = None, seed: int = 7, atol: float = 1e-9) -> list[str]:
    """Verify ``signals(df[:k]).iloc[-1] == signals(df).iloc[k-1]`` at random cut points.

    Returns a list of violations (empty = causal). Any violation means the
    strategy peeks at future bars and its backtest results are invalid.
    """
    full = strategy.signals(candles)
    rng = np.random.default_rng(seed)
    lo = min_index if min_index is not None else min(len(candles) - 1, strategy.warmup_bars + 50)
    lo = max(2, lo)
    ks = sorted(set(rng.integers(lo, len(candles) + 1, size=n_checks).tolist()) | {len(candles)})
    problems: list[str] = []
    for k in ks:
        part = strategy.signals(candles.iloc[:k])
        for col in ("target", "stop_dist"):
            a, b = part[col].iloc[-1], full[col].iloc[k - 1]
            if (math.isnan(a) != math.isnan(b)) or (not math.isnan(a) and abs(a - b) > atol * max(1.0, abs(b))):
                problems.append(f"{col} differs at {candles.index[k - 1]}: prefix={a!r} full={b!r}")
    return problems
