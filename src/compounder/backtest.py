"""Backtests: the full engine over a panel, and a fast linear approximation for signal research."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .data.panel import DAY_MS, Panel
from .engine import Engine, EngineParams, EngineState
from .metrics import daily_stats, monthly_returns, yearly_returns


@dataclass
class BacktestResult:
    params: EngineParams
    state: EngineState
    day_end_ms: np.ndarray        # end of each completed trading day
    daily_returns: np.ndarray     # simple return of each completed trading day
    day_flags: list[str]          # "T" target reached, "L" loss limit hit
    equity_ms: np.ndarray         # per-bar close time
    equity: np.ndarray            # per-bar equity
    gross: np.ndarray             # per-bar gross exposure (x equity)
    net: np.ndarray               # per-bar net exposure
    risk_mult: np.ndarray
    trades: list[dict[str, Any]]
    events: list[dict[str, Any]]
    metrics: dict[str, Any]


def ms(date: str | int | None) -> int | None:
    """'2024-01-01' -> epoch ms (UTC)."""
    if date is None or isinstance(date, (int, np.integer)):
        return None if date is None else int(date)
    return int(np.datetime64(date, "ms").astype("int64"))


def run_backtest(panel: Panel, weights: np.ndarray, params: EngineParams, initial_equity: float = 10_000.0,
                 start: str | int | None = None, end: str | int | None = None,
                 stop_frac: np.ndarray | float | None = None, record: bool = True) -> BacktestResult:
    """Run the engine over ``panel`` using precomputed target ``weights`` (T, N).

    ``weights[i]`` must be computable from bars ``<= i``; the engine executes it
    at the open of bar ``i + 1``. ``start``/``end`` bound the bar *open* times
    (end exclusive). The account starts flat at ``start``.
    """
    if weights.shape != (panel.n_bars, panel.n_symbols):
        raise ValueError(f"weights shape {weights.shape} != panel shape {(panel.n_bars, panel.n_symbols)}")
    lo = 0 if start is None else int(np.searchsorted(panel.times, ms(start), side="left"))
    hi = panel.n_bars if end is None else int(np.searchsorted(panel.times, ms(end), side="left"))
    if hi - lo < 2:
        raise ValueError("backtest window is empty")
    state = EngineState.fresh(panel.symbols, initial_equity)
    eng = Engine(params, state, record=record)
    o, h, l, c, f, t = panel.open, panel.high, panel.low, panel.close, panel.funding, panel.times
    sf_is_arr = isinstance(stop_frac, np.ndarray) and stop_frac.ndim == 2
    for i in range(lo, hi):
        sf = stop_frac[i] if sf_is_arr else stop_frac
        eng.on_close(int(t[i]), o[i], h[i], l[i], c[i], weights[i], sf, f[i])
        if state.liquidated and not state.qty.any():
            # Nothing can change any more; skip ahead cheaply but keep the day ledger complete.
            pass
    return collect(eng)


def collect(eng: Engine) -> BacktestResult:
    s = eng.state
    if eng.daily:
        d = np.asarray([(a, b, c) for a, b, c, _ in eng.daily], dtype="float64")
        day_end = d[:, 0].astype("int64")
        # After a wipe-out the account sits at zero: those days return nothing, not 0/0.
        with np.errstate(invalid="ignore", divide="ignore"):
            rets = np.where(d[:, 1] > 0, d[:, 2] / np.where(d[:, 1] > 0, d[:, 1], 1.0) - 1.0, 0.0)
        flags = [x[3] for x in eng.daily]
    else:
        day_end, rets, flags = np.empty(0, dtype="int64"), np.empty(0), []
    if eng.equity_curve:
        e = np.asarray(eng.equity_curve, dtype="float64")
        eq_ms, eq, gross, net, mult = e[:, 0].astype("int64"), e[:, 1], e[:, 2], e[:, 3], e[:, 4]
    else:
        eq_ms = np.empty(0, dtype="int64")
        eq = gross = net = mult = np.empty(0)
    m = daily_stats(rets, eng.params.daily_target if eng.params.daily_target is not None else 0.02)
    days = max(1, len(rets))
    m["wiped_out"] = bool(m.get("wiped_out")) or s.last_equity <= 0
    m.update({
        "initial_equity": s.initial_equity,
        "final_equity": s.last_equity,
        "engine_max_drawdown": s.max_drawdown,          # measured on every bar, not only at day ends
        "trades": s.trade_count,
        "stops": s.stop_count,
        "turnover_per_day": s.turnover / days / max(1e-9, _mean_equity(eq, s)),
        "fees_paid": s.fees_paid,
        "funding_paid": s.funding_paid,
        "days_target_hit": s.days_target_hit,
        "days_loss_limit": s.days_loss_limit,
        "killed": s.killed,
        "liquidated": s.liquidated,
    })
    if len(gross):
        m["avg_gross"] = float(gross.mean())
        m["avg_net"] = float(net.mean())
        m["max_gross"] = float(gross.max())
        m["time_in_market"] = float((gross > 0.01).mean())
        m["avg_risk_mult"] = float(mult.mean())
    if len(rets):
        m["by_year"] = yearly_returns(day_end, rets)
        m["by_month"] = monthly_returns(day_end, rets)
    return BacktestResult(eng.params, s, day_end, rets, flags, eq_ms, eq, gross, net, mult, eng.trades, eng.events, m)


def _mean_equity(eq: np.ndarray, s: EngineState) -> float:
    return float(eq.mean()) if len(eq) else float(s.initial_equity)


# ------------------------------------------------------------------- research
def open_to_open_returns(panel: Panel) -> np.ndarray:
    """r[i] = open[i+1] / open[i] - 1: what a position held over bar i earns. NaN where unknown."""
    o = panel.open
    r = np.full_like(o, np.nan)
    r[:-1] = o[1:] / o[:-1] - 1.0
    return r


def quick_returns(panel: Panel, weights: np.ndarray, cost: float = 0.0008, band: float = 0.0,
                  with_funding: bool = True) -> tuple[np.ndarray, np.ndarray, float]:
    """Fast linear approximation of the strategy's hourly returns, for signal research only.

    Weights decided at the close of bar ``i`` are held over bar ``i + 1`` (open
    to open) and rebalanced back to target every bar, paying ``cost`` on the
    traded notional. No stops, no governor, no compounding effects. With
    ``band`` > 0 a position only changes when the target differs from it by
    more than ``band`` times the larger of the two (the engine's no-trade band).

    Returns ``(bar_returns, bar_close_ms, mean turnover per bar)``.
    """
    t, n = weights.shape
    w = np.nan_to_num(weights)
    if band > 0:
        held = np.zeros_like(w)
        cur = np.zeros(n)
        for i in range(t):
            tgt = w[i]
            move = np.abs(tgt - cur) > band * np.maximum(np.abs(tgt), np.abs(cur))
            move |= (tgt == 0.0) & (cur != 0.0)
            cur = np.where(move, tgt, cur)
            held[i] = cur
        w = held
    r = np.nan_to_num(open_to_open_returns(panel))
    pos = np.zeros_like(w)
    pos[1:] = w[:-1]                       # position during bar i was decided at close of bar i-1
    gross_ret = (pos * r).sum(axis=1)
    trade = np.abs(np.diff(np.vstack([np.zeros((1, n)), pos]), axis=0)).sum(axis=1)
    ret = gross_ret - cost * trade
    if with_funding:
        ret = ret - (pos * panel.funding).sum(axis=1)
    return ret, panel.times + panel.interval_ms, float(trade.mean())


def to_daily(bar_returns: np.ndarray, bar_close_ms: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Compound bar returns into UTC-day returns. Returns (daily_returns, day_end_ms)."""
    day = (bar_close_ms - 1) // DAY_MS
    edges = np.nonzero(np.diff(day))[0] + 1
    starts = np.concatenate([[0], edges])
    logr = np.log1p(np.maximum(bar_returns, -0.999999))
    sums = np.add.reduceat(logr, starts)
    return np.expm1(sums), (day[starts] + 1) * DAY_MS
