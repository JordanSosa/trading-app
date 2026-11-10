"""Causal signal building blocks on (time x symbol) arrays.

Every function here uses finite trailing windows only, so the value at bar
``t`` depends on bars ``t - window .. t`` and nothing else. That is what lets
the forward run (which sees a rolling window of recent bars) reproduce the
backtest (which sees the full history).

Conventions: inputs are float arrays of shape (T, N) with NaN where a symbol
has no bar. Windows are in bars. "vol" is the standard deviation of one-bar
log returns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def log_returns(close: np.ndarray) -> np.ndarray:
    """One-bar log returns; NaN where either close is missing."""
    out = np.full_like(close, np.nan, dtype="float64")
    with np.errstate(invalid="ignore", divide="ignore"):
        out[1:] = np.log(close[1:] / close[:-1])
    return out


def rolling_mean(x: np.ndarray, window: int, min_periods: int | None = None) -> np.ndarray:
    mp = window if min_periods is None else min_periods
    return pd.DataFrame(x).rolling(window, min_periods=mp).mean().to_numpy()


def rolling_sum(x: np.ndarray, window: int, min_periods: int | None = None) -> np.ndarray:
    mp = window if min_periods is None else min_periods
    return pd.DataFrame(x).rolling(window, min_periods=mp).sum().to_numpy()


def rolling_std(x: np.ndarray, window: int, min_periods: int | None = None) -> np.ndarray:
    mp = window if min_periods is None else min_periods
    return pd.DataFrame(x).rolling(window, min_periods=mp).std(ddof=0).to_numpy()


def rolling_max(x: np.ndarray, window: int, min_periods: int | None = None) -> np.ndarray:
    mp = window if min_periods is None else min_periods
    return pd.DataFrame(x).rolling(window, min_periods=mp).max().to_numpy()


def rolling_min(x: np.ndarray, window: int, min_periods: int | None = None) -> np.ndarray:
    mp = window if min_periods is None else min_periods
    return pd.DataFrame(x).rolling(window, min_periods=mp).min().to_numpy()


def realised_vol(close: np.ndarray, window: int, min_frac: float = 0.8) -> np.ndarray:
    """Root-mean-square one-bar log return over ``window`` bars (zero-mean volatility)."""
    r = log_returns(close)
    ms = rolling_mean(r * r, window, max(2, int(window * min_frac)))
    return np.sqrt(ms)


def past_return(close: np.ndarray, lookback: int) -> np.ndarray:
    """log(close[t] / close[t - lookback]); NaN where either is missing."""
    out = np.full_like(close, np.nan, dtype="float64")
    if lookback < len(close):
        with np.errstate(invalid="ignore", divide="ignore"):
            out[lookback:] = np.log(close[lookback:] / close[:-lookback])
    return out


def momentum_z(close: np.ndarray, vol: np.ndarray, lookback: int) -> np.ndarray:
    """Past return over ``lookback`` bars in units of its own expected size (vol * sqrt(lookback))."""
    with np.errstate(invalid="ignore", divide="ignore"):
        return past_return(close, lookback) / (vol * np.sqrt(lookback))


def cross_rank(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Rank each row among ``mask`` members, scaled to [-1, 1] (0 = median). Non-members -> 0."""
    out = np.zeros_like(x, dtype="float64")
    ok = mask & np.isfinite(x)
    n = ok.sum(axis=1)
    order = np.argsort(np.where(ok, x, np.inf), axis=1, kind="stable")
    ranks = np.empty_like(order)
    rows = np.arange(x.shape[0])[:, None]
    ranks[rows, order] = np.arange(x.shape[1])[None, :]
    denom = np.maximum(n - 1, 1)[:, None]
    scaled = 2.0 * ranks / denom - 1.0
    out[ok] = scaled[ok]
    out[n < 3] = 0.0
    return out


def demean(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Subtract the row mean over ``mask`` members; non-members -> 0."""
    ok = mask & np.isfinite(x)
    n = np.maximum(ok.sum(axis=1, keepdims=True), 1)
    mean = np.where(ok, x, 0.0).sum(axis=1, keepdims=True) / n
    return np.where(ok, x - mean, 0.0)


def hold_latch(trigger: np.ndarray, hold: int) -> np.ndarray:
    """True from a trigger bar until ``hold`` bars after the most recent trigger (finite memory)."""
    if hold <= 1:
        return trigger.astype(bool)
    return rolling_max(trigger.astype("float64"), hold, 1) > 0


def portfolio_vol(weights: np.ndarray, vol: np.ndarray, rho: float) -> np.ndarray:
    """Ex-ante portfolio volatility under a constant pairwise correlation ``rho``.

    ``sigma_p^2 = rho * (sum_i w_i s_i)^2 + (1 - rho) * sum_i (w_i s_i)^2``: fully
    offsetting long and short books cancel in the first term, a one-sided book
    adds up almost linearly. Robust (nothing to estimate per pair) and exact to
    reproduce on a rolling window.
    """
    risk = np.nan_to_num(weights * vol)
    net = risk.sum(axis=1)
    return np.sqrt(rho * net * net + (1.0 - rho) * (risk * risk).sum(axis=1))
