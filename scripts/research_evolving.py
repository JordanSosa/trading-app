"""Would a bot that re-evaluates and refines its own strategy on recent results do better?

A walk-forward simulation of exactly that. The "evolving" bot holds a pool of
candidate strategies. At every review it scores each candidate on its own
realised, after-cost results over a look-back window and moves its capital to
the winners. Every decision uses only results that were already in when it was
made (a review at midnight UTC uses the daily results up to that midnight and
its first trades fill at 01:00), so the whole path is out of sample for the
procedure, however the pool was built. ``--check`` tests that on perturbed
data.

Pools
  rules          the eleven textbook indicator rules on 1-hour, 4-hour and
                 daily bars (33 candidates): a bot that has to find its
                 strategy by evolving. The 1-hour versions act on every signal
                 at once, with no smoothing: the day-trading speed. The others
                 are eased in over 12 hours like the bot's own targets.
  rules_and_bot  the same 33 plus the bot's trend sleeve, its cross-sectional
                 sleeve and the bot itself (36). The three additions come out
                 of this project's research and are known to have done well,
                 so this pool is flattered by hindsight.
  settings       sixteen settings of the bot itself: four sets of trend
                 horizons times four cross-sectional lookbacks (the frozen
                 default is one). This is "refining its own parameters".

Evolution policies (every one is reported, none is selected)
  look back 7 / 30 / 90 / 180 / 365 days or over everything since 2020-04-01,
  review every 1 / 7 / 30 / 30 / 30 / 30 days, and hold either the best
  candidate, the best three in equal parts, or every candidate whose record
  over the look-back is positive (cash if none). The score is the Sharpe ratio
  of the candidate's own daily returns over the look-back.

Baselines: the same pool held in equal parts and never changed, and the frozen
bot. Evaluation starts 2021-04-01, when every policy has a full year behind it.

Two measurements of every policy
  fast    the linear research backtest (8 bps a side, 25% no-trade band, no
          stops or governor) of the *combined* target weights, so switching
          between candidates is paid for like any other trade and opposite
          positions net out. The candidates are scored on this basis.
  engine  the app's own engine (stops, minimum order sizes, margin, risk
          governor with its 40% kill switch) from 1,000 USDT, started on
          2021-04-01 and again on 2025-01-01.

Last, what a paper run can tell: the range of results after 1 to 24 months
for the frozen bot with the edge measured out of sample, and with no edge at
all (the same days with the mean removed and the trading costs left in).

Run after the holdout had been seen: descriptive, post hoc, nothing selected.

Usage: python scripts/research_evolving.py [--check]
Writes research/results/evolving.json.
"""

from __future__ import annotations

import dataclasses
import itertools
import multiprocessing
import sys
import time

import numpy as np
import pandas as pd

from research_indicators import OTHER_TREND, REVERSION, TREND, coarse_bars, size, to_hourly
from rlib import BAND, COST, ROOT, dump, load_panel
from compounder.backtest import ms, quick_returns, run_backtest, to_daily
from compounder.config import load_config
from compounder.data.panel import Panel
from compounder.metrics import block_bootstrap, daily_stats
from compounder.strategy import compute_targets, universe_mask

EVAL_START, SPLIT, EVAL_END = "2021-04-01", "2025-01-01", "2026-11-01"
HISTORY_START = "2020-04-01"                                       # where "everything so far" begins
POLICIES = [(7, 1), (30, 7), (90, 30), (180, 30), (365, 30), (0, 30)]     # (look-back days, review every N days);
#                                                                           look-back 0 = everything so far
MODES = ("best one", "best three", "all in profit")
EQUAL = "equal parts, never changed"
HORIZONS = {"1-4d": (24, 48, 96), "2-32d": (48, 96, 192, 384, 768), "4-16d": (96, 192, 384), "8-32d": (192, 384, 768)}
XS = {"none": 0, "7d": 168, "14d": 336, "30d": 720}
PAPER_EQUITY = 1_000.0
EVIDENCE_DAYS = (30, 90, 180, 365, 730)
ANNUAL = np.sqrt(365.0)
FROZEN = "bot (frozen default)"


# ----------------------------------------------------------------------- candidates
def rule_candidates(panel: Panel, p, timeframes=((1, "1h"), (4, "4h"), (24, "1d"))) -> dict:
    """name -> (kind, function returning target weights)."""
    out = {}
    for hours, tf in timeframes:
        sizing = dataclasses.replace(p, smooth_bars=1) if hours == 1 else p

        def make(fn, hours=hours, sizing=sizing):
            return lambda: size(panel, to_hourly(fn(coarse_bars(panel, hours)), panel, hours), sizing)[0]

        def vote(hours=hours, sizing=sizing):
            bars = coarse_bars(panel, hours)
            return size(panel, np.mean([to_hourly(f(bars), panel, hours) for f in TREND.values()], axis=0), sizing)[0]

        for name, fn in TREND.items():
            out[f"{name} {tf}"] = ("continuation", make(fn))
        out[f"trend_vote {tf}"] = ("continuation", vote)
        for name, fn in OTHER_TREND.items():
            out[f"{name} {tf}"] = ("continuation", make(fn))
        for name, fn in REVERSION.items():
            out[f"{name} {tf}"] = ("reversal", make(fn))
    out["bot: trend sleeve"] = ("bot", lambda: compute_targets(panel, dataclasses.replace(p, xsmom_weight=0.0)).weights)
    out["bot: cross-sectional sleeve"] = ("bot", lambda: compute_targets(
        panel, dataclasses.replace(p, trend_weight=0.0, short_scale=1.0)).weights)
    out[FROZEN] = ("bot", lambda: compute_targets(panel, p).weights)
    return out


def setting_candidates(panel: Panel, p) -> dict:
    out = {}
    for (hn, hz), (xn, xl) in itertools.product(HORIZONS.items(), XS.items()):
        params = dataclasses.replace(p, trend_horizons=hz, xsmom_weight=1.0 if xl else 0.0, xsmom_lookback=xl or 336)
        name = f"trend {hn}, cross-sectional {xn}" + (" (frozen default)" if params == p else "")
        out[name] = ("setting", lambda params=params: compute_targets(panel, params).weights)
    if sum("(frozen default)" in n for n in out) != 1:
        raise RuntimeError("the settings grid must contain the frozen default exactly once")
    return out


# ------------------------------------------------------------------------ the evolution
def allocations(daily: np.ndarray, first: int, look: int, review: int, mode: str,
                origin: int = 0) -> tuple[np.ndarray, dict]:
    """Per-day allocation over candidates and what the reviews looked like.

    Row ``d`` is the allocation in force on day ``d``. It is set at the latest
    review on or before ``d`` and uses the candidates' returns on the ``look``
    days strictly before that review (``look`` 0: every day from ``origin``).
    Rows sum to 1, or to 0 when "all in profit" finds nothing with a positive
    record. Rows before ``first`` are 0.
    """
    n_days, n_cand = daily.shape
    alloc = np.zeros((n_days, n_cand))
    current = np.zeros(n_cand)
    leaders, looked, path = [], [], []                             # path: (day index, candidate) at each change of leader
    for d in range(first, n_days):
        if (d - first) % review == 0:
            past = daily[max(0, d - look):d] if look else daily[origin:d]
            sd = past.std(axis=0, ddof=1) if len(past) > 2 else np.zeros(n_cand)
            with np.errstate(invalid="ignore", divide="ignore"):
                score = np.where(sd > 0, past.mean(axis=0) / sd, -np.inf)
            order = np.argsort(-score, kind="stable")
            if mode == "best one":
                chosen = order[:1]
            elif mode == "best three":
                chosen = order[:3]
            elif mode == "all in profit":
                chosen = np.nonzero(score > 0)[0]
            else:
                raise ValueError(mode)
            chosen = chosen[np.isfinite(score[chosen])]
            current = np.zeros(n_cand)
            if len(chosen):
                current[chosen] = 1.0 / len(chosen)
                looked.append(float(score[chosen].mean()) * ANNUAL)
            leaders.append(int(order[0]))
            if len(leaders) == 1 or leaders[-1] != leaders[-2]:
                path.append((d, int(order[0])))
        alloc[d] = current
    info = {"reviews": len(leaders), "leader_path": path,
            "leader_changed_at_reviews": float(np.mean(np.diff(leaders) != 0)) if len(leaders) > 1 else 0.0,
            # What the chosen candidates' record looked like at the moment they were chosen (annualised Sharpe).
            "chosen_looked_like_sharpe": float(np.mean(looked)) if looked else float("nan"),
            "share_of_days_in_cash": float((alloc[first:].sum(axis=1) == 0).mean())}
    return alloc, info


def label_of(look: int, review: int, mode: str) -> str:
    return f"look back {f'{look}d' if look else 'over everything'}, review every {review}d: {mode}"


def plans_for(daily: np.ndarray, first: int, policies=POLICIES, origin: int = 0) -> dict[str, tuple[np.ndarray, dict]]:
    plans = {}
    for (look, review), mode in itertools.product(policies, MODES):
        plans[label_of(look, review, mode)] = allocations(daily, first, look, review, mode, origin)
    equal = np.full(daily.shape, 1.0 / daily.shape[1])
    plans[EQUAL] = (equal, {"reviews": 0, "leader_path": [], "leader_changed_at_reviews": 0.0,
                            "chosen_looked_like_sharpe": float("nan"), "share_of_days_in_cash": 0.0})
    return plans


def combine(weights: list[np.ndarray], plans: dict, ends: np.ndarray, close_ms: np.ndarray) -> dict[str, np.ndarray]:
    """Each plan's target weights: the candidates' weights mixed by the allocation in force at each hourly bar.

    The weights decided at the close of an hourly bar use the allocation of the
    UTC day that bar belongs to (a bar closing at 00:00 belongs to the day that
    just ended). The daily result a review needs last is complete at the 00:00
    open, so the first weights it can touch are those decided at 01:00.
    """
    row = np.clip(np.searchsorted(ends, close_ms), 0, len(ends) - 1)
    out = {label: np.zeros(weights[0].shape, dtype="float32") for label in plans}
    for c, w in enumerate(weights):
        for label, (alloc, _) in plans.items():
            a = alloc[row, c].astype("float32")
            if a.any():
                out[label] += w * a[:, None]
    return out


def candidate_series(panel: Panel, cands: dict, cost: float = COST, band: float = BAND, progress: bool = False):
    """Every candidate's float32 weights and its own daily returns. Returns (weights, daily matrix, day ends, turnover)."""
    weights, series, turns, ends = [], [], [], None
    t0 = time.time()
    for name in cands:
        w = np.nan_to_num(cands[name][1]()).astype("float32")
        ret, cms, turn = quick_returns(panel, w.astype("float64"), cost=cost, band=band)
        daily, ends = to_daily(ret, cms)
        weights.append(w)
        series.append(daily)
        turns.append(turn * 24)
        if progress:
            print(f"  .. {name} ({time.time() - t0:.0f}s)", flush=True)
    return weights, np.column_stack(series), ends, turns


# ------------------------------------------------------------------------- statistics
def stats(daily: np.ndarray, ends: np.ndarray, lo: str, hi: str) -> dict:
    sel = (ends > ms(lo)) & (ends <= ms(hi))
    st = daily_stats(daily[sel])
    return {k: st[k] for k in ("days", "compound_daily", "total_return", "cagr", "sharpe", "daily_vol", "max_drawdown",
                               "worst_day")}


def windows(daily: np.ndarray, ends: np.ndarray) -> dict:
    return {"all": stats(daily, ends, EVAL_START, EVAL_END), "to_2024": stats(daily, ends, EVAL_START, SPLIT),
            "from_2025": stats(daily, ends, SPLIT, EVAL_END)}


def at_risk(daily: np.ndarray, ends: np.ndarray, ref_vol: float) -> dict:
    """The same series scaled by one constant so its daily volatility over the evaluation equals ``ref_vol``.

    The fast backtest is linear in the weights, so this is exactly what holding
    the same positions at that constant multiple would have returned.
    """
    sel = (ends > ms(EVAL_START)) & (ends <= ms(EVAL_END))
    own = float(daily[sel].std(ddof=1))
    if not own > 0:
        return {}
    k = ref_vol / own
    w = windows(daily * k, ends)
    return {"scale": k, **{name: {x: w[name][x] for x in ("compound_daily", "total_return", "max_drawdown")} for name in w}}


def persistence(daily: np.ndarray, look: int) -> dict | None:
    """Does a candidate's ranking over one ``look``-day block say anything about the next block?"""
    k = len(daily) // look
    if k < 2:
        return None
    blocks = daily[:k * look].reshape(k, look, -1)
    sd = blocks.std(axis=1, ddof=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        sharpe = np.where(sd > 0, blocks.mean(axis=1) / sd, np.nan) * ANNUAL
    corr, pct, then, nxt, med = [], [], [], [], []
    for b in range(k - 1):
        ok = np.isfinite(sharpe[b]) & np.isfinite(sharpe[b + 1])
        if ok.sum() < 5:
            continue
        a, c = sharpe[b][ok], sharpe[b + 1][ok]
        ra, rc = pd.Series(a).rank().to_numpy(), pd.Series(c).rank().to_numpy()
        corr.append(float(np.corrcoef(ra, rc)[0, 1]))
        best = int(np.argmax(a))
        pct.append(float((rc[best] - 1) / (len(c) - 1)))
        then.append(float(a[best]))
        nxt.append(float(c[best]))
        med.append(float(np.median(c)))
    if not corr:
        return None
    return {"candidates": int(daily.shape[1]), "block_pairs": len(corr), "rank_correlation": float(np.mean(corr)),
            "share_of_pairs_positive": float(np.mean(np.array(corr) > 0)),
            # The candidate that was best in one block: how it looked then, what it did next, and where it ranked
            # next (0 = worst of the pool, 1 = best, 0.5 = no information).
            "best_looked_like_sharpe": float(np.mean(then)), "best_next_sharpe": float(np.mean(nxt)),
            "median_candidate_next_sharpe": float(np.mean(med)), "best_next_percentile": float(np.mean(pct))}


def evidence(daily: np.ndarray, cost_per_day: float, horizons=EVIDENCE_DAYS, paths: int = 20_000) -> dict:
    """What a run of ``h`` days can show: resampled outcomes with the measured edge and with none.

    "No edge" is the same days with their mean removed and the trading costs
    left in: the signals predict nothing and the bot still pays to trade.
    """
    null = daily - daily.mean() - cost_per_day
    out = {}
    for h in horizons:
        row = {}
        finals = {}
        for key, series in (("edge_as_measured", daily), ("no_edge", null)):
            final = np.prod(1.0 + block_bootstrap(series, h, n_paths=paths, block=10, seed=7), axis=1) - 1.0
            q = np.quantile(final, [0.05, 0.5, 0.95])
            finals[key] = final
            row[key] = {"p05": float(q[0]), "median": float(q[1]), "p95": float(q[2]),
                        "prob_loss": float((final < 0).mean())}
        # How often the bot with the edge would end above what 95% of no-edge runs reach: the chance the run proves it.
        row["chance_the_edge_shows"] = float((finals["edge_as_measured"] > row["no_edge"]["p95"]).mean())
        # Standard error of an annualised Sharpe ratio measured over h days.
        row["sharpe_standard_error"] = float(np.sqrt(365.0 / h))
        out[str(h)] = row
    return out


# ---------------------------------------------------------------------- the app's engine
_JOB: dict = {}                                                  # inherited by the forked workers


def engine_summary(res) -> dict:
    m = res.metrics
    out = {"end_value": float(m["final_equity"]), "total_return": float(m["total_return"]),
           "compound_daily": float(m["compound_daily"]), "sharpe": float(m["sharpe"]),
           "max_drawdown": float(m["engine_max_drawdown"]), "killed": bool(m["killed"]),
           "liquidated": bool(m["liquidated"]), "fills": int(m["trades"]), "stops": int(m["stops"]),
           "fees_paid": float(m["fees_paid"])}
    if out["killed"] or out["liquidated"]:
        active = np.nonzero(res.daily_returns != 0)[0]
        if len(active):
            out["stopped_on"] = str(np.datetime64(int(res.day_end_ms[active[-1]]) - 1, "ms").astype("datetime64[D]"))
            out["days_until_stopped"] = int(active[-1]) + 1
    return out


def _engine_task(task: tuple[str, str]) -> tuple[str, str, dict]:
    label, start = task
    res = run_backtest(_JOB["panel"], _JOB["weights"][label].astype("float64"), _JOB["ep"], PAPER_EQUITY, start,
                       EVAL_END, stop_frac=_JOB["stop"], record=False)
    return label, start, engine_summary(res)


def engine_runs(panel: Panel, combined: dict[str, np.ndarray], ep, stop: np.ndarray) -> dict[str, dict]:
    """Every plan through the app's engine from 1,000 USDT, started at the evaluation start and at the split."""
    _JOB.update(panel=panel, weights=combined, ep=ep, stop=stop)
    tasks = [(label, start) for label in combined for start in (EVAL_START, SPLIT)]
    out: dict[str, dict] = {label: {} for label in combined}
    try:
        ctx = multiprocessing.get_context("fork")
    except ValueError:
        ctx = None
    if ctx is None:
        results = map(_engine_task, tasks)
    else:
        pool = ctx.Pool(processes=min(4, multiprocessing.cpu_count()))
        results = pool.imap_unordered(_engine_task, tasks)
    for label, start, row in results:
        out[label]["from_2021" if start == EVAL_START else "from_2025"] = row
    if ctx is not None:
        pool.close()
        pool.join()
    _JOB.clear()
    return out


# ----------------------------------------------------------------------------- checks
def check(panel: Panel, p) -> None:
    """Self-checks: reviews never see the future, and an allocation earns exactly its candidates' returns."""
    rng = np.random.default_rng(7)
    # 1. A review's allocation depends only on the days before it.
    fake = rng.normal(0.0003, 0.01, (420, 9))
    other = fake.copy()
    k = 263
    other[k:] = rng.normal(0.0, 0.03, other[k:].shape)
    for (look, review), mode in itertools.product(POLICIES, MODES):
        a, info = allocations(fake, 120, look, review, mode, origin=40)
        b, _ = allocations(other, 120, look, review, mode, origin=40)
        assert np.array_equal(a[:k + 1], b[:k + 1]), (look, review, mode)
        assert not a[:120].any() and np.all(np.isin(np.round(a[120:].sum(axis=1), 12), (0.0, 1.0)))
        assert mode == "all in profit" or np.all(a[120:].sum(axis=1) > 0.999)
        assert info["reviews"] == len(range(120, 420, review))
    d = 120 + 7 * 11
    for look, past in ((30, fake[d - 30:d]), (0, fake[40:d])):
        one, _ = allocations(fake, 120, look, 7, "best one", origin=40)
        assert one[d].argmax() == int(np.argmax(past.mean(axis=0) / past.std(axis=0, ddof=1))) and one[d].max() == 1.0

    # A 500-day slice of the 25 symbols most often in the universe, starting and ending on a day boundary.
    lo, n = 24 * 833, 12_000
    member = universe_mask(panel, p.universe_n)[lo:lo + n].sum(axis=0)
    small = panel.select([panel.symbols[j] for j in np.sort(np.argsort(-member, kind="stable")[:25])]).slice(lo, lo + n)
    close_ms = small.times + small.interval_ms

    def pool(pan: Panel) -> dict:
        full = rule_candidates(pan, p, timeframes=((1, "1h"), (4, "4h")))
        keep = ("macd 1h", "rsi 1h", "donchian 4h", "stochastic 4h", "bot: trend sleeve", FROZEN)
        return {name: full[name] for name in keep}

    cands = pool(small)
    weights, daily, ends, _ = candidate_series(small, cands)
    assert all(np.abs(w).sum() > 0 for w in weights), "a check candidate never trades"
    first = 150

    # 2. Holding one candidate all the time reproduces that candidate, costs and no-trade band included.
    for c in (0, 3, 5):
        solo = np.zeros_like(daily)
        solo[:, c] = 1.0
        mixed = combine(weights, {"solo": (solo, {})}, ends, close_ms)["solo"]
        ret, cms, _ = quick_returns(small, mixed.astype("float64"), cost=COST, band=BAND)
        assert np.array_equal(mixed, weights[c]), c
        assert np.allclose(to_daily(ret, cms)[0], daily[:, c], rtol=0.0, atol=1e-12), c     # summation-order noise only

    # 3. Timing: a switch decided for day s takes effect in the weights decided at 01:00 on day s, not before, not later.
    s = 300
    switch = np.zeros_like(daily)
    switch[:s, 0], switch[s:, 5] = 1.0, 1.0
    mixed = combine(weights, {"switch": (switch, {})}, ends, close_ms)["switch"]
    k = int(np.nonzero(close_ms == ends[s - 1])[0][0]) + 1          # the hourly row closing at 01:00 on day s
    assert np.array_equal(mixed[:k], weights[0][:k]) and np.array_equal(mixed[k:], weights[5][k:])
    assert not np.array_equal(weights[0][k], weights[5][k])

    # 4. End to end: replace every price from hourly bar kk onward; the combined weights decided before kk must not move.
    short = [(7, 1), (30, 7), (90, 30), (0, 30)]
    plans = plans_for(daily, first, policies=short, origin=20)
    base = combine(weights, plans, ends, close_ms)
    for kk in (24 * 320, 24 * 320 + 1, 24 * 351 + 13):
        f = {x: getattr(small, x).copy() for x in ("open", "high", "low", "close")}
        for x in f:
            f[x][kk:] *= np.exp(np.cumsum(rng.normal(0.0, 0.02, f[x][kk:].shape[0])))[:, None]
        moved = Panel(**{**small.__dict__, **f})
        w2, d2, e2, _ = candidate_series(moved, pool(moved))
        alt = combine(w2, plans_for(d2, first, policies=short, origin=20), e2, close_ms)
        changed = 0
        for label in base:
            assert np.array_equal(base[label][:kk], alt[label][:kk]), (label, kk)
            changed += int(not np.array_equal(base[label][kk:], alt[label][kk:]))
        assert changed == len(base), "the perturbation should change every plan afterwards"

    # 5. The engine stage: the forked workers return what a direct run returns.
    ep = load_config(ROOT / "config" / "paper.toml").engine_params()
    stop = compute_targets(small, p).stop_frac
    some = {label: base[label] for label in list(base)[:2]}
    global EVAL_START, SPLIT, EVAL_END
    saved = EVAL_START, SPLIT, EVAL_END

    def day(i: int) -> str:
        return str(np.datetime64(int(small.times[0]) + i * 86_400_000, "ms").astype("datetime64[D]"))

    EVAL_START, SPLIT, EVAL_END = day(first), day(330), day(500)
    try:
        got = engine_runs(small, some, ep, stop)
        for label in some:
            for key, start in (("from_2021", EVAL_START), ("from_2025", SPLIT)):
                direct = engine_summary(run_backtest(small, some[label].astype("float64"), ep, PAPER_EQUITY, start,
                                                     EVAL_END, stop_frac=stop, record=False))
                assert got[label][key] == direct and direct["fills"] > 0, (label, key)
    finally:
        EVAL_START, SPLIT, EVAL_END = saved

    # 6. The evidence table: with no costs and no edge the two cases coincide; an edge moves the range up.
    r = rng.normal(0.0, 0.015, 600)
    same = evidence(r - r.mean(), 0.0, horizons=(90,), paths=2000)["90"]
    assert same["edge_as_measured"] == same["no_edge"]
    more = evidence(r - r.mean() + 0.002, 0.0003, horizons=(90,), paths=2000)["90"]
    assert more["edge_as_measured"]["median"] > more["no_edge"]["median"] and more["chance_the_edge_shows"] > 0.05
    print("checks passed: reviews use past days only; allocations earn their candidates' returns exactly; a switch "
          "first trades at 01:00 on its day; no combined weight depends on a later price; the engine stage matches "
          "direct runs")


# ------------------------------------------------------------------------------- main
def report_pool(name: str, members: list[int], names: list[str], kinds: list[str], weights: list[np.ndarray],
                daily_all: np.ndarray, ends: np.ndarray, close_ms: np.ndarray, ref_vol: float, panel: Panel, ep,
                stop: np.ndarray, groups: dict[str, list[int]], t0: float) -> dict:
    """Simulate every policy over the candidates in ``members`` and measure it both ways."""
    pool_names = [names[i] for i in members]
    daily_m = daily_all[:, members]
    first = int(np.searchsorted(ends, ms(EVAL_START), side="right"))
    origin = int(np.searchsorted(ends, ms(HISTORY_START), side="right"))
    print(f"\n=== pool '{name}': {len(members)} candidates", flush=True)
    plans = plans_for(daily_m, first, origin=origin)
    combined = combine([weights[i] for i in members], plans, ends, close_ms)
    engine = engine_runs(panel, combined, ep, stop)
    policies = {}
    for label, (alloc, info) in plans.items():
        ret, cms, turn = quick_returns(panel, combined.pop(label).astype("float64"), cost=COST, band=BAND)
        daily, e = to_daily(ret, cms)
        share = alloc[first:].mean(axis=0)
        top = np.argsort(-share, kind="stable")[:5]
        path = info.pop("leader_path")
        if len(path) <= 12:                                      # who led, from which review on (short paths only)
            info["leader_from"] = [[str(np.datetime64(int(ends[d]) - 86_400_000, "ms").astype("datetime64[D]")),
                                    pool_names[c]] for d, c in path]
        policies[label] = {
            "turnover_per_day": turn * 24, **info, **windows(daily, e), "at_bot_risk": at_risk(daily, e, ref_vol),
            # The same allocation applied to the candidates' own daily returns: no switching costs, no netting.
            "paper_mix": windows((alloc * daily_m).sum(axis=1), ends),
            "most_held": {pool_names[i]: float(share[i]) for i in top if share[i] > 0},
            "candidates_ever_held": int((alloc[first:].max(axis=0) > 0).sum()),
            "engine": engine[label]}
        a, b, c2 = (policies[label][x] for x in ("all", "to_2024", "from_2025"))
        g, h = engine[label]["from_2021"], engine[label]["from_2025"]
        print(f"  >> {label:55s} sharpe {a['sharpe']:5.2f} (to 2024 {b['sharpe']:5.2f}, from 2025 {c2['sharpe']:5.2f}) "
              f"looked like {info['chosen_looked_like_sharpe']:5.2f} | engine 1,000 -> {g['end_value']:7,.0f} "
              f"(mdd {g['max_drawdown']:4.0%}{', stopped ' + g.get('stopped_on', '?') if g['killed'] else ''}), from 2025 "
              f"-> {h['end_value']:6,.0f} (mdd {h['max_drawdown']:4.0%}"
              f"{', stopped ' + h.get('stopped_on', '?') if h['killed'] else ''})", flush=True)
    block = {"members": pool_names, "policies": policies, "persistence": {}}
    for key, idx in {"all": list(range(len(members))), **groups}.items():
        block["persistence"][key] = {str(look): persistence(daily_m[first:][:, idx], look) for look, _ in POLICIES if look}
        for look, row in block["persistence"][key].items():
            if row:
                print(f"  persistence [{key}, {row['candidates']} candidates] {look:>3s}d: rank correlation "
                      f"{row['rank_correlation']:+.2f} over {row['block_pairs']} pairs; the best looked like sharpe "
                      f"{row['best_looked_like_sharpe']:5.2f}, then made {row['best_next_sharpe']:5.2f} (median candidate "
                      f"{row['median_candidate_next_sharpe']:5.2f}), next percentile {row['best_next_percentile']:.2f}",
                      flush=True)
    print(f"  pool done, {time.time() - t0:.0f}s elapsed", flush=True)
    return block


def candidate_table(panel: Panel, names, kinds, weights, daily_m, ends, turns, ref_vol, before_costs: bool) -> dict:
    table = {}
    for c, name in enumerate(names):
        row = {"kind": kinds[c], "turnover_per_day": turns[c], **windows(daily_m[:, c], ends),
               "at_bot_risk": at_risk(daily_m[:, c], ends, ref_vol)}
        if before_costs:
            gross, cms, _ = quick_returns(panel, weights[c].astype("float64"), cost=0.0, band=BAND)
            row["before_trading_costs"] = windows(to_daily(gross, cms)[0], ends)
        table[name] = row
        a = row["all"]
        print(f"  {name:48s} sharpe {a['sharpe']:5.2f} (to 2024 {row['to_2024']['sharpe']:5.2f}, from 2025 "
              f"{row['from_2025']['sharpe']:5.2f})  /day {a['compound_daily']:+.3%}  turnover {turns[c]:5.2f}"
              + (f"  before costs {row['before_trading_costs']['all']['sharpe']:5.2f}"
                 if "before_trading_costs" in row else ""), flush=True)
    return table


def main() -> int:
    t0 = time.time()
    cfg = load_config(ROOT / "config" / "paper.toml")
    panel = load_panel()
    p, ep = cfg.strategy, cfg.engine_params()
    if "--check" in sys.argv:
        check(panel, p)
        return 0
    close_ms = panel.times + panel.interval_ms
    tg = compute_targets(panel, p)
    stop = tg.stop_frac
    out: dict = {"note": "Run after the holdout was seen. Every policy is reported and none is selected.",
                 "evaluation": [EVAL_START, EVAL_END], "split": SPLIT, "cost_per_side": COST, "band": BAND,
                 "paper_equity": PAPER_EQUITY,
                 "policies": [{"look_back_days": a or f"everything since {HISTORY_START}", "review_every_days": b}
                              for a, b in POLICIES], "modes": list(MODES), "candidates": {}, "pools": {}}

    # The frozen bot through the app's engine, and what a paper run of it can show.
    frozen = {}
    for key, start in (("from_2021", EVAL_START), ("from_2025", SPLIT)):
        res = run_backtest(panel, tg.weights, ep, PAPER_EQUITY, start, EVAL_END, stop_frac=stop, record=True)
        frozen[key] = engine_summary(res)
        if start == SPLIT:
            cost_per_day = res.metrics["turnover_per_day"] * (ep.fee_rate + ep.slippage)
            st = daily_stats(res.daily_returns)
            out["what_a_paper_run_can_show"] = {
                "basis": "frozen default from 1,000 USDT, engine, 2025-01-01 to 2026-10-31; 10-day block bootstrap",
                "days": int(st["days"]), "mean_daily": float(st["mean_daily"]), "daily_vol": float(st["daily_vol"]),
                "sharpe": float(st["sharpe"]), "trading_cost_per_day": float(cost_per_day),
                "trading_cost_per_year": float(cost_per_day * 365), "after_days": evidence(res.daily_returns, cost_per_day)}
            print(f"frozen bot, engine, from 2025: mean/day {st['mean_daily']:+.4%}, vol {st['daily_vol']:.3%}, sharpe "
                  f"{st['sharpe']:.2f}, trading costs {cost_per_day:.4%}/day = {cost_per_day * 365:.1%}/year")
            for h, row in out["what_a_paper_run_can_show"]["after_days"].items():
                e, z = row["edge_as_measured"], row["no_edge"]
                print(f"  after {h:>3s} days: with the edge {e['p05']:+.0%} .. {e['p95']:+.0%} (median {e['median']:+.0%}, "
                      f"P(loss) {e['prob_loss']:.0%}); with none {z['p05']:+.0%} .. {z['p95']:+.0%} (median "
                      f"{z['median']:+.0%}, P(loss) {z['prob_loss']:.0%}); chance the edge shows "
                      f"{row['chance_the_edge_shows']:.0%}; Sharpe standard error {row['sharpe_standard_error']:.2f}")
    out["frozen_bot"] = {"engine": frozen}
    print(f"frozen bot, engine: 1,000 -> {frozen['from_2021']['end_value']:,.0f} from 2021-04, -> "
          f"{frozen['from_2025']['end_value']:,.0f} from 2025; {time.time() - t0:.0f}s elapsed", flush=True)

    # Pools 1 and 2: the textbook rules, without and with the bot's own components.
    cands = rule_candidates(panel, p)
    names, kinds = list(cands), [cands[n][0] for n in cands]
    print(f"\n=== candidates: {len(names)} rules and bot components", flush=True)
    weights, daily_m, ends, turns = candidate_series(panel, cands, progress=True)
    reference = daily_m[:, names.index(FROZEN)].copy()
    ref_vol = stats(reference, ends, EVAL_START, EVAL_END)["daily_vol"]
    out["frozen_bot"]["fast"] = windows(reference, ends)
    out["candidates"]["rules_and_bot"] = candidate_table(panel, names, kinds, weights, daily_m, ends, turns, ref_vol, True)
    rules = [i for i, k in enumerate(kinds) if k != "bot"]
    slow = {"continuation rules on 4-hour and daily bars":
            [j for j, i in enumerate(rules) if kinds[i] == "continuation" and not names[i].endswith(" 1h")]}
    out["pools"]["rules"] = report_pool("rules", rules, names, kinds, weights, daily_m, ends, close_ms, ref_vol, panel,
                                        ep, stop, slow, t0)
    out["pools"]["rules_and_bot"] = report_pool("rules_and_bot", list(range(len(names))), names, kinds, weights, daily_m,
                                                ends, close_ms, ref_vol, panel, ep, stop, {}, t0)
    del weights

    # Pool 3: sixteen settings of the bot itself.
    cands = setting_candidates(panel, p)
    names, kinds = list(cands), [cands[n][0] for n in cands]
    print(f"\n=== candidates: {len(names)} settings of the bot", flush=True)
    weights, daily_m, ends, turns = candidate_series(panel, cands, progress=True)
    if not np.array_equal(reference, daily_m[:, next(i for i, n in enumerate(names) if "(frozen default)" in n)]):
        raise RuntimeError("the frozen default differs between the pools")
    out["candidates"]["settings"] = candidate_table(panel, names, kinds, weights, daily_m, ends, turns, ref_vol, False)
    out["pools"]["settings"] = report_pool("settings", list(range(len(names))), names, kinds, weights, daily_m, ends,
                                           close_ms, ref_vol, panel, ep, stop, {}, t0)
    dump(ROOT / "research" / "results" / "evolving.json", out)
    print(f"\nwritten research/results/evolving.json, {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
