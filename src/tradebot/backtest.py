"""Backtesting on historical candles using the live engine.

Two evaluation modes:

``run_backtest``
    One continuous simulation over a date range (classic equity curve).
``run_campaigns``
    Many independent fixed-length runs (default 30 days) started on a rolling
    schedule. Each starts flat with fresh capital and ends by flattening, i.e.
    exactly what the forward paper run does. The distribution of campaign
    returns is the honest answer to "how likely is a 30-day run to make money?".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

from .config import Config
from .data.candles import index_ms
from .engine import Engine, EngineParams, EngineState
from .metrics import campaign_summary, equity_frame, performance
from .strategies.base import Strategy

DAY_MS = 86_400_000


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    trips: pd.DataFrame
    events: list[dict[str, Any]]
    metrics: dict[str, Any]
    state: EngineState


def funding_per_bar(open_ms: np.ndarray, interval_ms: int, funding: pd.Series | None) -> np.ndarray:
    """Sum of funding rates settled inside each bar, i.e. at times in (open, open + interval].

    A settlement at 08:00 is booked at the close of the 07:00 bar, using that
    bar's close as the mark price. A settlement that falls inside a gap in the
    candle data (exchange outage) is booked at the first bar after the gap,
    so no funding is silently dropped.
    """
    out = np.zeros(len(open_ms))
    if funding is None or len(funding) == 0 or len(open_ms) == 0:
        return out
    t = np.asarray(funding.index, dtype="int64")
    r = np.asarray(funding.to_numpy(), dtype="float64")
    close_ms = open_ms + interval_ms
    pos = np.searchsorted(close_ms, t, side="left")      # first bar whose close >= t
    ok = (pos < len(open_ms)) & (t > open_ms[0])          # ignore settlements before the data
    np.add.at(out, pos[ok], r[ok])
    return out


@dataclass
class Arrays:
    ms: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    target: np.ndarray
    stop: np.ndarray
    funding: np.ndarray

    @classmethod
    def build(cls, candles: pd.DataFrame, signals: pd.DataFrame, funding: pd.Series | None = None,
              interval_ms: int = 3_600_000) -> "Arrays":
        ms = index_ms(candles.index)
        return cls(
            ms=ms,
            o=candles["open"].to_numpy(dtype="float64"),
            h=candles["high"].to_numpy(dtype="float64"),
            l=candles["low"].to_numpy(dtype="float64"),
            c=candles["close"].to_numpy(dtype="float64"),
            target=np.nan_to_num(signals["target"].to_numpy(dtype="float64"), nan=0.0),
            stop=signals["stop_dist"].to_numpy(dtype="float64"),
            funding=funding_per_bar(ms, interval_ms, funding),
        )


def _bounds(ms: np.ndarray, start: str | pd.Timestamp | None, end: str | pd.Timestamp | None) -> tuple[int, int]:
    i0 = 0 if start is None else int(np.searchsorted(ms, pd.Timestamp(start, tz="UTC").value // 1_000_000, "left"))
    # `end` is inclusive of the whole day when given as a date string.
    if end is None:
        i1 = len(ms)
    else:
        end_ts = pd.Timestamp(end, tz="UTC")
        if isinstance(end, str) and len(end) == 10:
            end_ts = end_ts + pd.Timedelta(days=1)
        i1 = int(np.searchsorted(ms, end_ts.value // 1_000_000, "left"))
    return i0, i1


def run_backtest(candles: pd.DataFrame, strategy: Strategy, cfg: Config,
                 start: str | None = None, end: str | None = None,
                 signals: pd.DataFrame | None = None, initial_cash: float | None = None,
                 campaign_stop: bool = False, funding: pd.Series | None = None) -> BacktestResult:
    """Continuous simulation. The campaign stop-loss is a property of a finite
    30-day run, so it is disabled here unless ``campaign_stop=True``."""
    sig = signals if signals is not None else strategy.signals(candles)
    a = Arrays.build(candles, sig, funding, cfg.run.timeframe_ms)
    i0, i1 = _bounds(a.ms, start, end)
    params = EngineParams.from_config(cfg)
    if not campaign_stop:
        params = replace(params, campaign_stop_loss=None)
    cash = initial_cash if initial_cash is not None else cfg.run.initial_cash
    eng = Engine(params, EngineState.fresh(cash))
    on_close = eng.on_close
    ms, o, h, l, c, tgt, sd, fu = a.ms, a.o, a.h, a.l, a.c, a.target, a.stop, a.funding
    for i in range(i0, i1):
        on_close(int(ms[i]), o[i], h[i], l[i], c[i], tgt[i], sd[i], fu[i])
    eq = equity_frame(eng.equity)
    trades = pd.DataFrame(eng.trades)
    trips = pd.DataFrame(eng.trips)
    metrics = performance(eq, trades, trips, cash, periods_per_year=365 * 86_400_000 / params.interval_ms)
    metrics["strategy"] = strategy.describe()
    metrics["funding_paid"] = float(eng.state.funding_paid)
    if i0 < strategy.warmup_bars:
        metrics["warning"] = f"only {i0} warm-up bars before start (strategy needs {strategy.warmup_bars})"
    return BacktestResult(eq, trades, trips, eng.events, metrics, eng.state)


def run_campaigns(candles: pd.DataFrame, strategy: Strategy, cfg: Config,
                  start: str | None = None, end: str | None = None, days: float = 30.0,
                  step_hours: int = 24, signals: pd.DataFrame | None = None,
                  funding: pd.Series | None = None) -> pd.DataFrame:
    """Simulate independent ``days``-long paper runs started every ``step_hours``.

    Every campaign must fit entirely inside [start, end]; the final flatten
    executes at the open of the bar after the campaign ends.
    """
    sig = signals if signals is not None else strategy.signals(candles)
    a = Arrays.build(candles, sig, funding, cfg.run.timeframe_ms)
    i0, i1 = _bounds(a.ms, start, end)
    params = replace(EngineParams.from_config(cfg), end_action="flatten")
    cash = cfg.run.initial_cash
    span_ms = int(days * DAY_MS)
    step_ms = step_hours * 3_600_000
    rows = []
    ms, o, h, l, c, tgt, sd, fu = a.ms, a.o, a.h, a.l, a.c, a.target, a.stop, a.funding
    if i1 <= i0:
        return pd.DataFrame()
    t = int(ms[i0])
    last_allowed_open = int(ms[i1 - 1])
    while True:
        s = int(np.searchsorted(ms, t, "left"))
        if s >= i1:
            break
        start_ms = int(ms[s])
        end_ms = start_ms + span_ms
        # Same end condition as the forward run: the campaign ends at the close
        # of the first bar whose close time reaches end_ms; the exit fill is at
        # the following bar's open.
        e = int(np.searchsorted(ms, end_ms - params.interval_ms, "left")) + 1
        if e >= i1 or int(ms[e]) > last_allowed_open:
            break
        state = EngineState.fresh(cash, start_ms=start_ms, end_ms=end_ms)
        eng = Engine(params, state, record_equity=False)
        on_close = eng.on_close
        for i in range(s, e):
            on_close(int(ms[i]), o[i], h[i], l[i], c[i], tgt[i], sd[i], fu[i])
        if not state.ended:  # only possible with irregular data; force the end
            state.ended = True
            state.pending = None if eng.is_flat(c[e - 1]) else {"target": 0.0, "reason": "campaign end",
                                                                 "stop_dist": None}
        eng.on_open(int(ms[e]), o[e])
        # Liquidation value (normally already flat after the end-of-campaign exit).
        # Liquidation value of any residual position (long or short) after costs.
        q = state.qty
        px = o[e] * ((1.0 - params.slippage) if q > 0 else (1.0 + params.slippage))
        fee = params.fee_rate if q >= 0 or params.short_fee_rate is None else params.short_fee_rate
        final = state.cash + q * px - abs(q) * px * fee
        rows.append({
            "start": pd.Timestamp(start_ms, unit="ms", tz="UTC"),
            "return": final / cash - 1.0,
            "max_drawdown": state.max_drawdown,
            "n_trades": state.trade_count,
            "time_in_market": state.in_market_bars / max(state.bars_processed, 1),
            "fees": state.fees_paid,
            "funding": state.funding_paid,
            "stopped": state.stopped,
            "bh_return": o[e] / o[s] - 1.0,
        })
        t = start_ms + step_ms
    return pd.DataFrame(rows)


def evaluate(candles: pd.DataFrame, strategy: Strategy, cfg: Config, start: str | None, end: str | None,
             days: float = 30.0, step_hours: int = 24, signals: pd.DataFrame | None = None) -> dict[str, Any]:
    """Continuous metrics + campaign distribution for one period."""
    sig = signals if signals is not None else strategy.signals(candles)
    bt = run_backtest(candles, strategy, cfg, start, end, signals=sig)
    camp = run_campaigns(candles, strategy, cfg, start, end, days=days, step_hours=step_hours, signals=sig)
    out = {"continuous": bt.metrics, "campaigns": campaign_summary(camp)}
    if not camp.empty:
        out["campaigns"]["bh_pct_positive"] = float((camp["bh_return"] > 0).mean())
        out["campaigns"]["bh_median"] = float(camp["bh_return"].median())
        out["campaigns"]["bh_mean"] = float(camp["bh_return"].mean())
    return out


def _finite(x: Any) -> Any:
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return x
