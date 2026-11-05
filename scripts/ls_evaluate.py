"""Evaluate a strategy under the long/short protocol (research/LONG_SHORT_PROTOCOL.md).

Usage:
  python scripts/ls_evaluate.py NAME [--params JSON] [--max-short 1.0] [--no-robust] [--json out.json]

Uses only data up to 2024-12-31 (data/research/). Prints every adoption-rule
metric against the deployed baseline (breakout_dual on spot).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from functools import lru_cache

import numpy as np

from tradebot.backtest import run_backtest, run_campaigns
from tradebot.config import config_from_dict, load_config
from tradebot.data.history import load_funding, load_history
from tradebot.metrics import campaign_summary
from tradebot.research import PERIODS, campaign_score, paired_block_t, param_variants
from tradebot.strategies import get_strategy

CANDLES = "data/research/BTCUSDT-1h-research.csv"
FUNDING = "data/research/BTCUSDT-funding-research.csv"
BASELINE_ROBUST = 0.03915
BASELINE_P05 = -0.0836
YEARS = range(2018, 2025)


def perp_cfg(max_short: float, mode: str = "perp"):
    """mode "perp": everything on perpetuals; "hybrid": longs on spot, shorts on perps."""
    if mode == "perp":
        return config_from_dict({"run": {"instrument": "perp"}, "risk": {"max_short": max_short}})
    return config_from_dict({"run": {"instrument": "hybrid"}, "risk": {"max_short": max_short}})


@lru_cache(maxsize=1)
def data():
    return load_history(CANDLES), load_funding(FUNDING)


@lru_cache(maxsize=1)
def baseline_campaigns():
    df, _ = data()
    s = get_strategy("breakout_dual")
    start, end = PERIODS["dev"]
    return run_campaigns(df, s, load_config("config/paper.toml"), start, end)


def dev_campaigns(strat, cfg):
    df, fund = data()
    start, end = PERIODS["dev"]
    return run_campaigns(df, strat, cfg, start, end, funding=fund)


def evaluate(name: str, params: dict | None, max_short: float, robust: bool = True, mode: str = "perp") -> dict:
    df, fund = data()
    cfg = perp_cfg(max_short, mode)
    strat = get_strategy(name, params or {})
    t0 = time.time()
    camp = dev_campaigns(strat, cfg)
    base = baseline_campaigns()
    summ = campaign_summary(camp)
    years = {y: campaign_score(camp[camp["start"].dt.year == y]) for y in YEARS}
    bt = run_backtest(df, strat, cfg, *PERIODS["dev"], funding=fund)
    paired = paired_block_t(camp, base)
    out = {
        "strategy": strat.describe(), "max_short": max_short, "mode": mode,
        "dev_score": campaign_score(camp), "baseline_dev_score": campaign_score(base),
        "campaigns": summ, "per_year": years,
        "years_positive": int(sum(v > 0 for v in years.values())),
        "paired_vs_baseline": paired,
        "continuous": {k: bt.metrics.get(k) for k in ("total_return", "cagr", "sharpe", "max_drawdown",
                                                      "n_trades", "round_trips", "time_in_market")},
        "funding_paid_continuous": float(bt.state.funding_paid),
        "short_time_share": float((bt.equity["exposure"] < -0.01).mean()) if len(bt.equity) else 0.0,
    }
    if robust and not params:
        scores = {}
        for label, p in param_variants(name):
            scores[label] = campaign_score(dev_campaigns(get_strategy(name, p), cfg))
        cfg_nocb = dataclasses.replace(cfg, risk=dataclasses.replace(cfg.risk, circuit_breaker_dd=0.99))
        scores["no_circuit_breaker"] = campaign_score(dev_campaigns(strat, cfg_nocb))
        vals = np.array(list(scores.values()))
        out["robust"] = {"mean": float(vals.mean()), "min": float(vals.min()), "n": int(len(vals)),
                         "scores": scores}
    rb = out.get("robust", {}).get("mean")
    out["rule"] = {
        "1_robust_plus_0.005": None if rb is None else bool(rb >= BASELINE_ROBUST + 0.005),
        "2_paired_t_ge_2": bool(paired["t"] >= 2.0),
        "3_years_positive_ge_5": bool(out["years_positive"] >= 5),
        "4_p05_within_1pp": bool(summ["p05"] >= BASELINE_P05 - 0.01),
    }
    out["seconds"] = round(time.time() - t0, 1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--params", default=None)
    ap.add_argument("--max-short", type=float, default=1.0)
    ap.add_argument("--no-robust", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--mode", choices=["perp", "hybrid"], default="perp")
    a = ap.parse_args()
    res = evaluate(a.name, json.loads(a.params) if a.params else None, a.max_short, robust=not a.no_robust,
                   mode=a.mode)
    c, pr = res["campaigns"], res["paired_vs_baseline"]
    print(f"{res['strategy']}  (max_short={a.max_short}, mode={a.mode})")
    print(f"  DEV score {res['dev_score']:+.4f} (baseline spot {res['baseline_dev_score']:+.4f}); "
          f"robust {res.get('robust', {}).get('mean', float('nan')):+.4f}")
    print(f"  30d: win {c['pct_positive']:.1%} lose {c['pct_negative']:.1%} mean {c['mean']:+.2%} "
          f"median {c['median']:+.2%} p05 {c['p05']:+.2%} worst {c['min']:+.2%}")
    print(f"  paired vs baseline: mean diff {pr['mean_diff']:+.4f} se {pr['se']:.4f} t {pr['t']:+.2f}")
    print(f"  years: " + " ".join(f"{y}:{v:+.3f}" for y, v in res["per_year"].items()))
    k = res["continuous"]
    print(f"  continuous: CAGR {k['cagr']:+.1%} Sharpe {k['sharpe']:.2f} maxDD {k['max_drawdown']:.1%} "
          f"trades {k['n_trades']} short-time {res['short_time_share']:.0%} funding paid {res['funding_paid_continuous']:+,.0f}")
    print(f"  rule: {res['rule']}")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
