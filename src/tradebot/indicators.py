"""Causal technical indicators.

Every function returns a Series aligned with its input where value ``t``
depends only on inputs at positions ``<= t`` (no look-ahead). Recursive
indicators (EMA, Wilder smoothing) are computed from the start of the series,
so callers must supply enough warm-up history; ``Strategy.warmup_bars``
declares how much.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HOURS_PER_YEAR = 24 * 365


def sma(x: pd.Series, n: int) -> pd.Series:
    return x.rolling(n, min_periods=n).mean()


def ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(x: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (alpha = 1/n), used by RSI and ATR."""
    return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = wilder(delta.clip(lower=0.0), n)
    down = wilder((-delta).clip(lower=0.0), n)
    rs = up / down.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    return out.where(down != 0.0, 100.0).where(up.notna())


def log_returns(close: pd.Series) -> pd.Series:
    return np.log(close).diff()


def realized_vol(close: pd.Series, n: int, periods_per_year: int = HOURS_PER_YEAR) -> pd.Series:
    """Annualised rolling volatility of log returns."""
    return log_returns(close).rolling(n, min_periods=n).std() * np.sqrt(periods_per_year)


def ewm_vol(close: pd.Series, halflife: int, periods_per_year: int = HOURS_PER_YEAR) -> pd.Series:
    r = log_returns(close)
    return np.sqrt((r ** 2).ewm(halflife=halflife, adjust=False, min_periods=halflife).mean() * periods_per_year)


def donchian_high(high: pd.Series, n: int) -> pd.Series:
    """Highest high of the *previous* n bars (excludes the current bar)."""
    return high.rolling(n, min_periods=n).max().shift(1)


def donchian_low(low: pd.Series, n: int) -> pd.Series:
    """Lowest low of the *previous* n bars (excludes the current bar)."""
    return low.rolling(n, min_periods=n).min().shift(1)


def zscore(x: pd.Series, n: int) -> pd.Series:
    m = x.rolling(n, min_periods=n).mean()
    s = x.rolling(n, min_periods=n).std()
    return (x - m) / s.replace(0.0, np.nan)


def efficiency_ratio(close: pd.Series, n: int) -> pd.Series:
    """Kaufman efficiency ratio: |net move| / sum(|moves|) over n bars, in [0, 1]."""
    change = (close - close.shift(n)).abs()
    noise = close.diff().abs().rolling(n, min_periods=n).sum()
    return change / noise.replace(0.0, np.nan)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = wilder(true_range(df), n)
    plus_di = 100 * wilder(plus_dm, n) / tr
    minus_di = 100 * wilder(minus_dm, n) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return wilder(dx, n)
