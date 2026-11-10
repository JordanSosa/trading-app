"""Stage 2 on TRAIN: placebo checks, sizing variants, other families and blends.

Reproduces every number quoted in research/CANDIDATES.md and writes
research/results/stage2_train.json. Fast linear backtest, 8 bps a side,
25% no-trade band, bars before 2024-01-01 only.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from rlib import COST, PERIODS, ROOT, brief, clip_panel, dump, load_panel, quick_eval
from compounder.backtest import run_backtest
from compounder.engine import EngineParams
from compounder.strategy import StrategyParams, compute_targets

TREND = dict(universe_n=20, short_scale=0.5, smooth_bars=12)
OUT: dict[str, dict] = {}


def run(panel, section: str, label: str, cost: float = COST, band: float = 0.25, targets=None, **kw) -> dict:
    p = StrategyParams(**{**TREND, **kw})
    st = quick_eval(panel, p, "train", cost=cost, band=band, targets=targets)
    keep = {k: st[k] for k in ("sharpe", "cagr", "daily_vol", "max_drawdown", "avg_gross", "turnover_per_day",
                               "compound_daily", "pct_days_at_target")}
    OUT.setdefault(section, {})[label] = keep
    print(f"[{section}] {label:56s} {brief(st)}", flush=True)
    return st


def main() -> int:
    panel = clip_panel(load_panel(), "2024-01-01")
    base = StrategyParams(**TREND)
    tg = compute_targets(panel, base)

    run(panel, "placebo", "as designed", targets=tg)
    for d in (1, 2, 6, 24):
        w = np.zeros_like(tg.weights)
        w[d:] = tg.weights[:-d]
        run(panel, "placebo", f"execution delayed {d}h", targets=dataclasses.replace(tg, weights=w))
    rng = np.random.default_rng(0)
    weeks = panel.n_bars // 168
    perm = rng.permutation(weeks)
    w = np.zeros_like(tg.weights)
    w[: weeks * 168] = tg.weights[: weeks * 168].reshape(weeks, 168, -1)[perm].reshape(weeks * 168, -1)
    run(panel, "placebo", "weeks shuffled in time", targets=dataclasses.replace(tg, weights=w))

    off = EngineParams(daily_target=None, target_mode="off", daily_loss_limit=None, dd_soft=1.0, dd_hard=1.0,
                       kill_dd=None, max_gross=10.0, max_weight=5.0)
    for label, sf in (("engine, no governor, no stops", None), ("engine, no governor, 3-sigma stops", tg.stop_frac)):
        m = run_backtest(panel, tg.weights, off, 10_000.0, *PERIODS["train"], stop_frac=sf, record=True).metrics
        OUT.setdefault("engine_check", {})[label] = {k: m[k] for k in (
            "sharpe", "cagr", "daily_vol", "engine_max_drawdown", "compound_daily", "by_year")}
        print(f"[engine_check] {label}: sharpe {m['sharpe']:.2f} cagr {m['cagr']:.1%} mdd "
              f"{m['engine_max_drawdown']:.1%} by year {m['by_year']}")

    for band in (0.1, 0.25, 0.5):
        run(panel, "band", f"band {band}", band=band)
    for kw in (dict(max_symbol_risk=0.25), dict(max_symbol_risk=0.5), dict(max_symbol_risk=1.0),
               dict(vol_mode="budget", risk_budget=0.002), dict(vol_mode="budget", risk_budget=0.004),
               dict(vol_mode="budget", risk_budget=0.006)):
        run(panel, "sizing", str(kw), **kw)
    for rho in (0.5, 0.7, 0.9):
        run(panel, "sizing", f"rho {rho}", rho=rho)
    for vw in (168, 336, 720):
        run(panel, "sizing", f"vol_window {vw}", vol_window=vw)
    for z in (0.5, 1.0, 2.0):
        run(panel, "sizing", f"trend_z_scale {z}", trend_z_scale=z)

    solo = dict(trend_weight=0.0, short_scale=1.0)
    for lb in (72, 168, 336, 720):
        run(panel, "families_smoothed", f"xsmom lb={lb}", xsmom_weight=1.0, xsmom_lookback=lb, **solo)
    for lb, z, hold in ((12, 2.5, 48), (24, 1.5, 24), (24, 2.0, 24), (48, 2.0, 48)):
        run(panel, "families_smoothed", f"reversal lb={lb} z={z} hold={hold}", reversal_weight=1.0,
            reversal_lookback=lb, reversal_z=z, reversal_hold=hold, **solo)
    for win in (72, 168, 336):
        run(panel, "families_smoothed", f"carry win={win}", carry_weight=1.0, carry_window=win, **solo)

    run(panel, "blends", "trend only")
    for lb in (168, 336, 720):
        for xw in (0.5, 1.0, 2.0):
            run(panel, "blends", f"trend + xsmom{lb} x{xw}", xsmom_weight=xw, xsmom_lookback=lb)
    for rw in (0.25, 0.5):
        run(panel, "blends", f"trend + reversal x{rw}", reversal_weight=rw, reversal_lookback=24, reversal_z=1.5,
            reversal_hold=24)
    for cw in (0.25, 0.5):
        run(panel, "blends", f"trend + carry x{cw}", carry_weight=cw, carry_window=168)

    run(panel, "double_cost", "trend only", cost=2 * COST)
    run(panel, "double_cost", "xsmom336 only", cost=2 * COST, xsmom_weight=1.0, xsmom_lookback=336, **solo)
    run(panel, "double_cost", "trend + xsmom336 x1.0", cost=2 * COST, xsmom_weight=1.0, xsmom_lookback=336)
    dump(ROOT / "research" / "results" / "stage2_train.json", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
