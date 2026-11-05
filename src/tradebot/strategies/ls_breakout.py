"""Long/short breakout strategies for BTC/USDT perpetual futures (1h candles).

These extend the deployed long-only ``breakout_dual`` with a short side. They
only short when run with ``run.instrument = "hybrid"`` (longs on spot, shorts
on perpetuals; recommended, because all-perp longs pay funding) or ``"perp"``
and ``risk.max_short > 0``. Under a long-only spot config the engine clips
negative targets to 0; the strategies then match ``breakout_dual`` except on
the ~0.6% (``lsb_bear_chandelier``) / ~1.5% (``lsb_mirror``) of bars where
a short nets against a still-open long leg.

Every strategy keeps the ``breakout_dual`` long side unchanged (Keltner leg +
Donchian leg sharing EMA(``trend_n``), weight 0.5 each), so any difference
from the deployed strategy comes from the short side alone. Targets are
``long_exposure - short_exposure`` in ``[-1, 1]`` (the legs net).

``lsb_mirror``
    The naive symmetric mirror: a Donchian breakdown leg and a Keltner
    lower-band leg, exactly the long rules reflected, scaled by
    ``short_weight``. Kept as the reference that shows why the short side
    must be asymmetric (research/ls_breakout.md): BTC's bear-market rallies
    and V-shaped bottoms squeeze a mirrored short whose exit sits far away,
    so the mirror adds almost nothing to the score (+0.001 on 2018-2024) and
    widens the 30-day loss tail.
``lsb_bear_chandelier`` / ``lsb_bear_chandelier_lite``
    Asymmetric short side, built from what the 2018-2023 research supported:

    * **Slow regime gate.** A short is only *entered* while the close is
      below its ``regime_n``-bar simple moving average (default 150 days),
      i.e. in an established bear market. In bull markets the short side
      stays silent.
    * **Donchian breakdown entry.** The close breaks the lowest low of the
      previous ``entry_n`` bars (the long side's entry length).
    * **Fast chandelier exit.** Cover once the close rises more than
      ``chand_k`` x ATR(24) above the lowest low of the previous ``exit_n``
      bars. After a breakdown the mirrored Donchian exit (the previous
      N-bar *high*) sits at the pre-crash price, so a V-shaped rebound gives
      back the whole move; the chandelier level trails the new lows instead.
      ATR(24) is used because it widens immediately after a volatility spike,
      so ordinary post-crash noise does not trigger the exit.
    * **Volatility-scaled, smaller size.** The short size is
      ``min(1, short_risk / (ATR(168) / close))`` sampled on the entry bar
      and held until the exit (frozen, so it does not churn). With the
      default ``short_risk = 0.004`` and a typical hourly ATR of ~0.9% of
      price the short is about 0.45-0.5 (median 0.48 on 2018-2024); after
      crashes it is smaller, in very calm markets it reaches the 1.0 cap.

    ``_lite`` is the same logic with ``short_risk = 0.003`` (about a quarter
    smaller short, median 0.36), the setting that best respected the 30-day
    loss-tail limit of the adoption rule on 2018-2023.

Why shorts are taken only then: on 2018-2023 data short breakdowns earned
money only in sustained bear markets (2018, 2022) and, with the chandelier
exit, in the March 2020 crash; the mirrored short lost in every other year.
Shorts also receive little
funding (about 0.1% of equity per year on 2020-2024, against 2-22% per
year that all-perp longs paid), because funding is lowest exactly when
bear-market shorts are open.

Fixed (not tunable) constants inherited from ``breakout_dual``: Keltner
band 3.0 x ATR(168), leg weight 0.5. The chandelier ATR length (24) is also
fixed. Each strategy has at most 6 tunable parameters.

All rules are vectorised latches (see ``breakout.latch``) on closed bars, so
``compute(df.iloc[:k]).iloc[-1] == compute(df).iloc[k-1]``; ``warmup_bars``
covers the EMA/Wilder recursions, the regime SMA and the latch
synchronisation (verified with ``research.check_causal``'s warmup+1 window
check on the research data).

Research notes and adoption-rule results: ``research/ls_breakout.md``.
Results on the pre-declared protocol (research/LONG_SHORT_PROTOCOL.md,
``scripts/ls_evaluate.py``, DEV 2018-2024): ``lsb_bear_chandelier`` scores
0.0482 (robust 0.0467) in hybrid mode and 0.0448 (robust 0.0432) in perp
mode against 0.0401 (robust 0.03915) for ``breakout_dual`` on spot. It is
positive in all 7 years, but the paired improvement is not significant
(t = 1.73 hybrid, 0.97 perp) and its 30-day 5th percentile (-10.2%) is wider
than the rule allows (-9.36%). The ``_lite`` variant also fails (t 1.65,
p05 -9.46% in hybrid mode). **No candidate meets the adoption rule**, so the
deployment stays long-only and these strategies remain an opt-in.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from . import register
from .base import Strategy
from .breakout import _require_min, donchian_state, keltner_state, latch

BAND_K = 3.0          # breakout_dual Keltner band (ATR multiples above the EMA)
ATR_N = 168           # breakout_dual ATR length (1h bars)
LEG_WEIGHT = 0.5      # breakout_dual Keltner/Donchian blend
FAST_ATR_N = 24       # chandelier-exit ATR length (1h bars)


def dual_long(df: pd.DataFrame, entry_n: int, exit_n: int, trend_n: int, band_k: float = BAND_K,
              atr_n: int = ATR_N) -> tuple[pd.Series, pd.Series, pd.Series]:
    """The ``breakout_dual`` long exposure (0 / 0.5 / 1), plus its trend EMA and ATR."""
    close = df["close"]
    mid = ind.ema(close, trend_n)
    atr = ind.atr(df, atr_n)
    k_leg = keltner_state(close, mid, atr, band_k, 0.0)
    d_leg = donchian_state(df, entry_n, exit_n, mid)
    return LEG_WEIGHT * k_leg + (1.0 - LEG_WEIGHT) * d_leg, mid, atr


def hold_from_entry(state: pd.Series, value: pd.Series) -> pd.Series:
    """``value`` sampled on the bar a 0/1 ``state`` switches on, held while it stays on; 0 when off.

    Causal: the held value only depends on the bar where the current on-span
    started. A NaN value on the entry bar counts as 0 (no position), and it
    never inherits a value from an earlier span.
    """
    on = state.to_numpy() > 0
    start = on & ~np.r_[False, on[:-1]]
    sampled = np.where(start, np.nan_to_num(value.to_numpy(dtype="float64"), nan=0.0), np.nan)
    held = pd.Series(sampled, index=state.index).ffill().to_numpy()
    return pd.Series(np.where(on, np.nan_to_num(held, nan=0.0), 0.0), index=state.index)


@register
class MirrorBreakout(Strategy):
    """``breakout_dual`` plus its exact mirror image on the short side.

    Long legs (as ``breakout_dual``): Keltner on at close > EMA(trend_n) +
    band_k*ATR, off below the EMA; Donchian on at a close above the previous
    ``entry_n``-bar high (and above the EMA), off below the previous
    ``exit_n``-bar low.

    Short legs (reflected): Keltner short on at close < EMA - band_k*ATR, off
    above the EMA; Donchian short on at a close below the previous
    ``entry_n``-bar low (and below the EMA), off above the previous
    ``exit_n``-bar high.

    ``target = 0.5*(K + D) - short_weight * 0.5*(K_short + D_short)``.
    This is the research reference for the symmetric design; it is not
    recommended (see the module docstring).
    """

    name = "lsb_mirror"
    description = "breakout_dual plus its mirrored short legs (Keltner lower band, Donchian breakdown) at half size."
    default_params = {"entry_n": 144, "exit_n": 72, "trend_n": 600, "band_k": 3.0, "atr_n": 168,
                      "short_weight": 0.5}

    def validate(self) -> None:
        p = self.params
        for key in ("entry_n", "exit_n", "trend_n", "atr_n"):
            _require_min(p, key, 2)
        if p["band_k"] <= 0:
            raise ValueError("band_k must be > 0")
        if not 0.0 <= p["short_weight"] <= 1.0:
            raise ValueError("short_weight must be in [0, 1]")

    @property
    def warmup_bars(self) -> int:
        p = self.params
        # Same as breakout_dual: EMA/Wilder seed weight ~1e-9 / ~1e-5, and the
        # latches synchronise at their first entry/exit event.
        return max(10 * p["trend_n"], 12 * p["atr_n"], 10 * max(p["entry_n"], p["exit_n"]))

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        close = df["close"]
        long_, mid, atr = dual_long(df, p["entry_n"], p["exit_n"], p["trend_n"], p["band_k"], p["atr_n"])
        k_short = latch(close < mid - p["band_k"] * atr, close > mid)
        d_short = latch((close < ind.donchian_low(df["low"], p["entry_n"])) & (close < mid),
                        close > ind.donchian_high(df["high"], p["exit_n"]))
        short = p["short_weight"] * (LEG_WEIGHT * k_short + (1.0 - LEG_WEIGHT) * d_short)
        return pd.DataFrame({"target": long_ - short}, index=df.index)


@register
class BearChandelierBreakout(Strategy):
    """``breakout_dual`` long side plus a regime-gated, volatility-sized short breakdown leg.

    Short entry (all on the bar's close):
      close < lowest low of the previous ``entry_n`` bars
      and close < SMA(``regime_n``)                        (bear regime only)
    Short exit:
      close > lowest low of the previous ``exit_n`` bars + ``chand_k`` * ATR(24)
    Short size, fixed at the entry bar:
      min(1, ``short_risk`` / (ATR(168) / close))

    ``target = breakout_dual long exposure - short size * short state``.
    The regime gate applies to entries only; an open short is managed by
    the chandelier exit alone (it is usually hit before the price gets back
    above the 150-day average).
    """

    name = "lsb_bear_chandelier"
    description = ("breakout_dual long side + vol-sized 6-day-low breakdown short below the 150d SMA, "
                   "covered by a 72h-low + 8xATR24 chandelier exit.")
    default_params = {"entry_n": 144, "exit_n": 72, "trend_n": 600, "regime_n": 3600,
                      "short_risk": 0.004, "chand_k": 8.0}

    def validate(self) -> None:
        p = self.params
        for key in ("entry_n", "exit_n", "trend_n", "regime_n"):
            _require_min(p, key, 2)
        if not 0.0 <= p["short_risk"] <= 0.05:
            raise ValueError("short_risk must be in [0, 0.05] (0 disables the short side)")
        if p["chand_k"] <= 0:
            raise ValueError("chand_k must be > 0")

    @property
    def warmup_bars(self) -> int:
        p = self.params
        latch_sync = 10 * max(p["entry_n"], p["exit_n"])
        # EMA seed weight ~1e-9 after 10 spans, Wilder ATR ~1e-5 after 12
        # periods, the regime SMA needs exactly regime_n bars; both latches
        # (and the frozen short size) synchronise at their first event.
        return max(10 * p["trend_n"], 12 * ATR_N, 12 * FAST_ATR_N, p["regime_n"] + latch_sync, latch_sync)

    def short_exposure(self, df: pd.DataFrame, atr: pd.Series) -> pd.Series:
        p = self.params
        close = df["close"]
        bear = close < ind.sma(close, p["regime_n"])                       # False while the SMA is NaN
        entry = (close < ind.donchian_low(df["low"], p["entry_n"])) & bear
        exit_ = close > ind.donchian_low(df["low"], p["exit_n"]) + p["chand_k"] * ind.atr(df, FAST_ATR_N)
        state = latch(entry, exit_)
        size = (p["short_risk"] / (atr / close)).clip(upper=1.0)
        return hold_from_entry(state, size)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        long_, _, atr = dual_long(df, p["entry_n"], p["exit_n"], p["trend_n"])
        short = self.short_exposure(df, atr)
        return pd.DataFrame({"target": long_ - short}, index=df.index)


@register
class BearChandelierBreakoutLite(BearChandelierBreakout):
    """``lsb_bear_chandelier`` with a smaller short (``short_risk = 0.003``, median size 0.36).

    Chosen on 2018-2023 data as the size that best respects the protocol's
    30-day loss-tail limit while keeping most of the short-side gain.
    """

    name = "lsb_bear_chandelier_lite"
    description = ("lsb_bear_chandelier with a smaller short (short_risk 0.003) for a tighter 30-day loss tail.")
    default_params = {**BearChandelierBreakout.default_params, "short_risk": 0.003}
