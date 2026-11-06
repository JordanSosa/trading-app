"""One-time holdout evaluation (2025-01-01 .. end of data).

Run ONLY after the strategy was selected on 2018-2024 data. It reports the
selected strategy next to buy & hold and the other finalists for context;
the holdout result must not be used to change the selection.

Usage: python scripts/holdout.py --data data/BTCUSDT-1h.csv --selected NAME [--others A B ...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from tradebot.backtest import run_backtest, run_campaigns
from tradebot.config import load_config
from tradebot.data.history import load_history
from tradebot.metrics import campaign_summary
from tradebot.research import PERIODS, campaign_score
from tradebot.strategies import get_strategy


def evaluate(df, name, cfg):
    s = get_strategy(name)
    sig = s.signals(df)
    start, end = PERIODS["holdout"]
    camp = run_campaigns(df, s, cfg, start, end, signals=sig)
    bt = run_backtest(df, s, cfg, start, end, signals=sig)
    summ = campaign_summary(camp)
    return {
        "score": campaign_score(camp),
        "campaigns": summ,
        "raw_buy_hold": {"pct_positive": float((camp["bh_return"] > 0).mean()),
                         "mean": float(camp["bh_return"].mean()),
                         "median": float(camp["bh_return"].median()),
                         "score": float(np.log1p(camp["bh_return"]).mean())},
        "continuous": {k: bt.metrics.get(k) for k in ("start", "end", "total_return", "cagr", "sharpe",
                                                      "max_drawdown", "n_trades", "round_trips", "win_rate",
                                                      "time_in_market", "fees_paid")},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--selected", required=True)
    ap.add_argument("--others", nargs="*", default=[])
    ap.add_argument("--out", default="research/holdout.json")
    args = ap.parse_args()
    cfg = load_config()
    df = load_history(args.data)
    res = {"period": PERIODS["holdout"], "data_end": str(df.index[-1]), "selected": args.selected,
           "results": {n: evaluate(df, n, cfg) for n in [args.selected, "buy_hold", *args.others]}}
    Path(args.out).write_text(json.dumps(res, indent=1, default=str))
    for n, r in res["results"].items():
        c, k = r["campaigns"], r["continuous"]
        print(f"{n:24s} score {r['score']:+.4f} win {c['pct_positive']:.1%} lose {c['pct_negative']:.1%} "
              f"mean {c['mean']:+.2%} median {c['median']:+.2%} p05 {c['p05']:+.2%} | total {k['total_return']:+.1%} "
              f"maxDD {k['max_drawdown']:.1%} trades {k['n_trades']}")
    rb = res["results"][args.selected]["raw_buy_hold"]
    print(f"raw buy&hold (no overlay)  score {rb['score']:+.4f} win {rb['pct_positive']:.1%} mean {rb['mean']:+.2%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
