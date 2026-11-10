"""The frozen default from 1,000 USDT against simply holding Bitcoin, period by period and year by year.

Re-runs the pre-declared 1,000 USDT account (deterministic: it reproduces the
figures in dev.json and holdout.json) to obtain its daily returns, and sets
them beside BTC bought at the first bar of each period and held, without
costs. Writes research/results/vs_bitcoin.json.
"""

from __future__ import annotations

import numpy as np

from rlib import PERIODS, ROOT, dump, load_panel
from compounder.backtest import ms, run_backtest
from compounder.config import load_config
from compounder.metrics import daily_stats, yearly_returns
from compounder.strategy import compute_targets

EQUITY = 1_000.0


def btc_days(panel, lo: str, hi: str) -> tuple[np.ndarray, np.ndarray]:
    j = panel.index_of("BTCUSDT")
    sel = (panel.times >= ms(lo)) & (panel.times < ms(hi))
    close_ms = panel.times[sel] + panel.interval_ms
    at_midnight = np.nonzero(close_ms % 86_400_000 == 0)[0]
    px = np.concatenate([[panel.open[sel, j][0]], panel.close[sel, j][at_midnight]])
    return close_ms[at_midnight], px[1:] / px[:-1] - 1.0


def main() -> int:
    cfg = load_config(ROOT / "config" / "paper.toml")
    panel = load_panel()
    tg = compute_targets(panel, cfg.strategy)
    out: dict = {"start_equity": EQUITY}
    for period in ("dev", "holdout"):
        lo, hi = PERIODS[period]
        res = run_backtest(panel, tg.weights, cfg.engine_params(), EQUITY, lo, hi, stop_frac=tg.stop_frac)
        ends, btc = btc_days(panel, lo, hi)
        bs = daily_stats(btc)
        _, ia, ib = np.intersect1d(res.day_end_ms, ends, return_indices=True)
        bot, ref = res.daily_returns[ia], btc[ib]
        m = res.metrics
        out[period] = {
            "bot": {"end_value": m["final_equity"], "total_return": m["total_return"], "cagr": m["cagr"],
                    "compound_daily": m["compound_daily"], "sharpe": m["sharpe"],
                    "max_drawdown": m["engine_max_drawdown"], "worst_day": m["worst_day"], "fills": m["trades"],
                    "fees_paid": m["fees_paid"], "funding_paid": m["funding_paid"], "by_year": m["by_year"]},
            "btc": {"end_value": EQUITY * (1.0 + bs["total_return"]), "total_return": bs["total_return"],
                    "cagr": bs["cagr"], "compound_daily": bs["compound_daily"], "sharpe": bs["sharpe"],
                    "max_drawdown": bs["max_drawdown"], "worst_day": bs["worst_day"],
                    "by_year": yearly_returns(ends, btc)},
            "daily_correlation": float(np.corrcoef(bot, ref)[0, 1]),
            "bot_mean_day_when_btc_up": float(bot[ref > 0].mean()),
            "bot_mean_day_when_btc_down": float(bot[ref <= 0].mean()),
        }
        o = out[period]
        print(f"{period}: bot {EQUITY:,.0f} -> {o['bot']['end_value']:,.0f} (max drawdown {o['bot']['max_drawdown']:.1%}); "
              f"BTC -> {o['btc']['end_value']:,.0f} (max drawdown {o['btc']['max_drawdown']:.1%}); "
              f"daily correlation {o['daily_correlation']:+.2f}")
        for y in o["bot"]["by_year"]:
            print(f"   {y}: bot {o['bot']['by_year'][y]:+.0%}, BTC {o['btc']['by_year'][y]:+.0%}")
    dump(ROOT / "research" / "results" / "vs_bitcoin.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
