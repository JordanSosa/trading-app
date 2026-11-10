"""Build the research dataset from Binance's public archive.

Stage ``daily``  : 1d klines for every USDT perpetual ever listed -> data/research/daily.npz
                   (used only to rank liquidity point-in-time, including delisted symbols).
Stage ``hourly`` : 1h klines + funding for every symbol that was ever in the top-K by trailing
                   30-day volume -> data/research/hourly.npz, with point-in-time ranks.

Usage:  python scripts/build_research_data.py daily
        python scripts/build_research_data.py hourly --top 40 --end 2026-10
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from compounder.data import archive  # noqa: E402
from compounder.data.panel import DAY_MS, HOUR_MS, Panel, build_panel, liquidity_rank  # noqa: E402
from compounder.data.universe import is_crypto_perp  # noqa: E402

log = logging.getLogger("build")


def month_end_ms(month: str) -> int:
    y, m = int(month[:4]), int(month[5:7])
    y2, m2 = (y + 1, 1) if m == 12 else (y, m + 1)
    return int(np.datetime64(f"{y2:04d}-{m2:02d}-01", "ms").astype("int64")) - 1


def stage_daily(args: argparse.Namespace) -> None:
    cache = Path(args.data_dir) / "raw"
    symbols = archive.perp_symbols()
    log.info("%d USDT perpetuals in the archive", len(symbols))

    def one(sym: str):
        try:
            return sym, archive.download_klines(sym, "1d", DAY_MS, cache, end_month=args.end)
        except archive.ArchiveError as exc:
            log.warning("skip %s: %s", sym, exc)
            return sym, np.empty((0, len(archive.KLINE_FIELDS)))

    t0 = time.time()
    res = archive.parallel_map(one, symbols, workers=args.workers, progress="daily klines")
    klines = {s: a for s, a in res if len(a)}
    log.info("downloaded daily klines for %d symbols in %.0fs", len(klines), time.time() - t0)
    panel = build_panel(DAY_MS, klines, end_ms=month_end_ms(args.end))
    excl = np.array([not is_crypto_perp(s) for s in panel.symbols])
    rank = liquidity_rank(panel.times, panel.quote_volume, panel.close, DAY_MS, exclude=excl)
    panel = Panel(**{**panel.__dict__, "rank": rank})
    out = Path(args.data_dir) / "research" / "daily.npz"
    panel.save(out)
    log.info("daily panel: %d days x %d symbols -> %s", panel.n_bars, panel.n_symbols, out)


def stage_hourly(args: argparse.Namespace) -> None:
    cache = Path(args.data_dir) / "raw"
    daily = Panel.load(Path(args.data_dir) / "research" / "daily.npz")
    ever = ((daily.rank > 0) & (daily.rank <= args.top)).any(axis=0)
    symbols = [s for s, e in zip(daily.symbols, ever) if e]
    log.info("%d symbols were ever in the top %d", len(symbols), args.top)

    def one(sym: str):
        k = archive.download_klines(sym, "1h", HOUR_MS, cache, end_month=args.end)
        f = archive.download_funding(sym, cache, end_month=args.end)
        return sym, k, f

    t0 = time.time()
    res = archive.parallel_map(one, symbols, workers=args.workers, progress="hourly klines")
    log.info("downloaded hourly klines + funding for %d symbols in %.0fs", len(res), time.time() - t0)
    klines = {s: k for s, k, _ in res}
    funding = {s: f for s, _, f in res}
    panel = build_panel(HOUR_MS, klines, funding, end_ms=month_end_ms(args.end))
    rank = liquidity_rank(panel.times, panel.quote_volume, panel.close, HOUR_MS)
    panel = Panel(**{**panel.__dict__, "rank": rank})
    out = Path(args.data_dir) / "research" / "hourly.npz"
    panel.save(out)
    meta = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "data.binance.vision USD-M perpetual monthly archives (MD5/SHA-256 verified)",
        "top": args.top, "end_month": args.end, "symbols": list(panel.symbols),
        "bars": panel.n_bars, "first": int(panel.times[0]), "last": int(panel.times[-1]),
    }
    (out.parent / "hourly.meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    log.info("hourly panel: %d bars x %d symbols -> %s", panel.n_bars, panel.n_symbols, out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["daily", "hourly"])
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--end", default="2026-10", help="last month to include (YYYY-MM)")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    {"daily": stage_daily, "hourly": stage_hourly}[args.stage](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
