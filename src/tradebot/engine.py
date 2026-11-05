"""Event-driven paper-trading engine shared by backtests and the live forward run.

Bar lifecycle (identical in backtest and forward mode)::

    on_open(t, open)    -> execute the order decided at the previous close
    on_close(t, ohlc)   -> 1. intrabar protective stop (uses high/low)
                           2. mark-to-market, risk checks
                           3. decide the target for the next bar's open

Decisions only ever use data from bars that have closed; fills happen at the
*next* bar's open with slippage and fees. Positions are signed: with
``max_short > 0`` (perpetual-futures mode) the target may be negative, a short
is booked as ``cash += proceeds, qty < 0`` so equity is always
``cash + qty * price``, and funding is settled at the close of every bar that
contains a funding timestamp. The engine is pure: all mutable
state lives in :class:`EngineState`, which is JSON-serialisable so the forward
run can stop and resume between ticks without changing results.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Config

EPS = 1e-12


@dataclass(frozen=True)
class EngineParams:
    interval_ms: int
    fee_rate: float
    slippage: float
    min_notional: float
    qty_step: float
    max_exposure: float
    min_rebalance: float
    cb_dd: float
    cb_ms: int
    campaign_stop_loss: float | None  # None disables (continuous backtests)
    stop_rearm: bool
    stop_cooldown_bars: int
    end_action: str = "flatten"
    max_short: float = 0.0  # > 0 allows short exposure down to -max_short (perp mode)

    @classmethod
    def from_config(cls, cfg: Config) -> "EngineParams":
        return cls(
            interval_ms=cfg.run.timeframe_ms,
            fee_rate=cfg.costs.fee_rate,
            slippage=cfg.costs.slippage,
            min_notional=cfg.costs.min_notional,
            qty_step=cfg.costs.qty_step,
            max_exposure=cfg.risk.max_exposure,
            min_rebalance=cfg.risk.min_rebalance,
            cb_dd=cfg.risk.circuit_breaker_dd,
            cb_ms=int(cfg.risk.circuit_breaker_hours * 3_600_000),
            campaign_stop_loss=cfg.risk.campaign_stop_loss,
            stop_rearm=cfg.risk.stop_rearm,
            stop_cooldown_bars=cfg.risk.stop_cooldown_bars,
            end_action=cfg.run.end_action,
            max_short=cfg.risk.max_short,
        )


@dataclass
class EngineState:
    initial_equity: float
    cash: float
    qty: float = 0.0
    avg_cost: float = 0.0            # long: USDT per BTC incl. buy fees; short: net proceeds per BTC
    fees_paid: float = 0.0
    realized_pnl: float = 0.0
    hwm: float = 0.0                 # high-water mark (reset after a circuit-breaker halt)
    peak_equity: float = 0.0         # all-time peak (never reset; for reporting)
    max_drawdown: float = 0.0        # worst peak-to-trough on closes
    pending: dict[str, Any] | None = None
    stop_price: float | None = None
    rearm_required: bool = False
    rearm_side: int = 1              # side of the stopped-out position (+1 long, -1 short)
    funding_paid: float = 0.0        # net funding paid (negative = received)
    cooldown_until_ms: int | None = None
    halt_until_ms: int | None = None
    stopped: bool = False            # campaign stop-loss hit: no more trading
    ended: bool = False              # campaign duration reached
    end_ms: int | None = None        # campaign end (None = run forever)
    start_ms: int | None = None
    trip: dict[str, Any] | None = None  # open round trip
    last_open_ms: int | None = None
    last_close_ms: int | None = None
    last_price: float | None = None
    last_equity: float | None = None
    last_target: float = 0.0
    trade_count: int = 0
    bars_processed: int = 0
    in_market_bars: int = 0

    @classmethod
    def fresh(cls, initial_cash: float, start_ms: int | None = None, end_ms: int | None = None) -> "EngineState":
        return cls(initial_equity=initial_cash, cash=initial_cash, hwm=initial_cash,
                   peak_equity=initial_cash, start_ms=start_ms, end_ms=end_ms, last_equity=initial_cash)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EngineState":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @property
    def status(self) -> str:
        if self.ended:
            return "ending" if self.pending is not None else "completed"
        if self.stopped:
            return "stopped"
        if self.halt_until_ms is not None:
            return "halted"
        return "running"


@dataclass
class Engine:
    params: EngineParams
    state: EngineState
    record_equity: bool = True
    trades: list[dict[str, Any]] = field(default_factory=list)
    trips: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    equity: list[tuple] = field(default_factory=list)

    # ------------------------------------------------------------------ utils
    def _floor_qty(self, q: float) -> float:
        step = self.params.qty_step
        return math.floor(q / step + 1e-9) * step if q > 0 else 0.0

    def is_flat(self, price: float) -> bool:
        q = self._floor_qty(abs(self.state.qty))
        return q <= 0 or q * price < self.params.min_notional

    def equity_at(self, price: float) -> float:
        return self.state.cash + self.state.qty * price

    def _event(self, ts_ms: int, kind: str, message: str, **extra: Any) -> None:
        self.events.append({"ts_ms": ts_ms, "kind": kind, "message": message, **extra})

    # ------------------------------------------------------------- execution
    def _fill(self, ts_ms: int, side: str, qty: float, ref_price: float, reason: str) -> None:
        """Book a fill; a fill that crosses zero is split into close + open."""
        s = self.state
        if side == "sell" and s.qty > 0 and qty > s.qty * (1 + 1e-12):
            first = s.qty
            self._fill_one(ts_ms, "sell", first, ref_price, reason)
            self._fill_one(ts_ms, "sell", qty - first, ref_price, reason)
        elif side == "buy" and s.qty < 0 and qty > -s.qty * (1 + 1e-12):
            first = -s.qty
            self._fill_one(ts_ms, "buy", first, ref_price, reason)
            self._fill_one(ts_ms, "buy", qty - first, ref_price, reason)
        else:
            self._fill_one(ts_ms, side, qty, ref_price, reason)

    def _open_trip(self, ts_ms: int, price: float, side: str, equity_before: float) -> None:
        self.state.trip = {"entry_ms": ts_ms, "entry_price": price, "cost": 0.0, "pnl": 0.0, "basis": 0.0,
                           "equity_before": equity_before, "side": side}

    def _fill_one(self, ts_ms: int, side: str, qty: float, ref_price: float, reason: str) -> None:
        p, s = self.params, self.state
        if side == "buy" and s.qty >= 0:           # open / add long
            price = ref_price * (1.0 + p.slippage)
            notional = qty * price
            fee = notional * p.fee_rate
            total_cost = s.avg_cost * s.qty + notional + fee
            s.cash -= notional + fee
            if s.cash < 0 and s.cash > -1e-6:
                s.cash = 0.0
            was_flat = self.is_flat(ref_price)
            s.qty += qty
            s.avg_cost = total_cost / s.qty
            # Buy fees are capitalised into avg_cost and realised on the sell,
            # so the buy row itself carries no realised P&L.
            pnl = 0.0
            if was_flat or s.trip is None:
                self._open_trip(ts_ms, price, "long", s.cash + notional + fee + (s.qty - qty) * ref_price)
            s.trip["cost"] += notional + fee
            # Peak capital committed during the trip (denominator for trip return).
            s.trip["basis"] = max(s.trip.get("basis", 0.0), s.qty * s.avg_cost)
            realized = 0.0
            reducing = False
        elif side == "sell" and s.qty > 0:         # reduce / close long
            price = ref_price * (1.0 - p.slippage)
            notional = qty * price
            fee = notional * p.fee_rate
            s.cash += notional - fee
            realized = notional - fee - qty * s.avg_cost
            s.realized_pnl += realized
            s.qty -= qty
            if s.qty < p.qty_step * 1e-6:
                s.qty = 0.0
            pnl = realized
            reducing = True
        elif side == "sell":                       # open / add short
            price = ref_price * (1.0 - p.slippage)
            notional = qty * price
            fee = notional * p.fee_rate
            was_flat = self.is_flat(ref_price)
            proceeds = s.avg_cost * (-s.qty) + notional - fee
            equity_before = s.cash + s.qty * ref_price
            s.cash += notional - fee
            s.qty -= qty
            s.avg_cost = proceeds / (-s.qty)
            pnl = realized = 0.0
            if was_flat or s.trip is None:
                self._open_trip(ts_ms, price, "short", equity_before)
            s.trip["cost"] += notional + fee
            s.trip["basis"] = max(s.trip.get("basis", 0.0), -s.qty * s.avg_cost)
            reducing = False
        else:                                      # buy to reduce / close short
            price = ref_price * (1.0 + p.slippage)
            notional = qty * price
            fee = notional * p.fee_rate
            s.cash -= notional + fee
            realized = qty * s.avg_cost - notional - fee
            s.realized_pnl += realized
            s.qty += qty
            if -s.qty < p.qty_step * 1e-6:
                s.qty = 0.0
            pnl = realized
            reducing = True
        s.fees_paid += fee
        s.trade_count += 1
        if s.trip is not None:
            s.trip["pnl"] += realized
        equity = s.cash + s.qty * ref_price
        self.trades.append({
            "ts_ms": ts_ms, "side": side, "qty": qty, "price": price, "ref_price": ref_price,
            "notional": notional, "fee": fee, "realized_pnl": pnl, "reason": reason,
            "cash_after": s.cash, "qty_after": s.qty, "equity_after": equity,
        })
        if reducing and self.is_flat(ref_price) and s.trip is not None:
            t = s.trip
            basis = t.get("basis") or t["cost"]
            self.trips.append({
                "entry_ms": t["entry_ms"], "exit_ms": ts_ms, "entry_price": t["entry_price"],
                "exit_price": price, "pnl": t["pnl"], "return": t["pnl"] / basis if basis else 0.0,
                "exit_reason": reason, "side": t.get("side", "long"),
            })
            s.trip = None
            s.stop_price = None

    def _rebalance(self, ts_ms: int, ref_price: float, target: float, reason: str) -> None:
        p, s = self.params, self.state
        if s.qty >= 0 and target >= 0.0:
            # Long side (the only path when max_short == 0).
            if target <= 0.0:
                q = self._floor_qty(s.qty)
                if q > 0 and q * ref_price >= p.min_notional:
                    self._fill(ts_ms, "sell", q, ref_price, reason)
                return
            equity = self.equity_at(ref_price)
            desired = target * equity / ref_price
            delta = desired - s.qty
            if delta > 0:
                fill = ref_price * (1.0 + p.slippage)
                affordable = s.cash / (fill * (1.0 + p.fee_rate))
                q = self._floor_qty(min(delta, affordable))
                if q > 0 and q * fill >= p.min_notional:
                    self._fill(ts_ms, "buy", q, ref_price, reason)
            elif delta < 0:
                q = self._floor_qty(min(-delta, s.qty))
                if q > 0 and q * ref_price >= p.min_notional:
                    self._fill(ts_ms, "sell", q, ref_price, reason)
            return
        # Signed path: an open short and/or a short target.
        if s.qty > 0 and not self.is_flat(ref_price):      # long -> short: close the long first
            self._fill(ts_ms, "sell", self._floor_qty(s.qty), ref_price, reason)
        if s.qty < 0 and target >= 0.0:                    # cover the short (fully)
            q = self._floor_qty(-s.qty)
            if q > 0 and q * ref_price >= p.min_notional:
                self._fill(ts_ms, "buy", q, ref_price, reason)
            if target > 0.0 and self.is_flat(ref_price):
                self._rebalance(ts_ms, ref_price, target, reason)  # then open the long
            return
        equity = self.equity_at(ref_price)
        desired = target * equity / ref_price              # <= 0 here
        delta = desired - s.qty
        if delta < 0:                                      # increase the short
            q = self._floor_qty(-delta)
            if q > 0 and q * ref_price >= p.min_notional:
                self._fill(ts_ms, "sell", q, ref_price, reason)
        elif delta > 0:                                    # reduce the short
            q = self._floor_qty(min(delta, -s.qty))
            if q > 0 and q * ref_price >= p.min_notional:
                self._fill(ts_ms, "buy", q, ref_price, reason)

    # ----------------------------------------------------------------- events
    def on_open(self, ts_ms: int, open_price: float) -> None:
        s = self.state
        if s.last_open_ms is not None and ts_ms <= s.last_open_ms:
            return
        s.last_open_ms = ts_ms
        order = s.pending
        if order is None:
            return
        s.pending = None
        was_flat = self.is_flat(open_price)
        self._rebalance(ts_ms, open_price, float(order["target"]), order.get("reason", "signal"))
        sd = order.get("stop_dist")
        if was_flat and not self.is_flat(open_price) and sd is not None and math.isfinite(sd) and sd > 0:
            if s.qty > 0:
                fill = open_price * (1.0 + self.params.slippage)
                s.stop_price = fill - sd
            else:
                fill = open_price * (1.0 - self.params.slippage)
                s.stop_price = fill + sd

    def on_close(self, ts_ms: int, o: float, h: float, l: float, c: float,
                 target: float, stop_dist: float = math.nan, funding: float = 0.0) -> None:
        """Process the bar that opened at ``ts_ms`` once it has closed.

        ``funding`` is the sum of perpetual funding rates settled inside this
        bar (at its close); longs pay positive rates and shorts receive them.
        """
        p, s = self.params, self.state
        if s.last_close_ms is not None and ts_ms <= s.last_close_ms:
            return
        if s.last_open_ms is None or s.last_open_ms < ts_ms:
            self.on_open(ts_ms, o)  # executes a pending end-of-campaign flatten too
        if s.ended:
            # The campaign is over: nothing after the end bar may change results,
            # however late the tick that delivers those bars arrives.
            s.last_close_ms = ts_ms
            return
        close_ms = ts_ms + p.interval_ms

        # 1) intrabar protective stop (resting stop order semantics)
        hit_long = s.stop_price is not None and not self.is_flat(c) and s.qty > 0 and l <= s.stop_price
        hit_short = s.stop_price is not None and not self.is_flat(c) and s.qty < 0 and h >= s.stop_price
        if hit_long or hit_short:
            stop_level = s.stop_price
            if hit_long:
                ref = min(o, s.stop_price)
                self._fill(ts_ms + p.interval_ms - 1, "sell", self._floor_qty(s.qty), ref, "stop")
            else:
                ref = max(o, s.stop_price)
                self._fill(ts_ms + p.interval_ms - 1, "buy", self._floor_qty(-s.qty), ref, "stop")
            s.stop_price = None
            if p.stop_rearm:
                s.rearm_required = True
                s.rearm_side = 1 if hit_long else -1
            if p.stop_cooldown_bars > 0:
                s.cooldown_until_ms = close_ms + p.stop_cooldown_bars * p.interval_ms
            self._event(close_ms, "stop", f"protective stop hit at {stop_level:.2f}", price=ref)

        # 2) funding settlement (perp mode), then mark to market
        if funding and s.qty != 0.0:
            payment = s.qty * c * funding
            s.cash -= payment
            s.funding_paid += payment
        equity = s.cash + s.qty * c
        s.last_price = c
        s.last_equity = equity
        s.bars_processed += 1
        if abs(s.qty) * c >= p.min_notional:
            s.in_market_bars += 1
        if equity > s.hwm:
            s.hwm = equity
        if equity > s.peak_equity:
            s.peak_equity = equity
        dd = 1.0 - equity / s.peak_equity if s.peak_equity > 0 else 0.0
        if dd > s.max_drawdown:
            s.max_drawdown = dd

        # 3) decide the target for the next open
        raw = target if (target is not None and math.isfinite(target)) else 0.0
        t = min(max(raw, -p.max_short if p.max_short > 0 else 0.0), p.max_exposure)
        reason = "signal"
        decide = True

        if s.ended:
            decide = False
        elif s.end_ms is not None and close_ms >= s.end_ms:
            s.ended = True
            self._event(close_ms, "end", "campaign duration reached")
            if p.end_action == "flatten":
                t, reason = 0.0, "campaign end"
            else:
                decide = False
        elif s.stopped:
            t, reason = 0.0, "campaign stop"
        elif p.campaign_stop_loss is not None and equity <= s.initial_equity * (1.0 - p.campaign_stop_loss):
            s.stopped = True
            t, reason = 0.0, "campaign stop"
            self._event(close_ms, "alert", f"campaign stop-loss hit: equity {equity:.2f} is "
                        f"{1 - equity / s.initial_equity:.1%} below initial; trading stopped")
        else:
            if s.halt_until_ms is not None:
                if close_ms < s.halt_until_ms:
                    t, reason = 0.0, "circuit breaker"
                else:
                    s.halt_until_ms = None
                    s.hwm = equity
                    self._event(close_ms, "info", "circuit breaker released; trading resumed")
            if s.halt_until_ms is None and equity <= s.hwm * (1.0 - p.cb_dd):
                t, reason = 0.0, "circuit breaker"
                dd_hwm = 1.0 - equity / s.hwm
                if p.cb_ms > 0:
                    s.halt_until_ms = close_ms + p.cb_ms
                else:
                    s.hwm = equity
                self._event(close_ms, "alert", f"circuit breaker: drawdown {dd_hwm:.1%} "
                            f"from high-water mark; flat for {p.cb_ms / 3_600_000:.0f}h")
            if s.rearm_required:
                side = s.rearm_side or 1
                if (side > 0 and raw <= 0.0) or (side < 0 and raw >= 0.0):
                    s.rearm_required = False
                elif self.is_flat(c) and t * side > 0:
                    t, reason = 0.0, "awaiting re-arm"
            if s.cooldown_until_ms is not None:
                if close_ms < s.cooldown_until_ms:
                    if self.is_flat(c):
                        t, reason = 0.0, "cooldown"
                else:
                    s.cooldown_until_ms = None

        # trailing stop update (after the stop check, using this close)
        if not self.is_flat(c) and stop_dist is not None and math.isfinite(stop_dist) and stop_dist > 0:
            if s.qty > 0:
                new_stop = c - stop_dist
                if s.stop_price is None or new_stop > s.stop_price:
                    s.stop_price = new_stop
            else:
                new_stop = c + stop_dist
                if s.stop_price is None or new_stop < s.stop_price:
                    s.stop_price = new_stop

        s.last_target = t
        if decide:
            flat = self.is_flat(c)
            exposure = (s.qty * c / equity) if equity > 0 else 0.0
            order = None
            if t == 0.0:
                if not flat:
                    order = {"target": 0.0, "reason": reason}
            elif flat:
                if abs(t) * equity >= p.min_notional:
                    order = {"target": t, "reason": reason}
            elif abs(t - exposure) >= p.min_rebalance - EPS:
                order = {"target": t, "reason": reason}
            if order is not None:
                order["stop_dist"] = stop_dist if (stop_dist is not None and math.isfinite(stop_dist)) else None
                order["decided_ms"] = close_ms
            s.pending = order
        else:
            s.pending = None

        s.last_close_ms = ts_ms
        if self.record_equity:
            self.equity.append((close_ms, c, s.cash, s.qty, equity, t))
