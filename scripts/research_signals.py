"""Stage 1 of the protocol: compare signal families on TRAIN with the fast linear backtest.

Usage: python scripts/research_signals.py {trend|carry|reversal|xsmom|all} [--period train]

Only bars before 2024-01-01 are loaded for ``--period train`` (2025-01-01 for
valid/dev), so later data cannot leak into the comparison.
"""

from __future__ import annotations

import argparse
import itertools
import time

import numpy as np

from rlib import ROOT, brief, clip_panel, dump, load_panel, quick_eval
from compounder.strategy import StrategyParams

HORIZONS = {
    "1-4d": (24, 48, 96),
    "2-32d": (48, 96, 192, 384, 768),
    "4-16d": (96, 192, 384),
    "8-32d": (192, 384, 768),
}


def grid_trend():
    for (hname, hz), resp, n, ss in itertools.product(HORIZONS.items(), ("linear", "sign"), (10, 20, 30),
                                                      (0.0, 0.5, 1.0)):
        yield (f"trend h={hname} resp={resp} N={n} short={ss}",
               StrategyParams(universe_n=n, trend_horizons=hz, trend_response=resp, short_scale=ss))


def grid_carry():
    for win, n in itertools.product((72, 168, 336), (20, 30)):
        yield (f"carry win={win} N={n}",
               StrategyParams(universe_n=n, trend_weight=0.0, carry_weight=1.0, carry_window=win))


def grid_reversal():
    for lb, z, hold, filt in itertools.product((12, 24, 48), (1.5, 2.0, 2.5), (24, 48), (768, 0)):
        yield (f"reversal lb={lb} z={z} hold={hold} trendfilter={'on' if filt else 'off'}",
               StrategyParams(universe_n=20, trend_weight=0.0, reversal_weight=1.0, reversal_lookback=lb,
                              reversal_z=z, reversal_hold=hold, reversal_trend=filt))


def grid_xsmom():
    for lb, n in itertools.product((72, 168, 336, 720), (20, 30)):
        yield (f"xsmom lb={lb} N={n}",
               StrategyParams(universe_n=n, trend_weight=0.0, xsmom_weight=1.0, xsmom_lookback=lb))


GRIDS = {"trend": grid_trend, "carry": grid_carry, "reversal": grid_reversal, "xsmom": grid_xsmom}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("family", choices=list(GRIDS) + ["all"])
    ap.add_argument("--period", default="train", choices=["train", "valid", "dev"])
    args = ap.parse_args()
    end = "2024-01-01" if args.period == "train" else "2025-01-01"
    panel = clip_panel(load_panel(), end)
    print(f"panel: {panel.n_bars} bars x {panel.n_symbols} symbols, period={args.period}")
    fams = list(GRIDS) if args.family == "all" else [args.family]
    for fam in fams:
        rows = []
        t0 = time.time()
        for name, p in GRIDS[fam]():
            st = quick_eval(panel, p, args.period)
            st0 = quick_eval(panel, p, args.period, cost=0.0)
            rows.append({"name": name, "params": p.to_dict(), "sharpe": st["sharpe"], "sharpe_gross": st0["sharpe"],
                         "cagr": st["cagr"], "daily_vol": st["daily_vol"], "max_drawdown": st["max_drawdown"],
                         "avg_gross": st["avg_gross"], "turnover_per_day": st["turnover_per_day"]})
            print(f"{name:58s} {brief(st)}  gross-of-cost sharpe {st0['sharpe']:5.2f}", flush=True)
        sh = np.array([r["sharpe"] for r in rows])
        print(f"== {fam}: {len(rows)} configs in {time.time() - t0:.0f}s; sharpe mean {sh.mean():.2f} "
              f"median {np.median(sh):.2f} best {sh.max():.2f} worst {sh.min():.2f} positive {np.mean(sh > 0):.0%}\n")
        dump(ROOT / "research" / "results" / f"signals_{fam}_{args.period}.json", rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
