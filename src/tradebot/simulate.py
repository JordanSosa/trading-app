"""Replay the forward run over historical data, tick by tick.

``HistoricalMarketData`` serves candles exactly as a live provider would at a
simulated wall-clock time (closed bars + the forming bar, whose high/low/close
only reflect the elapsed part of the hour approximated by its open). ``replay``
drives the real ``forward.tick`` (state written to disk and re-read every
tick), which proves that the live path reproduces the backtest.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import Config
from .data.candles import index_ms
from .data.providers import FetchResult, ProviderError
from .forward import TickResult, tick
from .state import StateStore


class HistoricalMarketData:
    def __init__(self, candles: pd.DataFrame, interval_ms: int):
        self.candles = candles
        self.ms = index_ms(candles.index)
        self.interval_ms = interval_ms
        self.now_ms = int(self.ms[0])
        self.fail_next = 0  # inject provider failures for tests

    def fetch(self, interval: str, closed_bars: int, min_bars: int) -> FetchResult:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ProviderError("simulated outage")
        import numpy as np
        n_closed = int(np.searchsorted(self.ms + self.interval_ms, self.now_ms, side="right"))
        if n_closed < min_bars:
            raise ProviderError(f"only {n_closed} closed bars")
        lo = max(0, n_closed - closed_bars)
        closed = self.candles.iloc[lo:n_closed]
        f_open = f_ms = f_close = None
        if n_closed < len(self.candles) and self.ms[n_closed] <= self.now_ms:
            f_ms = int(self.ms[n_closed])
            f_open = float(self.candles["open"].iloc[n_closed])
            # Inside the forming bar only its open is certainly known.
            f_close = f_open
        return FetchResult(closed, f_open, f_ms, "historical", [], f_close)

    def cross_check(self, primary, closed, interval, max_dev, bars=6):  # noqa: ARG002
        return True, "historical replay: cross-check skipped"


def replay(cfg: Config, candles: pd.DataFrame, start: str | pd.Timestamp, end: str | pd.Timestamp,
           state_dir: str | Path, tick_minutes: int = 15, write_reports: bool = False) -> list[TickResult]:
    """Run ``tick`` every ``tick_minutes`` of simulated time from start to end."""
    md = HistoricalMarketData(candles, cfg.run.timeframe_ms)
    store = StateStore(state_dir)
    t = pd.Timestamp(start, tz="UTC").value // 1_000_000 if not isinstance(start, pd.Timestamp) else start.value // 1_000_000
    t_end = pd.Timestamp(end, tz="UTC").value // 1_000_000 if not isinstance(end, pd.Timestamp) else end.value // 1_000_000
    results = []
    while t <= t_end:
        md.now_ms = int(t)
        results.append(tick(cfg, store, md, now_ms=int(t), write_reports=write_reports))
        if results[-1].status == "completed":
            break
        t += tick_minutes * 60_000
    return results
