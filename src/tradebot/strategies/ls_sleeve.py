"""Short sleeve: the deployed ``breakout_dual`` longs plus an independent bear-market short.

For 1h BTC/USDT candles on USDT-margined perpetual futures
(``run.instrument = "perp"``) or in hybrid mode (longs on spot, shorts on
perps; ``"hybrid"``), with ``risk.max_short >= 1.0`` for
``lss_breakdown_vol`` (its short can reach 1.0) or ``>= short_size`` for
``lss_breakdown``. On a spot config the engine clips negative targets to 0,
so both strategies reduce exactly to ``breakout_dual``.

Target
------
``L`` is the ``breakout_dual`` target with its deployed defaults (0, 0.5 or
1; frozen in ``LONG_LEG_PARAMS`` and not re-tuned here). ``S`` is the short
sleeve, a 0/1 latch computed independently of ``L``::

    target = L              if L > 0       (longs always have priority)
           = -size * S      otherwise
           = 0              if neither

The strategy is never long and short at once. When the long leg exits while
the sleeve is on, the position flips straight to the short; when the long leg
re-enters, the short is covered.

When the sleeve is on (and why)
-------------------------------
``S`` switches **on** when the close breaks the lowest low of the previous
``entry_n`` bars (10 days) while the close is *below* its ``regime_n``-bar
simple moving average (125 days) but *not more than* ``floor`` (35%) below
it. It switches **off** when the close breaks the highest high of the
previous ``exit_n`` bars (2 days) or falls more than ``floor`` below the
average. If both fire on the same bar, off wins.

The reasons come from the TRAIN (2018-2023) research in research/ls_sleeve.md:

* **Slow-trend gate (close < SMA(regime_n)).** BTC drifts up. Breakdowns
  above the slow average are mostly bear traps; without the gate the sleeve
  lost money in most years.
* **Event entry (fresh 10-day low), not a level.** After an exit the sleeve
  waits for a new breakdown instead of re-shorting the same level.
* **Fast exit (2-day high).** Bear-market rallies are short and violent. The
  long leg's slow exits (3-day low, 25-day EMA) are far too loose for a short.
  Exits of 36 bars or less churned; 48-60 bars was a plateau.
* **Oversold floor.** No short, and any open short is covered, once the close
  is more than ``floor`` below the regime average. Capitulation lows (Nov-Dec
  2018, Mar 2020, Jun-Jul 2021, Jun 2022) are where BTC's short squeezes
  start. The floor was the single largest improvement found: about +0.005 of
  TRAIN score at size 0.5, with a smaller loss tail.
* "Clearly bearish" confirmations that wait longer (EMA600 below the regime
  average, a falling regime average) *hurt*: the profitable shorts come early
  in a decline, and late-bear breakdowns are the ones that get squeezed.
  Crash-momentum entries (short after a large 1-7 day drop) were worse than
  the breakdown entry for the same reason.

Sizing
------
``lss_breakdown``
    Fixed short size ``short_size`` (0.5). Size trades score against the
    30-day loss tail: on TRAIN, 0.65 already pushed the 5th-percentile 30-day
    return past the adoption limit.
``lss_breakdown_vol``
    Size ``clip(short_risk / (ATR(168) / close), 0.25, 1.0)``, sampled on the
    bar the sleeve switches on and held until it switches off (so it does not
    churn). With ``short_risk = 0.004`` the median short is about 0.5; it is
    smaller after volatility spikes, when squeezes are largest.

Funding: shorts receive positive funding, but bear-regime funding is low, so
the sleeve receives little (research/ls_sleeve.md has the attribution). In
perp mode the long leg pays funding, which is why hybrid mode is the better way
to run it.

Causality: everything is a vectorised latch (``breakout.latch``) over finite
rolling windows, plus the long leg's EMA/Wilder recursions and ATR(168).
``warmup_bars`` covers those recursions and lets the latch resynchronise, so
a rolling live window reproduces the full-history signals. This is checked by
``research.check_causal`` (prefix and warmup+1 window).
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from .. import indicators as ind
from . import register
from .base import Strategy
from .breakout import DualBreakout, latch

# The deployed long leg, frozen (selected in research/SELECTION.md).
LONG_LEG_PARAMS: dict[str, Any] = dict(DualBreakout.default_params)

VOL_ATR_N = 168          # ATR length for volatility sizing (the long leg's ATR)
VOL_MIN_SIZE = 0.25      # volatility-sized short is clipped to [0.25, 1.0]
VOL_MAX_SIZE = 1.0


def bear_breakdown_sleeve(df: pd.DataFrame, entry_n: int, exit_n: int, regime_n: int,
                          floor: float) -> pd.Series:
    """0/1 short-sleeve latch (see module docstring)."""
    close = df["close"]
    dev = close / ind.sma(close, regime_n) - 1.0          # NaN during the SMA warm-up
    entry = (close < ind.donchian_low(df["low"], entry_n)) & (dev < 0.0) & (dev > -floor)
    exit_ = (close > ind.donchian_high(df["high"], exit_n)) | (dev <= -floor)
    return latch(entry, exit_)


def frozen_at_switch_on(state: pd.Series, value: pd.Series) -> pd.Series:
    """``value`` sampled on the bar ``state`` switches on, held while it stays on (0 when off)."""
    on = state.to_numpy() > 0
    prev = np.concatenate([[False], on[:-1]])
    sampled = pd.Series(np.where(on & ~prev, value.to_numpy(dtype="float64"), np.nan), index=state.index).ffill()
    return pd.Series(np.where(on, sampled.fillna(0.0).to_numpy(), 0.0), index=state.index)


def combine(long_target: pd.Series, short_size: pd.Series | float, sleeve: pd.Series) -> pd.Series:
    """Long priority: the long target when long, else ``-short_size`` while the sleeve is on."""
    size = short_size if isinstance(short_size, pd.Series) else pd.Series(float(short_size), index=sleeve.index)
    lt = long_target.to_numpy(dtype="float64")
    out = np.where(lt > 0.0, lt, -size.to_numpy(dtype="float64") * sleeve.to_numpy(dtype="float64"))
    return pd.Series(out + 0.0, index=long_target.index)  # + 0.0 turns -0.0 into 0.0


class _ShortSleeveBase(Strategy):
    """Shared parameter checks, warm-up and long leg."""

    def _validate_sleeve(self) -> None:
        p = self.params
        for key in ("entry_n", "exit_n", "regime_n"):
            if p[key] < 2:
                raise ValueError(f"{key} must be >= 2")
        if not 0.0 < p["floor"] < 1.0:
            raise ValueError("floor must be in (0, 1)")

    @property
    def warmup_bars(self) -> int:
        p = self.params
        long_need = DualBreakout(**LONG_LEG_PARAMS).warmup_bars      # 6000: EMA600 / ATR168 recursions
        # The sleeve needs the SMA window plus room for the latch to meet an
        # entry/exit event (exits fire every time a 2-day high breaks, or on
        # every bar beyond the floor) and, for vol sizing, the ATR seed.
        sleeve_need = p["regime_n"] + 10 * max(p["entry_n"], p["exit_n"])
        return max(long_need, sleeve_need, 12 * VOL_ATR_N)

    def _legs(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        p = self.params
        long_t = DualBreakout(**LONG_LEG_PARAMS).compute(df)["target"]
        sleeve = bear_breakdown_sleeve(df, p["entry_n"], p["exit_n"], p["regime_n"], p["floor"])
        return long_t, sleeve


@register
class BreakdownSleeve(_ShortSleeveBase):
    """``breakout_dual`` longs + fixed-size bear-breakdown short sleeve (longs have priority).

    Short ``short_size`` while the sleeve is on and the long leg is flat. The
    sleeve is on after a close below the previous ``entry_n``-bar low while the
    close is 0-``floor`` below SMA(``regime_n``). It is off after a close above
    the previous ``exit_n``-bar high, or more than ``floor`` below the SMA.
    """

    name: ClassVar[str] = "lss_breakdown"
    description: ClassVar[str] = ("breakout_dual longs; short 0.5 on a 10-day-low breakdown 0-35% below the "
                                  "125-day SMA, covered on a 2-day high (perp/hybrid)")
    default_params: ClassVar[dict[str, Any]] = {"entry_n": 240, "exit_n": 48, "regime_n": 3000, "floor": 0.35,
                                                "short_size": 0.5}

    def validate(self) -> None:
        self._validate_sleeve()
        if not 0.0 < self.params["short_size"] <= 1.0:
            raise ValueError("short_size must be in (0, 1]")

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        long_t, sleeve = self._legs(df)
        return pd.DataFrame({"target": combine(long_t, self.params["short_size"], sleeve)}, index=df.index)


@register
class BreakdownSleeveVol(_ShortSleeveBase):
    """As ``lss_breakdown``, but the short is volatility-sized at the moment the sleeve switches on.

    ``size = clip(short_risk / (ATR(168) / close), 0.25, 1.0)``, sampled on the
    bar the sleeve switches on and held until it switches off.
    """

    name: ClassVar[str] = "lss_breakdown_vol"
    description: ClassVar[str] = ("breakout_dual longs; volatility-sized (0.25-1.0) short on a 10-day-low breakdown "
                                  "0-35% below the 125-day SMA, covered on a 2-day high (perp/hybrid)")
    default_params: ClassVar[dict[str, Any]] = {"entry_n": 240, "exit_n": 48, "regime_n": 3000, "floor": 0.35,
                                                "short_risk": 0.004}

    def validate(self) -> None:
        self._validate_sleeve()
        if not 0.0 < self.params["short_risk"] <= 0.05:
            raise ValueError("short_risk must be in (0, 0.05] (hourly ATR as a fraction of price)")

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        long_t, sleeve = self._legs(df)
        atr_frac = ind.atr(df, VOL_ATR_N) / df["close"]
        raw = (self.params["short_risk"] / atr_frac).clip(lower=VOL_MIN_SIZE, upper=VOL_MAX_SIZE)
        size = frozen_at_switch_on(sleeve, raw.fillna(VOL_MIN_SIZE))
        return pd.DataFrame({"target": combine(long_t, size, sleeve)}, index=df.index)
