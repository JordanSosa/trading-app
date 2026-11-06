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
                    per_year: bool = True, funding: pd.Series | None = None) -> dict[str, Any]:
    """Score + campaign distribution + continuous metrics (+ per-year) for a period."""
    cfg = cfg or load_config()
    start, end = PERIODS[period]
    sig = signals if signals is not None else strategy.signals(candles)
    camp = run_campaigns(candles, strategy, cfg, start, end, signals=sig, funding=funding)
    bt = run_backtest(candles, strategy, cfg, start, end, signals=sig, funding=funding)
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
                 min_index: int | None = None, seed: int = 7, atol: float = 1e-9,
                 window: int | None = -1, window_atol: float = 1e-6) -> list[str]:
    """Verify the strategy is causal AND gives the same signals on the forward window.

    1. Prefix check: ``signals(df[:k]).iloc[-1] == signals(df).iloc[k-1]`` - any
       violation means the strategy peeks at future bars.
    2. Window check: ``signals(df[k-L:k]).iloc[-1] == signals(df).iloc[k-1]``
       with ``L = warmup_bars + 1`` - the least history any bar processed by
       the forward run can have (it computes on a rolling window and only
       replays bars with at least ``warmup_bars`` bars before them).
       Recursive indicators (EMA, Wilder, EWM volatility) depend on where
       their input starts; a violation means ``warmup_bars`` is too small and
       the live run would trade differently from the backtest. Pass
       ``window=None`` to skip.

    Returns a list of violations (empty = OK).
    """
    full = strategy.signals(candles)
    rng = np.random.default_rng(seed)
    lo = min_index if min_index is not None else min(len(candles) - 1, strategy.warmup_bars + 50)
    lo = max(2, lo)
    ks = sorted(set(rng.integers(lo, len(candles) + 1, size=n_checks).tolist()) | {len(candles)})
    if window == -1:
        window = strategy.warmup_bars + 1
    problems: list[str] = []

    def _cmp(part: pd.DataFrame, k: int, tol: float, label: str) -> None:
        for col in ("target", "stop_dist"):
            a, b = part[col].iloc[-1], full[col].iloc[k - 1]
            if (math.isnan(a) != math.isnan(b)) or (not math.isnan(a) and abs(a - b) > tol * max(1.0, abs(b))):
                problems.append(f"{label}: {col} differs at {candles.index[k - 1]}: {label}={a!r} full={b!r}")

    for k in ks:
        _cmp(strategy.signals(candles.iloc[:k]), k, atol, "prefix")
        if window is not None and k > window:
            _cmp(strategy.signals(candles.iloc[k - window:k]), k, window_atol, f"window[{window}]")
    return problems


PERTURB_STEPS = (0.70, 0.85, 1.15, 1.30)


def param_variants(name: str):
    """Default params plus each numeric param at -30/-15/+15/+30% (one at a time)."""
    from .strategies import get_strategy
    base = get_strategy(name)
    yield "default", dict(base.params)
    for k, v in base.default_params.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v == 0:
            continue
        for f in PERTURB_STEPS:
            nv = v * f
            if isinstance(v, int):
                nv = int(round(nv))
                if nv == v:
                    continue
            params = {**base.params, k: nv}
            try:
                get_strategy(name, params)
            except ValueError:
                continue
            yield f"{k}x{f}", params


def paired_block_t(a: pd.DataFrame, b: pd.DataFrame, block: int = 30, n_boot: int = 2000,
                   seed: int = 0) -> dict[str, float]:
    """Paired comparison of two campaign sets on identical start dates.

    Uses d = log1p(r_a) - log1p(r_b) per start. Overlapping 30-day campaigns
    are strongly autocorrelated, so the standard error comes from a moving-
    block bootstrap with ``block`` consecutive starts per block.
    """
    m = a[["start", "return"]].merge(b[["start", "return"]], on="start", suffixes=("_a", "_b"))
    d = (np.log1p(m["return_a"]) - np.log1p(m["return_b"])).to_numpy()
    n = len(d)
    if n < 2 * block:
        return {"n": n, "mean_diff": float(d.mean()) if n else float("nan"), "se": float("nan"), "t": float("nan")}
    rng = np.random.default_rng(seed)
    k = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, k))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    boots = d[idx].mean(axis=1)
    se = float(boots.std(ddof=1))
    return {"n": n, "mean_diff": float(d.mean()), "se": se, "t": float(d.mean() / se) if se > 0 else float("nan")}
