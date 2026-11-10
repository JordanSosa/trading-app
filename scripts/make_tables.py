"""Print the result tables used in README.md and research/RESULTS.md from the raw JSON.

Usage: python scripts/make_tables.py
Reads research/results/dev.json and research/results/holdout.json (written by scripts/holdout.py).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pct(x, nd=1, sign=False):
    if x is None:
        return "n/a"
    return f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%"


def flag(r):
    return " (liquidated)" if r.get("liquidated") else " (kill switch)" if r.get("killed") else ""


def main() -> int:
    dev = json.loads((ROOT / "research" / "results" / "dev.json").read_text())
    hold = json.loads((ROOT / "research" / "results" / "holdout.json").read_text())
    d, h = dev["runs"], hold["runs"]

    def both(label):
        return d[label], h[label]

    print("## Headline: default and aggressive, in-sample against out-of-sample\n")
    print("| | Development 2020-04..2024-12 (in-sample) | Holdout 2025-01..2026-10 (out-of-sample) |")
    print("|---|---:|---:|")
    for title, label in (("Default (1.5%), 10,000 USDT", "default, 10,000 USDT (PRIMARY)"),
                         ("Default (1.5%), 100 USDT", "default, 100 USDT"),
                         ("Aggressive (3%), 10,000 USDT", "aggressive 3%, 10,000 USDT"),
                         ("Aggressive (3%), 100 USDT", "aggressive 3%, 100 USDT")):
        a, b = both(label)
        print(f"| **{title}** | | |")
        print(f"| Compound return per day | {pct(a['compound_daily'], 3, True)} | {pct(b['compound_daily'], 3, True)} |")
        print(f"| Total return | {pct(a['total_return'], 0, True)} | {pct(b['total_return'], 1, True)}{flag(b)} |")
        print(f"| Annualised | {pct(a['cagr'], 0, True)} | {pct(b['cagr'], 1, True)} |")
        print(f"| Sharpe ratio | {a['sharpe']:.2f} | {b['sharpe']:.2f} |")
        print(f"| Maximum drawdown | {pct(a['engine_max_drawdown'])} | {pct(b['engine_max_drawdown'])} |")
        print(f"| Days at or above +2% | {pct(a['pct_days_at_target'])} | {pct(b['pct_days_at_target'])} |")
        print(f"| Days up / down | {pct(a['pct_days_up'], 0)} / {pct(a['pct_days_down'], 0)} | "
              f"{pct(b['pct_days_up'], 0)} / {pct(b['pct_days_down'], 0)} |")
        print(f"| Best / worst day | {pct(a['best_day'], 1, True)} / {pct(a['worst_day'], 1, True)} | "
              f"{pct(b['best_day'], 1, True)} / {pct(b['worst_day'], 1, True)} |")

    print("\n## Risk ladder (10,000 USDT)\n")
    print("| Daily volatility target | DEV compound/day | DEV max drawdown | HOLDOUT compound/day | HOLDOUT total | "
          "HOLDOUT max drawdown | HOLDOUT days >= +2% |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    for label in d:
        if not label.startswith("risk ladder"):
            continue
        a, b = both(label)
        tv = float(label.split()[-1])
        print(f"| {tv * 100:.1f}% | {pct(a['compound_daily'], 3, True)}{flag(a)} | {pct(a['engine_max_drawdown'])} | "
              f"{pct(b['compound_daily'], 3, True)}{flag(b)} | {pct(b['total_return'], 1, True)} | "
              f"{pct(b['engine_max_drawdown'])} | {pct(b['pct_days_at_target'])} |")

    print("\n## Variants of the default (10,000 USDT)\n")
    print("| Variant | DEV compound/day | DEV max drawdown | HOLDOUT compound/day | HOLDOUT total | HOLDOUT max drawdown |")
    print("|---|---:|---:|---:|---:|---:|")
    for label in ("default, 10,000 USDT (PRIMARY)", "default, 1,000 USDT", "default, doubled costs",
                  "default, stops fill at the bar extreme", "default, governor off",
                  "default, +2% profit lock (flatten)", "default, +2% profit lock (trail)",
                  "attribution: trend only (T0)", "attribution: cross-sectional only (X0)",
                  "spot preset (long or flat, no leverage)"):
        a, b = both(label)
        print(f"| {label} | {pct(a['compound_daily'], 3, True)} | {pct(a['engine_max_drawdown'])} | "
              f"{pct(b['compound_daily'], 3, True)}{flag(b)} | {pct(b['total_return'], 1, True)} | "
              f"{pct(b['engine_max_drawdown'])} |")

    for name, src in (("DEV", dev), ("HOLDOUT", hold)):
        bh = src["btc_buy_and_hold"]
        print(f"\nBTC buy and hold, {name}: total {pct(bh['total_return'], 1, True)}, compound/day "
              f"{pct(bh['compound_daily'], 3, True)}, max drawdown {pct(bh['max_drawdown'])}")

    p = h["default, 10,000 USDT (PRIMARY)"]
    print("\n## HOLDOUT primary: by year and by month\n")
    print("By year:", {k: pct(v, 1, True) for k, v in p["by_year"].items()})
    print("\n| Month | Return |\n|---|---:|")
    for k, v in p["by_month"].items():
        print(f"| {k} | {pct(v, 1, True)} |")
    print(f"\nFees paid {p['fees_paid']:.0f} USDT, funding paid {p['funding_paid']:.0f} USDT on a "
          f"{p['initial_equity']:.0f} USDT start; {p['trades']} fills, {p['stops']} stops, "
          f"{p['days_loss_limit']} daily-loss-limit days, turnover {p['turnover_per_day']:.2f}x a day, "
          f"average gross exposure {p['avg_gross']:.2f}x, longest drawdown {p['longest_drawdown_days']} days.")
    for label in ("default, 10,000 USDT (PRIMARY)", "aggressive 3%, 10,000 USDT"):
        for name, src in (("DEV", d), ("HOLDOUT", h)):
            b = src[label].get("bootstrap_1y")
            if b:
                fm = b["final_multiple"]
                print(f"\nBootstrap one year from {name} days, {label}: median x{fm['median']:.2f}, 5th pct "
                      f"x{fm['p05']:.2f}, 95th pct x{fm['p95']:.2f}; P(loss) {pct(b['prob_loss'])}; "
                      f"P(drawdown >= 20/35/50%) {pct(b['prob_drawdown']['20%'])} / "
                      f"{pct(b['prob_drawdown']['35%'])} / {pct(b['prob_drawdown']['50%'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
