"""Short-horizon mean-reversion strategies for 1h BTC/USDT candles.

Both strategies buy sharp, volatility-adjusted dips *only while the
long-term trend is up*, then hold for a fixed time and step aside. In
2018-2023 data, dips inside an uptrend were followed by above-average
returns for about a week. Dips inside a downtrend kept falling, so the
trend filter is what makes the idea work at all.

Design rules shared by both strategies:

* **Dip measure.** ``z = ln(close / SMA_n) / (sigma_1h * sqrt(n))``: the log
  distance of the close from its ``n``-hour simple moving average, in units
  of the ``n``-hour volatility implied by the last 30 days of hourly returns.
  A dip of ``-1.5`` means "1.5 typical ``n``-hour moves below the mean",
  whatever the volatility regime.
* **Trend filter.** A dip counts only while ``close`` is above its
  ``trend_days`` simple moving average of hourly closes.
* **Exit by time, not on reversion.** The position is held for
  ``hold_hours`` after the *last* bar that qualified as a dip, so a dip that
  keeps deepening keeps the position open. Research showed that exiting as
  soon as price reverts to the mean (or on a protective stop) gives up most
  of the edge: the post-dip drift continues well past the mean.
* **Finite memory.** Only rolling windows, no recursive filters. A live run
  that sees ``warmup_bars`` of history produces exactly the target of a
  backtest over the full history.
* **Causal.** Every value at bar ``t`` uses closed bars ``<= t`` only.

Strategies
----------
``meanrev_dip``
    One dip horizon (default 72 hours). Few, large trades: about 0.3 entries
    per 30-day run, in the market about 5% of the time.
``meanrev_dip_multi``
    Three dip horizons (``base``, ``3 * base`` and ``7 * base`` hours,
    default 24 / 72 / 168). A dip on *any* horizon triggers. More frequent
    trades (about 0.75 entries per 30-day run), lower edge per trade.

Both are long-only and fully invested (target 1) while holding.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from ..data.candles import index_ms
from . import register
from .base import Strategy

BAR_MS = 3_600_000          # these strategies are specified on 1h candles
BARS_PER_DAY = 24
VOL_BARS = 30 * BARS_PER_DAY  # window for the hourly volatility estimate (fixed, not tuned)
MULTI_RATIOS = (1, 3, 7)    # dip horizons of meanrev_dip_multi, as multiples of base_hours


# ---------------------------------------------------------------- helpers

def _require_hourly(df: pd.DataFrame) -> None:
    """Fail loudly instead of silently mis-scaling hour/day-based windows."""
    if len(df) > 1:
        step = int(np.min(np.diff(index_ms(df.index))))
        if step != BAR_MS:
            raise ValueError(f"meanrev strategies expect 1h candles; got a {step / BAR_MS:g}h bar spacing")


def dip_z(close: pd.Series, lookback: int, vol_bars: int = VOL_BARS) -> pd.Series:
    """Volatility-normalised log distance of ``close`` from its ``lookback``-bar SMA.

    ``z_t = ln(close_t / SMA_t) / (sigma_t * sqrt(lookback))`` where ``sigma_t``
    is the standard deviation of hourly log returns over the last
    ``vol_bars`` bars. NaN until both windows are full (or if volatility is 0).
    """
    log_close = np.log(close)
    sma = close.rolling(lookback, min_periods=lookback).mean()
    sigma = log_close.diff().rolling(vol_bars, min_periods=vol_bars).std()
    scale = (sigma * math.sqrt(lookback)).replace(0.0, np.nan)
    return (log_close - np.log(sma)) / scale


def trend_average(close: pd.Series, trend_days: int) -> pd.Series:
    """``trend_days`` simple moving average of hourly closes (NaN until the window is full)."""
    window = trend_days * BARS_PER_DAY
    return close.rolling(window, min_periods=window).mean()


def hold_after(trigger: pd.Series, bars: int) -> pd.Series:
    """1.0 on every bar within ``bars`` bars of a ``True`` trigger (the trigger bar counts as the first)."""
    return trigger.astype("float64").rolling(bars, min_periods=1).max()


def _check_int(name: str, value: int, lo: int) -> None:
    if value < lo:
        raise ValueError(f"{name} must be >= {lo}")


def _check_positive(name: str, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a finite number > 0")


# ------------------------------------------------------------- strategies

@register
class MeanrevDip(Strategy):
    """Buy volatility-adjusted dips in an uptrend; hold for a fixed time.

    Logic (evaluated on every closed 1h bar):

    1. ``z`` = :func:`dip_z` of the close versus its ``lookback_hours`` SMA.
    2. A bar is a *dip* when ``z < -entry_z`` and the close is above its
       ``trend_days`` SMA.
    3. ``target`` = 1 from a dip bar until ``hold_hours`` bars after the most
       recent dip bar, otherwise 0. The fill is at the next bar's open.

    Defaults are the centre of a TRAIN (2018-2023) plateau; see
    ``research/meanrev.md``. The engine's default risk overlay (10%
    circuit breaker, 15% campaign stop) costs this strategy some return
    because it can flatten a dip that deepens before it rebounds.
    """

    name = "meanrev_dip"
    description = "Buy volatility-adjusted dips while the long-term trend is up; time-stop exit."
    default_params: ClassVar[dict[str, Any]] = {
        "lookback_hours": 72,   # SMA horizon of the dip measure
        "entry_z": 1.5,         # dip depth in n-hour volatility units
        "hold_hours": 192,      # hold this long after the last dip bar
        "trend_days": 125,      # uptrend filter: close above this SMA (days of hourly closes)
    }

    def validate(self) -> None:
        p = self.params
        _check_int("lookback_hours", p["lookback_hours"], 2)
        _check_positive("entry_z", p["entry_z"])
        _check_int("hold_hours", p["hold_hours"], 1)
        _check_int("trend_days", p["trend_days"], 1)

    def _lookbacks(self) -> list[int]:
        return [self.params["lookback_hours"]]

    @property
    def warmup_bars(self) -> int:
        # The target at bar t depends on dips in the last hold_hours bars; each
        # dip needs the trend SMA and the dip measure (SMA + volatility) to be
        # defined. Finite memory: this many bars reproduce the full-history
        # target exactly.
        p = self.params
        dip_needs = max(self._lookbacks()) + VOL_BARS + 1
        return p["hold_hours"] + max(p["trend_days"] * BARS_PER_DAY, dip_needs)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        _require_hourly(df)
        p = self.params
        close = df["close"]
        trend_sma = trend_average(close, p["trend_days"])
        # Deepest dip across the horizons; NaN while any horizon is not yet defined.
        z_min = pd.concat([dip_z(close, n) for n in self._lookbacks()], axis=1).min(axis=1, skipna=False)
        is_dip = (z_min < -p["entry_z"]) & (close > trend_sma)
        target = hold_after(is_dip, p["hold_hours"])
        # No signal (NaN, i.e. flat) until every indicator exists.
        ready = trend_sma.notna() & z_min.notna()
        return pd.DataFrame({"target": target.where(ready)}, index=df.index)


@register
class MeanrevDipMulti(MeanrevDip):
    """:class:`MeanrevDip` with three dip horizons; a dip on any one of them triggers.

    Horizons are ``base_hours`` x (1, 3, 7) hours (default 24, 72 and 168).
    Short horizons catch fast flushes; long ones catch week-long slides.
    The result is about 2.7 times as many entries as ``meanrev_dip``, each
    with a smaller average gain.
    """

    name = "meanrev_dip_multi"
    description = "Dips on 1-day, 3-day or 7-day horizons while the long-term trend is up; time-stop exit."
    default_params: ClassVar[dict[str, Any]] = {
        "base_hours": 24,       # shortest dip horizon; the others are 3x and 7x
        "entry_z": 1.5,
        "hold_hours": 192,
        "trend_days": 125,
    }

    def validate(self) -> None:
        p = self.params
        _check_int("base_hours", p["base_hours"], 2)
        _check_positive("entry_z", p["entry_z"])
        _check_int("hold_hours", p["hold_hours"], 1)
        _check_int("trend_days", p["trend_days"], 1)

    def _lookbacks(self) -> list[int]:
        return [self.params["base_hours"] * r for r in MULTI_RATIOS]
