"""Strategy interface.

A strategy is a *stateless, causal* function of candle history. It returns a
frame aligned with the input candles containing:

``target``
    Desired BTC exposure as a fraction of equity in ``[-1, 1]`` to hold from
    the *next* bar's open (negative = short; the engine clips it to the
    configured ``[-max_short, max_exposure]``, so long-only configs never
    short). NaN is treated as 0 (flat).
``stop_dist`` (optional)
    Distance in USDT below price for an intrabar protective trailing stop.
    The engine sets the stop at ``entry_fill - stop_dist`` and ratchets it up
    to ``close - stop_dist`` on each later close. NaN means "no stop update".

All path-dependent behaviour (positions, trailing stops, circuit breakers) is
handled by the engine so that a strategy can be evaluated identically in a
single-pass backtest or tick-by-tick in the forward run.

Causality contract: ``compute(df.iloc[:k]).iloc[-1]`` must equal
``compute(df).iloc[k-1]`` for every ``k`` (see ``tests/test_causality.py``).
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd


class Strategy:
    name: ClassVar[str] = "base"
    description: ClassVar[str] = ""
    default_params: ClassVar[dict[str, Any]] = {}

    def __init__(self, **params: Any):
        unknown = set(params) - set(self.default_params)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {sorted(unknown)}; "
                             f"valid: {sorted(self.default_params)}")
        merged = {**self.default_params, **params}
        for key, default in self.default_params.items():
            val = merged[key]
            if isinstance(default, bool):
                merged[key] = bool(val)
            elif isinstance(default, int) and not isinstance(val, bool):
                if float(val) != int(val):
                    raise ValueError(f"{self.name}: param {key} must be an integer")
                merged[key] = int(val)
            elif isinstance(default, float):
                merged[key] = float(val)
        self.params: dict[str, Any] = merged
        self.validate()

    # -- to override -------------------------------------------------------
    def validate(self) -> None:  # pragma: no cover - default: accept
        """Raise ValueError for invalid parameter combinations."""

    @property
    def warmup_bars(self) -> int:
        """Bars of history needed before signals are reliable."""
        raise NotImplementedError

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    # -- helpers -------------------------------------------------------------
    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """``compute`` plus schema enforcement; use this from engines."""
        out = self.compute(df)
        if "target" not in out.columns:
            raise ValueError(f"{self.name}.compute() must return a 'target' column")
        if not out.index.equals(df.index):
            raise ValueError(f"{self.name}.compute() must return a frame aligned with the input")
        res = pd.DataFrame(index=df.index)
        res["target"] = out["target"].astype("float64").clip(lower=-1.0, upper=1.0)
        res["stop_dist"] = out["stop_dist"].astype("float64") if "stop_dist" in out.columns else float("nan")
        return res

    def describe(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({args})"
