"""The frozen default from 1,000 USDT against simply holding Bitcoin, and whether anything
here beats Bitcoin both when it rises and when it falls.

Everything is measured on UTC-day returns over the two research periods:

* ``bot``         the frozen default (re-run from 1,000 USDT; deterministic, it reproduces
                  the figures in dev.json and holdout.json)
* ``bot_2x``      the same strategy at twice the risk (the aggressive preset), which runs at
                  about Bitcoin's own volatility
* ``btc_timing``  the frozen trend rules applied to Bitcoin alone, long when it trends up
                  and short when it trends down, at a 3% daily volatility target
* ``btc``         Bitcoin bought at the first bar of the period and held, no costs
* ``overlay``     Bitcoin held with the bot run on top of it on the same equity
                  (daily return = BTC's + the bot's; about 1.7x exposure, on margin)
* ``half_half``   half the equity in Bitcoin, half in the bot, no leverage

The last four are descriptive arithmetic added after the holdout had been seen. They are
not part of the pre-declared evaluation and nothing was selected on them.

Writes research/results/vs_bitcoin.json.
"""

from __future__ import annotations

import dataclasses

import numpy as np

from rlib import PERIODS, ROOT, dump, load_panel
from compounder.backtest import ms, run_backtest
from compounder.config import load_config
from compounder.data.panel import Panel
from compounder.metrics import max_drawdown
from compounder.strategy import compute_targets

EQUITY = 1_000.0


def btc_days(panel, lo: str, hi: str) -> tuple[np.ndarray, np.ndarray]:
    j = panel.index_of("BTCUSDT")
    sel = (panel.times >= ms(lo)) & (panel.times < ms(hi))
    close_ms = panel.times[sel] + panel.interval_ms
    at_midnight = np.nonzero(close_ms % 86_400_000 == 0)[0]
    px = np.concatenate([[panel.open[sel, j][0]], panel.close[sel, j][at_midnight]])
    return close_ms[at_midnight], px[1:] / px[:-1] - 1.0


def compound(r: np.ndarray) -> float:
    return float(np.prod(1.0 + r) - 1.0)


def sharpe(r: np.ndarray) -> float:
    return float(r.mean() / r.std(ddof=1) * np.sqrt(365)) if len(r) > 2 and r.std() > 0 else float("nan")


def summary(r: np.ndarray, btc: np.ndarray, years: np.ndarray, months: np.ndarray) -> dict:
    eq = np.concatenate([[1.0], np.cumprod(1.0 + r)])
    month_list = sorted(set(months))
    mine = np.array([compound(r[months == m]) for m in month_list])
    ref = np.array([compound(btc[months == m]) for m in month_list])
    up, down = ref > 0, ref <= 0
    beta = float(np.cov(r, btc)[0, 1] / btc.var(ddof=1))
    return {
        "end_value": EQUITY * float(eq[-1]), "total_return": compound(r),
        "compound_daily": float(np.expm1(np.log1p(r).mean())), "sharpe": sharpe(r), "daily_vol": float(r.std(ddof=1)),
        "max_drawdown_daily_closes": max_drawdown(eq), "worst_day": float(r.min()),
        "beta_to_btc": beta, "correlation_with_btc": float(np.corrcoef(r, btc)[0, 1]),
        "by_year": {str(y): compound(r[years == y]) for y in sorted(set(years))},
        "sharpe_by_year": {str(y): sharpe(r[years == y]) for y in sorted(set(years))},
        "btc_up_months": {"count": int(up.sum()), "btc_mean": float(ref[up].mean()), "mean": float(mine[up].mean()),
                          "beat_btc": int((mine[up] > ref[up]).sum())},
        "btc_down_months": {"count": int(down.sum()), "btc_mean": float(ref[down].mean()),
                            "mean": float(mine[down].mean()), "beat_btc": int((mine[down] > ref[down]).sum()),
                            "made_money": int((mine[down] > 0).sum())},
    }


def main() -> int:
    cfg = load_config(ROOT / "config" / "research.toml")
    panel = load_panel()
    ep = cfg.engine_params()
    tg = compute_targets(panel, cfg.strategy)
    solo = panel.select(["BTCUSDT"])
    solo = Panel(**{**solo.__dict__, "rank": np.ones_like(solo.rank)})
    timing = dataclasses.replace(cfg.strategy, universe_n=1, xsmom_weight=0.0, short_scale=1.0, target_vol=0.03,
                                 max_symbol_risk=1.0)
    tt = compute_targets(solo, timing)
    out: dict = {"start_equity": EQUITY}
    for period in ("dev", "holdout"):
        lo, hi = PERIODS[period]
        ends, btc = btc_days(panel, lo, hi)
        runs = {
            "bot": run_backtest(panel, tg.weights, ep, EQUITY, lo, hi, stop_frac=tg.stop_frac),
            "bot_2x": run_backtest(panel, tg.weights * 2.0, ep, EQUITY, lo, hi, stop_frac=tg.stop_frac),
            "btc_timing": run_backtest(solo, tt.weights, dataclasses.replace(ep, max_weight=3.0), EQUITY, lo, hi,
                                       stop_frac=tt.stop_frac),
        }
        series: dict[str, np.ndarray] = {"btc": btc}
        for name, res in runs.items():
            _, ia, ib = np.intersect1d(res.day_end_ms, ends, return_indices=True)
            if len(ia) != len(ends):
                raise RuntimeError(f"{name}: day ledger does not line up with the benchmark")
            series[name] = res.daily_returns[ia]
        series["overlay"] = btc + series["bot"]
        series["half_half"] = 0.5 * btc + 0.5 * series["bot"]
        years = (ends - 1).astype("datetime64[ms]").astype("datetime64[Y]").astype(str)
        months = (ends - 1).astype("datetime64[ms]").astype("datetime64[M]").astype(str)
        block = {name: summary(r, btc, years, months) for name, r in series.items()}
        for name, res in runs.items():
            block[name].update({"max_drawdown": res.metrics["engine_max_drawdown"], "killed": res.metrics["killed"],
                                "fills": res.metrics["trades"], "fees_paid": res.metrics["fees_paid"],
                                "avg_gross": res.metrics.get("avg_gross")})
        # How concentrated Bitcoin's gains are, and how much of those days the bot caught.
        order = np.argsort(-btc)
        conc = {}
        for frac in (0.01, 0.05):
            k = max(1, int(round(frac * len(btc))))
            rest = np.ones(len(btc), dtype=bool)
            rest[order[:k]] = False
            conc[f"{frac:.0%}"] = {"days": k, "btc_total_without_them": compound(btc[rest]),
                                   "btc_mean_on_them": float(btc[order[:k]].mean()),
                                   "bot_mean_on_them": float(series["bot"][order[:k]].mean()),
                                   "bot_2x_mean_on_them": float(series["bot_2x"][order[:k]].mean())}
        block["btc_best_days"] = conc
        out[period] = block
        print(f"\n== {period} ({len(btc)} days)")
        for name in ("btc", "bot", "bot_2x", "btc_timing", "half_half", "overlay"):
            b = block[name]
            print(f"  {name:11s} {EQUITY:,.0f} -> {b['end_value']:>9,.0f}  sharpe {b['sharpe']:5.2f}  max drawdown "
                  f"{b.get('max_drawdown', b['max_drawdown_daily_closes']):5.1%}  by year "
                  + "  ".join(f"{y} {v:+.0%}" for y, v in b["by_year"].items()))
            if name != "btc":
                u, d = b["btc_up_months"], b["btc_down_months"]
                print(f"              BTC-up months: {u['mean']:+.1%} against {u['btc_mean']:+.1%}, ahead in "
                      f"{u['beat_btc']}/{u['count']};  BTC-down months: {d['mean']:+.1%} against {d['btc_mean']:+.1%}, "
                      f"ahead in {d['beat_btc']}/{d['count']}, made money in {d['made_money']}/{d['count']}")
        for frac, c in conc.items():
            print(f"  BTC without its best {frac} of days ({c['days']}): {c['btc_total_without_them']:+.0%}; on those days "
                  f"BTC averaged {c['btc_mean_on_them']:+.1%}, the bot {c['bot_mean_on_them']:+.1%}")
    dump(ROOT / "research" / "results" / "vs_bitcoin.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
