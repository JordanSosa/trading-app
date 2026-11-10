"""Live execution through ccxt (opt-in).

The engine stays the single source of decisions. In live mode the broker

1. mirrors the exchange account (equity, positions) into the engine state
   before any decision is made, so the risk governor acts on real numbers;
2. turns the engine's planned orders into market orders on the exchange;
3. keeps one reduce-only stop-market order per open position at the stop
   price the engine assigned, so the account is protected while the bot is
   offline.

Nothing is sent unless ``live.dry_run`` is false, API keys are present and the
command carried ``--i-understand-live-trading``. Every order is checked
against hard limits first, and the account is re-read afterwards: a position
far larger than intended is closed and the kill switch is set.

Supported: OKX USDT-margined perpetual swaps in one-way (net) position mode,
in a dedicated account. The adapter is written against ccxt and is exercised
in the test suite against a simulated exchange only. Run it on OKX's demo
environment (``live.sandbox = true``) before funding it.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

import numpy as np

from .config import Config, ConfigError
from .engine import Engine
from .errors import BrokerError

log = logging.getLogger(__name__)

# One venue: the order, position and conditional-order semantics below are OKX's.
# Another exchange needs its own review of every call in this module.
SUPPORTED = ("okx",)


def ccxt_symbol(symbol: str) -> str:
    """'BTCUSDT' -> 'BTC/USDT:USDT' (ccxt's name for a USDT-margined perpetual)."""
    return f"{symbol[:-4]}/USDT:USDT"


def make_live_broker(cfg: Config, env: dict[str, str] | None = None) -> "LiveBroker":
    env = dict(os.environ if env is None else env)
    lv = cfg.live
    if lv.exchange not in SUPPORTED:
        raise ConfigError(f"live.exchange {lv.exchange!r} is not supported; choose one of {SUPPORTED}")
    if not cfg.risk.allow_short:
        raise ConfigError("live trading supports perpetual swaps only (risk.allow_short = true)")
    try:
        import ccxt  # type: ignore
    except ImportError as exc:
        raise ConfigError("live trading needs the ccxt package: pip install 'compounder[live]'") from exc
    key, secret = env.get("COMPOUNDER_API_KEY"), env.get("COMPOUNDER_API_SECRET")
    if not key or not secret:
        raise ConfigError("set COMPOUNDER_API_KEY, COMPOUNDER_API_SECRET and COMPOUNDER_API_PASSWORD in the "
                          "environment; never put keys in a config file")
    params: dict[str, Any] = {"apiKey": key, "secret": secret, "enableRateLimit": True,
                              "options": {"defaultType": "swap"}}
    if env.get("COMPOUNDER_API_PASSWORD"):
        params["password"] = env["COMPOUNDER_API_PASSWORD"]
    exchange = getattr(ccxt, lv.exchange)(params)
    if lv.sandbox:
        exchange.set_sandbox_mode(True)
    return LiveBroker(cfg, exchange)


class LiveBroker:
    def __init__(self, cfg: Config, exchange: Any):
        self.cfg = cfg
        self.ex = exchange
        self.engine: Engine | None = None
        self.events: list[dict[str, Any]] = []
        self.now_ms = 0
        self.prices: np.ndarray | None = None
        self._markets: dict[str, Any] | None = None

    # ------------------------------------------------------------------ account
    def markets(self) -> dict[str, Any]:
        if self._markets is None:
            self._markets = self.ex.load_markets()
        return self._markets

    def market(self, symbol: str) -> dict[str, Any] | None:
        m = self.markets().get(ccxt_symbol(symbol))
        if not m or not m.get("swap") or not m.get("linear") or m.get("active") is False:
            return None
        return m

    def equity(self) -> float:
        """Account equity in USDT including unrealised P&L."""
        try:
            bal = self.ex.fetch_balance()
        except Exception as exc:  # noqa: BLE001 - network or authentication failure
            raise BrokerError(f"could not read the account balance: {type(exc).__name__}: {exc}") from exc
        usdt = bal.get("USDT") or {}
        total = usdt.get("total")
        if total is None:
            raise BrokerError("the exchange did not report a USDT balance")
        total = float(total)          # OKX: "eq", the currency's equity including unrealised P&L
        if not math.isfinite(total) or total < 0:
            raise BrokerError(f"implausible account equity: {total}")
        return total

    def positions(self) -> dict[str, dict[str, float]]:
        """Open positions keyed by internal symbol: signed qty in base units, entry and mark price."""
        out: dict[str, dict[str, float]] = {}
        try:
            raw = self.ex.fetch_positions()
        except Exception as exc:  # noqa: BLE001
            raise BrokerError(f"could not read positions: {type(exc).__name__}: {exc}") from exc
        for p in raw:
            contracts = float(p.get("contracts") or 0.0)
            if contracts == 0.0:
                continue
            sym = p.get("symbol") or ""
            if not sym.endswith("/USDT:USDT"):
                raise BrokerError(f"unexpected open position {sym}: the bot needs a dedicated USDT swap account")
            side = p.get("side")
            if side not in ("long", "short"):
                raise BrokerError(f"position {sym} has no side; hedge (two-way) position mode is not supported")
            size = float(p.get("contractSize") or (self.markets().get(sym) or {}).get("contractSize") or 1.0)
            qty = contracts * size * (1.0 if side == "long" else -1.0)
            mark = float(p.get("markPrice") or p.get("entryPrice") or 0.0)
            internal = sym.split("/")[0] + "USDT"
            out[internal] = {"qty": qty, "entry": float(p.get("entryPrice") or mark), "mark": mark,
                             "contracts": contracts}
        return out

    def describe(self) -> dict[str, Any]:
        pos = self.positions()
        return {"exchange": self.cfg.live.exchange, "sandbox": self.cfg.live.sandbox, "dry_run": self.cfg.live.dry_run,
                "equity_usdt": self.equity(), "swap_markets": sum(1 for m in self.markets().values()
                                                                 if m.get("swap") and m.get("linear")),
                "positions": pos}

    # ------------------------------------------------------- hooks used by tick
    def attach(self, engine: Engine, snap: Any, events: list[dict[str, Any]], now_ms: int) -> None:
        self.engine, self.events, self.now_ms = engine, events, now_ms
        self.prices = np.where(np.isfinite(snap.last), snap.last, engine.state.last_price)
        engine.simulate = False
        self.sync()

    def _event(self, kind: str, message: str) -> None:
        self.events.append({"ts_ms": self.now_ms, "kind": kind, "message": message})

    def sync(self, closed_on_purpose: frozenset[int] = frozenset()) -> None:
        """Mirror the exchange account into the engine state (the exchange is the truth).

        A position that has vanished since the last look was closed by its resting
        stop, unless the bot has just closed it itself (``closed_on_purpose``).
        """
        eng = self.engine
        assert eng is not None and self.prices is not None
        s = eng.state
        equity = self.equity()
        if equity > self.cfg.live.max_account_equity:
            raise BrokerError(f"account equity {equity:,.2f} USDT exceeds live.max_account_equity "
                              f"({self.cfg.live.max_account_equity:,.2f}); refusing to trade it")
        pos = self.positions()
        unknown = [sym for sym in pos if sym not in s.symbols]
        if unknown:
            raise BrokerError(f"positions in symbols the bot is not tracking: {unknown}; close them or let the "
                              "bot manage a dedicated account")
        value = 0.0
        for j, sym in enumerate(s.symbols):
            had = float(s.qty[j])
            p = pos.get(sym)
            now = p["qty"] if p else 0.0
            if had != 0.0 and now == 0.0 and math.isfinite(s.stop_price[j]) and j not in closed_on_purpose:
                # The position vanished between ticks: its resting stop was filled.
                s.blocked[j] = 1.0 if had > 0 else -1.0
                s.stop_count += 1
                self._event("stop", f"{sym}: position closed on the exchange (protective stop)")
            s.qty[j] = now
            if p:
                s.entry_price[j] = p["entry"]
                mark = p["mark"] if p["mark"] > 0 else float(self.prices[j])
                value += now * mark
                if not math.isfinite(s.last_price[j]):
                    s.last_price[j] = mark
            else:
                s.entry_price[j] = np.nan
                s.stop_price[j] = np.nan
        s.cash = equity - value
        if s.bars_processed == 0 and s.trade_count == 0:
            # First contact: the account's own equity is the starting point of the run.
            s.initial_equity = s.hwm = s.peak_equity = s.day_start_equity = s.day_peak_equity = equity
            s.last_equity = equity

    def execute(self, forming_ms: int) -> None:
        """Send the engine's pending orders, then verify the account and refresh the stops."""
        eng = self.engine
        assert eng is not None and self.prices is not None
        s, lv = eng.state, self.cfg.live
        orders = eng.plan(self.prices)
        stop_frac = s.pending_stop
        s.pending, s.pending_stop = None, None
        s.last_open_ms = forming_ms
        before = s.qty.copy()
        equity = eng.equity_at(self.prices)
        intended = before * self.prices
        # Reductions first: they free margin for the increases.
        orders.sort(key=lambda o: 0 if abs(intended[o[0]] + o[1]) < abs(intended[o[0]]) else 1)
        sent = 0
        closing: set[int] = set()
        for j, delta in orders:
            sym, px = s.symbols[j], float(self.prices[j])
            m = self.market(sym)
            if m is None or not math.isfinite(px) or px <= 0:
                self._event("warning", f"{sym}: not tradable on {lv.exchange}; order skipped")
                continue
            cur = float(before[j]) * px
            target = cur + delta
            legs: list[tuple[float, bool]] = []
            if cur != 0.0 and (target == 0.0 or cur * target < 0):
                closing.add(j)
                legs.append((-cur, True))                       # close the whole position, reduce-only
                if cur * target < 0:
                    legs.append((target, False))                # then open the other side
            else:
                legs.append((delta, abs(target) < abs(cur)))
            for notional, reduce in legs:
                if abs(notional) > lv.max_order_notional:
                    self._event("alert", f"{sym}: order of {notional:,.0f} USDT exceeds live.max_order_notional; "
                                "skipped")
                    continue
                size = float(m.get("contractSize") or 1.0)
                if reduce and abs(abs(notional) - abs(cur)) < 1e-9:
                    contracts = abs(float(before[j])) / size    # exact size: leaves no dust
                else:
                    contracts = abs(notional) / (px * size)
                amount = float(self.ex.amount_to_precision(m["symbol"], contracts))
                min_amt = float(((m.get("limits") or {}).get("amount") or {}).get("min") or 0.0)
                if amount <= 0 or amount < min_amt:
                    continue
                side = "buy" if notional > 0 else "sell"
                intended[j] += notional
                if lv.dry_run:
                    self._event("info", f"DRY RUN: would {side} {amount:g} contracts of {sym} "
                                f"(~{abs(notional):,.2f} USDT){' reduce-only' if reduce else ''}")
                    continue
                params: dict[str, Any] = {"tdMode": lv.margin_mode}
                if reduce:
                    params["reduceOnly"] = True
                try:
                    if not reduce:
                        self._prepare(m["symbol"])
                    self.ex.create_order(m["symbol"], "market", side, amount, None, params)
                    sent += 1
                except Exception as exc:  # noqa: BLE001 - one failed order must not abort the rest
                    self._event("alert", f"{sym}: {side} order failed: {type(exc).__name__}: {exc}")
        if lv.dry_run:
            return
        self.sync(frozenset(closing))
        self._verify(intended, equity)
        # New or reversed positions get the stop the engine assigned at decision time.
        for j in range(len(s.symbols)):
            q = float(s.qty[j])
            if q != 0.0 and (before[j] == 0.0 or (before[j] > 0) != (q > 0)):
                s.stop_price[j] = eng.stop_for(j, float(s.entry_price[j]), q, stop_frac)
        self.refresh_stops()
        if sent:
            self._event("info", f"sent {sent} order(s) to {lv.exchange}")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _lot(market: dict[str, Any]) -> float:
        return float(((market.get("limits") or {}).get("amount") or {}).get("min") or 0.0)

    def _prepare(self, symbol: str) -> None:
        """Set leverage for a contract before opening it (a margin ceiling, not an exposure target)."""
        try:
            self.ex.set_leverage(self.cfg.live.leverage, symbol, {"mgnMode": self.cfg.live.margin_mode})
        except Exception as exc:  # noqa: BLE001 - many venues reject a no-op change
            log.debug("set_leverage(%s) ignored: %s", symbol, exc)

    def _verify(self, intended: np.ndarray, equity: float) -> None:
        """After trading, compare what the account holds with what was intended."""
        eng = self.engine
        assert eng is not None and self.prices is not None
        s = eng.state
        for j, sym in enumerate(s.symbols):
            actual = float(s.qty[j]) * float(self.prices[j]) if s.qty[j] else 0.0
            want = float(intended[j])
            if not math.isfinite(actual):
                continue
            if abs(actual) > 2.0 * abs(want) + max(20.0, 0.02 * equity):
                # Far larger than intended: a unit or contract-size error. Close it and stop.
                self._event("alert", f"{sym}: position {actual:,.2f} USDT is far larger than the intended "
                            f"{want:,.2f}; closing it and setting the kill switch")
                self._close(j)
                s.killed = True
            elif abs(actual - want) > max(0.25 * abs(want), 10.0):
                self._event("warning", f"{sym}: holds {actual:,.2f} USDT, intended {want:,.2f}")
        gross = float(np.nansum(np.abs(s.qty * self.prices)))
        if equity > 0 and gross > (self.cfg.risk.max_gross * 1.25) * equity:
            self._event("alert", f"gross exposure {gross / equity:.2f}x exceeds the cap; flattening and "
                        "setting the kill switch")
            for j in np.nonzero(s.qty)[0]:
                self._close(int(j))
            s.killed = True

    def _close(self, j: int) -> None:
        s = self.engine.state  # type: ignore[union-attr]
        m = self.market(s.symbols[j])
        if m is None or s.qty[j] == 0.0:
            return
        contracts = abs(float(s.qty[j])) / float(m.get("contractSize") or 1.0)
        params = {"reduceOnly": True, "tdMode": self.cfg.live.margin_mode}
        try:
            self.ex.create_order(m["symbol"], "market", "sell" if s.qty[j] > 0 else "buy",
                                 float(self.ex.amount_to_precision(m["symbol"], contracts)), None, params)
        except Exception as exc:  # noqa: BLE001
            self._event("alert", f"{s.symbols[j]}: emergency close failed: {type(exc).__name__}: {exc}")

    def refresh_stops(self) -> None:
        """One reduce-only stop-market order per open position, at the engine's stop price."""
        s = self.engine.state  # type: ignore[union-attr]
        lv = self.cfg.live
        for j, sym in enumerate(s.symbols):
            m = self.market(sym)
            if m is None:
                continue
            q, stop = float(s.qty[j]), float(s.stop_price[j])
            try:
                # OKX files a stop-loss as a "conditional" algo order. The account is dedicated
                # to the bot, so every such order on a tracked symbol is one of its stops.
                open_stops = list(self.ex.fetch_open_orders(m["symbol"], None, None, {"ordType": "conditional"}))
            except Exception as exc:  # noqa: BLE001
                self._event("warning", f"{sym}: could not read stop orders: {type(exc).__name__}: {exc}")
                continue
            contracts = abs(q) / float(m.get("contractSize") or 1.0)
            keep = None
            for o in open_stops:
                trigger = float(o.get("stopLossPrice") or o.get("triggerPrice") or o.get("stopPrice") or 0.0)
                want_side = "sell" if q > 0 else "buy"
                same = (q != 0.0 and math.isfinite(stop) and o.get("side") == want_side
                        and abs(float(o.get("amount") or 0.0) - contracts) <= self._lot(m) * 0.5 + 1e-12
                        and abs(trigger / stop - 1.0) < 1e-4)
                if same and keep is None:
                    keep = o
                    continue
                try:
                    self.ex.cancel_order(o["id"], m["symbol"], {"stop": True})
                except Exception as exc:  # noqa: BLE001
                    self._event("warning", f"{sym}: could not cancel a stale stop: {type(exc).__name__}: {exc}")
            if q == 0.0 or not math.isfinite(stop) or keep is not None:
                continue
            params = {"reduceOnly": True, "tdMode": lv.margin_mode,
                      "stopLossPrice": float(self.ex.price_to_precision(m["symbol"], stop))}
            try:
                self.ex.create_order(m["symbol"], "market", "sell" if q > 0 else "buy",
                                     float(self.ex.amount_to_precision(m["symbol"], contracts)), None, params)
            except Exception as exc:  # noqa: BLE001
                self._event("alert", f"{sym}: could not place the protective stop at {stop:.6g}: "
                            f"{type(exc).__name__}: {exc}")
