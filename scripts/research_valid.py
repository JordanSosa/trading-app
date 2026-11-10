"""The single VALID check of the frozen candidates (see research/CANDIDATES.md).

Loads bars before 2025-01-01 only. Reports TRAIN, VALID and DEV for T0, X0
and B0 with the fast linear backtest, at normal and doubled costs.
"""

from __future__ import annotations

from rlib import COST, ROOT, brief, clip_panel, dump, load_panel, quick_eval
from compounder.strategy import StrategyParams, compute_targets

COMMON = dict(universe_n=20, smooth_bars=12)
CANDIDATES = {
    "T0": StrategyParams(**COMMON, short_scale=0.5),
    "X0": StrategyParams(**COMMON, trend_weight=0.0, xsmom_weight=1.0, xsmom_lookback=336, short_scale=1.0),
    "B0": StrategyParams(**COMMON, xsmom_weight=1.0, xsmom_lookback=336, short_scale=0.5),
}


def main() -> int:
    panel = clip_panel(load_panel(), "2025-01-01")
    out = {}
    for name, p in CANDIDATES.items():
        tg = compute_targets(panel, p)
        out[name] = {"params": p.to_dict()}
        for period in ("train", "valid", "dev"):
            for mult in (1.0, 2.0):
                st = quick_eval(panel, p, period, cost=COST * mult, targets=tg)
                key = period + ("" if mult == 1.0 else "_2x_cost")
                out[name][key] = {k: st[k] for k in ("sharpe", "cagr", "daily_vol", "max_drawdown", "avg_gross",
                                                     "turnover_per_day", "compound_daily", "pct_days_at_target",
                                                     "worst_day")}
                print(f"{name} {key:16s} {brief(st)}", flush=True)
    dump(ROOT / "research" / "results" / "valid_check.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
