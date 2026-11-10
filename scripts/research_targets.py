"""What does a realistic target look like? The frozen strategy at lower risk settings.

Only the daily volatility target changes: 0.5%, 0.75%, 1.0% and the default
1.5%. Each setting is run through the engine from 1,000 and from 10,000 USDT
over DEV and HOLDOUT, and a year ahead is resampled from its out-of-sample
days (10-day blocks, 4,000 paths), as in section 4 of RESULTS.md.

Run after the holdout had been seen: descriptive, nothing is selected. The
10,000 USDT rows repeat the risk ladder of section 2.

Usage: python scripts/research_targets.py
Writes research/results/targets.json.
"""

from __future__ import annotations

import dataclasses

from rlib import PERIODS, ROOT, dump, load_panel
from compounder.backtest import run_backtest
from compounder.config import load_config
from compounder.metrics import ruin_report
from compounder.strategy import compute_targets

LEVELS = (0.005, 0.0075, 0.010, 0.015)
KEEP = ("compound_daily", "total_return", "cagr", "sharpe", "daily_vol", "engine_max_drawdown", "worst_day", "avg_gross",
        "turnover_per_day", "trades", "stops", "killed", "liquidated", "fees_paid", "by_year")


def main() -> int:
    cfg = load_config(ROOT / "config" / "paper.toml")
    panel = load_panel()
    ep = cfg.engine_params()
    out: dict = {"note": "Run after the holdout was seen. Only the volatility target changes; nothing is selected.",
                 "levels": {}}
    for level in LEVELS:
        tg = compute_targets(panel, dataclasses.replace(cfg.strategy, target_vol=level))
        block = {}
        for equity in (1_000.0, 10_000.0):
            row = {}
            for period in ("dev", "holdout"):
                res = run_backtest(panel, tg.weights, ep, equity, *PERIODS[period], stop_frac=tg.stop_frac, record=True)
                row[period] = {k: res.metrics.get(k) for k in KEEP}
                if period == "holdout":
                    row["year_ahead"] = ruin_report(res.daily_returns, 365, levels=(0.1, 0.2, 0.3), n_paths=4000)
            block[f"{equity:,.0f} USDT"] = row
            d, h, y = row["dev"], row["holdout"], row["year_ahead"]
            print(f"target {level:.2%}, {equity:>7,.0f} USDT | DEV {d['cagr']:+6.1%}/yr mdd {d['engine_max_drawdown']:5.1%} | "
                  f"HOLDOUT {h['cagr']:+6.1%}/yr total {h['total_return']:+6.1%} sharpe {h['sharpe']:4.2f} mdd "
                  f"{h['engine_max_drawdown']:5.1%} worst day {h['worst_day']:+.1%} | a year ahead: median "
                  f"x{y['final_multiple']['median']:.2f}, 5% to 95% x{y['final_multiple']['p05']:.2f} to "
                  f"x{y['final_multiple']['p95']:.2f}, P(loss) {y['prob_loss']:.0%}, P(drawdown >= 10% / 20% / 30%) "
                  + " / ".join(f"{v:.0%}" for v in y["prob_drawdown"].values()), flush=True)
        out["levels"][f"{level:.2%}"] = block
    dump(ROOT / "research" / "results" / "targets.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
