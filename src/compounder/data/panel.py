"""A panel is a set of aligned candle arrays: time on axis 0, symbols on axis 1.

Missing bars (before listing, after delisting, exchange outages) are NaN in the
price fields and 0 in volume/funding. ``rank`` is the point-in-time liquidity
rank of each symbol (1 = most liquid, 0 = not ranked) *as known at the open of
the bar*, so selecting a universe from it never looks ahead.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

PRICE_FIELDS = ("open", "high", "low", "close")
FIELDS = PRICE_FIELDS + ("quote_volume", "taker_quote", "funding")


@dataclass(frozen=True)
class Panel:
    interval_ms: int
    times: np.ndarray          # int64 bar open time (epoch ms), shape (T,)
    symbols: tuple[str, ...]   # length N
    open: np.ndarray           # float64 (T, N)
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    quote_volume: np.ndarray   # USDT traded in the bar
    taker_quote: np.ndarray    # USDT bought by takers in the bar
    funding: np.ndarray        # funding rate settled at the bar's close (0 if none)
    rank: np.ndarray           # int16 (T, N) point-in-time liquidity rank, 0 = unranked

    def __post_init__(self) -> None:
        t, n = len(self.times), len(self.symbols)
        for name in FIELDS + ("rank",):
            arr = getattr(self, name)
            if arr.shape != (t, n):
                raise ValueError(f"panel field {name} has shape {arr.shape}, expected {(t, n)}")
        if t > 1 and not (np.diff(self.times) > 0).all():
            raise ValueError("panel times must be strictly increasing")

    @property
    def n_bars(self) -> int:
        return len(self.times)

    @property
    def n_symbols(self) -> int:
        return len(self.symbols)

    def index_of(self, symbol: str) -> int:
        return self.symbols.index(symbol)

    def slice(self, start: int, stop: int) -> "Panel":
        kw = {f: getattr(self, f)[start:stop] for f in FIELDS + ("rank",)}
        return replace(self, times=self.times[start:stop], **kw)

    def select(self, symbols: list[str] | tuple[str, ...]) -> "Panel":
        idx = [self.symbols.index(s) for s in symbols]
        kw = {f: getattr(self, f)[:, idx] for f in FIELDS + ("rank",)}
        return replace(self, symbols=tuple(symbols), **kw)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part.npz")
        np.savez_compressed(tmp, interval_ms=np.int64(self.interval_ms), times=self.times,
                            symbols=np.array(self.symbols), rank=self.rank,
                            **{f: getattr(self, f).astype("float64") for f in FIELDS})
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "Panel":
        with np.load(Path(path), allow_pickle=False) as z:
            return cls(interval_ms=int(z["interval_ms"]), times=z["times"].astype("int64"),
                       symbols=tuple(str(s) for s in z["symbols"]), rank=z["rank"].astype("int16"),
                       **{f: z[f].astype("float64") for f in FIELDS})


def build_panel(interval_ms: int, klines: dict[str, np.ndarray], funding: dict[str, np.ndarray] | None = None,
                start_ms: int | None = None, end_ms: int | None = None) -> Panel:
    """Align per-symbol kline arrays (archive column order) on a common regular grid.

    ``klines[symbol]`` has columns open_ms, open, high, low, close, volume,
    quote_volume, trades, taker_quote. ``funding[symbol]`` has columns
    settlement_ms, rate; a settlement at time ``f`` is booked on the bar that
    closes at ``f`` (positions held through that close pay or receive it).
    """
    symbols = tuple(sorted(s for s, a in klines.items() if len(a)))
    if not symbols:
        raise ValueError("no kline data")
    lo = min(int(klines[s][0, 0]) for s in symbols)
    hi = max(int(klines[s][-1, 0]) for s in symbols)
    if start_ms is not None:
        lo = max(lo, start_ms - start_ms % interval_ms)
    if end_ms is not None:
        hi = min(hi, end_ms - end_ms % interval_ms)
    times = np.arange(lo, hi + interval_ms, interval_ms, dtype="int64")
    t, n = len(times), len(symbols)
    out = {f: np.full((t, n), np.nan) for f in PRICE_FIELDS}
    out.update({f: np.zeros((t, n)) for f in ("quote_volume", "taker_quote", "funding")})
    for j, sym in enumerate(symbols):
        a = klines[sym]
        pos = ((a[:, 0].astype("int64") - lo) // interval_ms).astype("int64")
        ok = (pos >= 0) & (pos < t) & (a[:, 4] > 0) & (a[:, 1] > 0)
        pos = pos[ok]
        out["open"][pos, j] = a[ok, 1]
        out["high"][pos, j] = a[ok, 2]
        out["low"][pos, j] = a[ok, 3]
        out["close"][pos, j] = a[ok, 4]
        out["quote_volume"][pos, j] = a[ok, 6]
        out["taker_quote"][pos, j] = a[ok, 8]
        if funding and sym in funding and len(funding[sym]):
            f = funding[sym]
            # Settlement at f is booked on the bar that closes at f, i.e. opens at f - interval.
            fpos = ((f[:, 0].astype("int64") - interval_ms - lo) // interval_ms).astype("int64")
            aligned = (f[:, 0].astype("int64") % interval_ms) == 0
            fok = (fpos >= 0) & (fpos < t) & aligned & np.isfinite(f[:, 1])
            np.add.at(out["funding"][:, j], fpos[fok], f[fok, 1])
    return Panel(interval_ms=interval_ms, times=times, symbols=symbols, rank=np.zeros((t, n), dtype="int16"), **out)


def liquidity_rank(times: np.ndarray, quote_volume: np.ndarray, close: np.ndarray, interval_ms: int,
                   window_days: int = 30, min_history_days: int = 30, refresh_days: int = 7,
                   exclude: np.ndarray | None = None) -> np.ndarray:
    """Point-in-time liquidity rank per bar.

    At every refresh instant (every ``refresh_days`` on the UTC-day grid) symbols
    are ranked by their mean daily USDT volume over the previous ``window_days``
    complete days. A symbol needs ``min_history_days`` of bars before it can be
    ranked. The rank computed at instant ``r`` applies to bars opening in
    ``[r, next refresh)``, and uses only bars that closed at or before ``r``.
    """
    t, n = quote_volume.shape
    per_day = DAY_MS // interval_ms
    if per_day < 1 or DAY_MS % interval_ms:
        raise ValueError("interval must divide one day")
    have = np.isfinite(close)
    csum_v = np.vstack([np.zeros((1, n)), np.cumsum(np.where(have, quote_volume, 0.0), axis=0)])
    csum_h = np.vstack([np.zeros((1, n), dtype="int64"), np.cumsum(have, axis=0)])
    rank = np.zeros((t, n), dtype="int16")
    w, mh, step = window_days * per_day, min_history_days * per_day, refresh_days * DAY_MS
    first_refresh = times[0] - times[0] % step + step
    current = np.zeros(n, dtype="int16")
    refresh_at = first_refresh
    excl = np.zeros(n, dtype=bool) if exclude is None else exclude
    for i in range(t):
        if times[i] >= refresh_at:
            lo = max(0, i - w)
            vol = (csum_v[i] - csum_v[lo]) / window_days
            seen = csum_h[i] - csum_h[max(0, i - mh)]
            # Listed for the whole history window and still trading in the last day.
            alive = (csum_h[i] - csum_h[max(0, i - per_day)]) > 0
            ok = (seen >= int(mh * 0.9)) & alive & (vol > 0) & ~excl
            current = np.zeros(n, dtype="int16")
            order = np.argsort(-np.where(ok, vol, -1.0), kind="stable")
            k = int(ok.sum())
            current[order[:k]] = np.arange(1, k + 1, dtype="int16")
            while refresh_at <= times[i]:
                refresh_at += step
        rank[i] = current
    return rank
