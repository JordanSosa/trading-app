"""Write the research references that the status page's self-audit judges a run against.

One reference per configuration that has an out-of-sample record: the shipped
default (config/paper.toml) and the research setting (config/research.toml).
Each is the strategy through the app's engine from 1,000 USDT over the holdout
(2025-01-01 to 2026-10-31): its daily returns, how much it traded and what
that cost. ``compounder.audit`` resamples those days to say what is normal for
a run of any length.

A reference is tied to its configuration by a hash. Re-run this script
whenever either file changes; tests/test_audit.py fails until then.

Usage: python scripts/make_reference.py
Writes src/compounder/reference.json.
"""

from __future__ import annotations

import json

import numpy as np

from rlib import PERIODS, ROOT, load_panel
from compounder.audit import REFERENCE_FILE, reference_key
from compounder.backtest import run_backtest
from compounder.config import load_config
from compounder.strategy import compute_targets

EQUITY = 1_000.0
CONFIGS = (("paper.toml", "shipped default out of sample (2025-01 to 2026-10, from 1,000 USDT)"),
           ("research.toml", "research setting, a 1.5% volatility target, out of sample (2025-01 to 2026-10, from 1,000 "
                             "USDT)"))


def reference(panel, name: str, basis: str) -> dict:
    cfg = load_config(ROOT / "config" / name)
    ep = cfg.engine_params()
    tg = compute_targets(panel, cfg.strategy)
    lo, hi = PERIODS["holdout"]
    res = run_backtest(panel, tg.weights, ep, EQUITY, lo, hi, stop_frac=tg.stop_frac, record=True)
    m = res.metrics
    turnover = float(m["turnover_per_day"])
    ref = {
        "basis": basis, "config": f"config/{name}", "key": reference_key(cfg), "target_vol": cfg.strategy.target_vol,
        "period": [lo, hi], "initial_equity": EQUITY, "days": int(m["days"]),
        "total_return": round(float(m["total_return"]), 6), "annual_return": round(float(m["cagr"]), 6),
        "sharpe": round(float(m["sharpe"]), 4), "mean_daily": round(float(m["mean_daily"]), 8),
        "daily_vol": round(float(m["daily_vol"]), 8), "max_drawdown": round(float(m["engine_max_drawdown"]), 6),
        "turnover_per_day": round(turnover, 6),
        # Fees and slippage on the amount traded each day, as a share of equity.
        "fee_per_day": round(turnover * ep.fee_rate, 8),
        "trading_cost_per_day": round(turnover * (ep.fee_rate + ep.slippage), 8),
        "daily_returns": [round(float(x), 6) for x in res.daily_returns],
    }
    total = float(np.prod(1.0 + np.asarray(ref["daily_returns"])) - 1.0)
    print(f"{name}: {ref['days']} days, total {ref['total_return']:+.2%} (rounded days compound to {total:+.2%}), "
          f"{ref['annual_return']:+.1%} a year, sharpe {ref['sharpe']:.2f}, max drawdown {ref['max_drawdown']:.1%}, turnover "
          f"{turnover:.3f}/day, trading costs {ref['trading_cost_per_day']:.4%}/day, key {ref['key']}")
    return ref


def main() -> int:
    panel = load_panel()
    refs = [reference(panel, name, basis) for name, basis in CONFIGS]
    REFERENCE_FILE.write_text(json.dumps({"references": refs}, separators=(",", ":")) + "\n")
    print(f"written {REFERENCE_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
