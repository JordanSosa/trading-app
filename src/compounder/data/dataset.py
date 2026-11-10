"""Build the backtest dataset from Binance's public archive.

Two stages:

``daily``   1d klines for every USDT perpetual ever listed. Used only to rank
            liquidity point-in-time, delisted symbols included.
``hourly``  1h klines and funding for every symbol that was ever in the top K
            by trailing 30-day volume, with point-in-time ranks attached.

The result is ``<data_dir>/research/hourly.npz``, the panel every backtest runs on.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

from . import archive
from .panel import DAY_MS, HOUR_MS, Panel, build_panel, liquidity_rank
from .universe import is_crypto_perp

log = logging.getLogger(__name__)


def month_end_ms(month: str) -> int:
    """Last millisecond of 'YYYY-MM'."""
    y, m = int(month[:4]), int(month[5:7])
    y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
    return int(np.datetime64(f"{y2:04d}-{m2:02d}-01", "ms").astype("int64")) - 1


def daily_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "research" / "daily.npz"


def hourly_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "research" / "hourly.npz"


def build_daily(data_dir: str | Path = "data", end_month: str = "9999-12", workers: int = 8) -> Panel:
    cache = Path(data_dir) / "raw"
    symbols = archive.perp_symbols()
    log.info("%d USDT perpetuals in the archive", len(symbols))

    def one(sym: str):
        try:
            return sym, archive.download_klines(sym, "1d", DAY_MS, cache, end_month=end_month)
        except archive.ArchiveError as exc:
            log.warning("skip %s: %s", sym, exc)
            return sym, np.empty((0, len(archive.KLINE_FIELDS)))

    t0 = time.time()
    res = archive.parallel_map(one, symbols, workers=workers, progress="daily klines")
    klines = {s: a for s, a in res if len(a)}
    log.info("downloaded daily klines for %d symbols in %.0fs", len(klines), time.time() - t0)
    end_ms = None if end_month.startswith("9999") else month_end_ms(end_month)
    panel = build_panel(DAY_MS, klines, end_ms=end_ms)
    excl = np.array([not is_crypto_perp(s) for s in panel.symbols])
    rank = liquidity_rank(panel.times, panel.quote_volume, panel.close, DAY_MS, exclude=excl)
    panel = Panel(**{**panel.__dict__, "rank": rank})
    panel.save(daily_path(data_dir))
    log.info("daily panel: %d days x %d symbols -> %s", panel.n_bars, panel.n_symbols, daily_path(data_dir))
    return panel


def build_hourly(data_dir: str | Path = "data", top: int = 30, end_month: str = "9999-12",
                 workers: int = 8) -> Panel:
    cache = Path(data_dir) / "raw"
    daily = Panel.load(daily_path(data_dir))
    excl = np.array([not is_crypto_perp(s) for s in daily.symbols])
    drank = liquidity_rank(daily.times, daily.quote_volume, daily.close, DAY_MS, exclude=excl)
    ever = ((drank > 0) & (drank <= top)).any(axis=0)
    symbols = [s for s, e in zip(daily.symbols, ever) if e]
    log.info("%d symbols were ever in the top %d", len(symbols), top)

    # Days missing for the most liquid contract are missing for every contract: holes in the
    # futures archive itself, to be filled from spot candles.
    btc = archive.download_klines("BTCUSDT", "1h", HOUR_MS, cache, end_month=end_month, patch=True)
    market_holes = set(archive.missing_days(btc, HOUR_MS))
    if market_holes:
        log.warning("the futures archive has no data at all on %s; filling from spot candles", sorted(market_holes))

    def one(sym: str):
        k = archive.download_klines(sym, "1h", HOUR_MS, cache, end_month=end_month, patch=True)
        n = 0
        if market_holes and len(k):
            k, n = archive.patch_from_spot(sym, "1h", HOUR_MS, cache, k, market_holes)
        f = archive.download_funding(sym, cache, end_month=end_month)
        return sym, k, f, n

    t0 = time.time()
    res = archive.parallel_map(one, symbols, workers=workers, progress="hourly klines")
    log.info("downloaded hourly klines + funding for %d symbols in %.0fs", len(res), time.time() - t0)
    end_ms = None if end_month.startswith("9999") else month_end_ms(end_month)
    spot_filled = {s: n for s, _, _, n in res if n}
    panel = build_panel(HOUR_MS, {s: k for s, k, _, _ in res}, {s: f for s, _, f, _ in res}, end_ms=end_ms)
    # Every symbol that can rank in the top K is in this panel, so ranks 1..K computed
    # here equal the ranks over the whole exchange.
    rank = liquidity_rank(panel.times, panel.quote_volume, panel.close, HOUR_MS)
    panel = Panel(**{**panel.__dict__, "rank": rank})
    out = hourly_path(data_dir)
    panel.save(out)
    meta = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "data.binance.vision USD-M perpetual monthly archives (MD5/SHA-256 verified)",
        "top": top, "end_month": end_month, "symbols": list(panel.symbols), "bars": panel.n_bars,
        "first_ms": int(panel.times[0]), "last_ms": int(panel.times[-1]),
        "repairs": {"monthly_holes_filled_from": "daily futures archives",
                    "market_wide_holes": sorted(market_holes),
                    "market_wide_holes_filled_from": "spot daily archives, rescaled to the perpetual's last close",
                    "bars_filled_from_spot": int(sum(spot_filled.values())),
                    "symbols_filled_from_spot": len(spot_filled)},
    }
    (out.parent / "hourly.meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    log.info("hourly panel: %d bars x %d symbols -> %s", panel.n_bars, panel.n_symbols, out)
    return panel
