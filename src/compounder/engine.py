"""Event-driven multi-asset engine shared by backtests, paper trading and live runs.

Bar lifecycle (identical everywhere)::

    on_open(t, open)          execute the weights decided at the previous close
    on_close(t, h, l, c, ...) 1. protective stops (resting-order semantics, uses high/low)
                              2. liquidation check on worst-case intrabar equity
                              3. funding, mark to market, day roll-over
                              4. risk governor: kill switch, daily loss limit,
                                 daily target lock, drawdown scaling
                              5. decide the weights for the next open

Decisions only use bars that have closed; fills happen at the next bar's open
with slippage and fees. Positions are signed quantities; equity is always
``cash + sum(qty * price)``, which is the same number a USDT-margined
perpetual account reports (wallet balance + unrealised P&L).

All mutable state lives in :class:`EngineState`, which serialises to JSON so a
forward run can stop and resume between ticks without changing results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

DAY_MS = 86_400_000
EPS = 1e-12


@dataclass(frozen=True)
class EngineParams:
    interval_ms: int = 3_600_000
    # --- costs -----------------------------------------------------------------
    fee_rate: float = 0.0005          # taker fee per side
    slippage: float = 0.0003          # adverse price move per side on ordinary fills
    stop_slippage: float = 0.0010     # extra adverse move on protective-stop fills
    # A stop is a market order triggered inside the bar. In a fast move it fills
    # beyond its trigger: this fraction of the distance between the trigger and
    # the bar's extreme is given up (0 = fill at the trigger, 1 = at the extreme).
    stop_overshoot: float = 0.25
    # After a stop-out that side of the symbol stays closed until the signal has
    # left it. With a cooldown > 0 it also reopens after this many bars, so one
    # wick does not keep the account out of the rest of a trend.
    stop_cooldown_bars: int = 0
    # --- venue constraints -------------------------------------------------------
    min_notional: float = 5.0         # smallest order the venue accepts, in USDT
    min_position_mult: float = 2.0    # only open positions of at least this many min_notionals
    allow_short: bool = True          # False = spot (long or flat, no leverage)
    maint_margin: float = 0.025       # maintenance margin rate used for the liquidation check
    liquidation_fee: float = 0.015    # charged on gross notional when liquidated
    # --- portfolio limits --------------------------------------------------------
    max_gross: float = 3.0            # cap on sum(|weights|)
    max_weight: float = 0.6           # cap on any single |weight|
    rebalance_band: float = 0.25      # skip trades smaller than this fraction of the larger of |target|, |current|
    # --- risk governor -----------------------------------------------------------
    daily_target: float | None = 0.02     # lock the day once equity is up this much on the day
    target_mode: str = "trail"            # "flatten": go flat at the target; "trail": protect it; "off": measure only
    trail_giveback: float = 0.5           # trail mode: share of the gain above the target that may be given back
    daily_loss_limit: float | None = 0.03  # go flat for the rest of the day after losing this much on the day
    dd_soft: float = 0.10             # drawdown at which risk starts to be scaled down
    dd_hard: float = 0.30             # drawdown at which risk reaches min_risk_mult
    min_risk_mult: float = 0.25
    kill_dd: float | None = 0.40      # drawdown at which the bot flattens and stops for good
    hwm_window_days: float = 0.0      # > 0: drawdown is measured from the peak of the last N days
    day_offset_ms: int = 0            # the trading day starts at 00:00 UTC + offset

    def __post_init__(self) -> None:
        if self.target_mode not in ("flatten", "trail", "off"):
            raise ValueError("target_mode must be 'flatten', 'trail' or 'off'")
        if not 0 <= self.dd_soft <= self.dd_hard:
            raise ValueError("need 0 <= dd_soft <= dd_hard")


@dataclass
class EngineState:
    """Everything that changes while trading. Arrays are aligned to ``symbols``."""

    symbols: tuple[str, ...]
    initial_equity: float
    cash: float
    qty: np.ndarray
    entry_price: np.ndarray           # average fill price of the open position (NaN when flat)
    stop_price: np.ndarray            # protective stop (NaN = none)
    blocked: np.ndarray               # +1/-1: side that was stopped out and may not be re-entered yet
    blocked_until: np.ndarray         # close time (ms) at which a cooldown releases the block (NaN = none)
    last_price: np.ndarray            # last known price per symbol (NaN before the first bar)
    pending: np.ndarray | None = None       # weights to execute at the next open
    pending_stop: np.ndarray | None = None  # stop distance (fraction of price) for new positions
    pending_reason: str = ""
    hwm: float = 0.0
    hwm_history: list[list[float]] = field(default_factory=list)  # [day, peak equity that day] (rolling HWM)
    peak_equity: float = 0.0
    max_drawdown: float = 0.0
    day: int | None = None            # index of the current trading day
    day_start_equity: float = 0.0
    day_peak_equity: float = 0.0
    day_locked: bool = False          # daily target secured: flat until the next day
    day_target_hit: bool = False      # target reached at some point today
    day_stopped: bool = False         # daily loss limit hit: flat until the next day
    killed: bool = False              # kill switch: flat until a manual reset
    liquidated: bool = False
    risk_mult: float = 1.0
    last_open_ms: int | None = None
    last_close_ms: int | None = None
    last_equity: float = 0.0
    fees_paid: float = 0.0
    funding_paid: float = 0.0
    turnover: float = 0.0
    trade_count: int = 0
    stop_count: int = 0
    bars_processed: int = 0
    days_completed: int = 0
    days_target_hit: int = 0
    days_loss_limit: int = 0

    @classmethod
    def fresh(cls, symbols: tuple[str, ...] | list[str], initial_equity: float) -> "EngineState":
        n = len(symbols)
        return cls(symbols=tuple(symbols), initial_equity=float(initial_equity), cash=float(initial_equity),
                   qty=np.zeros(n), entry_price=np.full(n, np.nan), stop_price=np.full(n, np.nan),
                   blocked=np.zeros(n), blocked_until=np.full(n, np.nan), last_price=np.full(n, np.nan),
                   hwm=float(initial_equity),
                   peak_equity=float(initial_equity), day_start_equity=float(initial_equity),
                   day_peak_equity=float(initial_equity), last_equity=float(initial_equity))

    # ------------------------------------------------------------ serialisation
    _ARRAYS = ("qty", "entry_price", "stop_price", "blocked", "blocked_until", "last_price")
    _OPT_ARRAYS = ("pending", "pending_stop")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if k in self._ARRAYS or (k in self._OPT_ARRAYS and v is not None):
                out[k] = {s: (None if not math.isfinite(x) else float(x)) for s, x in zip(self.symbols, v)}
            elif k == "symbols":
                out[k] = list(v)
            else:
                out[k] = v
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any], symbols: tuple[str, ...] | list[str] | None = None) -> "EngineState":
        """Rebuild state, optionally re-aligned to a different symbol list.

        Symbols that left the list must be flat; new symbols start flat.
        """
        stored = tuple(d["symbols"])
        symbols = tuple(symbols) if symbols is not None else stored
        kw: dict[str, Any] = {}
        defaults = {"qty": 0.0, "entry_price": np.nan, "stop_price": np.nan, "blocked": 0.0,
                    "blocked_until": np.nan, "last_price": np.nan, "pending": 0.0, "pending_stop": np.nan}
        for k in cls._ARRAYS + cls._OPT_ARRAYS:
            raw = d.get(k)
            if raw is None:
                if k in cls._ARRAYS:
                    raise ValueError(f"engine state is missing {k}")
                kw[k] = None
                continue
            if k == "qty":
                lost = [s for s in stored if s not in symbols and abs(raw.get(s) or 0.0) > 0]
                if lost:
                    raise ValueError(f"cannot drop symbols with open positions: {lost}")
            kw[k] = np.array([defaults[k] if raw.get(s) is None else float(raw[s]) for s in symbols], dtype="float64")
        known = set(cls.__dataclass_fields__) - set(cls._ARRAYS) - set(cls._OPT_ARRAYS) - {"symbols"}
        kw.update({k: v for k, v in d.items() if k in known})
        return cls(symbols=symbols, **kw)

    @property
    def status(self) -> str:
        if self.liquidated:
            return "liquidated"
        if self.killed:
            return "killed"
        if self.day_stopped:
            return "day-stopped"
        if self.day_locked:
            return "day-locked"
        return "running"


def drawdown_multiplier(dd: float, soft: float, hard: float, floor: float) -> float:
    """Risk multiplier as a function of drawdown: 1 up to ``soft``, linear down to ``floor`` at ``hard``."""
    if dd <= soft or hard <= soft:
        return 1.0 if dd <= soft else floor
    if dd >= hard:
        return floor
    return 1.0 - (1.0 - floor) * (dd - soft) / (hard - soft)


@dataclass
class Engine:
    params: EngineParams
    state: EngineState
    record: bool = True                 # keep per-bar equity and fills (off for fast parameter sweeps)
    # False in live mode: the exchange holds the account, so fills, stops, funding and
    # liquidation are not simulated here; a broker mirrors the real account into ``state``.
    simulate: bool = True
    trades: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    equity_curve: list[tuple] = field(default_factory=list)   # (close_ms, equity, gross, net, risk_mult)
    daily: list[tuple] = field(default_factory=list)          # (day_end_ms, start_equity, end_equity, flags)

    # ------------------------------------------------------------------ helpers
    def _event(self, ts_ms: int, kind: str, message: str, **extra: Any) -> None:
        self.events.append({"ts_ms": int(ts_ms), "kind": kind, "message": message, **extra})

    def equity_at(self, prices: np.ndarray) -> float:
        s = self.state
        px = np.where(np.isfinite(prices), prices, s.last_price)
        held = s.qty != 0.0
        return float(s.cash + (s.qty[held] * px[held]).sum())

    def _book(self, ts_ms: int, j: int, dq: float, fill: float, reason: str, fee_rate: float | None = None) -> float:
        """Apply a fill of ``dq`` units of symbol ``j`` at ``fill``; returns the fee."""
        s, p = self.state, self.params
        notional = abs(dq) * fill
        fee = notional * (p.fee_rate if fee_rate is None else fee_rate)
        s.cash -= dq * fill + fee
        old = s.qty[j]
        new = old + dq
        if abs(new) * fill < 1e-9:
            new = 0.0
        if new == 0.0:
            s.entry_price[j] = np.nan
            s.stop_price[j] = np.nan
        elif old == 0.0 or (old > 0) != (new > 0):
            s.entry_price[j] = fill                      # opened or flipped
        elif abs(new) > abs(old):
            s.entry_price[j] = (abs(old) * s.entry_price[j] + abs(dq) * fill) / abs(new)  # added
        s.qty[j] = new
        s.fees_paid += fee
        s.turnover += notional
        s.trade_count += 1
        if self.record:
            self.trades.append({"ts_ms": int(ts_ms), "symbol": s.symbols[j], "side": "buy" if dq > 0 else "sell",
                                "qty": abs(dq), "price": fill, "notional": notional, "fee": fee,
                                "reason": reason, "qty_after": new})
        return fee

    # ------------------------------------------------------------------- open
    def plan(self, prices: np.ndarray) -> list[tuple[int, float]]:
        """Orders that move the account from its positions to the pending weights.

        Returns ``(symbol index, signed USDT notional to trade)`` pairs, sells
        first. Pure: nothing is changed. The paper engine fills these itself
        (:meth:`on_open`); a live broker sends them to the exchange.
        """
        s, p = self.state, self.params
        if s.pending is None:
            return []
        w = s.pending
        tradable = np.isfinite(prices) & (prices > 0)
        mark = np.where(tradable, prices, s.last_price)
        held = s.qty != 0.0
        equity = float(s.cash + (s.qty[held] * mark[held]).sum())
        if equity <= 0:
            return []
        cur = np.where(held, s.qty * np.where(np.isfinite(mark), mark, 0.0), 0.0)
        desired = np.where(tradable, w * equity, cur)
        floor = p.min_notional * p.min_position_mult
        # A reversal too small to open the other side just closes the position.
        flip = (cur * desired) < 0.0
        desired = np.where(flip & (np.abs(desired) < floor), 0.0, desired)
        delta = desired - cur
        size = np.maximum(np.abs(desired), np.abs(cur))
        exit_ = (desired == 0.0) & (cur != 0.0)
        opening = (cur == 0.0) & (desired != 0.0)
        go = tradable & (np.abs(delta) > 1e-9) & (
            exit_ | flip
            | (opening & (np.abs(desired) >= floor))
            | (~opening & ~exit_ & (np.abs(delta) >= np.maximum(p.min_notional, p.rebalance_band * size))))
        idx = np.nonzero(go)[0]
        return [(int(j), float(delta[j])) for j in sorted(idx, key=lambda k: delta[k])]

    def on_open(self, ts_ms: int, open_px: np.ndarray) -> None:
        """Execute the pending target weights at this bar's open (simulated fills)."""
        s, p = self.state, self.params
        if s.last_open_ms is not None and ts_ms <= s.last_open_ms:
            return
        s.last_open_ms = ts_ms
        if s.pending is None:
            return
        orders = self.plan(open_px)
        stop_frac, reason = s.pending_stop, s.pending_reason or "signal"
        s.pending = None
        s.pending_stop = None
        for j, d in orders:
            px = float(open_px[j])
            if not p.allow_short and d > 0:
                # Spot: a buy is limited by the cash actually available.
                d = min(d, max(0.0, s.cash / (1.0 + p.slippage) / (1.0 + p.fee_rate)))
                if d < p.min_notional:
                    continue
            fill = px * (1.0 + p.slippage * (1.0 if d > 0 else -1.0))
            was = s.qty[j]
            self._book(ts_ms, j, d / px, fill, reason)
            now = s.qty[j]
            if now != 0.0 and (was == 0.0 or (was > 0) != (now > 0)):
                s.stop_price[j] = self.stop_for(j, fill, now, stop_frac)

    def stop_for(self, j: int, fill: float, qty: float, stop_frac: np.ndarray | None) -> float:
        """Protective stop price for a position just opened at ``fill`` (NaN = none)."""
        sf = float(stop_frac[j]) if stop_frac is not None else math.nan
        if not (math.isfinite(sf) and sf > 0):
            return math.nan
        return fill * (1.0 - sf) if qty > 0 else fill * (1.0 + sf)

    # ------------------------------------------------------------------ close
    def on_close(self, ts_ms: int, open_px: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                 target: np.ndarray, stop_frac: np.ndarray | None = None,
                 funding: np.ndarray | None = None) -> None:
        """Process the bar that opened at ``ts_ms`` once it has closed.

        ``target`` holds the strategy's desired weights (signed fractions of
        equity) computed from data up to and including this bar's close.
        """
        s, p = self.state, self.params
        if s.last_close_ms is not None and ts_ms <= s.last_close_ms:
            return
        if not self.simulate:
            s.pending = None                 # an order the broker never sent is void, not filled late
            s.pending_stop = None
        elif s.last_open_ms is None or s.last_open_ms < ts_ms:
            self.on_open(ts_ms, open_px)
        close_ms = ts_ms + p.interval_ms
        have = np.isfinite(close) & (close > 0)

        # 1) protective stops -------------------------------------------------
        held = (s.qty != 0.0) & have & np.isfinite(s.stop_price) & self.simulate
        if held.any():
            long_hit = held & (s.qty > 0) & (low <= s.stop_price)
            short_hit = held & (s.qty < 0) & (high >= s.stop_price)
            for j in np.nonzero(long_hit | short_hit)[0]:
                stop = float(s.stop_price[j])
                if s.qty[j] > 0:
                    ref = min(float(open_px[j]), stop)
                    fill = (ref - p.stop_overshoot * max(0.0, ref - float(low[j]))) * (1.0 - p.stop_slippage)
                    side = 1.0
                else:
                    ref = max(float(open_px[j]), stop)
                    fill = (ref + p.stop_overshoot * max(0.0, float(high[j]) - ref)) * (1.0 + p.stop_slippage)
                    side = -1.0
                self._book(close_ms - 1, j, -float(s.qty[j]), fill, "stop")
                s.blocked[j] = side
                s.blocked_until[j] = (close_ms + p.stop_cooldown_bars * p.interval_ms
                                      if p.stop_cooldown_bars > 0 else np.nan)
                s.stop_count += 1
                if self.record:
                    self._event(close_ms, "stop", f"{s.symbols[j]}: protective stop filled at {fill:.6g}",
                                symbol=s.symbols[j])

        # 2) liquidation check on worst-case intrabar equity ---------------------
        held = s.qty != 0.0
        if self.simulate and p.allow_short and held.any() and not s.liquidated:
            lo = np.where(have, low, s.last_price)
            hi = np.where(have, high, s.last_price)
            adverse = np.where(s.qty > 0, lo, hi)
            worst = float(s.cash + (s.qty[held] * adverse[held]).sum())
            gross_adv = float((np.abs(s.qty[held]) * adverse[held]).sum())
            if worst <= p.maint_margin * gross_adv:
                left = max(0.0, worst - p.liquidation_fee * gross_adv)
                s.cash = left
                s.qty[:] = 0.0
                s.entry_price[:] = np.nan
                s.stop_price[:] = np.nan
                s.liquidated = True
                s.killed = True
                self._event(close_ms, "alert", f"LIQUIDATED: worst-case equity {worst:.2f} fell below maintenance "
                            f"margin on {gross_adv:.2f} gross; {left:.2f} USDT left")

        # 3) funding, mark to market, day roll-over ---------------------------
        s.last_price = np.where(have, close, s.last_price)
        held = s.qty != 0.0
        if self.simulate and funding is not None and held.any():
            pay = float((s.qty[held] * s.last_price[held] * np.nan_to_num(funding[held])).sum())
            if pay:
                s.cash -= pay
                s.funding_paid += pay
        pos_val = s.qty[held] * s.last_price[held]
        equity = float(s.cash + pos_val.sum())
        s.last_equity = equity
        s.bars_processed += 1
        day = (close_ms - p.day_offset_ms) // DAY_MS
        if s.day is None:
            s.day = day
            s.day_start_equity = s.day_peak_equity = equity if s.bars_processed > 1 else s.initial_equity
        elif day > s.day:
            flags = ("T" if s.day_target_hit else "") + ("L" if s.day_stopped else "")
            self.daily.append((close_ms, s.day_start_equity, equity, flags))
            s.days_completed += 1
            s.days_target_hit += int(s.day_target_hit)
            s.days_loss_limit += int(s.day_stopped)
            s.day = day
            s.day_start_equity = s.day_peak_equity = equity
            s.day_locked = s.day_target_hit = s.day_stopped = False
        if equity > s.peak_equity:
            s.peak_equity = equity
        if p.hwm_window_days > 0:
            if s.hwm_history and s.hwm_history[-1][0] == day:
                s.hwm_history[-1][1] = max(s.hwm_history[-1][1], equity)
            else:
                s.hwm_history.append([day, equity])
            cutoff = day - p.hwm_window_days
            while s.hwm_history and s.hwm_history[0][0] < cutoff:
                s.hwm_history.pop(0)
            s.hwm = max(h for _, h in s.hwm_history)
        elif equity > s.hwm:
            s.hwm = equity
        s.day_peak_equity = max(s.day_peak_equity, equity)
        dd = 1.0 - equity / s.hwm if s.hwm > 0 else 0.0
        dd_peak = 1.0 - equity / s.peak_equity if s.peak_equity > 0 else 0.0
        if dd_peak > s.max_drawdown:
            s.max_drawdown = dd_peak

        # 4) risk governor --------------------------------------------------------
        reason = "signal"
        day_ret = equity / s.day_start_equity - 1.0 if s.day_start_equity > 0 else 0.0
        if not s.killed and p.kill_dd is not None and dd >= p.kill_dd:
            s.killed = True
            self._event(close_ms, "alert", f"KILL SWITCH: drawdown {dd:.1%} reached {p.kill_dd:.0%}; "
                        "flat until manually reset")
        if not s.day_stopped and p.daily_loss_limit is not None and day_ret <= -p.daily_loss_limit:
            s.day_stopped = True
            if self.record:
                self._event(close_ms, "risk", f"daily loss limit: {day_ret:+.2%} on the day; flat until tomorrow")
        if p.daily_target is not None and day_ret >= p.daily_target and not s.day_target_hit:
            s.day_target_hit = True
            if p.target_mode == "flatten":
                s.day_locked = True
            if self.record:
                self._event(close_ms, "target", f"daily target reached: {day_ret:+.2%} on the day")
        if (s.day_target_hit and not s.day_locked and p.target_mode == "trail" and p.daily_target is not None):
            base = s.day_start_equity * (1.0 + p.daily_target)
            floor = base + max(0.0, s.day_peak_equity - base) * (1.0 - p.trail_giveback)
            if equity < floor:
                s.day_locked = True
                if self.record:
                    self._event(close_ms, "target", f"daily gain locked at {day_ret:+.2%}; flat until tomorrow")
        s.risk_mult = drawdown_multiplier(dd, p.dd_soft, p.dd_hard, p.min_risk_mult)

        # 5) decide the weights for the next open -----------------------------
        w = np.nan_to_num(np.asarray(target, dtype="float64"), nan=0.0, posinf=0.0, neginf=0.0).copy()
        if s.blocked.any():
            # A stopped-out side stays out until the signal has left that side
            # (or, with a cooldown, until the cooldown has run out).
            released = (s.blocked != 0.0) & ((np.sign(w) != s.blocked) | (close_ms >= s.blocked_until))
            s.blocked[released] = 0.0
            s.blocked_until[released] = np.nan
            w[s.blocked != 0.0] = 0.0
        if s.killed:
            w[:] = 0.0
            reason = "kill switch" if not s.liquidated else "liquidated"
        elif s.day_stopped:
            w[:] = 0.0
            reason = "daily loss limit"
        elif s.day_locked:
            w[:] = 0.0
            reason = "daily target locked"
        else:
            w *= s.risk_mult
            if not p.allow_short:
                w = np.maximum(w, 0.0)
            w = np.clip(w, -p.max_weight, p.max_weight)
            w = self._concentrate(w, equity)
            gross = float(np.abs(w).sum())
            cap = p.max_gross if p.allow_short else min(p.max_gross, 0.98)
            if gross > cap:
                w *= cap / gross
        s.pending = w
        sf = None if stop_frac is None else np.asarray(stop_frac, dtype="float64")
        s.pending_stop = None if sf is None else (np.full(len(w), float(sf)) if sf.ndim == 0 else sf.copy())
        s.pending_reason = reason
        s.last_close_ms = ts_ms
        if self.record:
            gross_now = float(np.abs(pos_val).sum() / equity) if equity > 0 else 0.0
            net_now = float(pos_val.sum() / equity) if equity > 0 else 0.0
            self.equity_curve.append((close_ms, equity, gross_now, net_now, s.risk_mult))

    def _concentrate(self, w: np.ndarray, equity: float) -> np.ndarray:
        """Small accounts cannot hold many positions above the venue minimum.

        When some target positions would be smaller than the minimum position
        size, keep only the largest weights and scale them up to the same gross
        exposure, so the account stays invested in the strongest signals
        instead of holding nothing. Symbols already held are preferred, which
        stops two similar weights from swapping places every bar.
        """
        p = self.params
        floor = p.min_notional * p.min_position_mult
        absw = np.abs(w)
        live = absw > 0
        n = int(live.sum())
        if equity <= 0 or floor <= 0 or n == 0:
            return w
        if float(absw[live].min()) * equity >= floor:
            return w
        gross = float(absw.sum())
        if gross * equity < floor:
            return np.zeros_like(w)                     # cannot afford even one position
        priority = absw * np.where(self.state.qty != 0.0, 1.5, 1.0)
        order = np.argsort(-priority, kind="stable")[:n]
        csum = np.cumsum(absw[order])
        out = np.zeros_like(w)
        for k in range(n, 0, -1):
            kept = order[:k]
            scale = gross / float(csum[k - 1])
            if float(absw[kept].min()) * scale * equity >= floor:
                out[kept] = np.clip(w[kept] * scale, -p.max_weight, p.max_weight)
                return out
        j = order[0]
        out[j] = np.clip(np.sign(w[j]) * gross, -p.max_weight, p.max_weight)
        return out

    # ------------------------------------------------------------------ manual
    def reset_kill(self, ts_ms: int, equity: float | None = None) -> None:
        """Operator action: resume after a kill switch (not after a liquidation of everything)."""
        s = self.state
        s.killed = False
        s.liquidated = False
        eq = s.last_equity if equity is None else equity
        s.hwm = eq
        s.hwm_history = []
        self._event(ts_ms, "info", f"kill switch reset by operator at equity {eq:.2f}")
