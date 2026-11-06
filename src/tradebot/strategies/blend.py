"""Blend: weighted average of several strategies' target exposures.

Diversifying across signal families (e.g. slow trend + short-term mean
reversion) tends to give smoother, more robust results than any single
signal. Component protective stops are not combined (a blended stop is not
well defined); each component's exit logic still acts through its target.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from . import REGISTRY, _discover, register
from .base import Strategy


@register
class Blend(Strategy):
    name = "blend"
    description = "Weighted average of component strategies' target exposures."
    default_params: dict[str, Any] = {"components": [{"name": "buy_hold", "params": {}, "weight": 1.0}]}

    def validate(self) -> None:
        comps = self.params["components"]
        if not isinstance(comps, list) or not comps:
            raise ValueError("blend: 'components' must be a non-empty list of {name, params, weight}")
        _discover()
        self._members: list[tuple[Strategy, float]] = []
        for c in comps:
            if not isinstance(c, dict) or "name" not in c:
                raise ValueError("blend: each component needs a 'name'")
            if c["name"] == self.name:
                raise ValueError("blend: components cannot be blends")
            if c["name"] not in REGISTRY:
                raise ValueError(f"blend: unknown component strategy {c['name']!r}")
            w = float(c.get("weight", 1.0))
            if w <= 0:
                raise ValueError("blend: weights must be positive")
            self._members.append((REGISTRY[c["name"]](**dict(c.get("params", {}))), w))

    @property
    def warmup_bars(self) -> int:
        return max(s.warmup_bars for s, _ in self._members)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        total = sum(w for _, w in self._members)
        acc = pd.Series(0.0, index=df.index)
        valid = pd.Series(True, index=df.index)
        for strat, w in self._members:
            t = strat.signals(df)["target"]
            valid &= t.notna()
            acc = acc + t.fillna(0.0) * (w / total)
        return pd.DataFrame({"target": acc.where(valid)}, index=df.index)

    def describe(self) -> str:
        parts = ", ".join(f"{w:g}x {s.describe()}" for s, w in self._members)
        return f"blend[{parts}]"
