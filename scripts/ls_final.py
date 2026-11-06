"""Final long/short comparison after the adoption decision.

Reports DEV (2018-2024), VALID (2024) and the 2025-26 period for the deployed
long-only strategy and the long/short candidate(s). The 2025-26 numbers are
NOT a clean test for short strategies: that period is already known to be a
36% decline. They are reported for completeness and cannot change the decision.

Usage: python scripts/ls_final.py --data DIR --candidate NAME:MAX_SHORT[:MODE] [...]
"""

from __future__ import annotations

import argparse
import json

import numpy as np

from tradebot.backtest import run_backtest, run_campaigns
from tradebot.config import config_from_dict, load_config
from tradebot.data.history import load_funding, load_history
from tradebot.metrics import campaign_summary
from tradebot.research import PERIODS, campaign_score, paired_block_t
from tradebot.strategies import get_strategy


def cfg_for(mode: str, max_short: float):
    if mode == "spot":
        return load_config("config/paper.toml")
    return config_from_dict({"run": {"instrument": mode}, "risk": {"max_short": max_short}})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="dir with BTCUSDT-1h.csv and BTCUSDT-funding.csv (full history)")
    ap.add_argument("--candidate", action="append", required=True)
    ap.add_argument("--out", default="research/ls_final.json")
    a = ap.parse_args()
    df = load_history(f"{a.data}/BTCUSDT-1h.csv")
    fund = load_funding(f"{a.data}/BTCUSDT-funding.csv")
    specs = [("breakout_dual", 0.0, "spot")]
    for c in a.candidate:
        parts = c.split(":")
        specs.append((parts[0], float(parts[1]), parts[2] if len(parts) > 2 else "hybrid"))
    out = {}
    base_camps = {}
    for name, ms, mode in specs:
        s = get_strategy(name)
        sig = s.signals(df)
        cfg = cfg_for(mode, ms)
        row = {}
        for per in ("dev", "valid", "holdout"):
            start, end = PERIODS[per]
            camp = run_campaigns(df, s, cfg, start, end, signals=sig, funding=fund)
            bt = run_backtest(df, s, cfg, start, end, signals=sig, funding=fund)
            summ = campaign_summary(camp)
            r = {"score": campaign_score(camp), "mean": summ["mean"], "median": summ["median"],
                 "win": summ["pct_positive"], "lose": summ["pct_negative"], "p05": summ["p05"], "worst": summ["min"],
                 "total_return": bt.metrics["total_return"], "max_dd": bt.metrics["max_drawdown"],
                 "funding_paid": float(bt.state.funding_paid),
                 "short_time": float((bt.equity["exposure"] < -0.01).mean())}
            if name == "breakout_dual" and mode == "spot":
                base_camps[per] = camp
            else:
                r["paired_vs_deployed"] = paired_block_t(camp, base_camps[per])
            row[per] = r
        key = f"{name}[{mode},max_short={ms}]"
        out[key] = row
        for per, r in row.items():
            pt = r.get("paired_vs_deployed", {}).get("t")
            print(f"{key:44s} {per:8s} score {r['score']:+.4f} mean30 {r['mean']:+.2%} win {r['win']:.0%} "
                  f"lose {r['lose']:.0%} p05 {r['p05']:+.2%} | total {r['total_return']:+.1%} maxDD {r['max_dd']:.1%} "
                  f"short {r['short_time']:.0%}" + (f" | paired t {pt:+.2f}" if pt is not None else ""))
    with open(a.out, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
