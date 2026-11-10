"""The single HOLDOUT evaluation of the frozen configuration (research/SELECTION.md, section 6).

Runs the full engine on 2025-01-01 .. 2026-10-31 for everything declared in
advance and writes research/results/holdout.json. Not to be re-run with
different settings.
"""

from __future__ import annotations

import dataclasses
import sys

import numpy as np

from rlib import PERIODS, ROOT, dump, ebrief, load_panel
from compounder.backtest import ms, run_backtest
from compounder.config import load_config
from compounder.metrics import daily_stats, ruin_report
from compounder.strategy import compute_targets

KEEP = ("days", "compound_daily", "mean_daily", "total_return", "cagr", "sharpe", "sortino", "daily_vol",
        "max_drawdown", "engine_max_drawdown", "pct_days_at_target", "pct_days_below_minus_target", "pct_days_up",
        "pct_days_down", "best_day",
        "worst_day", "p05_day", "p95_day", "avg_gross", "avg_net", "max_gross", "trades", "stops", "turnover_per_day",
        "fees_paid", "funding_paid", "days_target_hit", "days_loss_limit", "killed", "liquidated", "initial_equity",
        "final_equity", "by_year", "by_month", "longest_drawdown_days", "avg_risk_mult")


def btc_buy_and_hold(panel, lo: str, hi: str) -> dict:
    """Context only: BTC bought at the first bar's open and held to the last close, no costs.

    Statistics are on UTC-day returns, the first day running from that first open.
    """
    j = panel.index_of("BTCUSDT")
    sel = (panel.times >= ms(lo)) & (panel.times < ms(hi))
    close_ms = panel.times[sel] + panel.interval_ms
    close = panel.close[sel, j]
    at_midnight = np.nonzero(close_ms % 86_400_000 == 0)[0]
    px = np.concatenate([[panel.open[sel, j][0]], close[at_midnight]])
    bh = daily_stats(px[1:] / px[:-1] - 1.0)
    return {k: bh[k] for k in ("total_return", "cagr", "compound_daily", "sharpe", "daily_vol", "max_drawdown",
                               "worst_day", "pct_days_at_target")}


def main() -> int:
    period = sys.argv[1] if len(sys.argv) > 1 else "holdout"
    if "--benchmark-only" in sys.argv:
        # Recompute only the BTC context block of an existing result file; no strategy run.
        path = ROOT / "research" / "results" / f"{period}.json"
        import json
        out = json.loads(path.read_text())
        out["btc_buy_and_hold"] = btc_buy_and_hold(load_panel(), *PERIODS[period])
        dump(path, out)
        print(period, out["btc_buy_and_hold"])
        return 0
    cfg = load_config(ROOT / "config" / "research.toml")
    spot = load_config(ROOT / "config" / "spot.toml")
    panel = load_panel()
    lo, hi = PERIODS[period]
    ep = cfg.engine_params()
    strat = cfg.strategy
    tg = compute_targets(panel, strat)
    out: dict = {"period": [lo, hi], "strategy": strat.to_dict(), "engine": dataclasses.asdict(ep), "runs": {}}

    def run(label: str, weights, stop_frac, params, equity: float = 10_000.0, ruin: bool = False):
        res = run_backtest(panel, weights, params, equity, lo, hi, stop_frac=stop_frac, record=True)
        row = {k: res.metrics.get(k) for k in KEEP}
        if ruin and len(res.daily_returns) > 100:
            row["bootstrap_1y"] = ruin_report(res.daily_returns, 365, levels=(0.2, 0.35, 0.5), n_paths=4000)
        out["runs"][label] = row
        print(f"{label:44s} {ebrief(res.metrics)}", flush=True)
        return res

    base_tv = strat.target_vol
    primary = run("default, 10,000 USDT (PRIMARY)", tg.weights, tg.stop_frac, ep, ruin=True)
    print("   by year:", {k: round(v, 4) for k, v in primary.metrics["by_year"].items()})
    print("   by month:", {k: round(v, 3) for k, v in primary.metrics["by_month"].items()})
    run("default, 100 USDT", tg.weights, tg.stop_frac, ep, equity=100.0)
    run("default, 1,000 USDT", tg.weights, tg.stop_frac, ep, equity=1000.0)
    aggr = tg.weights * (0.03 / base_tv)
    run("aggressive 3%, 10,000 USDT", aggr, tg.stop_frac, ep, ruin=True)
    run("aggressive 3%, 100 USDT", aggr, tg.stop_frac, ep, equity=100.0)
    run("default, doubled costs", tg.weights, tg.stop_frac,
        dataclasses.replace(ep, fee_rate=ep.fee_rate * 2, slippage=ep.slippage * 2))
    run("default, stops fill at the bar extreme", tg.weights, tg.stop_frac, dataclasses.replace(ep, stop_overshoot=1.0))
    run("default, governor off", tg.weights, tg.stop_frac,
        dataclasses.replace(ep, daily_loss_limit=None, dd_soft=1.0, dd_hard=1.0, kill_dd=None))
    run("default, +2% profit lock (flatten)", tg.weights, tg.stop_frac, dataclasses.replace(ep, target_mode="flatten"))
    run("default, +2% profit lock (trail)", tg.weights, tg.stop_frac, dataclasses.replace(ep, target_mode="trail"))
    for tv in (0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.06, 0.08):
        run(f"risk ladder: target vol {tv:.3f}", tg.weights * (tv / base_tv), tg.stop_frac,
            dataclasses.replace(ep, max_gross=10.0, max_weight=3.0))
    t0 = compute_targets(panel, dataclasses.replace(strat, xsmom_weight=0.0))
    run("attribution: trend only (T0)", t0.weights, t0.stop_frac, ep)
    x0 = compute_targets(panel, dataclasses.replace(strat, trend_weight=0.0, short_scale=1.0))
    run("attribution: cross-sectional only (X0)", x0.weights, x0.stop_frac, ep)
    sp = compute_targets(panel, spot.strategy)
    run("spot preset (long or flat, no leverage)", sp.weights, sp.stop_frac, spot.engine_params())

    bh = btc_buy_and_hold(panel, lo, hi)
    out["btc_buy_and_hold"] = bh
    print(f"BTC buy and hold: total {bh['total_return']:+.1%}, max drawdown {bh['max_drawdown']:.1%}, "
          f"compound/d {bh['compound_daily']:+.3%}")
    dump(ROOT / "research" / "results" / f"{period}.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
