"""Would trading like a professional swing trader or day trader do better than the bot?

The professional playbook, as published, in the bot's place: entries taken from
the classic setups and managed the way traders are taught to manage them (a
stop at a multiple of the average range, a profit target at a multiple of the
risk, a trailing stop, a time stop, flat at the end of the session, one trade
per market per day). Everything else is the bot's: the same 20-symbol
universe, inverse-volatility sizing to the same 1.5% daily volatility target
(which also caps the risk in any one position), costs, engine and risk
governor, from 10,000 USDT. Positions are taken and closed at once, with no
smoothing.

Swing systems (daily bars; stops, targets and trails watched every hour)
  breakout_turtle     20-day breakout, out on a 10-day reversal, stop at 2N (N = 20-day average true range)
  breakout_2r         the same entry, stop at 2N, profit taken at twice the risk
  breakout_3r         the same entry, stop at 2N, profit taken at three times the risk
  breakout_trail      the same entry, a trailing stop 3N behind the best price ("chandelier exit")
  breakout_10d        the same entry, stop at 2N, out after ten days
  breakout_55_20      55-day breakout, out on a 20-day reversal, stop at 2N
  breakout_btc_trend  breakout_turtle, long only while Bitcoin is above its 200-day average and short only below it
  dip_in_trend        above the 200-day average buy a two-day RSI under 10 and sell above the 5-day average; the mirror below
  pullback_2r         in a 20/50-day EMA trend, a day that dips to the 20-day EMA and closes back beyond it;
                      stop at 1.5N, profit taken at twice the risk

Day systems (hourly bars; one trade per market per day; flat at the session's end)
  orb_utc             breakout of the first four hours' range of the UTC day, stop at the far side of the range
  orb_ny              breakout of the 13:00-15:00 UTC range (the US open), flat at 21:00 UTC
  prev_day_breakout   breakout of the previous day's high or low, stop at N/2
  fade_bands          fade an hourly close outside the Bollinger band (20, 2) back to its middle, stop at N/2
  morning_momentum    at 12:00 UTC go with the day's move so far, stop at N/2

A stop, target or trail that is touched inside an hourly bar is acted on at the
next hourly open, like every other decision here. Hourly bars are the finest
data available: a day trader on one-minute charts trades more often than
anything tested here, and pays for it more often.

Run after the holdout had been seen. Textbook parameters, no tuning; every
system is reported and none is selected. Writes research/results/playbook.json.

Usage: python scripts/research_playbook.py [--check]
"""

from __future__ import annotations

import dataclasses
import multiprocessing
import sys
import time

import numpy as np

from research_indicators import coarse_bars, ema, highest, lowest, rsi_value, shift, size, sma, std, to_hourly, wilder
from rlib import PERIODS, ROOT, dump, load_panel
from compounder.backtest import ms, run_backtest
from compounder.config import load_config
from compounder.data.panel import HOUR_MS, Panel
from compounder.strategy import compute_targets, universe_mask

EQUITY = 10_000.0
BOT = "bot (frozen default)"
KEEP = ("compound_daily", "total_return", "cagr", "sharpe", "daily_vol", "engine_max_drawdown", "worst_day", "avg_gross",
        "time_in_market", "turnover_per_day", "trades", "stops", "killed", "liquidated", "by_year")


# ------------------------------------------------------------------- trade management
def manage(high: np.ndarray, low: np.ndarray, close: np.ndarray, hour: np.ndarray, enter_long: np.ndarray,
           enter_short: np.ndarray, *, stop=None, target: float | None = None, trail: np.ndarray | None = None,
           hold: int | None = None, exit_long: np.ndarray | None = None, exit_short: np.ndarray | None = None,
           entry_hours=None, flat_hour: int | None = None, one_per_day: bool = False) -> np.ndarray:
    """Hourly positions (+1 long, -1 short, 0 flat) of a trader following fixed rules.

    Everything is decided at an hourly close from that bar and earlier ones.

    ``hour``         UTC hour at which each bar opens.
    ``enter_*``      (T, N) entry conditions; taken only when flat, on bars opening at one of ``entry_hours``.
    ``stop``         (T, N) price distance from the entry, or a pair (for longs, for shorts); fixed at entry. The
                     position is closed once a bar's low (long) or high (short) reaches it.
    ``target``       profit is taken once price has moved this multiple of the stop distance in favour.
    ``trail``        (T, N) distance, fixed at entry: closed once price is that far off its best since entry.
    ``hold``         closed after this many bars.
    ``exit_*``       (T, N) exit conditions.
    ``flat_hour``    every position is closed at the close of the bar opening at this hour, and none is opened there.
    ``one_per_day``  at most one entry per market per UTC day.
    """
    t, n = close.shape
    stop_long, stop_short = stop if isinstance(stop, tuple) else (stop, stop)
    allowed = np.ones(24, dtype=bool)
    if entry_hours is not None:
        allowed[:] = False
        allowed[list(entry_hours)] = True
    pos = np.zeros((t, n))
    cur = np.zeros(n)
    entry, dist, tdist, best = (np.full(n, np.nan) for _ in range(4))
    held = np.zeros(n, dtype="int64")
    done = np.zeros(n, dtype=bool)
    for i in range(t):
        hi, lo, cl = high[i], low[i], close[i]
        if hour[i] == 0:
            done[:] = False
        long_, short_ = cur > 0, cur < 0
        held = np.where(cur != 0, held + 1, 0)                   # bars held, counting this one
        out = np.zeros(n, dtype=bool)
        with np.errstate(invalid="ignore"):
            if stop is not None:
                out |= (long_ & (lo <= entry - dist)) | (short_ & (hi >= entry + dist))
                if target is not None:
                    out |= (long_ & (hi >= entry + target * dist)) | (short_ & (lo <= entry - target * dist))
            if trail is not None:
                out |= (long_ & (lo <= best - tdist)) | (short_ & (hi >= best + tdist))
                best = np.where(long_, np.fmax(best, hi), np.where(short_, np.fmin(best, lo), best))
        if hold is not None:
            out |= (cur != 0) & (held >= hold)
        if exit_long is not None:
            out |= long_ & exit_long[i]
        if exit_short is not None:
            out |= short_ & exit_short[i]
        flat_now = flat_hour is not None and hour[i] == flat_hour
        if flat_now:
            out |= cur != 0
        cur = np.where(out, 0.0, cur)
        if allowed[hour[i]] and not flat_now:
            can = (cur == 0) & np.isfinite(cl)
            if one_per_day:
                can &= ~done
            go_long = can & enter_long[i]
            go_short = can & enter_short[i] & ~go_long
            if stop is not None:
                with np.errstate(invalid="ignore"):
                    go_long &= np.isfinite(stop_long[i]) & (stop_long[i] > 0)
                    go_short &= np.isfinite(stop_short[i]) & (stop_short[i] > 0)
            new = go_long | go_short
            if new.any():
                cur = np.where(go_long, 1.0, np.where(go_short, -1.0, cur))
                entry = np.where(new, cl, entry)
                if stop is not None:
                    dist = np.where(go_long, stop_long[i], np.where(go_short, stop_short[i], dist))
                if trail is not None:
                    tdist = np.where(new, trail[i], tdist)
                    best = np.where(new, cl, best)
                held = np.where(new, 0, held)
                done |= new
        pos[i] = cur
    return pos


def session_range(high: np.ndarray, low: np.ndarray, hours: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    """High and low of the bars opening at ``hours`` of each UTC day, repeated on every bar of that day.

    The values are only known once the last of those bars has closed: the caller must not use them earlier.
    """
    t, n = high.shape
    hi = high.reshape(t // 24, 24, n)[:, list(hours), :].max(axis=1)        # NaN if any of the bars is missing
    lo = low.reshape(t // 24, 24, n)[:, list(hours), :].min(axis=1)
    return np.repeat(hi, 24, axis=0), np.repeat(lo, 24, axis=0)


def systems(panel: Panel) -> dict:
    """name -> (family, what it is, function returning hourly positions)."""
    if panel.n_bars % 24 or (panel.times[0] // HOUR_MS) % 24:
        raise ValueError("the panel must start at 00:00 UTC and span whole days")
    o, h, l, c = panel.open, panel.high, panel.low, panel.close
    n = panel.n_symbols
    hour = ((panel.times // HOUR_MS) % 24).astype("int64")
    b = coarse_bars(panel, 24)
    dc, dh, dl = b["close"], b["high"], b["low"]
    pc = shift(dc, 1)
    with np.errstate(all="ignore"):
        tr = np.fmax(dh - dl, np.fmax(np.abs(dh - pc), np.abs(dl - pc)))
    atr = wilder(tr, 20)

    def daily(x) -> np.ndarray:                 # a daily series on the hourly grid: the latest completed day's value
        return to_hourly(np.asarray(x, dtype="float64"), panel, 24)

    def flag(x) -> np.ndarray:
        return daily(x) > 0.5

    big_n = daily(atr)
    up20, dn20 = dc > shift(highest(dh, 20), 1), dc < shift(lowest(dl, 20), 1)
    lo10, hi10 = dc < shift(lowest(dl, 10), 1), dc > shift(highest(dh, 10), 1)
    up55, dn55 = dc > shift(highest(dh, 55), 1), dc < shift(lowest(dl, 55), 1)
    sma200, sma5, rsi2 = sma(dc, 200), sma(dc, 5), rsi_value(dc, 2)
    e20, e50 = ema(dc, 20), ema(dc, 50)
    j = panel.index_of("BTCUSDT")
    btc_up, btc_down = dc[:, [j]] > sma200[:, [j]], dc[:, [j]] < sma200[:, [j]]

    def swing(**kw):                             # a daily bar completes at the close of the bar opening at 23:00
        return manage(h, l, c, hour, entry_hours=(23,), **kw)

    def day(**kw):
        return manage(h, l, c, hour, one_per_day=True, **kw)

    def breakout(**kw):
        return swing(enter_long=flag(up20), enter_short=flag(dn20), **kw)

    out = {
        "breakout_turtle": ("swing", "20-day breakout, out on a 10-day reversal, stop 2N",
                            lambda: breakout(exit_long=flag(lo10), exit_short=flag(hi10), stop=2 * big_n)),
        "breakout_2r": ("swing", "20-day breakout, stop 2N, target 2R",
                        lambda: breakout(stop=2 * big_n, target=2.0)),
        "breakout_3r": ("swing", "20-day breakout, stop 2N, target 3R",
                        lambda: breakout(stop=2 * big_n, target=3.0)),
        "breakout_trail": ("swing", "20-day breakout, trailing stop 3N", lambda: breakout(trail=3 * big_n)),
        "breakout_10d": ("swing", "20-day breakout, stop 2N, out after ten days",
                         lambda: breakout(stop=2 * big_n, hold=240)),
        "breakout_55_20": ("swing", "55-day breakout, out on a 20-day reversal, stop 2N",
                           lambda: swing(enter_long=flag(up55), enter_short=flag(dn55), exit_long=flag(dn20),
                                         exit_short=flag(up20), stop=2 * big_n)),
        "breakout_btc_trend": ("swing", "20-day breakout in the direction of Bitcoin's 200-day trend only",
                               lambda: swing(enter_long=flag(up20 & btc_up), enter_short=flag(dn20 & btc_down),
                                             exit_long=flag(lo10), exit_short=flag(hi10), stop=2 * big_n)),
        "dip_in_trend": ("swing", "two-day RSI dip in a 200-day trend, out beyond the 5-day average",
                         lambda: swing(enter_long=flag((dc > sma200) & (rsi2 < 10)),
                                       enter_short=flag((dc < sma200) & (rsi2 > 90)),
                                       exit_long=flag(dc > sma5), exit_short=flag(dc < sma5))),
        "pullback_2r": ("swing", "pullback to the 20-day EMA in a 20/50 EMA trend, stop 1.5N, target 2R",
                        lambda: swing(enter_long=flag((e20 > e50) & (dl <= e20) & (dc > e20)),
                                      enter_short=flag((e20 < e50) & (dh >= e20) & (dc < e20)),
                                      stop=1.5 * big_n, target=2.0)),
    }
    utc_hi, utc_lo = session_range(h, l, (0, 1, 2, 3))
    ny_hi, ny_lo = session_range(h, l, (13, 14))
    y_hi, y_lo = daily(dh), daily(dl)                     # yesterday's range on bars opening 00:00 to 22:00
    mid, sd = sma(c, 20), std(c, 20)
    day_open = np.repeat(o.reshape(-1, 24, n)[:, 0, :], 24, axis=0)
    with np.errstate(invalid="ignore"):
        out.update({
            "orb_utc": ("day", "breakout of the first four hours of the UTC day, stop at the far side",
                        lambda: day(enter_long=c > utc_hi, enter_short=c < utc_lo, stop=(c - utc_lo, utc_hi - c),
                                    entry_hours=range(4, 20), flat_hour=23)),
            "orb_ny": ("day", "breakout of the 13:00-15:00 UTC range, flat at 21:00 UTC",
                       lambda: day(enter_long=c > ny_hi, enter_short=c < ny_lo, stop=(c - ny_lo, ny_hi - c),
                                   entry_hours=range(15, 20), flat_hour=20)),
            "prev_day_breakout": ("day", "breakout of the previous day's high or low, stop N/2",
                                  lambda: day(enter_long=c > y_hi, enter_short=c < y_lo, stop=0.5 * big_n,
                                              entry_hours=range(0, 20), flat_hour=23)),
            "fade_bands": ("day", "fade an hourly close outside the Bollinger band to its middle, stop N/2",
                           lambda: day(enter_long=c < mid - 2 * sd, enter_short=c > mid + 2 * sd, exit_long=c >= mid,
                                       exit_short=c <= mid, stop=0.5 * big_n, entry_hours=range(0, 20), flat_hour=23)),
            "morning_momentum": ("day", "at 12:00 UTC go with the day's move so far, stop N/2",
                                 lambda: day(enter_long=c > day_open, enter_short=c < day_open, stop=0.5 * big_n,
                                             entry_hours=(11,), flat_hour=23)),
        })
    return out


# ------------------------------------------------------------------------- statistics
def trade_stats(weights: np.ndarray, close: np.ndarray, times: np.ndarray, lo: str, hi: str) -> dict:
    """The trades as a trader would count them: one per stretch of holding a market on one side.

    Returns are from the close at which the position was decided to the close at which it was ended, before
    costs, per unit of the position. A stretch still open at the end of the window is left out.
    """
    sel = (times >= ms(lo)) & (times < ms(hi))
    s, px = np.sign(np.nan_to_num(weights[sel])), close[sel]
    rets, bars = [], []
    for col in range(s.shape[1]):
        side = s[:, col]
        if not side.any():
            continue
        change = np.nonzero(np.diff(side) != 0)[0] + 1
        starts = np.concatenate([[0], change])
        ends = np.concatenate([change, [len(side)]])
        for a, e in zip(starts, ends):
            if side[a] == 0 or e >= len(side):
                continue
            p0, p1 = px[a, col], px[e, col]
            if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                rets.append(side[a] * (p1 / p0 - 1.0))
                bars.append(e - a)
    r, hb = np.asarray(rets), np.asarray(bars, dtype="float64")
    if not len(r):
        return {"trades": 0}
    wins, losses = r[r > 0], r[r <= 0]
    days = sel.sum() / 24.0
    return {"trades": int(len(r)), "trades_per_day": float(len(r) / days), "win_rate": float(len(wins) / len(r)),
            "average_win": float(wins.mean()) if len(wins) else 0.0,
            "average_loss": float(losses.mean()) if len(losses) else 0.0,
            "payoff_ratio": float(wins.mean() / -losses.mean()) if len(wins) and len(losses) and losses.mean() < 0 else None,
            "average_trade": float(r.mean()), "median_hold_hours": float(np.median(hb)),
            "mean_hold_hours": float(hb.mean())}


# ---------------------------------------------------------------------- the app's engine
_JOB: dict = {}                                                  # inherited by the forked workers


def _task(task: tuple[str, str, bool]) -> tuple[str, str, bool, dict]:
    name, period, costs = task
    res = run_backtest(_JOB["panel"], _JOB["weights"][name].astype("float64"), _JOB["ep"] if costs else _JOB["free"],
                       EQUITY, *PERIODS[period], stop_frac=_JOB["stop"], record=True)
    return name, period, costs, {k: res.metrics.get(k) for k in KEEP}


def engine_runs(panel: Panel, weights: dict[str, np.ndarray], ep, stop: np.ndarray) -> dict:
    """Every system through the engine on DEV and HOLDOUT, with the modelled costs and with none."""
    free = dataclasses.replace(ep, fee_rate=0.0, slippage=0.0, stop_slippage=0.0)
    _JOB.update(panel=panel, weights=weights, ep=ep, free=free, stop=stop)
    tasks = [(name, period, costs) for name in weights for period in ("dev", "holdout") for costs in (True, False)]
    out: dict = {name: {} for name in weights}
    try:
        pool = multiprocessing.get_context("fork").Pool(processes=min(4, multiprocessing.cpu_count()))
        results = pool.imap_unordered(_task, tasks)
    except ValueError:
        pool, results = None, map(_task, tasks)
    for name, period, costs, row in results:
        out[name][period if costs else f"{period}_before_costs"] = row
    if pool is not None:
        pool.close()
        pool.join()
    _JOB.clear()
    return out


# ----------------------------------------------------------------------------- checks
def check(panel: Panel, p) -> None:
    """Self-checks: the trade manager does what its rules say, and no system looks ahead."""
    # 1. The manager on a hand-made path: 3 days of one market, entries allowed at the close of the bar opening 01:00.
    t = 72
    hour = np.arange(t) % 24
    px = np.full((t, 1), 100.0)
    on = np.zeros((t, 1), dtype=bool)
    off = np.zeros((t, 1), dtype=bool)
    go = on.copy()
    go[1] = True
    five = np.full((t, 1), 5.0)

    def run(high=None, low=None, long=True, **kw):
        hi, lo = px.copy() if high is None else high, px.copy() if low is None else low
        return manage(hi, lo, px, hour, go if long else off, off if long else go, **kw)[:, 0]

    low = px.copy()
    low[6] = 94.9
    got = run(low=low, stop=five)
    assert got[0] == 0 and (got[1:6] == 1).all() and (got[6:] == 0).all(), "stop"
    high = px.copy()
    high[7] = 110.0
    got = run(high=high, stop=five, target=2.0)
    assert (got[1:7] == 1).all() and (got[7:] == 0).all(), "target"
    assert (run(high=high, stop=five, target=2.1)[1:] == 1).all(), "target not reached"
    got = run(high=high, low=low, long=False, stop=five, target=2.0)
    assert (got[1:7] == -1).all() and (got[7:] == 0).all(), "short stop (the high of bar 7) before its target"
    high2, low2 = px.copy(), px.copy()
    high2[4], low2[5:], low2[9] = 120.0, 118.0, 114.9             # up to 120, holds 118, then dips to 114.9
    got = run(high=high2, low=low2, trail=five)
    assert (got[1:9] == 1).all() and (got[9:] == 0).all(), "trailing stop follows the best price"
    assert (run(high=high2, low=low2, stop=five)[1:] == 1).all(), "a fixed stop does not trail"
    got = run(hold=10)
    assert (got[1:11] == 1).all() and (got[11:] == 0).all(), "time stop"
    got = run(flat_hour=23)
    assert (got[1:23] == 1).all() and (got[23:] == 0).all(), "flat at the session's end"
    every = np.ones((t, 1), dtype=bool)
    got = manage(px, low, px, hour, every, off, stop=five, entry_hours=range(0, 20), flat_hour=23, one_per_day=True)[:, 0]
    assert (got[0:6] == 1).all() and (got[6:24] == 0).all() and (got[24:47] == 1).all() and got[47] == 0, "one a day"
    got = manage(px, low, px, hour, every, off, stop=five, entry_hours=range(0, 20), flat_hour=23)[:, 0]
    assert got[6] == 1, "without the one-a-day rule the stopped trade is re-entered at once"
    assert not manage(px, px, px, hour, every, off, stop=np.full((t, 1), np.nan)).any(), "no entry without a stop"
    hi_r, lo_r = session_range(np.arange(48.0)[:, None], np.arange(48.0)[:, None], (2, 3))
    assert (hi_r[:24] == 3).all() and (lo_r[24:] == 26).all()

    # 2. No look-ahead: replace every hourly bar from kk onward; positions before kk must not change.
    lo, nb = 24 * 833, 12_000
    member = universe_mask(panel, p.universe_n)[lo:lo + nb].sum(axis=0)
    keep = set(np.argsort(-member, kind="stable")[:24]) | {panel.index_of("BTCUSDT")}
    small = panel.select([panel.symbols[k] for k in sorted(keep)]).slice(lo, lo + nb)
    base = {name: fn() for name, (_, _, fn) in systems(small).items()}
    rng = np.random.default_rng(3)
    for name, pos in base.items():
        assert (pos != 0).any(), f"{name} never trades in the check window"
    differs = set()
    for kk in (24 * 300, 24 * 300 + 5, 24 * 371 + 14):
        f = {x: getattr(small, x).copy() for x in ("open", "high", "low", "close")}
        walk = np.exp(np.cumsum(rng.normal(0.0, 0.01, f["close"][kk:].shape[0])))[:, None]
        for x in f:
            f[x][kk:] *= walk
        other = {name: fn() for name, (_, _, fn) in systems(Panel(**{**small.__dict__, **f})).items()}
        for name, pos in base.items():
            assert np.array_equal(pos[:kk], other[name][:kk]), (name, kk)
            if not np.array_equal(pos[kk:], other[name][kk:]):
                differs.add(name)
    assert differs == set(base), "the perturbation should change every system afterwards"

    # 3. Trade statistics on a known position: one winning long of 3 bars, one losing short of 2.
    w = np.zeros((12, 1))
    w[2:5], w[7:9] = 0.5, -0.5
    cl = np.array([100, 100, 100, 101, 102, 110, 110, 100, 102, 105, 105, 105.0])[:, None]
    st = trade_stats(w, cl, np.arange(12) * HOUR_MS, "1970-01-01", "1970-01-02")
    assert st["trades"] == 2 and st["win_rate"] == 0.5 and st["median_hold_hours"] == 2.5
    assert np.isclose(st["average_win"], 0.10) and np.isclose(st["average_loss"], -0.05) and np.isclose(st["payoff_ratio"], 2.0)
    print("checks passed: stops, targets, trails, time stops, session exits and the one-a-day rule behave as written; "
          "no system's positions depend on a later price")


# ------------------------------------------------------------------------------- main
def main() -> int:
    t0 = time.time()
    cfg = load_config(ROOT / "config" / "research.toml")
    panel = load_panel()
    p, ep = cfg.strategy, cfg.engine_params()
    if "--check" in sys.argv:
        check(panel, p)
        return 0
    tg = compute_targets(panel, p)
    at_once = dataclasses.replace(p, smooth_bars=1)
    weights = {BOT: tg.weights.astype("float32")}
    info = {BOT: ("reference", "trend and cross-sectional momentum, eased in over 12 hours")}
    for name, (family, what, fn) in systems(panel).items():
        w, stop = size(panel, fn(), at_once)
        assert np.allclose(stop, tg.stop_frac, equal_nan=True)
        weights[name] = w.astype("float32")
        info[name] = (family, what)
        print(f"  .. {name} ({time.time() - t0:.0f}s)", flush=True)
    runs = engine_runs(panel, weights, ep, tg.stop_frac)
    out: dict = {"equity": EQUITY, "note": "Run after the holdout was seen. Textbook parameters, no tuning, no "
                                           "selection: every system is reported.", "systems": {}}
    lo, hi = PERIODS["dev"][0], PERIODS["holdout"][1]
    for name, w in weights.items():
        row = {"family": info[name][0], "what": info[name][1], **runs[name],
               "trades": trade_stats(w, panel.close, panel.times, lo, hi)}
        out["systems"][name] = row
        d, h, g, t = row["dev"], row["holdout"], row["holdout_before_costs"], row["trades"]
        print(f"{name:20s} {row['family']:9s} DEV {d['compound_daily']:+.3%}/d mdd {d['engine_max_drawdown']:5.1%}"
              f"{' KILL' if d['killed'] else '     '} | HOLDOUT {h['compound_daily']:+.3%}/d total {h['total_return']:+7.1%} "
              f"mdd {h['engine_max_drawdown']:5.1%}{' KILL' if h['killed'] else '     '} | before costs DEV "
              f"{row['dev_before_costs']['compound_daily']:+.3%} HOLDOUT {g['compound_daily']:+.3%} | traded/d "
              f"{d['turnover_per_day']:.2f} | trades {t['trades']:6d} win {t.get('win_rate', 0):.0%} payoff "
              f"{t.get('payoff_ratio') or 0:.2f} avg {t.get('average_trade', 0) * 1e4:+.0f}bp hold "
              f"{t.get('median_hold_hours', 0):.0f}h", flush=True)
    dump(ROOT / "research" / "results" / "playbook.json", out)
    print(f"written research/results/playbook.json, {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
