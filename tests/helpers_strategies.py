"""Test-only strategies (registered under names starting with '_test')."""

from __future__ import annotations

import pandas as pd

from tradebot import indicators as ind
from tradebot.strategies import REGISTRY, register
from tradebot.strategies.base import Strategy


class SmaStop(Strategy):
    name = "_test_sma_stop"
    description = "test: SMA crossover with ATR trailing stop"
    default_params = {"fast": 24, "slow": 120, "k": 3.0}

    @property
    def warmup_bars(self) -> int:
        return 300

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        f = ind.sma(df["close"], self.params["fast"])
        s = ind.sma(df["close"], self.params["slow"])
        a = ind.sma(ind.true_range(df), 48)
        return pd.DataFrame({"target": (f > s).astype(float).where(s.notna()),
                             "stop_dist": self.params["k"] * a}, index=df.index)


class Scripted(Strategy):
    """Replays a fixed target array (set via class attribute) for engine tests."""
    name = "_test_scripted"
    default_params = {}
    targets: list[float] = []
    stops: list[float] | None = None

    @property
    def warmup_bars(self) -> int:
        return 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        n = len(df)
        t = (list(self.targets) + [self.targets[-1] if self.targets else 0.0] * n)[:n]
        out = pd.DataFrame({"target": t}, index=df.index)
        if self.stops is not None:
            out["stop_dist"] = (list(self.stops) + [self.stops[-1]] * n)[:n]
        return out


for cls in (SmaStop, Scripted):
    if cls.name not in REGISTRY:
        register(cls)


class SmaLongShort(Strategy):
    """test: +1 above the slow SMA, -1 below (exercises shorts and flips)."""
    name = "_test_sma_ls"
    default_params = {"slow": 120}

    @property
    def warmup_bars(self) -> int:
        return 200

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        s = ind.sma(df["close"], self.params["slow"])
        tgt = (df["close"] > s).astype(float) * 2 - 1
        return pd.DataFrame({"target": tgt.where(s.notna())}, index=df.index)


if SmaLongShort.name not in REGISTRY:
    register(SmaLongShort)
