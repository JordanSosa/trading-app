"""Stage 2 of the protocol: sweep sizing and turnover controls around a base configuration.

Usage examples (TRAIN only unless --period says otherwise):

  python scripts/research_sweep.py --name turnover --base '{"universe_n": 20}' \
      --grid '{"rebalance_every": [1, 4, 8, 24], "smooth_bars": [1, 12, 24]}'
  python scripts/research_sweep.py --name band --base '{...}' --bands 0.1,0.25,0.5

Every combination of the grid values is evaluated with the fast linear backtest.
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import time

from rlib import BAND, COST, ROOT, brief, clip_panel, dump, load_panel, quick_eval
from compounder.strategy import StrategyParams


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--base", default="{}", help="JSON of StrategyParams overrides")
    ap.add_argument("--grid", default="{}", help="JSON mapping parameter -> list of values")
    ap.add_argument("--bands", default=str(BAND), help="comma-separated no-trade bands")
    ap.add_argument("--period", default="train", choices=["train", "valid", "dev"])
    ap.add_argument("--cost", type=float, default=COST)
    args = ap.parse_args()
    end = "2024-01-01" if args.period == "train" else "2025-01-01"
    panel = clip_panel(load_panel(), end)
    base = json.loads(args.base)
    grid = json.loads(args.grid)
    bands = [float(x) for x in args.bands.split(",")]
    keys = list(grid)
    rows = []
    t0 = time.time()
    for combo in itertools.product(*(grid[k] for k in keys)):
        over = dict(zip(keys, combo))
        kw = {**base, **over}
        if "trend_horizons" in kw:
            kw["trend_horizons"] = tuple(kw["trend_horizons"])
        p = StrategyParams(**kw)
        for band in bands:
            st = quick_eval(panel, p, args.period, cost=args.cost, band=band)
            label = " ".join(f"{k}={v}" for k, v in over.items()) + (f" band={band}" if len(bands) > 1 else "")
            rows.append({"label": label, "overrides": over, "band": band, "params": p.to_dict(),
                         **{k: st[k] for k in ("sharpe", "cagr", "daily_vol", "max_drawdown", "avg_gross",
                                               "turnover_per_day", "compound_daily", "pct_days_at_target")}})
            print(f"{label:70s} {brief(st)}", flush=True)
    print(f"== {args.name}: {len(rows)} runs in {time.time() - t0:.0f}s on {args.period}")
    dump(ROOT / "research" / "results" / f"sweep_{args.name}_{args.period}.json",
         {"base": base, "grid": grid, "bands": bands, "period": args.period, "cost": args.cost, "rows": rows})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
