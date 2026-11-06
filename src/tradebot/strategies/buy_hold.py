"""Benchmark: buy at the first opportunity and hold."""

from __future__ import annotations

import pandas as pd

from . import register
from .base import Strategy


@register
class BuyHold(Strategy):
    name = "buy_hold"
    description = "Benchmark: 100% BTC from the first bar."
    default_params = {"exposure": 1.0}

    def validate(self) -> None:
        if not 0 < self.params["exposure"] <= 1:
            raise ValueError("exposure must be in (0, 1]")

    @property
    def warmup_bars(self) -> int:
        return 1

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"target": self.params["exposure"]}, index=df.index)
