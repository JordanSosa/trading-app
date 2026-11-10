"""Do the classic technical indicators do better than the bot's own signals?

Each textbook indicator, with its textbook parameters and no tuning, replaces
the bot's signal and nothing else: same 20-symbol universe, same
inverse-volatility sizing to the same 1.5% daily volatility target, same
12-hour smoothing, stops, costs, engine and risk governor, from 10,000 USDT.
Indicators are computed on completed daily and 4-hour bars built from the
hourly data; a position decided at a bar's close is filled at the next
hourly open.

Trend-type rules (trade with the move):
  sma_cross     50/200 simple moving average crossover ("golden cross")
  macd          MACD(12, 26, 9): long while MACD is above its signal line
  donchian      20-bar breakout, 10-bar exit (the Turtle rules)
  supertrend    Supertrend(10, 3)
  ichimoku      price above / below the Ichimoku cloud (9, 26, 52)
  rsi_momentum  RSI(14) above / below 50
  boll_breakout close outside the Bollinger band (20, 2), held to the middle band
  trend_vote    average of the first five

Reversion-type rules (trade against the move: "oversold" / "overbought"):
  rsi           RSI(14) below 30 buys, above 70 sells, exit at 50
  bollinger     close below the lower band buys, above the upper sells, exit at the middle
  stochastic    slow %K(14, 3) below 20 buys, above 80 sells, exit at 50

This was run after the holdout had been seen. Every rule is reported and none
is selected: it is a description of what these indicators did, not a search
for one to trade. Writes research/results/indicators.json.

Usage: python scripts/research_indicators.py [--check]
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from rlib import PERIODS, ROOT, dump, load_panel
from compounder import signals as sg
from compounder.backtest import run_backtest
from compounder.config import load_config
from compounder.data.panel import HOUR_MS, Panel
from compounder.strategy import StrategyParams, compute_targets, universe_mask

KEEP = ("compound_daily", "total_return", "cagr", "sharpe", "daily_vol", "engine_max_drawdown", "pct_days_at_target",
        "worst_day", "avg_gross", "turnover_per_day", "trades", "stops", "killed", "liquidated", "by_year")


# ------------------------------------------------------------------ coarse bars
def coarse_bars(panel: Panel, hours: int) -> dict[str, np.ndarray]:
    """Aggregate hourly bars into ``hours``-hour bars on the UTC grid. A bar without its final hour is missing."""
    t, n = panel.close.shape
    if t % hours or (panel.times[0] // HOUR_MS) % hours:
        raise ValueError("the panel must start on, and span whole, coarse bars")
    shape = (t // hours, hours, n)
    o, h, l, c = (getattr(panel, f).reshape(shape) for f in ("open", "high", "low", "close"))
    with np.errstate(all="ignore"):
        first = np.argmax(np.isfinite(o), axis=1)
        op = np.take_along_axis(o, first[:, None, :], axis=1)[:, 0, :]
        hi, lo = np.nanmax(np.where(np.isfinite(h), h, -np.inf), axis=1), np.nanmin(np.where(np.isfinite(l), l, np.inf), axis=1)
    cl = c[:, -1, :]
    ok = np.isfinite(cl)
    return {"open": np.where(ok, op, np.nan), "high": np.where(ok, hi, np.nan), "low": np.where(ok, lo, np.nan),
            "close": np.where(ok, cl, np.nan)}


def to_hourly(pos: np.ndarray, panel: Panel, hours: int) -> np.ndarray:
    """Position per hourly bar: that of the latest coarse bar completed by the hourly bar's close."""
    first_group = panel.times[0] // (hours * HOUR_MS)
    idx = (panel.times + HOUR_MS) // (hours * HOUR_MS) - 1 - first_group
    out = np.zeros((len(panel.times), pos.shape[1]))
    ok = idx >= 0
    out[ok] = pos[idx[ok]]
    return out


# ------------------------------------------------------------------- building blocks
def sma(x, n):
    return pd.DataFrame(x).rolling(n, min_periods=n).mean().to_numpy()


def std(x, n):
    return pd.DataFrame(x).rolling(n, min_periods=n).std(ddof=0).to_numpy()


def ema(x, n):
    return pd.DataFrame(x).ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()


def wilder(x, n):
    return pd.DataFrame(x).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean().to_numpy()


def highest(x, n):
    return pd.DataFrame(x).rolling(n, min_periods=n).max().to_numpy()


def lowest(x, n):
    return pd.DataFrame(x).rolling(n, min_periods=n).min().to_numpy()


def shift(x, k):
    out = np.full_like(x, np.nan)
    out[k:] = x[:-k]
    return out


def sign(x):
    return np.nan_to_num(np.sign(x))


def latch(enter_long, exit_long, enter_short, exit_short) -> np.ndarray:
    """Hold +1 / -1 from an entry until its exit. Exits are applied before entries on each bar."""
    pos = np.zeros(enter_long.shape)
    cur = np.zeros(enter_long.shape[1])
    for i in range(len(pos)):
        cur = np.where((cur > 0) & exit_long[i], 0.0, cur)
        cur = np.where((cur < 0) & exit_short[i], 0.0, cur)
        cur = np.where((cur == 0) & enter_long[i], 1.0, cur)
        cur = np.where((cur == 0) & enter_short[i], -1.0, cur)
        pos[i] = cur
    return pos


def rsi_value(c, n=14):
    d = np.full_like(c, np.nan)
    d[1:] = c[1:] - c[:-1]
    up, dn = wilder(np.where(np.isfinite(d), np.maximum(d, 0.0), np.nan), n), \
        wilder(np.where(np.isfinite(d), np.maximum(-d, 0.0), np.nan), n)
    with np.errstate(all="ignore"):
        return 100.0 - 100.0 / (1.0 + up / dn)


# ----------------------------------------------------------------------- indicators
def sma_cross(b):
    return sign(sma(b["close"], 50) - sma(b["close"], 200))


def macd(b):
    line = ema(b["close"], 12) - ema(b["close"], 26)
    return sign(line - ema(line, 9))


def donchian(b):
    c = b["close"]
    return latch(c > shift(highest(b["high"], 20), 1), c < shift(lowest(b["low"], 10), 1),
                 c < shift(lowest(b["low"], 20), 1), c > shift(highest(b["high"], 10), 1))


def supertrend(b, n=10, mult=3.0):
    h, l, c = b["high"], b["low"], b["close"]
    pc = shift(c, 1)
    with np.errstate(all="ignore"):
        tr = np.fmax(h - l, np.fmax(np.abs(h - pc), np.abs(l - pc)))
    atr = wilder(tr, n)
    mid = (h + l) / 2.0
    bu, bl = mid + mult * atr, mid - mult * atr
    t, k = c.shape
    fu, fl, trend = np.full(k, np.nan), np.full(k, np.nan), np.zeros(k)
    out = np.zeros((t, k))
    for i in range(t):
        ok = np.isfinite(bu[i]) & np.isfinite(c[i])
        new = ok & ~np.isfinite(fu)
        prev_c = pc[i]
        up = np.where(ok & ((bu[i] < fu) | (prev_c > fu)), bu[i], fu)
        dn = np.where(ok & ((bl[i] > fl) | (prev_c < fl)), bl[i], fl)
        up = np.where(new, bu[i], up)
        dn = np.where(new, bl[i], dn)
        trend = np.where(ok & (c[i] > fu), 1.0, np.where(ok & (c[i] < fl), -1.0, trend))
        trend = np.where(new, np.where(c[i] >= mid[i], 1.0, -1.0), trend)
        fu, fl = up, dn
        out[i] = np.where(ok, trend, 0.0)
    return out


def ichimoku(b):
    h, l, c = b["high"], b["low"], b["close"]
    tenkan = (highest(h, 9) + lowest(l, 9)) / 2.0
    kijun = (highest(h, 26) + lowest(l, 26)) / 2.0
    a, bb = shift((tenkan + kijun) / 2.0, 26), shift((highest(h, 52) + lowest(l, 52)) / 2.0, 26)
    return np.where(c > np.fmax(a, bb), 1.0, np.where(c < np.fmin(a, bb), -1.0, 0.0)) * (np.isfinite(a) & np.isfinite(bb))


def rsi_momentum(b):
    return sign(rsi_value(b["close"]) - 50.0)


def boll_breakout(b):
    c = b["close"]
    mid, sd = sma(c, 20), std(c, 20)
    return latch(c > mid + 2 * sd, c <= mid, c < mid - 2 * sd, c >= mid)


def rsi(b):
    r = rsi_value(b["close"])
    return latch(r < 30, r > 50, r > 70, r < 50)


def bollinger(b):
    c = b["close"]
    mid, sd = sma(c, 20), std(c, 20)
    return latch(c < mid - 2 * sd, c >= mid, c > mid + 2 * sd, c <= mid)


def stochastic(b):
    ll, hh = lowest(b["low"], 14), highest(b["high"], 14)
    with np.errstate(all="ignore"):
        k = sma(100.0 * (b["close"] - ll) / (hh - ll), 3)
    return latch(k < 20, k > 50, k > 80, k < 50)


TREND = {"sma_cross": sma_cross, "macd": macd, "donchian": donchian, "supertrend": supertrend, "ichimoku": ichimoku}
OTHER_TREND = {"rsi_momentum": rsi_momentum, "boll_breakout": boll_breakout}
REVERSION = {"rsi": rsi, "bollinger": bollinger, "stochastic": stochastic}


# --------------------------------------------------------------------------- sizing
def size(panel: Panel, score: np.ndarray, p: StrategyParams, scaled: bool = False):
    """The bot's own sizing layer applied to an arbitrary score (mirrors compute_targets)."""
    hvol = sg.realised_vol(panel.close, p.vol_window)
    dvol = np.maximum(hvol * np.sqrt(24), p.vol_floor)
    member = universe_mask(panel, p.universe_n) & np.isfinite(hvol) & (hvol > 0)
    score = np.where(member, np.nan_to_num(score), 0.0)
    if not scaled:
        score = np.where(score > 0, score * p.long_scale, score * p.short_scale)
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = np.nan_to_num(np.where(member, score / dvol, 0.0))
        pv = sg.portfolio_vol(raw, dvol, p.rho)
        unit = np.minimum(np.where(pv > 0, p.target_vol / pv, 0.0), p.max_symbol_risk * p.target_vol)
    w = raw * unit[:, None]
    if p.smooth_bars > 1:
        w = np.nan_to_num(sg.rolling_mean(w, p.smooth_bars, p.smooth_bars))
    return w, np.clip(p.stop_vol_mult * dvol, p.stop_min, p.stop_max)


def check(panel: Panel, p: StrategyParams) -> None:
    """Self-checks: the sizing replica equals the bot's, the indicators behave, nothing looks ahead."""
    tg = compute_targets(panel, p)
    w, stop = size(panel, tg.score, p, scaled=True)
    assert np.allclose(w, tg.weights, rtol=1e-12, atol=1e-15), "sizing replica differs from compute_targets"
    assert np.allclose(stop, tg.stop_frac, equal_nan=True)
    # A flat stretch followed by an accelerating climb, and its mirror image; wicks smaller than a bar's move.
    path = np.concatenate([np.full(150, 100.0), 100.0 + 0.02 * np.arange(1, 251) ** 2])[:, None]
    wicks = (("open", 1), ("high", 1.0005), ("low", 0.9995), ("close", 1))
    ramp = {k: path * f for k, f in wicks}
    fall = {k: (2000.0 - path) * f for k, f in wicks}
    for name, fn in {**TREND, **OTHER_TREND}.items():
        assert fn(ramp)[-1, 0] == 1.0 and fn(fall)[-1, 0] == -1.0, name
    assert rsi_value(ramp["close"])[-1, 0] == 100.0
    assert rsi(fall)[-1, 0] == 1.0 and bollinger(ramp)[250:, 0].max() <= 0.0 and stochastic(ramp)[-1, 0] == -1.0
    # Look-ahead: replace every hourly bar from k onward; hourly positions before k must not change.
    lo, kk = 24 * 833, 10_008                                    # the slice starts and ends on a day boundary
    small = panel.select(list(panel.symbols[:25])).slice(lo, lo + 12_000)
    f = {x: getattr(small, x).copy() for x in ("open", "high", "low", "close")}
    for x in f:
        f[x][kk:] *= 1.7
    other = Panel(**{**small.__dict__, **f})
    for hours in (4, 24):
        for name, fn in {**TREND, **OTHER_TREND, **REVERSION}.items():
            a = to_hourly(fn(coarse_bars(small, hours)), small, hours)
            b = to_hourly(fn(coarse_bars(other, hours)), other, hours)
            assert np.array_equal(a[:kk], b[:kk]), (name, hours)
    print("checks passed: sizing replica exact; indicator directions; no look-ahead in any rule")


def main() -> int:
    cfg = load_config(ROOT / "config" / "paper.toml")
    panel = load_panel()
    p, ep = cfg.strategy, cfg.engine_params()
    if "--check" in sys.argv:
        check(panel, p)
        return 0
    out: dict = {"equity": 10_000.0, "note": "Run after the holdout was seen. Textbook parameters, no tuning, no "
                                              "selection: every rule is reported.", "rules": {}}

    def evaluate(label: str, kind: str, score: np.ndarray) -> None:
        w, stop = size(panel, score, p)
        row = {"kind": kind}
        for period in ("dev", "holdout"):
            m = run_backtest(panel, w, ep, 10_000.0, *PERIODS[period], stop_frac=stop, record=True).metrics
            row[period] = {k: m.get(k) for k in KEEP}
        out["rules"][label] = row
        d, h = row["dev"], row["holdout"]
        print(f"{label:22s} {kind:9s} DEV {d['compound_daily']:+.3%}/d sharpe {d['sharpe']:5.2f} mdd "
              f"{d['engine_max_drawdown']:5.1%}{' KILL' if d['killed'] else '     '} | HOLDOUT {h['compound_daily']:+.3%}/d "
              f"total {h['total_return']:+6.1%} sharpe {h['sharpe']:5.2f} mdd {h['engine_max_drawdown']:5.1%}"
              f"{' KILL' if h['killed'] else '     '} | turnover/d {d['turnover_per_day']:.2f}", flush=True)

    tg = compute_targets(panel, p)
    w, stop = size(panel, tg.score, p, scaled=True)
    assert np.allclose(w, tg.weights, rtol=1e-12, atol=1e-15)
    evaluate("bot (frozen default)", "reference", np.where(tg.score > 0, tg.score / p.long_scale, tg.score / p.short_scale))
    for hours, tf in ((24, "1d"), (4, "4h")):
        bars = coarse_bars(panel, hours)
        votes = []
        for name, fn in TREND.items():
            pos = to_hourly(fn(bars), panel, hours)
            votes.append(pos)
            evaluate(f"{name} {tf}", "trend", pos)
        evaluate(f"trend_vote {tf}", "trend", np.mean(votes, axis=0))
        for name, fn in OTHER_TREND.items():
            evaluate(f"{name} {tf}", "trend", to_hourly(fn(bars), panel, hours))
        for name, fn in REVERSION.items():
            evaluate(f"{name} {tf}", "reversion", to_hourly(fn(bars), panel, hours))
    dump(ROOT / "research" / "results" / "indicators.json", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
