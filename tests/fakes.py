"""A deterministic fake exchange for forward-run tests (no network)."""

from __future__ import annotations

import numpy as np

from compounder.data.live import DataError, Instrument
from compounder.data.panel import DAY_MS, HOUR_MS, Panel


class FakeSource:
    """Serves candles from a synthetic panel as if the wall clock were ``now_ms``."""

    def __init__(self, world: Panel, name: str = "fake", price_scale: float = 1.0):
        self.world = world
        self.name = name
        self.now_ms = int(world.times[0])
        self.price_scale = price_scale
        self.fail = False
        self.fail_symbols: set[str] = set()
        self.delisted: set[str] = set()
        self.calls = 0

    # ------------------------------------------------------------------ helpers
    def _check(self) -> None:
        self.calls += 1
        if self.fail:
            raise DataError(f"{self.name} is down")

    def _visible(self) -> int:
        """Index one past the last bar that has opened by now (the last one may be forming)."""
        return int(np.searchsorted(self.world.times, self.now_ms, side="right"))

    # ---------------------------------------------------------------- interface
    def instruments(self) -> dict[str, Instrument]:
        self._check()
        return {s: Instrument(s, s.replace("USDT", "-USDT-SWAP"), s[:-4], 1.0, 0.001, 0.001, 0.01, 50.0, 0)
                for s in self.world.symbols if s not in self.delisted}

    def candles(self, inst: Instrument, limit: int, interval_ms: int = HOUR_MS) -> np.ndarray:
        self._check()
        if inst.symbol in self.fail_symbols:
            raise DataError(f"no candles for {inst.symbol}")
        w, j, hi = self.world, self.world.index_of(inst.symbol), self._visible()
        t, o, h, l, c, v = (w.times[:hi], w.open[:hi, j], w.high[:hi, j], w.low[:hi, j], w.close[:hi, j],
                            w.quote_volume[:hi, j])
        ok = np.isfinite(c)
        rows = np.column_stack([t, o, h, l, c, v / np.where(ok, c, 1.0), v, np.zeros(hi), np.zeros(hi)])[ok]
        rows[:, 1:5] *= self.price_scale
        if len(rows) and rows[-1, 0] + HOUR_MS > self.now_ms:
            # The forming bar has only traded part of its range so far: show open = high = low = close.
            rows[-1, 2:5] = rows[-1, 1]
        if interval_ms == DAY_MS:
            rows = _to_daily(rows)
        return rows[-limit:]

    def funding(self, inst: Instrument) -> np.ndarray:
        self._check()
        w, j, hi = self.world, self.world.index_of(inst.symbol), self._visible()
        idx = np.nonzero(w.funding[:hi, j])[0]
        settle = w.times[idx] + HOUR_MS
        keep = settle <= self.now_ms
        return np.column_stack([settle[keep], w.funding[idx[keep], j]])[-100:]

    def last_prices(self) -> dict[str, float]:
        self._check()
        hi = self._visible()
        return {s: float(self.world.close[hi - 1, j]) for j, s in enumerate(self.world.symbols)}

    def day_volumes(self) -> dict[str, float]:
        self._check()
        hi = self._visible()
        lo = max(0, hi - 24)
        return {s: float(self.world.quote_volume[lo:hi, j].sum()) for j, s in enumerate(self.world.symbols)}


def _to_daily(rows: np.ndarray) -> np.ndarray:
    if not len(rows):
        return rows
    day = (rows[:, 0] // DAY_MS).astype("int64")
    out = []
    for d in np.unique(day):
        r = rows[day == d]
        out.append([d * DAY_MS, r[0, 1], r[:, 2].max(), r[:, 3].min(), r[-1, 4], r[:, 5].sum(), r[:, 6].sum(), 0.0, 0.0])
    return np.asarray(out, dtype="float64")


class FakeExchange:
    """A minimal ccxt-like USDT swap exchange: one-way positions, market and stop orders."""

    def __init__(self, prices: dict[str, float], balance: float = 1000.0, contract_size: float = 0.01,
                 lot: float = 0.01):
        self.prices = dict(prices)                    # internal symbol -> price
        self.wallet = float(balance)
        self.contract_size = contract_size
        self.lot = lot
        self.pos: dict[str, dict] = {}                # ccxt symbol -> {"contracts": signed, "entry": price}
        self.orders: list[dict] = []
        self.stops: list[dict] = []
        self.leverage: dict[str, int] = {}
        self.fail_orders = False
        self.size_bug = 1.0                           # multiply every fill (simulates a unit error)
        self._id = 0

    @staticmethod
    def _sym(internal: str) -> str:
        return f"{internal[:-4]}/USDT:USDT"

    def _price(self, ccxt_sym: str) -> float:
        return self.prices[ccxt_sym.split("/")[0] + "USDT"]

    def load_markets(self):
        return {self._sym(s): {"symbol": self._sym(s), "swap": True, "linear": True, "active": True,
                               "contractSize": self.contract_size, "limits": {"amount": {"min": self.lot}}}
                for s in self.prices}

    def amount_to_precision(self, symbol, amount):
        return f"{int(amount / self.lot + 1e-9) * self.lot:.10f}"

    def price_to_precision(self, symbol, price):
        return f"{price:.8f}"

    def _upnl(self) -> float:
        return sum(p["contracts"] * self.contract_size * (self._price(s) - p["entry"]) for s, p in self.pos.items())

    def fetch_balance(self):
        return {"USDT": {"total": self.wallet + self._upnl(), "free": self.wallet}, "info": {}}

    def fetch_positions(self, symbols=None):
        return [{"symbol": s, "contracts": abs(p["contracts"]), "side": "long" if p["contracts"] > 0 else "short",
                 "contractSize": self.contract_size, "entryPrice": p["entry"], "markPrice": self._price(s)}
                for s, p in self.pos.items() if p["contracts"] != 0]

    def set_leverage(self, leverage, symbol, params=None):
        self.leverage[symbol] = leverage

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        params = dict(params or {})
        self._id += 1
        if self.fail_orders:
            raise RuntimeError("exchange rejected the order")
        if "stopLossPrice" in params:
            order = {"id": str(self._id), "symbol": symbol, "side": side, "amount": float(amount),
                     "stopLossPrice": float(params["stopLossPrice"]), "triggerPrice": float(params["stopLossPrice"]),
                     "reduceOnly": bool(params.get("reduceOnly"))}
            self.stops.append(order)
            return order
        signed = float(amount) * (1.0 if side == "buy" else -1.0) * self.size_bug
        px = self._price(symbol)
        cur = self.pos.get(symbol, {"contracts": 0.0, "entry": px})
        if params.get("reduceOnly"):
            if cur["contracts"] == 0 or (cur["contracts"] > 0) == (signed > 0):
                raise RuntimeError("reduce-only order would increase the position")
            signed = max(signed, -cur["contracts"]) if signed < 0 else min(signed, -cur["contracts"])
        new = cur["contracts"] + signed
        if cur["contracts"] != 0 and (abs(new) < abs(cur["contracts"]) or cur["contracts"] * new < 0):
            closed = min(abs(signed), abs(cur["contracts"])) * (1.0 if cur["contracts"] > 0 else -1.0)
            self.wallet += closed * self.contract_size * (px - cur["entry"])
        if new == 0 or cur["contracts"] * new <= 0:
            entry = px
        elif abs(new) > abs(cur["contracts"]):
            entry = (abs(cur["contracts"]) * cur["entry"] + abs(signed) * px) / abs(new)
        else:
            entry = cur["entry"]
        if abs(new) < 1e-12:
            self.pos.pop(symbol, None)
        else:
            self.pos[symbol] = {"contracts": new, "entry": entry}
        order = {"id": str(self._id), "symbol": symbol, "side": side, "amount": float(amount), "params": params}
        self.orders.append(order)
        return order

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        if (params or {}).get("ordType") != "conditional":
            return []                                 # stops live in the conditional-order book, as on OKX
        return [o for o in self.stops if symbol is None or o["symbol"] == symbol]

    def cancel_order(self, id_, symbol=None, params=None):
        self.stops = [o for o in self.stops if o["id"] != id_]

    def trigger_stop(self, internal: str) -> None:
        """Simulate the resting stop of a position being filled."""
        sym = self._sym(internal)
        self.stops = [o for o in self.stops if o["symbol"] != sym]
        p = self.pos.pop(sym, None)
        if p:
            self.wallet += p["contracts"] * self.contract_size * (self._price(sym) - p["entry"])
