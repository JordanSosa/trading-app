"""Write the research reference that the status page's self-audit judges a run against.

The frozen default through the app's engine from 1,000 USDT over the holdout
(2025-01-01 to 2026-10-31, out of sample): its daily returns, how much it
traded and what that cost. ``compounder.audit`` resamples these days to say
what is normal for a run of any length.

The reference is tied to the configuration by a hash. Re-run this script
whenever config/paper.toml changes; tests/test_audit.py fails until then.

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


def main() -> int:
    cfg = load_config(ROOT / "config" / "paper.toml")
    panel = load_panel()
    ep = cfg.engine_params()
    tg = compute_targets(panel, cfg.strategy)
    lo, hi = PERIODS["holdout"]
    res = run_backtest(panel, tg.weights, ep, EQUITY, lo, hi, stop_frac=tg.stop_frac, record=True)
    m = res.metrics
    turnover = float(m["turnover_per_day"])
    ref = {
        "basis": "frozen default out of sample (2025-01 to 2026-10, from 1,000 USDT)",
        "key": reference_key(cfg),
        "period": [lo, hi], "initial_equity": EQUITY, "days": int(m["days"]),
        "total_return": round(float(m["total_return"]), 6), "sharpe": round(float(m["sharpe"]), 4),
        "mean_daily": round(float(m["mean_daily"]), 8), "daily_vol": round(float(m["daily_vol"]), 8),
        "max_drawdown": round(float(m["engine_max_drawdown"]), 6),
        "turnover_per_day": round(turnover, 6),
        # Fees and slippage on the notional traded each day, as a share of equity.
        "fee_per_day": round(turnover * ep.fee_rate, 8),
        "trading_cost_per_day": round(turnover * (ep.fee_rate + ep.slippage), 8),
        "daily_returns": [round(float(x), 6) for x in res.daily_returns],
    }
    REFERENCE_FILE.write_text(json.dumps(ref, separators=(",", ":")) + "\n")
    total = float(np.prod(1.0 + np.asarray(ref["daily_returns"])) - 1.0)
    print(f"written {REFERENCE_FILE.relative_to(ROOT)}: {ref['days']} days, total {ref['total_return']:+.2%} "
          f"(rounded days compound to {total:+.2%}), sharpe {ref['sharpe']:.2f}, turnover {turnover:.3f}/day, "
          f"trading costs {ref['trading_cost_per_day']:.4%}/day, key {ref['key']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
