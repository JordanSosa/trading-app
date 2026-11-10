"""What would the frozen strategy gain from patient limit orders instead of crossing the spread?

The fast research backtest (``compounder.backtest.quick_returns``) trades every change of a held
weight at the open of the next bar and pays 8 bps of the traded amount (5 bps taker fee + 3 bps
slippage). ``simulate`` keeps its no-trade band and its accounting and adds how the order is executed:

  taker  exactly that baseline (``--check``: the same bar returns to 1e-12).
  maker  when the band logic wants a change D in a symbol at the open of bar i, a limit order for D
         rests at open[i] during bar i. A buy (D > 0) fills if and only if
         low[i] <= open[i] * (1 - margin), a sell if and only if high[i] >= open[i] * (1 + margin).
         A filled order changes the held weight from bar i on: it traded at open[i], so it earns
         bar i's open-to-open return exactly as a taker fill at the open would, and it pays the
         maker fee on |D| and no slippage. An unfilled order is cancelled at the end of the bar and
         the held weight stays what it was for bar i; at the next open the band logic is evaluated
         afresh against the new target. After ``patience`` unfilled bars in a row the symbol's next
         attempt crosses the spread at the open (taker fee + slippage). The count restarts when the
         symbol fills or the band logic no longer wants a change. A bar with no open, high or low
         never fills a limit order.

Grid: margin 0 / 2 / 5 bps x patience 1 / 2 / 4 bars at a maker fee of 2 bps, with 0 and 5 bps as
sensitivity (the fee does not change which orders fill, so those are the same runs re-priced).
Periods dev and holdout, bars chosen by open time, compounded to UTC days. Per scenario and period,
besides the daily statistics:

  turnover_per_day          traded |D| per day, in multiples of equity
  maker_share               share of the traded amount that filled as maker
  cost_per_day              fees + slippage per day
  gross_per_day             mean daily return before those costs (funding is in it)
  equivalent_cost_per_side  (the taker run's gross_per_day - this scenario's mean net daily return)
                            / the taker run's turnover_per_day: the flat cost per unit traded at
                            which the taker simulation would match this scenario
  equivalent_cost_se        its standard error, from the day-to-day spread of that difference
  unfilled_share            share of resting limit orders (one per symbol and bar) that did not fill
  unfilled_move_bps         their mean move from that bar's open to the next bar's open, signed in
                            the order's direction: positive means the price ran away from the order
                            (orders with either open missing are left out of this mean)

Limits. Hourly bars cannot show a fill; the rule above is an assumption, and the answer hangs on it:
  * With margin 0 every order fills by construction (every bar has low <= open <= high), so
    those rows are the taker run's trades at the maker fee: an upper bound, not a fill model.
  * With a margin the bar must trade through the limit price by that much. For a price without
    drift, the moves missed by the orders that then do not fill cost about the margin per unit
    ordered (optional stopping): the margin enters the result almost one for one, as a cost.
  * A trade through the price shows a fill only for an order that was in the book at exactly the
    open from the first instant of the bar, on the venue the bars come from. The open is not known
    before it prints, the order arrives after it, the bars are Binance's and the app trades on OKX.
  * No queue, partial fills, order sizes or tick sizes (a tick can be wider than the margin). An
    unfilled order is retried only at the next hourly open, and its taker fallback pays the same
    3 bps of slippage as any other trade although the price has run away.
  * As in quick_returns: no stops, no minimum order sizes, no risk governor.

Last, what a basis point is worth: the frozen strategy through the app's own engine (stops, minimum
order sizes, governor) from 10,000 and 1,000 USDT with the modelled fee and slippage scaled to 0, 2, 4,
6, 8 (the model) and 16 bps a side, as ``compounder backtest --cost-mult`` does. This says what an
execution method would be worth if it reached a given all-in cost; it does not say that one can.

Run after the holdout had been seen: descriptive, post hoc, nothing selected.

Usage: python scripts/research_execution.py [--check]
Writes research/results/execution.json.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
import sys
import time

import numpy as np

from rlib import BAND, COST, PERIODS, ROOT, dump, load_panel
from compounder.backtest import ms, open_to_open_returns, quick_returns, run_backtest, to_daily
from compounder.config import load_config
from compounder.data.panel import Panel
from compounder.metrics import daily_stats
from compounder.strategy import compute_targets

TAKER_FEE, SLIPPAGE = 0.0005, 0.0003                 # the baseline's 8 bps a side
MAKER_FEES = (0.0002, 0.0, 0.0005)                   # the main case first
MARGINS = (0.0, 0.0002, 0.0005)
PATIENCE = (1, 2, 4)
SERIES = ("gross", "maker", "taker", "orders", "unfilled", "runaway", "runaway_n")
LADDER = (0.0, 0.25, 0.5, 0.75, 1.0, 2.0)            # multiples of the modelled fee and slippage (8 bps a side)


def priced(sim: dict, maker_fee: float, taker_cost: float = TAKER_FEE + SLIPPAGE) -> tuple[np.ndarray, np.ndarray]:
    """Per-bar explicit cost and net return of a simulated run at the given fees."""
    cost = maker_fee * sim["maker"] + taker_cost * sim["taker"]
    return cost, sim["gross"] - cost


def simulate(panel: Panel, weights: np.ndarray, execution: str = "taker", margin: float = 0.0, patience: int = 1,
             maker_fee: float = MAKER_FEES[0], taker_fee: float = TAKER_FEE, slippage: float = SLIPPAGE,
             band: float = BAND) -> dict[str, np.ndarray]:
    """quick_returns' band logic and accounting under an execution model (module docstring). One value per bar:

    ``gross`` return before fees and slippage (after funding), ``maker`` / ``taker`` the amount traded each
    way, ``orders`` / ``unfilled`` the limit orders resting in the bar and those that did not fill, ``runaway``
    / ``runaway_n`` sum and count of the unfilled orders' moves to the next open (signed in the order's
    direction, both opens known), ``cost`` fees and slippage, ``ret`` the net return.
    """
    if execution not in ("taker", "maker"):
        raise ValueError(f"unknown execution model {execution!r}")
    if execution == "taker":
        patience = 0                                   # no patience at all: every change crosses the spread at once
    t, n = weights.shape
    w = np.nan_to_num(weights)
    move = open_to_open_returns(panel)                 # open[i+1] / open[i] - 1, NaN where unknown
    r = np.nan_to_num(move)
    with np.errstate(invalid="ignore"):                # row i of these looks at bar i only
        ok = np.isfinite(panel.open) & np.isfinite(panel.high) & np.isfinite(panel.low)
        buy_fills = ok & (panel.low <= panel.open * (1.0 - margin))
        sell_fills = ok & (panel.high >= panel.open * (1.0 + margin))
    out = {k: np.zeros(t) for k in SERIES}
    cur = np.zeros(n)                                  # held weights
    miss = np.zeros(n, dtype="int64")                  # consecutive unfilled bars per symbol
    for i in range(1, t):                              # bar 0 is flat: nothing was decided before it
        tgt = w[i - 1]                                 # decided at the close of bar i-1, traded at the open of bar i
        d = tgt - cur
        size = np.abs(d)
        want = (size > band * np.maximum(np.abs(tgt), np.abs(cur))) | ((tgt == 0.0) & (cur != 0.0))
        miss[~want] = 0
        cross = want & (miss >= patience)              # out of patience: taker at the open
        rest = want & ~cross                           # a limit order for d rests at open[i] during bar i
        fill = rest & np.where(d > 0.0, buy_fills[i], sell_fills[i])
        lost = rest & ~fill                            # cancelled at the end of the bar
        miss[lost] += 1
        miss[fill | cross] = 0
        cur = np.where(fill | cross, tgt, cur)         # held during bar i
        out["gross"][i] = cur @ r[i] - cur @ panel.funding[i]
        out["maker"][i], out["taker"][i] = size[fill].sum(), size[cross].sum()
        out["orders"][i], out["unfilled"][i] = rest.sum(), lost.sum()
        if lost.any():
            ran = (np.sign(d) * move[i])[lost]
            out["runaway"][i], out["runaway_n"][i] = np.nansum(ran), np.isfinite(ran).sum()
    out["cost"], out["ret"] = priced(out, maker_fee, taker_fee + slippage)
    return out


def summarise(sim: dict, maker_fee: float, sel: np.ndarray, close_ms: np.ndarray, taker: dict | None = None) -> dict:
    """One scenario over the bars in ``sel``, set against the taker run of the same bars (None: ``sim`` is it)."""
    cost, ret = priced(sim, maker_fee)
    daily = to_daily(ret[sel], close_ms[sel])[0]
    st = daily_stats(daily)
    tot = {k: float(sim[k][sel].sum()) for k in SERIES}
    days, traded, nan = st["days"], tot["maker"] + tot["taker"], float("nan")
    taker = taker or sim
    gap = to_daily(taker["gross"][sel], close_ms[sel])[0] - daily      # taker before costs - this scenario after them
    taker_turnover = float(taker["taker"][sel].sum()) / days
    row = {k: st[k] for k in ("days", "sharpe", "compound_daily", "total_return", "max_drawdown", "mean_daily")}
    row.update({
        "turnover_per_day": traded / days,
        "maker_share": tot["maker"] / traded if traded else nan,
        "cost_per_day": float(cost[sel].sum()) / days,
        "gross_per_day": float(to_daily(sim["gross"][sel], close_ms[sel])[0].mean()),
        "equivalent_cost_per_side": float(gap.mean()) / taker_turnover,
        "equivalent_cost_se": float(gap.std(ddof=1)) / math.sqrt(days) / taker_turnover,
        "limit_orders": int(tot["orders"]),
        "unfilled_share": tot["unfilled"] / tot["orders"] if tot["orders"] else nan,
        "unfilled_move_bps": 1e4 * tot["runaway"] / tot["runaway_n"] if tot["runaway_n"] else nan})
    return row


def cost_ladder(panel: Panel, cfg) -> dict:
    """The frozen strategy through the engine at multiples of the modelled trading costs."""
    ep, tg = cfg.engine_params(), compute_targets(panel, cfg.strategy)
    out = {}
    for mult in LADDER:
        params = dataclasses.replace(ep, fee_rate=ep.fee_rate * mult, slippage=ep.slippage * mult)
        row = {}
        for equity in (10_000.0, 1_000.0):
            for period in ("dev", "holdout"):
                m = run_backtest(panel, tg.weights, params, equity, *PERIODS[period], stop_frac=tg.stop_frac,
                                 record=True).metrics
                row[f"{equity:,.0f} USDT, {period}"] = {k: m[k] for k in (
                    "compound_daily", "total_return", "cagr", "sharpe", "engine_max_drawdown", "turnover_per_day",
                    "killed")}
        cost = (ep.fee_rate + ep.slippage) * mult
        out[f"{cost * 1e4:g} bps a side"] = {"cost_per_side": cost, **row}
        a, b = row["10,000 USDT, holdout"], row["1,000 USDT, holdout"]
        print(f"engine at {cost * 1e4:4.1f} bps a side: from 10,000 DEV {row['10,000 USDT, dev']['compound_daily']:+.3%}/d, "
              f"HOLDOUT {a['compound_daily']:+.3%}/d total {a['total_return']:+6.1%} ({a['cagr']:+.1%} a year); from "
              f"1,000 HOLDOUT total {b['total_return']:+6.1%} ({b['cagr']:+.1%} a year)", flush=True)
    return out


# ----------------------------------------------------------------------------- checks
def reference(panel: Panel, weights: np.ndarray, margin: float, patience: int, maker_fee: float) -> np.ndarray:
    """The maker rules written out for one symbol and one bar at a time (slow; ``--check`` only).

    Columns: net return, then as SERIES[1:] (amount filled as maker and traded as taker, limit orders, the
    unfilled ones, the sum and the count of their moves to the next open).
    """
    t, n = weights.shape
    out = np.zeros((t, 7))
    for j in range(n):
        o, h, l, f, w = (x[:, j].tolist() for x in (panel.open, panel.high, panel.low, panel.funding, weights))
        held, missed = 0.0, 0
        for i in range(1, t):
            target = 0.0 if math.isnan(w[i - 1]) else w[i - 1]
            change = target - held
            earned = o[i + 1] / o[i] - 1.0 if i + 1 < t else math.nan      # NaN when either open is missing
            known = math.isfinite(earned)
            if abs(change) > BAND * max(abs(target), abs(held)) or (target == 0.0 and held != 0.0):
                bar = math.isfinite(o[i]) and math.isfinite(h[i]) and math.isfinite(l[i])
                if missed >= patience:
                    held, missed = target, 0
                    out[i, :3] += (-(TAKER_FEE + SLIPPAGE) * abs(change), 0.0, abs(change))
                elif bar and (l[i] <= o[i] * (1.0 - margin) if change > 0 else h[i] >= o[i] * (1.0 + margin)):
                    held, missed = target, 0
                    out[i, :4] += (-maker_fee * abs(change), abs(change), 0.0, 1.0)
                else:
                    missed += 1
                    out[i, 3:] += (1.0, 1.0, (earned if change > 0 else -earned) if known else 0.0, float(known))
            else:
                missed = 0
            out[i, 0] += held * (earned if known else 0.0) - held * f[i]
    return out


def check(panel: Panel, p) -> None:
    """Self-checks: the baseline is reproduced, the maker rules are what the docstring says, nothing looks ahead."""
    w = compute_targets(panel, p).weights
    close_ms, every, eight = panel.times + panel.interval_ms, np.ones(panel.n_bars, dtype=bool), TAKER_FEE + SLIPPAGE
    # 0. The taker model is the repo's baseline, and its equivalent cost is the 8 bps it pays.
    ref, _, turn = quick_returns(panel, w, cost=COST, band=BAND)
    taker = simulate(panel, w)
    worst = float(np.abs(taker["ret"] - ref).max())
    assert worst < 1e-12 and abs(taker["taker"].mean() - turn) < 1e-12 and not taker["orders"].any()
    assert abs(summarise(taker, 0.0, every, close_ms)["equivalent_cost_per_side"] - eight) < 5e-6

    # 1. Limit orders that always fill and pay 8 bps are the taker run, bar for bar. A margin of -1 fills on every
    #    bar that exists, so this runs on the symbols that miss no bar after their first one.
    ok = np.isfinite(panel.open) & np.isfinite(panel.high) & np.isfinite(panel.low)
    complete = ok.any(axis=0) & (ok | ~np.maximum.accumulate(ok, axis=0)).all(axis=0)
    wc = np.where(complete, w, 0.0)
    a, b = simulate(panel, wc), simulate(panel, wc, "maker", margin=-1.0, patience=1, maker_fee=eight)
    assert np.array_equal(a["ret"], b["ret"]) and np.array_equal(a["taker"], b["maker"]) and a["taker"].sum() > 100
    assert not b["taker"].any() and not b["unfilled"].any() and b["orders"].sum() > 100_000

    # 2. No look-ahead, on 3,000 bars of the 25 symbols with the most non-zero weights there, with the weights
    #    recomputed from the changed prices. Replace every price from bar k on: no result before bar k-1 may move
    #    (bar k-1 earns up to open[k]). Leave open[k] alone and nothing before bar k may move: a limit order
    #    resting in bar k-1 is not filled by anything that happens later.
    lo, n, ks = 30_000, 3_000, (2_387, 2_458, 2_499, 2_500)
    most = np.sort(np.argsort(-(w[lo:lo + n] != 0).sum(axis=0), kind="stable")[:25])
    small = panel.select([panel.symbols[j] for j in most]).slice(lo, lo + n)
    ws = compute_targets(small, p).weights
    assert ((ws[:ks[0]] != 0).sum(axis=0) > 100).sum() >= 20, "the check symbols hardly trade"
    rng = np.random.default_rng(7)
    grid = list(itertools.product(MARGINS[1:], PATIENCE))
    base = [simulate(small, ws, "maker", m, pt) for m, pt in grid]
    for (m, pt), s in zip(grid, base):               # the test needs fills, misses, fallbacks and orders in bar k-1
        before = {x: s[x][:ks[0]].sum() for x in SERIES}
        assert before["maker"] > 0 and before["runaway_n"] > 100 and (before["taker"] > 0 or pt == max(PATIENCE))
        assert all(s["orders"][k - 1] > 0 for k in ks), "no limit order rests in the bar before the change"
    for k, keep_open in itertools.product(ks, (False, True)):
        # order="K": a copy in another memory layout would round compute_targets' row sums differently (1e-17).
        f = {x: getattr(small, x).copy(order="K") for x in ("open", "high", "low", "close")}
        for x in f:
            f[x][k:] *= np.exp(rng.normal(0.0, 0.02, f[x][k:].shape))
        if keep_open:
            f["open"][k] = small.open[k]
        moved = dataclasses.replace(small, **f)
        w2 = compute_targets(moved, p).weights
        assert np.array_equal(ws[:k], w2[:k]) and not np.array_equal(ws[k], w2[k])
        stop = k if keep_open else k - 1
        for (m, pt), s in zip(grid, base):
            alt = simulate(moved, w2, "maker", m, pt)
            assert all(np.array_equal(s[x][:stop], alt[x][:stop]) for x in s), (k, keep_open, m, pt)
            assert not np.array_equal(s["ret"][stop:], alt["ret"][stop:]), "the perturbation should change what follows"

    # 3. A margin nothing can reach and patience 1: no limit order fills and every trade crosses the spread.
    none = simulate(panel, w, "maker", margin=np.inf, patience=1)
    row = summarise(none, MAKER_FEES[0], every, close_ms, taker)
    assert row["maker_share"] == 0.0 and row["turnover_per_day"] > 0 and row["unfilled_share"] == 1.0
    assert np.array_equal(none["cost"], eight * none["taker"]) and none["orders"].sum() > 100_000

    # 4. The vectorised simulation is the per-symbol transcription of the rules: on the slice for every margin and
    #    patience, and on the whole panel (orders that meet missing bars included) for one of them.
    cases = [(small, ws, m, pt, s) for (m, pt), s in zip(grid, base)]
    cases.append((panel, w, MARGINS[2], PATIENCE[1], simulate(panel, w, "maker", MARGINS[2], PATIENCE[1])))
    for pan, wts, m, pt, s in cases:
        slow = reference(pan, wts, m, pt, MAKER_FEES[0])
        assert max(np.abs(slow[:, c] - s[x]).max() for c, x in enumerate(("ret",) + SERIES[1:])) < 1e-12
        assert s["unfilled"].sum() > 100 and s["runaway_n"].sum() > 100
    print(f"checks passed: taker reproduces quick_returns (largest bar difference {worst:.1e}); limit orders that "
          f"always fill at 8 bps reproduce the taker run exactly ({int(complete.sum())} symbols with complete bars, "
          f"{int(b['orders'].sum()):,} orders); no result before bar k-1 depends on a price from bar k on, and none "
          f"before bar k on anything later than open[k] ({len(ks) * 2 * len(grid)} perturbed runs); with an "
          f"unreachable margin and patience 1 nothing fills as maker and all {int(none['orders'].sum()):,} limit "
          f"orders go unfilled; the vectorised runs match a per-symbol reference loop on the slice ({len(grid)} "
          f"settings) and on the whole panel (margin {MARGINS[2] * 1e4:g} bps, patience {PATIENCE[1]})")


# ------------------------------------------------------------------------------- main
def show(label: str, rows: dict) -> None:
    for period, r in rows.items():
        print(f"{label:45s} {period:7s} sharpe {r['sharpe']:5.2f}  /day {r['compound_daily']:+.4%}  total "
              f"{r['total_return']:+9.1%}  mdd {r['max_drawdown']:5.1%}  turn/d {r['turnover_per_day']:.3f}  maker "
              f"{r['maker_share']:6.1%}  cost/d {r['cost_per_day']:.4%}  gross/d {r['gross_per_day']:+.4%}  equivalent "
              f"{r['equivalent_cost_per_side'] * 1e4:5.2f} +- {r['equivalent_cost_se'] * 1e4:4.2f} bps  unfilled "
              f"{r['unfilled_share']:6.2%} ({r['unfilled_move_bps']:+6.1f} bps)", flush=True)


def main() -> int:
    t0 = time.time()
    cfg = load_config(ROOT / "config" / "paper.toml")
    panel = load_panel()
    if "--check" in sys.argv:
        check(panel, cfg.strategy)
        return 0
    w = compute_targets(panel, cfg.strategy).weights
    close_ms = panel.times + panel.interval_ms
    sels = {name: (panel.times >= ms(PERIODS[name][0])) & (panel.times < ms(PERIODS[name][1]))
            for name in ("dev", "holdout")}
    out: dict = {"note": "Run after the holdout was seen. Every scenario is reported and none is selected. The fill "
                         "rule is an assumption on hourly bars: see the docstring of scripts/research_execution.py.",
                 "periods": {name: list(PERIODS[name]) for name in sels}, "band": BAND, "taker_fee": TAKER_FEE,
                 "slippage": SLIPPAGE, "maker_fees": list(MAKER_FEES), "margins": list(MARGINS),
                 "patience": list(PATIENCE), "scenarios": {}}
    taker = simulate(panel, w)
    base = {name: summarise(taker, 0.0, sel, close_ms) for name, sel in sels.items()}
    out["scenarios"]["taker"] = {"execution": "taker", **base}
    show("taker", base)
    for margin, patience in itertools.product(MARGINS, PATIENCE):
        sim = simulate(panel, w, "maker", margin, patience)
        for fee in MAKER_FEES:
            label = f"maker fee {fee * 1e4:g} bps, margin {margin * 1e4:g} bps, patience {patience}"
            rows = {name: summarise(sim, fee, sel, close_ms, taker) for name, sel in sels.items()}
            out["scenarios"][label] = {"execution": "maker", "maker_fee": fee, "margin": margin, "patience": patience,
                                       **rows}
            show(label, rows)
    out["engine_cost_ladder"] = cost_ladder(panel, cfg)
    dump(ROOT / "research" / "results" / "execution.json", out)
    print(f"written research/results/execution.json, {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
