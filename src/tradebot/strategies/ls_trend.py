"""Long/short trend strategies: ``breakout_dual`` longs plus regime-gated, floor-limited shorts.

For 1h BTC/USDT candles traded on perpetual futures (``run.instrument = "perp"``)
or in hybrid mode (longs on spot, shorts on perps; ``"hybrid"``), with
``risk.max_short > 0``. On a spot config the engine clips targets at 0, so
these strategies reduce to their long leg.

Why shorts are taken only when they are
---------------------------------------
BTC has strong positive drift, so a short side mirrored from a long trend
follower loses: on TRAIN (2018-2023) every symmetric short leg tried (mirrored
Keltner/Donchian breakouts, SMA-band votes, multi-lookback momentum signs)
scored about zero on its own and blew up the 30-day loss tail when combined
with the long leg (research/ls_trend.md). Shorts here are asymmetric:

1. **Bear regime only.** A short can only exist while the hourly close is
   below its ``regime_days`` simple moving average (150 days by default).
   Above it the forward drift was strongly positive on TRAIN.
2. **Fresh breakdown entry.** The short latch switches on when the close
   breaks the lowest low of the previous ``short_entry_n`` bars (10 days).
   The trigger is an *event*, so after an exit the strategy waits for a new
   low instead of re-shorting into the same level.
3. **Fast exit.** It switches off when the close breaks the highest high of
   the previous ``short_exit_n`` bars (2 days). Bear-market rallies are fast
   and large, so shorts are covered much sooner than longs are sold
   (the long leg's Donchian exit is 3 days, its Keltner exit is the 25-day EMA).
4. **Oversold floor.** It also switches off, and cannot switch on, while the
   close is more than ``floor`` (30%) below the regime average. Capitulation
   lows are where BTC's violent short squeezes start: on TRAIN the next-day
   return averaged +33 to +52 bp/day when the close was more than 25% below
   its 150-day average, against -15 to -36 bp/day between 0 and -25%.
5. **Longs have priority.** No short while the long leg holds any position.

Strategies
----------
``lst_dual_bear``
    Long leg = ``breakout_dual`` (deployed defaults, frozen). In the bear
    regime every position, long or short, is capped at ``bear_exposure``
    (0.75): longs are scaled by it and shorts are sized at it. Capping bear
    longs offsets the extra loss tail the shorts add (both sides whipsaw in
    the same choppy bear months).
``lst_dual_overlay``
    Long leg = ``breakout_dual`` unchanged; the short leg is added at
    ``short_weight`` (0.5). The cleanest test of what shorting alone adds.

Both are vectorised latches (``breakout.latch``) on finite-history inputs
plus the long leg's EMA/Wilder recursions, so ``warmup_bars`` reproduces the
full-history signals on a rolling live window (verified with
``research.check_causal``'s prefix and warmup+1 window checks).
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from .. import indicators as ind
from ..data.candles import index_ms
from . import register
from .base import Strategy
from .breakout import DualBreakout, latch

BAR_MS = 3_600_000
BARS_PER_DAY = 24

# The long leg is the deployed long-only strategy with its selected defaults
# (research/SELECTION.md). It is frozen here on purpose: these parameters were
# tuned in the long-only research, not in the long/short one.
LONG_LEG_PARAMS: dict[str, Any] = {"entry_n": 144, "exit_n": 72, "trend_n": 600, "band_k": 3.0,
                                   "atr_n": 168, "keltner_weight": 0.5}


def _require_hourly(df: pd.DataFrame) -> None:
    """Day-based windows are converted at 24 bars/day; refuse other bar sizes."""
    if len(df) > 1:
        step = int(np.min(np.diff(index_ms(df.index))))
        if step != BAR_MS:
            raise ValueError(f"ls_trend strategies expect 1h candles; got a {step / BAR_MS:g}h bar spacing")


def bear_short_state(df: pd.DataFrame, entry_n: int, exit_n: int, regime_bars: int,
                     floor: float) -> tuple[pd.Series, pd.Series]:
    """Return ``(short_state, bear)``.

    ``bear`` is True while the close is below its ``regime_bars`` SMA (False
    while the SMA is undefined). ``short_state`` is a 0/1 latch: on when the
    close breaks the previous ``entry_n``-bar low while
    ``-floor < close / SMA - 1 < 0``; off when the close breaks the previous
    ``exit_n``-bar high, or falls to ``floor`` or more below the SMA, or
    recovers to the SMA or above. If entry and exit fire on the same bar the
    exit wins.
    """
    close = df["close"].astype("float64")
    sma = close.rolling(regime_bars, min_periods=regime_bars).mean()
    dev = close / sma - 1.0                      # NaN while the SMA is undefined
    bear = (dev < 0.0).fillna(False)
    entry = (close < ind.donchian_low(df["low"], entry_n)) & (dev < 0.0) & (dev > -floor)
    exit_ = (close > ind.donchian_high(df["high"], exit_n)) | (dev <= -floor) | (dev >= 0.0)
    return latch(entry, exit_), bear


class _BearShortBase(Strategy):
    """Shared validation, warm-up and short-leg plumbing (not registered)."""

    short_size_key: ClassVar[str] = ""

    def validate(self) -> None:
        p = self.params
        for key in ("short_entry_n", "short_exit_n"):
            if p[key] < 2:
                raise ValueError(f"{key} must be >= 2")
        if not 20 <= p["regime_days"] <= 400:
            raise ValueError("regime_days must be in [20, 400]")
        if not 0.0 < p["floor"] < 1.0:
            raise ValueError("floor must be in (0, 1)")
        if not 0.0 < p[self.short_size_key] <= 1.0:
            raise ValueError(f"{self.short_size_key} must be in (0, 1]")

    @property
    def warmup_bars(self) -> int:
        p = self.params
        long_leg = DualBreakout(**LONG_LEG_PARAMS).warmup_bars
        # The regime SMA needs regime_days of history before any short can
        # register; the short latch then synchronises at its next entry/exit
        # event. Short spans reached ~510 bars across the robustness sweep on
        # 2017-2024, so 10 channel lengths leave a wide margin.
        short_leg = p["regime_days"] * BARS_PER_DAY + 10 * max(p["short_entry_n"], p["short_exit_n"])
        return max(long_leg, short_leg)

    def _legs(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
        _require_hourly(df)
        p = self.params
        long_raw = DualBreakout(**LONG_LEG_PARAMS).compute(df)["target"].astype("float64")
        short, bear = bear_short_state(df, p["short_entry_n"], p["short_exit_n"],
                                       p["regime_days"] * BARS_PER_DAY, p["floor"])
        short = short.where(long_raw <= 0.0, 0.0)      # longs have priority
        return long_raw, short, bear


@register
class DualBearShort(_BearShortBase):
    """``breakout_dual`` longs; in the bear regime, exposure capped and breakdown shorts allowed.

    ``target = L * (bear_exposure if bear else 1) - bear_exposure * S`` where

    * ``L`` is the ``breakout_dual`` target (0, 0.5 or 1; frozen defaults);
    * ``bear`` means close < SMA(``regime_days`` days of 1h closes);
    * ``S`` is the floor-limited breakdown short latch of
      :func:`bear_short_state`, forced to 0 while ``L > 0``.

    With the defaults, exposure is 0 / 0.5 / 1 above the 150-day average and
    -0.75 / 0 / 0.375 / 0.75 below it. ``bear_exposure`` is set where TRAIN's
    30-day 5th-percentile return stays within ~0.5 pp of the long-only
    strategy's; larger values score more and widen the loss tail.
    """

    name = "lst_dual_bear"
    description = ("breakout_dual longs; below the 150d SMA exposure is capped at 0.75 and a 10-day-low "
                   "breakdown short (2-day-high exit) is taken unless price is >30% below the SMA.")
    default_params = {
        "short_entry_n": 240,   # short on a break of the previous 240-bar (10-day) low
        "short_exit_n": 48,     # cover on a break of the previous 48-bar (2-day) high
        "regime_days": 150,     # bear regime: close below this SMA of hourly closes
        "floor": 0.30,          # no short once close is this far below the regime SMA
        "bear_exposure": 0.75,  # exposure cap (long and short) in the bear regime
    }
    short_size_key = "bear_exposure"

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        b = self.params["bear_exposure"]
        long_raw, short, bear = self._legs(df)
        long_ = long_raw * np.where(bear.to_numpy(), b, 1.0)
        return pd.DataFrame({"target": long_ - b * short}, index=df.index)


@register
class DualShortOverlay(_BearShortBase):
    """``breakout_dual`` unchanged, plus a ``short_weight`` floor-limited breakdown short.

    ``target = L - short_weight * S`` with ``L`` and ``S`` as in
    ``lst_dual_bear``. Whenever the short leg is off this is exactly the
    deployed long-only strategy, so in hybrid mode the difference from it is
    purely the short trades. Half size keeps the 30-day loss tail close to
    the long-only one; on TRAIN, full size raised the short gain from +0.012
    to +0.021 but widened the 5th-percentile 30-day loss by 2 pp.
    """

    name = "lst_dual_overlay"
    description = ("breakout_dual unchanged plus a half-size 10-day-low breakdown short below the 150d SMA, "
                   "covered on a 2-day high or once price is >30% below the SMA.")
    default_params = {
        "short_entry_n": 240,
        "short_exit_n": 48,
        "regime_days": 150,
        "floor": 0.30,
        "short_weight": 0.5,    # short size as a fraction of equity
    }
    short_size_key = "short_weight"

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        long_raw, short, _ = self._legs(df)
        return pd.DataFrame({"target": long_raw - self.params["short_weight"] * short}, index=df.index)
