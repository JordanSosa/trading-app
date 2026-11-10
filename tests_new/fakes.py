"""A deterministic fake exchange for forward-run tests (no network)."""

from __future__ import annotations

import numpy as np

from compounder.data.live import DataError, Instrument
from compounder.data.panel import DAY_MS, HOUR_MS, Panel


class FakeSource:
    """Serves candles from a synthetic panel as if the wall clock were ``now_ms``."""

    def __init__(self, world: Panel, name: str = "fake", price_scale: float = 1.0):
        self.world = world
        self.name = name
        self.now_ms = int(world.times[0])
        self.price_scale = price_scale
        self.fail = False
        self.fail_symbols: set[str] = set()
        self.delisted: set[str] = set()
        self.calls = 0

    # ------------------------------------------------------------------ helpers
    def _check(self) -> None:
        self.calls += 1
        if self.fail:
            raise DataError(f"{self.name} is down")

    def _visible(self) -> int:
        """Index one past the last bar that has opened by now (the last one may be forming)."""
        return int(np.searchsorted(self.world.times, self.now_ms, side="right"))

    # ---------------------------------------------------------------- interface
    def instruments(self) -> dict[str, Instrument]:
        self._check()
        return {s: Instrument(s, s.replace("USDT", "-USDT-SWAP"), s[:-4], 1.0, 0.001, 0.001, 0.01, 50.0, 0)
                for s in self.world.symbols if s not in self.delisted}

    def candles(self, inst: Instrument, limit: int, interval_ms: int = HOUR_MS) -> np.ndarray:
        self._check()
        if inst.symbol in self.fail_symbols:
            raise DataError(f"no candles for {inst.symbol}")
        w, j, hi = self.world, self.world.index_of(inst.symbol), self._visible()
        t, o, h, l, c, v = (w.times[:hi], w.open[:hi, j], w.high[:hi, j], w.low[:hi, j], w.close[:hi, j],
                            w.quote_volume[:hi, j])
        ok = np.isfinite(c)
        rows = np.column_stack([t, o, h, l, c, v / np.where(ok, c, 1.0), v, np.zeros(hi), np.zeros(hi)])[ok]
        rows[:, 1:5] *= self.price_scale
        if len(rows) and rows[-1, 0] + HOUR_MS > self.now_ms:
            # The forming bar has only traded part of its range so far: show open = high = low = close.
            rows[-1, 2:5] = rows[-1, 1]
        if interval_ms == DAY_MS:
            rows = _to_daily(rows)
        return rows[-limit:]

    def funding(self, inst: Instrument) -> np.ndarray:
        self._check()
        w, j, hi = self.world, self.world.index_of(inst.symbol), self._visible()
        idx = np.nonzero(w.funding[:hi, j])[0]
        settle = w.times[idx] + HOUR_MS
        keep = settle <= self.now_ms
        return np.column_stack([settle[keep], w.funding[idx[keep], j]])[-100:]

    def last_prices(self) -> dict[str, float]:
        self._check()
        hi = self._visible()
        return {s: float(self.world.close[hi - 1, j]) for j, s in enumerate(self.world.symbols)}

    def day_volumes(self) -> dict[str, float]:
        self._check()
        hi = self._visible()
        lo = max(0, hi - 24)
        return {s: float(self.world.quote_volume[lo:hi, j].sum()) for j, s in enumerate(self.world.symbols)}


def _to_daily(rows: np.ndarray) -> np.ndarray:
    if not len(rows):
        return rows
    day = (rows[:, 0] // DAY_MS).astype("int64")
    out = []
    for d in np.unique(day):
        r = rows[day == d]
        out.append([d * DAY_MS, r[0, 1], r[:, 2].max(), r[:, 3].min(), r[-1, 4], r[:, 5].sum(), r[:, 6].sum(), 0.0, 0.0])
    return np.asarray(out, dtype="float64")
