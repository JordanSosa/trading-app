"""Stage 3 of the protocol, on DEV with the full engine: stops, risk governor, risk level.

Usage: python scripts/research_risk.py {stops|governor|levels|all}

Loads bars before 2025-01-01 only. The strategy is the frozen blend B0.
Writes research/results/risk_<stage>_dev.json.
"""

from __future__ import annotations

import argparse
import dataclasses
import time

import numpy as np

from rlib import PERIODS, ROOT, clip_panel, dump, ebrief, load_panel
from compounder.backtest import run_backtest
from compounder.engine import EngineParams
from compounder.metrics import ruin_report
from compounder.strategy import StrategyParams, compute_targets

B0 = StrategyParams(universe_n=20, smooth_bars=12, xsmom_weight=1.0, xsmom_lookback=336, short_scale=0.5)
# Everything off: the reference the governor rules are compared against.
OFF = EngineParams(daily_target=0.02, target_mode="off", daily_loss_limit=None, dd_soft=1.0, dd_hard=1.0,
                   kill_dd=None, max_gross=10.0, max_weight=3.0)
KEEP = ("compound_daily", "mean_daily", "cagr", "sharpe", "daily_vol", "max_drawdown", "engine_max_drawdown",
        "pct_days_at_target", "pct_days_up", "worst_day", "best_day", "avg_gross", "max_gross", "trades", "stops",
        "turnover_per_day", "fees_paid", "funding_paid", "days_target_hit", "days_loss_limit", "killed",
        "liquidated", "final_equity", "by_year", "longest_drawdown_days")


def bt(panel, weights, stop_frac, ep: EngineParams, equity: float = 10_000.0, period: str = "dev"):
    return run_backtest(panel, weights, ep, equity, *PERIODS[period], stop_frac=stop_frac, record=True)


def row(res, extra: dict | None = None) -> dict:
    out = {k: res.metrics.get(k) for k in KEEP}
    out.update(extra or {})
    return out


def stage_stops(panel, out: dict) -> None:
    rows = {}
    for mult in (0.0, 2.0, 3.0, 4.0):
        tg = compute_targets(panel, dataclasses.replace(B0, stop_vol_mult=mult))
        res = bt(panel, tg.weights, tg.stop_frac, OFF)
        label = "no stops" if mult == 0 else f"stop at {mult:g} daily sigmas"
        rows[label] = row(res)
        print(f"[stops] {label:28s} {ebrief(res.metrics)}", flush=True)
    for over in (0.5, 1.0):
        tg = compute_targets(panel, B0)
        res = bt(panel, tg.weights, tg.stop_frac, dataclasses.replace(OFF, stop_overshoot=over))
        label = f"3 sigma, stop overshoot {over:g}"
        rows[label] = row(res)
        print(f"[stops] {label:28s} {ebrief(res.metrics)}", flush=True)
    for mult in (3.0, 4.0):
        tg = compute_targets(panel, dataclasses.replace(B0, stop_vol_mult=mult))
        for cool in (24, 72):
            res = bt(panel, tg.weights, tg.stop_frac, dataclasses.replace(OFF, stop_cooldown_bars=cool))
            label = f"{mult:g} sigma, re-entry after {cool}h"
            rows[label] = row(res)
            print(f"[stops] {label:28s} {ebrief(res.metrics)}", flush=True)
    out["stops"] = rows


def stage_governor(panel, out: dict, stop_mult: float) -> None:
    tg = compute_targets(panel, dataclasses.replace(B0, stop_vol_mult=stop_mult))
    rows = {}
    variants: list[tuple[str, dict]] = [("governor off", {})]
    variants += [(f"target lock: {mode}", {"target_mode": mode}) for mode in ("flatten", "trail")]
    variants += [(f"target lock: trail, giveback {g:g}", {"target_mode": "trail", "trail_giveback": g})
                 for g in (0.25, 0.75)]
    variants += [(f"target lock: flatten at {t:.0%}", {"target_mode": "flatten", "daily_target": t})
                 for t in (0.03, 0.05)]
    variants += [(f"daily loss limit {x:.0%}", {"daily_loss_limit": x}) for x in (0.02, 0.03, 0.05)]
    variants += [("drawdown scaling 10%-30% to 0.25x", {"dd_soft": 0.10, "dd_hard": 0.30, "min_risk_mult": 0.25}),
                 ("drawdown scaling 15%-35% to 0.5x", {"dd_soft": 0.15, "dd_hard": 0.35, "min_risk_mult": 0.5}),
                 ("drawdown scaling, 180-day peak", {"dd_soft": 0.10, "dd_hard": 0.30, "min_risk_mult": 0.25,
                                                    "hwm_window_days": 180.0}),
                 ("kill switch at 40%", {"kill_dd": 0.40})]
    for label, kw in variants:
        res = bt(panel, tg.weights, tg.stop_frac, dataclasses.replace(OFF, **kw))
        rows[label] = row(res)
        print(f"[governor] {label:36s} {ebrief(res.metrics)}", flush=True)
    out["governor"] = rows


def stage_levels(panel, out: dict, stop_mult: float, gov: dict) -> None:
    tg = compute_targets(panel, dataclasses.replace(B0, stop_vol_mult=stop_mult))
    rows = {}
    for tv in (0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.16, 0.20):
        ep = dataclasses.replace(OFF, kill_dd=0.40, **gov)
        res = bt(panel, tg.weights * (tv / B0.target_vol), tg.stop_frac, ep)
        extra = {}
        if len(res.daily_returns) > 100 and not res.metrics["liquidated"]:
            rr = ruin_report(res.daily_returns, 365, levels=(0.2, 0.35, 0.5), n_paths=4000)
            extra = {"p_dd50_1y": rr["prob_drawdown"]["50%"], "p_dd35_1y": rr["prob_drawdown"]["35%"],
                     "p_loss_1y": rr["prob_loss"], "median_multiple_1y": rr["final_multiple"]["median"],
                     "p05_multiple_1y": rr["final_multiple"]["p05"]}
        rows[f"{tv:.3f}"] = row(res, extra)
        print(f"[levels] target vol {tv:6.3f}  {ebrief(res.metrics)}  "
              f"P(dd50) {extra.get('p_dd50_1y', float('nan')):.3f}", flush=True)
    out["levels"] = rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["stops", "governor", "levels", "all"])
    ap.add_argument("--stop-mult", type=float, default=3.0)
    ap.add_argument("--gov", default="{}", help="JSON of governor settings for the levels stage")
    args = ap.parse_args()
    import json
    panel = clip_panel(load_panel(), "2025-01-01")
    out: dict = {"strategy": B0.to_dict(), "period": "dev"}
    t0 = time.time()
    if args.stage in ("stops", "all"):
        stage_stops(panel, out)
    if args.stage in ("governor", "all"):
        stage_governor(panel, out, args.stop_mult)
    if args.stage in ("levels", "all"):
        stage_levels(panel, out, args.stop_mult, json.loads(args.gov))
    print(f"done in {time.time() - t0:.0f}s")
    dump(ROOT / "research" / "results" / f"risk_{args.stage}_dev.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
