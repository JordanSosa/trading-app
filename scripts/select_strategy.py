"""Model selection over research candidates (no holdout data is touched).

Usage:  python scripts/select_strategy.py candidates.json [--out research/selection.json]

candidates.json: [{"label": "...", "name": "<strategy>", "params": {...}}, ...]

Pre-declared rule: rank by the campaign score on DEV (2018-2024 = TRAIN+VALID);
eligible candidates must have a positive VALID score and a positive DEV score.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from tradebot.config import load_config
from tradebot.data.history import load_history
from tradebot.research import evaluate_period
from tradebot.strategies import get_strategy

DATA = "data/research/BTCUSDT-1h-research.csv"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates")
    ap.add_argument("--out", default="research/selection.json")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    df = load_history(DATA)
    cands = json.loads(Path(args.candidates).read_text())
    rows = []
    for c in cands:
        t = time.time()
        strat = get_strategy(c["name"], c.get("params", {}))
        sig = strat.signals(df)
        res = {p: evaluate_period(df, strat, cfg, period=p, signals=sig, per_year=(p == "train"))
               for p in ("train", "valid", "dev")}
        yrs = res["train"].get("per_year", {})
        row = {
            "label": c["label"], "name": c["name"], "params": strat.params, "describe": strat.describe(),
            "train_score": res["train"]["score"], "valid_score": res["valid"]["score"], "dev_score": res["dev"]["score"],
            "dev_pct_positive": res["dev"]["campaigns"]["pct_positive"], "dev_mean": res["dev"]["campaigns"]["mean"],
            "dev_median": res["dev"]["campaigns"]["median"], "dev_p05": res["dev"]["campaigns"]["p05"],
            "dev_worst_dd": res["dev"]["campaigns"]["worst_max_dd"],
            "dev_cagr": res["dev"]["continuous"]["cagr"], "dev_sharpe": res["dev"]["continuous"]["sharpe"],
            "dev_maxdd": res["dev"]["continuous"]["max_drawdown"], "dev_trips": res["dev"]["continuous"]["round_trips"],
            "dev_time_in_market": res["dev"]["continuous"]["time_in_market"],
            "train_years_positive": sum(1 for v in yrs.values() if v["score"] > 0),
            "per_year": {str(k): v["score"] for k, v in yrs.items()},
            "eligible": res["valid"]["score"] > 0 and res["dev"]["score"] > 0,
            "detail": res,
        }
        rows.append(row)
        print(f"{c['label']:34s} train {row['train_score']:+.4f} valid {row['valid_score']:+.4f} "
              f"dev {row['dev_score']:+.4f} pos {row['dev_pct_positive']:.1%} p05 {row['dev_p05']:+.2%} "
              f"yrs+ {row['train_years_positive']}/6 maxDD {row['dev_maxdd']:.1%} ({time.time() - t:.1f}s)",
              flush=True)
    ranked = sorted(rows, key=lambda r: (r["eligible"], r["dev_score"]), reverse=True)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({"ranking": [r["label"] for r in ranked], "rows": ranked},
                                         indent=1, default=str))
    print("\nselected:", ranked[0]["label"] if ranked and ranked[0]["eligible"] else "NONE ELIGIBLE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
