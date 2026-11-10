"""Performance statistics built on *daily* returns (the unit the profit target is defined in)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

DAYS_PER_YEAR = 365.0


def max_drawdown(equity: np.ndarray) -> float:
    e = np.asarray(equity, dtype="float64")
    if e.size == 0:
        return 0.0
    peak = np.maximum.accumulate(e)
    return float(np.max(1.0 - e / peak))


def daily_stats(daily_returns: np.ndarray, target: float | None = 0.02) -> dict[str, Any]:
    """Statistics of a series of simple daily returns."""
    r = np.asarray(daily_returns, dtype="float64")
    r = r[np.isfinite(r)]
    n = int(r.size)
    if n == 0:
        return {"days": 0}
    equity = np.concatenate([[1.0], np.cumprod(1.0 + r)])
    wiped = bool((r <= -1.0).any()) or equity[-1] <= 0
    log_growth = float(np.log1p(np.maximum(r, -0.999999)).mean())
    mu, sd = float(r.mean()), float(r.std(ddof=1)) if n > 1 else 0.0
    neg = r[r < 0]
    dsd = float(np.sqrt((neg ** 2).sum() / n)) if n else 0.0
    mdd = max_drawdown(equity)
    years = n / DAYS_PER_YEAR
    total = float(equity[-1] - 1.0)
    cagr = float(equity[-1] ** (1.0 / years) - 1.0) if years > 0 and equity[-1] > 0 else -1.0
    out: dict[str, Any] = {
        "days": n,
        "total_return": total,
        "cagr": cagr,
        "mean_daily": mu,
        # The constant daily return that compounds to the same result: the number
        # to compare with a "2% a day" target.
        "compound_daily": float(math.expm1(log_growth)),
        "daily_vol": sd,
        "sharpe": float(mu / sd * math.sqrt(DAYS_PER_YEAR)) if sd > 0 else 0.0,
        "sortino": float(mu / dsd * math.sqrt(DAYS_PER_YEAR)) if dsd > 0 else 0.0,
        "max_drawdown": mdd,
        "calmar": float(cagr / mdd) if mdd > 0 else float("nan"),
        "pct_days_up": float((r > 0).mean()),
        "pct_days_down": float((r < 0).mean()),
        "best_day": float(r.max()),
        "worst_day": float(r.min()),
        "p01_day": float(np.quantile(r, 0.01)),
        "p05_day": float(np.quantile(r, 0.05)),
        "p95_day": float(np.quantile(r, 0.95)),
        "wiped_out": bool(wiped),
    }
    if target is not None:
        out["target"] = target
        out["pct_days_at_target"] = float((r >= target).mean())
        out["pct_days_below_minus_target"] = float((r <= -target).mean())
    # Longest stretch below a previous equity peak, in days.
    peak = np.maximum.accumulate(equity)
    under = equity < peak * (1 - 1e-12)
    longest = cur = 0
    for u in under:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    out["longest_drawdown_days"] = int(longest)
    return out


def monthly_returns(day_end_ms: np.ndarray, daily_returns: np.ndarray) -> dict[str, float]:
    """Calendar-month compounded returns keyed 'YYYY-MM' (the month the day ended in)."""
    out: dict[str, float] = {}
    # A day that ends at 00:00 belongs to the previous calendar day.
    months = (np.asarray(day_end_ms, dtype="int64") - 1).astype("datetime64[ms]").astype("datetime64[M]").astype(str)
    for m, r in zip(months, daily_returns):
        out[str(m)] = (1.0 + out.get(str(m), 0.0)) * (1.0 + float(r)) - 1.0
    return out


def yearly_returns(day_end_ms: np.ndarray, daily_returns: np.ndarray) -> dict[str, float]:
    out: dict[str, float] = {}
    years = (np.asarray(day_end_ms, dtype="int64") - 1).astype("datetime64[ms]").astype("datetime64[Y]").astype(str)
    for y, r in zip(years, daily_returns):
        out[str(y)] = (1.0 + out.get(str(y), 0.0)) * (1.0 + float(r)) - 1.0
    return out


def block_bootstrap(daily_returns: np.ndarray, horizon_days: int, n_paths: int = 2000, block: int = 10,
                    seed: int = 7) -> np.ndarray:
    """Stationary-style block bootstrap of daily returns -> array (n_paths, horizon_days).

    Blocks keep the short-range dependence of real returns (volatility clusters,
    losing streaks), which matters for drawdown and ruin estimates.
    """
    r = np.asarray(daily_returns, dtype="float64")
    n = len(r)
    if n < block:
        raise ValueError("not enough daily returns to bootstrap")
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(horizon_days / block)
    starts = rng.integers(0, n, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return r[idx.reshape(n_paths, -1)[:, :horizon_days]]


def ruin_report(daily_returns: np.ndarray, horizon_days: int = 365, levels: tuple[float, ...] = (0.2, 0.3, 0.5),
                n_paths: int = 2000, block: int = 10, seed: int = 7) -> dict[str, Any]:
    """Bootstrap distribution of outcomes over ``horizon_days``.

    Reports the probability of ever being down by each level from the starting
    equity or from a peak, and the distribution of the final multiple.
    """
    paths = block_bootstrap(daily_returns, horizon_days, n_paths, block, seed)
    eq = np.cumprod(1.0 + np.maximum(paths, -1.0), axis=1)
    eq = np.concatenate([np.ones((n_paths, 1)), eq], axis=1)
    peak = np.maximum.accumulate(eq, axis=1)
    dd = (1.0 - eq / peak).max(axis=1)
    low = eq.min(axis=1)
    final = eq[:, -1]
    q = np.quantile(final, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "horizon_days": horizon_days,
        "paths": n_paths,
        "final_multiple": {"p05": float(q[0]), "p25": float(q[1]), "median": float(q[2]), "p75": float(q[3]),
                           "p95": float(q[4]), "mean": float(final.mean())},
        "prob_loss": float((final < 1.0).mean()),
        "prob_drawdown": {f"{lv:.0%}": float((dd >= lv).mean()) for lv in levels},
        "prob_below_start": {f"{lv:.0%}": float((low <= 1.0 - lv).mean()) for lv in levels},
        "median_max_drawdown": float(np.median(dd)),
        "p95_max_drawdown": float(np.quantile(dd, 0.95)),
    }


def fmt_pct(x: float | None, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x * 100:.{digits}f}%"
