"""Regime-adaptive trend strategies with volatility-managed sizing (1h BTC/USDT).

Every closed 1h bar is put into one of three market regimes. The regime
decides *which* trend rule is allowed to hold BTC and *how much*:

* **bull** - the close is above its ``regime_days`` simple moving average
  (SMA) *and* that SMA is higher than it was ``SLOPE_DAYS`` ago.
* **bear** - the close is below the regime SMA *and* the SMA is falling.
* **transition** - anything else (a rally inside a downtrend, or a pullback
  that has pushed price under a still-rising average).

Allocation:

* In a **bull** regime the strategy follows a *fast* trend rule at full size:
  long while the close is above its ``fast_days`` SMA, flat once it closes
  ``EXIT_BAND`` (0.5%) below it. Research on 2018-2023 showed a fast exit
  is what pays in an established uptrend; holding through dips, buying
  dips and vol-scaling all cost return there.
* In **transition** and **bear** regimes a long position needs a *slow*,
  confirmed trend signal: the close must clear its ``slow_days`` SMA by
  ``slow_band`` (2%), and it exits ``EXIT_BAND`` below that SMA. The
  position is sized by a volatility target,
  ``min(1, vol_target / realised_vol)`` rounded down to steps of
  ``SIZE_STEP``, where ``realised_vol`` is the annualised standard
  deviation of hourly log returns over ``VOL_DAYS``. The size is then
  multiplied by ``bear_weight`` in the bear regime. Counter-trend rallies
  are where large, fast losses happen, so this is where sizing down in
  high volatility helps.

Strategies (both take the six parameters above):

``regime_volswitch``
    The rule exactly as described.
``regime_confirm``
    Outside the bull regime, both the fast *and* the slow rule must be long
    (enter on confirmation, exit on the first failure). On TRAIN its score
    equals ``regime_volswitch`` within noise, with smaller drawdowns and
    roughly break-even bear-market years. It is the recommended member.

With the default ``bear_weight = 1`` the bear and transition regimes are
traded alike. A lower value adds de-risking in confirmed downtrends, at
some cost in score on 2018-2023. Research notes: ``research/regime.md``.

Engineering properties:

* Causal and finite-memory. Only rolling windows are used (no EMAs, no
  full-sample statistics). A trend latch is fixed by any bar that crosses
  one of its bands, and ``warmup_bars`` includes ``SYNC_DAYS`` of slack for
  the latches to settle. So a live run that sees ``warmup_bars`` of history
  reproduces the full-history backtest target exactly.
* Discrete exposure (multiples of ``SIZE_STEP``). Every change is at least
  0.25 of equity, so the engine's 10% minimum-rebalance rule never strands
  a decision.
* The target is NaN (flat) until every indicator is defined.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from ..data.candles import index_ms
from . import register
from .base import Strategy

BAR_MS = 3_600_000          # strategies are specified on 1h candles
BARS_PER_DAY = 24
HOURS_PER_YEAR = 24 * 365
SLOPE_DAYS = 5              # regime SMA "rising" = higher than SLOPE_DAYS ago
EXIT_BAND = 0.005           # both trend rules exit 0.5% below their SMA
VOL_DAYS = 7                # realised-volatility window for the vol target
SIZE_STEP = 0.25            # exposure granularity outside the bull regime
SYNC_DAYS = 21              # extra history for the hysteresis latches to settle (bands <= 5%)

BULL, TRANSITION, BEAR = 2, 1, 0


# ---------------------------------------------------------------- helpers

def _require_hourly(df: pd.DataFrame) -> None:
    """Fail loudly instead of silently mis-scaling the day-based windows."""
    if len(df) > 1:
        step = int(np.min(np.diff(index_ms(df.index))))
        if step != BAR_MS:
            raise ValueError(f"regime strategies expect 1h candles; got a {step / BAR_MS:g}h bar spacing")


def sma(close: pd.Series, bars: int) -> pd.Series:
    return close.rolling(bars, min_periods=bars).mean()


def trend_latch(close: pd.Series, average: pd.Series, entry_band: float, exit_band: float) -> pd.Series:
    """1 once ``close > average * (1 + entry_band)``, 0 once ``close < average * (1 - exit_band)``.

    In between, the previous state is kept (hysteresis). The state is 0 before
    the first signal and wherever ``average`` is undefined. Causal: the forward
    fill only carries past values forward.
    """
    dev = close / average - 1.0
    state = pd.Series(np.nan, index=close.index)
    state[(dev > entry_band).to_numpy()] = 1.0
    state[(dev < -exit_band).to_numpy()] = 0.0
    return state.ffill().fillna(0.0).where(average.notna(), 0.0)


def realised_vol(close: pd.Series, bars: int) -> pd.Series:
    """Annualised rolling standard deviation of hourly log returns."""
    r = np.log(close).diff()
    return r.rolling(bars, min_periods=bars).std() * math.sqrt(HOURS_PER_YEAR)


def vol_scale(vol: pd.Series, vol_target: float, step: float = SIZE_STEP) -> pd.Series:
    """``min(1, vol_target / vol)`` rounded down to ``step``; 1 when targeting is off."""
    if vol_target <= 0:
        return pd.Series(1.0, index=vol.index).where(vol.notna())
    raw = (vol_target / vol.replace(0.0, np.nan)).clip(upper=1.0)
    return (np.floor(raw / step + 1e-9) * step).where(vol.notna())


def classify_regime(close: pd.Series, regime_bars: int, slope_bars: int = SLOPE_DAYS * BARS_PER_DAY) -> pd.Series:
    """Regime per bar: 2 = bull, 1 = transition, 0 = bear, NaN until defined."""
    avg = sma(close, regime_bars)
    prior = avg.shift(slope_bars)
    above = close > avg
    rising = avg > prior
    state = above.astype(float) + rising.astype(float)
    return state.where(prior.notna())


# ------------------------------------------------------------- strategies

class _RegimeTrend(Strategy):
    """Shared implementation; concrete strategies only choose defaults and the confirm rule."""

    #: Outside the bull regime, require the fast rule to agree with the slow rule.
    confirm_outside_bull: ClassVar[bool] = False

    default_params: ClassVar[dict[str, Any]] = {
        "regime_days": 150,     # regime SMA window (days)
        "fast_days": 20,        # bull-regime trend SMA (days)
        "slow_days": 32,        # transition/bear trend SMA (days)
        "slow_band": 0.02,      # entry band above the slow SMA
        "vol_target": 0.80,     # annualised vol target outside the bull regime (0 = off)
        "bear_weight": 1.0,     # extra size multiplier in the bear regime
    }

    def validate(self) -> None:
        p = self.params
        if not 20 <= p["regime_days"] <= 400:
            raise ValueError("regime_days must be in [20, 400]")
        for key in ("fast_days", "slow_days"):
            if not 2 <= p[key] <= 200:
                raise ValueError(f"{key} must be in [2, 200]")
            if p[key] >= p["regime_days"]:
                raise ValueError(f"{key} must be shorter than regime_days")
        # Wider bands can leave the slow latch undecided for longer than
        # SYNC_DAYS, which would break live/backtest parity.
        if not 0.0 <= p["slow_band"] <= 0.05:
            raise ValueError("slow_band must be in [0, 0.05]")
        if not (math.isfinite(p["vol_target"]) and 0.0 <= p["vol_target"] <= 5.0):
            raise ValueError("vol_target must be in [0, 5] (0 disables volatility targeting)")
        if not 0.0 <= p["bear_weight"] <= 1.0:
            raise ValueError("bear_weight must be in [0, 1]")

    @property
    def warmup_bars(self) -> int:
        p = self.params
        longest = max((p["regime_days"] + SLOPE_DAYS), p["slow_days"], p["fast_days"], VOL_DAYS) * BARS_PER_DAY
        return longest + 1 + SYNC_DAYS * BARS_PER_DAY

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        _require_hourly(df)
        p = self.params
        close = df["close"].astype("float64")

        regime = classify_regime(close, p["regime_days"] * BARS_PER_DAY)
        fast = trend_latch(close, sma(close, p["fast_days"] * BARS_PER_DAY), 0.0, EXIT_BAND)
        slow = trend_latch(close, sma(close, p["slow_days"] * BARS_PER_DAY), p["slow_band"], EXIT_BAND)
        if self.confirm_outside_bull:
            slow = np.minimum(fast, slow)
        vol = realised_vol(close, VOL_DAYS * BARS_PER_DAY)
        size = vol_scale(vol, p["vol_target"])

        outside = slow * size * np.where(regime == BEAR, p["bear_weight"], 1.0)
        target = pd.Series(np.where(regime == BULL, fast, outside), index=df.index)
        target = target.where(regime.notna() & vol.notna())
        return pd.DataFrame({"target": target}, index=df.index)


@register
class RegimeVolSwitch(_RegimeTrend):
    """Bull regime: fast SMA trend at full size. Otherwise: slow confirmed trend, vol-targeted.

    See the module docstring for the regime definitions. Highest TRAIN score
    of the family. ``regime_confirm`` gives up a little of it for a smaller tail.
    """

    name = "regime_volswitch"
    description = ("Regime switch on a rising/falling 150d SMA: bull -> 20d SMA trend at full size; "
                   "otherwise 32d SMA +2% trend sized to an 80% vol target.")


@register
class RegimeConfirm(_RegimeTrend):
    """``regime_volswitch`` where, outside the bull regime, the fast rule must also be long.

    Entries in transition/bear regimes need both the slow (+2% over the 32d
    SMA) and the fast (above the 20d SMA) rule. The position exits as soon as
    either fails. This trims counter-trend losses in bear markets. Recommended
    member of the family.
    """

    name = "regime_confirm"
    description = ("regime_volswitch, but outside the bull regime both the 20d and the 32d trend "
                   "rules must be long (vol-targeted).")
    confirm_outside_bull = True
