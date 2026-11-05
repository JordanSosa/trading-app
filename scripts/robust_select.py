"""Robustness-weighted model selection (decided before the holdout was touched).

For every candidate, the DEV (2018-2024) campaign score is computed at
 * the default parameters,
 * each numeric parameter moved -30%, -15%, +15%, +30% one at a time,
 * the default parameters with the circuit breaker disabled.
The robust score is the mean over all these variants: it rewards plateaus and
penalises parameter or risk-overlay knife edges. The candidate with the highest
robust score is selected; audits with overfit_risk == "high" are excluded.

Usage: python scripts/robust_select.py --out research/robust_selection.json
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from pathlib import Path

import numpy as np

from tradebot.backtest import run_campaigns
from tradebot.config import load_config
from tradebot.data.history import load_history
from tradebot.research import PERIODS, campaign_score
from tradebot.strategies import get_strategy

CANDIDATES = ["breakout_dual", "breakout_keltner", "breakout_donchian", "trend_sma_band",
              "trend_sma_band_regime", "trend_strength", "regime_confirm", "regime_volswitch"]
EXCLUDED_BY_AUDIT = {"meanrev_dip": "overfit risk high", "meanrev_dip_multi": "overfit risk high"}
STEPS = (0.70, 0.85, 1.15, 1.30)


def dev_score(df, strat, cfg) -> float:
    start, end = PERIODS["dev"]
    return campaign_score(run_campaigns(df, strat, cfg, start, end))


def variants(name: str):
    base = get_strategy(name)
    yield "default", base.params
    for k, v in base.default_params.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v == 0:
            continue
        for f in STEPS:
            nv = v * f
            if isinstance(v, int):
                nv = int(round(nv))
                if nv == v:
                    continue
            params = {**base.params, k: nv}
            try:
                get_strategy(name, params)
            except ValueError:
                continue
            yield f"{k}x{f}", params


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="research/robust_selection.json")
    args = ap.parse_args()
    df = load_history("data/research/BTCUSDT-1h-research.csv")
    cfg = load_config()
    cfg_nocb = dataclasses.replace(cfg, risk=dataclasses.replace(cfg.risk, circuit_breaker_dd=0.99))
    out = {}
    for name in CANDIDATES:
        t = time.time()
        scores = {}
        for label, params in variants(name):
            scores[label] = dev_score(df, get_strategy(name, params), cfg)
        scores["no_circuit_breaker"] = dev_score(df, get_strategy(name), cfg_nocb)
        vals = np.array(list(scores.values()))
        out[name] = {"default": scores["default"], "robust_mean": float(vals.mean()),
                     "robust_min": float(vals.min()), "n_variants": len(vals), "scores": scores}
        print(f"{name:24s} default {scores['default']:+.4f} robust mean {vals.mean():+.4f} "
              f"min {vals.min():+.4f} no-CB {scores['no_circuit_breaker']:+.4f} "
              f"({len(vals)} variants, {time.time() - t:.0f}s)", flush=True)
    bh = dev_score(df, get_strategy("buy_hold"), cfg)
    ranked = sorted(out, key=lambda n: out[n]["robust_mean"], reverse=True)
    result = {"rule": __doc__, "excluded": EXCLUDED_BY_AUDIT, "buy_hold_dev": bh, "ranking": ranked,
              "candidates": out}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1))
    print(f"buy_hold (with overlay) DEV {bh:+.4f}\nselected: {ranked[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
