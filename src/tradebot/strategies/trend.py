"""Trend-following strategies for 1h BTC/USDT candles.

All three strategies hold BTC only while price trends up over a multi-week
horizon and step aside (fully or partly) in downtrends. They share these
design rules:

* Finite-memory indicators only (rolling means/std over a fixed window, no
  recursive EMAs), so a live run that sees ``warmup_bars`` of history
  produces exactly the same target as a backtest over the full history.
* Slow signals: the median holding period is days to weeks, which keeps fee
  and slippage drag low (roughly 3-4 round trips per 30-day run).
* Causal: every value at bar ``t`` uses closed bars ``<= t`` only. Daily bars
  come from :func:`tradebot.data.candles.resample_causal`.

Strategies
----------
``trend_sma_band``
    Three moving averages of the hourly close (lookback / spread, lookback,
    lookback * spread days). Each average votes "long" once price closes more
    than ``entry_band`` above it and "flat" once price closes more than
    ``exit_band`` below it; in between the vote keeps its previous value
    (hysteresis, which removes most whipsaw trades around the average).
    Exposure is the fraction of long votes: 0, 1/3, 2/3 or 1.
``trend_sma_band_regime``
    ``trend_sma_band`` with a long-term regime filter: while the hourly close
    is below its ``regime_days`` simple moving average, exposure is scaled by
    ``regime_floor``. Cuts bear-market whipsaw losses and the drawdown tail
    at a small cost in bull markets.
``trend_strength``
    Daily-cadence, volatility-normalised trend strength: the log distance of
    the daily close above its ``sma_days`` average, divided by the daily
    volatility scaled to the lookback (``sigma_daily * sqrt(sma_days)``).
    Exposure ramps linearly from 0 (at or below the average) to 1 (at
    ``full_z``). Fractional sizing makes it the lowest-tail-risk member.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..data.candles import index_ms, resample_causal
from . import register
from .base import Strategy

BAR_MS = 3_600_000          # these strategies are specified on 1h candles
BARS_PER_DAY = 24
SYNC_DAYS = 14              # history beyond the longest window for band states to settle


# ---------------------------------------------------------------- helpers

def _require_hourly(df: pd.DataFrame) -> None:
    """Fail loudly instead of silently mis-scaling day-based lookbacks."""
    if len(df) > 1:
        step = int(np.min(np.diff(index_ms(df.index))))
        if step != BAR_MS:
            raise ValueError(f"trend strategies expect 1h candles; got a {step / BAR_MS:g}h bar spacing")


def band_state(close: pd.Series, window: int, entry_band: float, exit_band: float) -> pd.Series:
    """Hysteresis trend state versus a ``window``-bar simple moving average.

    1 once ``close > sma * (1 + entry_band)``; 0 once ``close < sma * (1 - exit_band)``;
    otherwise the previous state (0 before the first signal). Causal: the
    forward fill only propagates past values. Any bar that crosses either
    band fully determines the state, so the result does not depend on how
    much history precedes the last crossing.
    """
    sma = close.rolling(window, min_periods=window).mean()
    dev = close / sma - 1.0
    state = pd.Series(np.nan, index=close.index)
    state[dev > entry_band] = 1.0
    state[dev < -exit_band] = 0.0
    return state.ffill().fillna(0.0)


def band_lookbacks(lookback_days: float, spread: float) -> list[int]:
    """The three ensemble windows in bars: lookback / spread, lookback, lookback * spread."""
    return [int(round(lookback_days * spread ** k * BARS_PER_DAY)) for k in (-1, 0, 1)]


def daily_closes(df: pd.DataFrame) -> pd.Series:
    """Completed daily closes, indexed by the hourly bar at whose close each day became final.

    Uses :func:`resample_causal`, so a partially formed day is never visible.
    A row is a new day when the visible daily bar differs from the previous
    row's (a day with no candles at all therefore does not create a duplicate).
    """
    htf = resample_causal(df, "1D", BAR_MS)
    visible = htf["close"].notna()
    new_day = visible & htf.ne(htf.shift(1)).any(axis=1)
    return htf.loc[new_day, "close"]


def _check_positive(name: str, value: float) -> None:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be > 0")


# ------------------------------------------------------------- strategies

@register
class TrendSmaBand(Strategy):
    """Ensemble of three hourly SMA trend states with entry/exit hysteresis bands.

    Logic (evaluated on every closed 1h bar):

    1. For each window ``w`` in (lookback / spread, lookback, lookback * spread) days,
       compute ``sma_w`` of the hourly close.
    2. Vote long when ``close > sma_w * (1 + entry_band)``; vote flat when
       ``close < sma_w * (1 - exit_band)``; otherwise keep the previous vote.
       A wide entry band confirms the breakout; a tight exit band gets out
       quickly once the trend fails.
    3. ``target`` = mean of the three votes (0, 1/3, 2/3, 1).

    The engine's 10% minimum-rebalance rule never blocks a vote change
    because each vote moves exposure by 1/3.
    """

    name = "trend_sma_band"
    description = "Three hourly SMAs (20/30/45d) with 2% entry / 0.5% exit hysteresis; exposure = share of long votes."
    default_params = {
        "lookback_days": 30.0,   # centre SMA window (days)
        "spread": 1.5,           # ratio between neighbouring windows
        "entry_band": 0.02,      # go long once close is this far above the SMA
        "exit_band": 0.005,      # go flat once close is this far below the SMA
    }

    def validate(self) -> None:
        p = self.params
        if not 1.0 <= p["lookback_days"] <= 365.0:
            raise ValueError("lookback_days must be in [1, 365]")
        if not 1.0 <= p["spread"] <= 3.0:
            raise ValueError("spread must be in [1, 3]")
        for key in ("entry_band", "exit_band"):
            if not 0.0 <= p[key] < 0.5:
                raise ValueError(f"{key} must be in [0, 0.5)")
        if min(band_lookbacks(p["lookback_days"], p["spread"])) < 2:
            raise ValueError("shortest SMA window must be at least 2 bars")

    @property
    def warmup_bars(self) -> int:
        return max(band_lookbacks(self.params["lookback_days"], self.params["spread"])) + SYNC_DAYS * BARS_PER_DAY

    def trend_exposure(self, df: pd.DataFrame) -> pd.Series:
        p = self.params
        close = df["close"].astype("float64")
        votes = [band_state(close, w, p["entry_band"], p["exit_band"])
                 for w in band_lookbacks(p["lookback_days"], p["spread"])]
        return sum(votes) / len(votes)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        _require_hourly(df)
        return pd.DataFrame({"target": self.trend_exposure(df)}, index=df.index)


@register
class TrendSmaBandRegime(TrendSmaBand):
    """``trend_sma_band`` scaled down while price is below its long-term average.

    ``target = band_exposure * (1 if close > SMA(regime_days) else regime_floor)``.
    Until the long-term average has enough history the regime is treated as
    bearish (``regime_floor``), which is the conservative choice.
    """

    name = "trend_sma_band_regime"
    description = ("trend_sma_band with a 150d SMA regime filter: exposure halves while "
                   "price is below its 150-day average.")
    default_params = {
        **TrendSmaBand.default_params,
        "regime_days": 150.0,    # long-term SMA window (days)
        "regime_floor": 0.5,     # exposure multiplier while below the long-term SMA
    }

    def validate(self) -> None:
        super().validate()
        p = self.params
        if not 20.0 <= p["regime_days"] <= 400.0:
            raise ValueError("regime_days must be in [20, 400]")
        if not 0.0 <= p["regime_floor"] <= 1.0:
            raise ValueError("regime_floor must be in [0, 1]")

    @property
    def warmup_bars(self) -> int:
        return max(super().warmup_bars, int(round(self.params["regime_days"] * BARS_PER_DAY)))

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        _require_hourly(df)
        p = self.params
        close = df["close"].astype("float64")
        window = int(round(p["regime_days"] * BARS_PER_DAY))
        sma = close.rolling(window, min_periods=window).mean()
        bull = (close > sma).to_numpy()            # False while the SMA is NaN
        scale = np.where(bull, 1.0, p["regime_floor"])
        return pd.DataFrame({"target": self.trend_exposure(df) * scale}, index=df.index)


@register
class TrendStrength(Strategy):
    """Volatility-normalised distance above the daily SMA, mapped to fractional exposure.

    Once per day, on the bar that completes the UTC day:

    * ``z = ln(close_d / SMA_d(sma_days)) / (sigma_d * sqrt(sma_days))`` where
      ``sigma_d`` is the standard deviation of daily log returns over ``vol_days``.
    * ``target = clip(z / full_z, 0, 1)``; held unchanged for the next 24 bars.

    Normalising by volatility means a given price distance above the average
    earns less exposure in turbulent markets, which tightens the loss tail
    without a separate volatility-targeting layer.
    """

    name = "trend_strength"
    description = "Daily close vs 40d SMA in volatility units; exposure ramps 0->1 as z goes 0->0.5."
    default_params = {
        "sma_days": 40,          # trend window (daily closes)
        "vol_days": 30,          # window for daily log-return volatility
        "full_z": 0.5,           # z at which exposure reaches 100%
    }

    def validate(self) -> None:
        p = self.params
        if not 2 <= p["sma_days"] <= 365:
            raise ValueError("sma_days must be in [2, 365]")
        if not 2 <= p["vol_days"] <= 365:
            raise ValueError("vol_days must be in [2, 365]")
        _check_positive("full_z", p["full_z"])

    @property
    def warmup_bars(self) -> int:
        days = max(self.params["sma_days"], self.params["vol_days"] + 1) + 2
        return days * BARS_PER_DAY

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        _require_hourly(df)
        p = self.params
        close_d = daily_closes(df)
        n = p["sma_days"]
        sma = close_d.rolling(n, min_periods=n).mean()
        sigma = np.log(close_d).diff().rolling(p["vol_days"], min_periods=p["vol_days"]).std()
        z = np.log(close_d / sma) / (sigma.where(sigma > 0) * math.sqrt(n))
        # Undefined z (warm-up, zero volatility) means "no trend": flat for that day.
        exposure_d = (z / p["full_z"]).clip(lower=0.0, upper=1.0).fillna(0.0)
        target = exposure_d.reindex(df.index).ffill().fillna(0.0)
        return pd.DataFrame({"target": target}, index=df.index)
