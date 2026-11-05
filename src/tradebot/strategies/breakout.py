"""Breakout strategies: Donchian channel, Keltner (EMA +/- ATR) band, and a blend.

All three are long-only trend followers for 1h BTC/USDT candles. They are
"latched" state machines: an *entry event* switches the position on and it
stays on until an *exit event*, so the position is held through the noise
between the two levels (hysteresis) instead of flipping every bar.

The latch is vectorised (mark entry bars 1, exit bars 0, forward-fill the last
mark), which only ever looks backwards, so ``compute(df.iloc[:k]).iloc[-1] ==
compute(df).iloc[k-1]`` holds. Once at least one entry or exit event lies
inside the supplied history the state no longer depends on where that history
starts; with the default parameters that happens within days, far inside
``warmup_bars``.

Research notes, tables and parameter sensitivity: ``research/breakout.md``.
Protective intrabar ATR stops (``stop_dist``) were tested and are *off* by
default: with the engine's re-arm rule a stop-out blocks re-entry until the
latch resets, which cut winning trends short and made results fragile.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from . import register
from .base import Strategy


def latch(entry: pd.Series, exit_: pd.Series) -> pd.Series:
    """1.0 from an entry event until the next exit event, else 0.0.

    ``entry`` and ``exit_`` are boolean Series (NaN counts as no event). If both
    fire on the same bar the exit wins.
    """
    on = entry.fillna(False).to_numpy(dtype=bool)
    off = exit_.fillna(False).to_numpy(dtype=bool)
    marks = np.where(off, 0.0, np.where(on, 1.0, np.nan))
    return pd.Series(marks, index=entry.index).ffill().fillna(0.0)


def donchian_state(df: pd.DataFrame, entry_n: int, exit_n: int,
                   trend: pd.Series | None = None) -> pd.Series:
    """Donchian latch: on when the close breaks the previous ``entry_n``-bar high
    (and is above ``trend`` if given), off when it breaks the previous
    ``exit_n``-bar low."""
    close = df["close"]
    entry = close > ind.donchian_high(df["high"], entry_n)
    if trend is not None:
        entry &= close > trend
    exit_ = close < ind.donchian_low(df["low"], exit_n)
    return latch(entry, exit_)


def keltner_state(close: pd.Series, mid: pd.Series, atr: pd.Series,
                  entry_k: float, exit_k: float) -> pd.Series:
    """Keltner latch: on when the close is above ``mid + entry_k * atr``, off when
    it is below ``mid - exit_k * atr``."""
    return latch(close > mid + entry_k * atr, close < mid - exit_k * atr)


def _require_min(params: dict, key: str, lo: int) -> None:
    if params[key] < lo:
        raise ValueError(f"{key} must be >= {lo}")


@register
class DonchianBreakout(Strategy):
    """Donchian channel breakout with a moving-average trend filter.

    Entry: the 1h close exceeds the highest high of the previous ``entry_n``
    bars *and* is above the ``trend_n``-bar simple moving average
    (``trend_n = 0`` disables the filter). Exit: the close falls below the
    lowest low of the previous ``exit_n`` bars. The trend filter is only an
    entry condition; an open position is managed by the channel exit alone.

    Defaults (6-day high entry, 3-day low exit, 20-day SMA filter) sit inside a
    broad plateau on TRAIN 2018-2023, on its lower-drawdown side.
    ``stop_atr > 0`` adds an intrabar trailing stop at ``stop_atr * ATR(atr_n)``
    below the close; it is off by default (see module docstring).
    """

    name = "breakout_donchian"
    description = "Donchian breakout (6d high entry, 3d low exit) with a 20-day SMA entry filter."
    default_params = {"entry_n": 144, "exit_n": 72, "trend_n": 480, "atr_n": 168, "stop_atr": 0.0}

    def validate(self) -> None:
        p = self.params
        _require_min(p, "entry_n", 2)
        _require_min(p, "exit_n", 2)
        if p["trend_n"] != 0:
            _require_min(p, "trend_n", 2)
        _require_min(p, "atr_n", 2)
        if p["stop_atr"] < 0:
            raise ValueError("stop_atr must be >= 0 (0 disables the stop)")

    @property
    def warmup_bars(self) -> int:
        p = self.params
        need = max(p["entry_n"], p["exit_n"], p["trend_n"])
        if p["stop_atr"] > 0:
            need = max(need, 6 * p["atr_n"])
        # plus room for at least one latch event after the windows are full
        return need + 2 * max(p["entry_n"], p["exit_n"])

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        trend = ind.sma(df["close"], p["trend_n"]) if p["trend_n"] > 0 else None
        out = pd.DataFrame({"target": donchian_state(df, p["entry_n"], p["exit_n"], trend)},
                           index=df.index)
        if p["stop_atr"] > 0:
            out["stop_dist"] = p["stop_atr"] * ind.atr(df, p["atr_n"])
        return out


@register
class KeltnerBreakout(Strategy):
    """Volatility (Keltner-style) breakout around a slow EMA.

    Entry: the 1h close exceeds ``EMA(ema_n) + entry_k * ATR(atr_n)``.
    Exit: the close falls below ``EMA(ema_n) - exit_k * ATR(atr_n)``.
    The gap between the two bands is the hysteresis that keeps the position
    through ordinary pullbacks. ATR is the 1h Wilder ATR, so with the defaults
    (25-day EMA, 7-day ATR, entry 3 ATR above, exit at the EMA) the entry band
    sits roughly 2% above the EMA.
    """

    name = "breakout_keltner"
    description = "Keltner breakout: close > EMA(600h) + 3*ATR(168h); exit below the EMA."
    default_params = {"ema_n": 600, "atr_n": 168, "entry_k": 3.0, "exit_k": 0.0}

    def validate(self) -> None:
        p = self.params
        _require_min(p, "ema_n", 2)
        _require_min(p, "atr_n", 2)
        if p["entry_k"] < 0:
            raise ValueError("entry_k must be >= 0")
        if p["entry_k"] + p["exit_k"] <= 0:
            raise ValueError("the exit band must lie below the entry band (entry_k + exit_k > 0)")

    @property
    def warmup_bars(self) -> int:
        p = self.params
        # EMA/Wilder recursions: initial-value weight < 0.1% after 4 EMA spans / 6 ATR periods
        return max(4 * p["ema_n"], 6 * p["atr_n"])

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        mid = ind.ema(close, p["ema_n"])
        atr = ind.atr(df, p["atr_n"])
        return pd.DataFrame({"target": keltner_state(close, mid, atr, p["entry_k"], p["exit_k"])},
                            index=df.index)


@register
class DualBreakout(Strategy):
    """Blend of a Keltner leg and a Donchian leg sharing one trend EMA.

    ``target = keltner_weight * K + (1 - keltner_weight) * D`` where

    * K (Keltner leg): on when close > ``EMA(trend_n) + band_k * ATR(atr_n)``,
      off when close < ``EMA(trend_n)``;
    * D (Donchian leg): on when close > previous ``entry_n``-bar high and
      close > ``EMA(trend_n)``, off when close < previous ``exit_n``-bar low.

    The legs enter and exit at different times, so exposure steps through
    0 / 0.5 / 1 with the defaults. Each leg alone is a decent trend follower;
    the blend has a better 30-day loss tail and drawdown than either because
    their false breakouts only partly overlap.
    """

    name = "breakout_dual"
    description = "50/50 Keltner (EMA 600h + 3 ATR) and Donchian (6d high / 3d low) breakout blend."
    default_params = {"entry_n": 144, "exit_n": 72, "trend_n": 600, "band_k": 3.0, "atr_n": 168,
                      "keltner_weight": 0.5}

    def validate(self) -> None:
        p = self.params
        _require_min(p, "entry_n", 2)
        _require_min(p, "exit_n", 2)
        _require_min(p, "trend_n", 2)
        _require_min(p, "atr_n", 2)
        if p["band_k"] <= 0:
            raise ValueError("band_k must be > 0")
        if not 0.0 <= p["keltner_weight"] <= 1.0:
            raise ValueError("keltner_weight must be in [0, 1]")

    @property
    def warmup_bars(self) -> int:
        p = self.params
        return max(4 * p["trend_n"], 6 * p["atr_n"], 3 * max(p["entry_n"], p["exit_n"]))

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        mid = ind.ema(close, p["trend_n"])
        atr = ind.atr(df, p["atr_n"])
        k_leg = keltner_state(close, mid, atr, p["band_k"], 0.0)
        d_leg = donchian_state(df, p["entry_n"], p["exit_n"], mid)
        w = p["keltner_weight"]
        return pd.DataFrame({"target": w * k_leg + (1.0 - w) * d_leg}, index=df.index)
