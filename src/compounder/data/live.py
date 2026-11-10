"""Live public market data for the forward run (no API keys needed).

``OkxData`` is the default source: OKX's public REST API serves USDT-margined
perpetual swaps with candles, funding and contract specifications, and it is
reachable from US-hosted runners such as GitHub Actions (Binance's and Bybit's
futures APIs are geo-blocked there). ``GateData`` is an independent second
venue used as a fallback and to cross-check prices before trading on them.

Symbols are named ``<BASE>USDT`` inside the bot whatever the venue calls them.
Candle arrays use the archive column layout
(open_ms, open, high, low, close, base_volume, quote_volume, trades, taker_quote).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import numpy as np

from .http import HttpClient, HttpError
from .universe import is_crypto_perp

log = logging.getLogger(__name__)

HOUR_MS = 3_600_000
DAY_MS = 86_400_000
N_COLS = 9


class DataError(RuntimeError):
    """Raised when a market-data source cannot deliver usable data."""


@dataclass(frozen=True)
class Instrument:
    symbol: str             # internal name, e.g. BTCUSDT
    venue_id: str           # the venue's own identifier, e.g. BTC-USDT-SWAP
    base: str
    contract_size: float    # base units per contract (1.0 when the venue trades in base units)
    lot_size: float         # order size increment, in contracts
    min_size: float         # smallest order, in contracts
    tick_size: float
    max_leverage: float
    listed_ms: int

    def min_notional(self, price: float) -> float:
        return self.min_size * self.contract_size * price


class MarketSource(Protocol):
    name: str

    def instruments(self) -> dict[str, Instrument]: ...
    def candles(self, inst: Instrument, limit: int, interval_ms: int = HOUR_MS) -> np.ndarray: ...
    def funding(self, inst: Instrument) -> np.ndarray: ...
    def last_prices(self) -> dict[str, float]: ...
    def day_volumes(self) -> dict[str, float]: ...


def _rows_to_array(rows: list[tuple]) -> np.ndarray:
    if not rows:
        return np.empty((0, N_COLS))
    arr = np.asarray(rows, dtype="float64")
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    keep = np.ones(len(arr), dtype=bool)
    keep[:-1] = arr[1:, 0] != arr[:-1, 0]
    return arr[keep]


class OkxData:
    name = "okx"
    BASE = "https://www.okx.com"
    _BARS = {HOUR_MS: "1H", DAY_MS: "1Dutc"}

    def __init__(self, http: HttpClient | None = None, pause_s: float = 0.11,
                 sleep: Callable[[float], None] = time.sleep):
        self.http = http or HttpClient()
        self.pause_s = pause_s
        self._sleep = sleep

    def _get(self, path: str, params: dict) -> list:
        try:
            data = self.http.get_json(self.BASE + path, params)
        except HttpError as exc:
            raise DataError(str(exc)) from exc
        if not isinstance(data, dict) or str(data.get("code")) != "0":
            raise DataError(f"okx error on {path}: {data.get('msg') if isinstance(data, dict) else data!r}")
        return data.get("data") or []

    def instruments(self) -> dict[str, Instrument]:
        out: dict[str, Instrument] = {}
        for r in self._get("/api/v5/public/instruments", {"instType": "SWAP"}):
            try:
                if r.get("settleCcy") != "USDT" or r.get("ctType") != "linear" or r.get("state") != "live":
                    continue
                # OKX labels the underlying: 1 = crypto, 3 = equities, 4 = commodities.
                if str(r.get("instCategory", "1")) != "1":
                    continue
                base = r["instId"].split("-")[0]
                symbol = base + "USDT"
                if not is_crypto_perp(symbol):
                    continue
                out[symbol] = Instrument(
                    symbol=symbol, venue_id=r["instId"], base=base,
                    contract_size=float(r["ctVal"]) * float(r.get("ctMult") or 1.0),
                    lot_size=float(r["lotSz"]), min_size=float(r["minSz"]), tick_size=float(r["tickSz"]),
                    max_leverage=float(r.get("lever") or 1.0), listed_ms=int(r.get("listTime") or 0))
            except (KeyError, ValueError, TypeError):
                continue
        if not out:
            raise DataError("okx returned no usable USDT perpetuals")
        return out

    def candles(self, inst: Instrument, limit: int, interval_ms: int = HOUR_MS) -> np.ndarray:
        """The most recent ``limit`` candles (oldest first), including the forming one."""
        bar = self._BARS[interval_ms]
        rows: list[tuple] = []
        after: int | None = None
        first = True
        while len(rows) < limit:
            # The plain endpoint serves 300 recent bars a page; older ones come 100 at a time.
            path = "/api/v5/market/candles" if first else "/api/v5/market/history-candles"
            page_size = 300 if first else 100
            params = {"instId": inst.venue_id, "bar": bar, "limit": min(page_size, limit - len(rows))}
            if after is not None:
                params["after"] = after
            page = self._get(path, params)
            first = False
            if not page:
                break
            for k in page:
                # [ts, o, h, l, c, contracts, base volume, quote volume, confirm]
                rows.append((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[6]),
                             float(k[7]), 0.0, 0.0))
            oldest = min(int(k[0]) for k in page)
            if after is not None and oldest >= after:
                break
            after = oldest
            if self.pause_s:
                self._sleep(self.pause_s)
        return _rows_to_array(rows)[-limit:]

    def funding(self, inst: Instrument) -> np.ndarray:
        """Recent funding settlements as (settlement_ms, rate), oldest first (about a month)."""
        rows = []
        for r in self._get("/api/v5/public/funding-rate-history", {"instId": inst.venue_id, "limit": 100}):
            t = int(r["fundingTime"])
            rate = float(r.get("realizedRate") or r["fundingRate"])
            if abs(rate) < 0.05:
                rows.append((t - t % 60_000, rate))
        if not rows:
            return np.empty((0, 2))
        arr = np.asarray(rows, dtype="float64")
        return arr[np.argsort(arr[:, 0], kind="stable")]

    def _tickers(self) -> list[tuple[str, float, float]]:
        out = []
        for r in self._get("/api/v5/market/tickers", {"instType": "SWAP"}):
            inst_id = r.get("instId", "")
            if inst_id.endswith("-USDT-SWAP"):
                try:
                    last = float(r["last"])
                    # volCcy24h is in base currency for swaps.
                    out.append((inst_id.split("-")[0] + "USDT", last, float(r.get("volCcy24h") or 0.0) * last))
                except (KeyError, ValueError, TypeError):
                    continue
        return out

    def last_prices(self) -> dict[str, float]:
        return {s: last for s, last, _ in self._tickers()}

    def day_volumes(self) -> dict[str, float]:
        """Approximate USDT volume of the last 24 hours per symbol."""
        return {s: vol for s, _, vol in self._tickers()}


class GateData:
    name = "gate"
    BASE = "https://api.gateio.ws/api/v4/futures/usdt"
    _BARS = {HOUR_MS: "1h", DAY_MS: "1d"}

    def __init__(self, http: HttpClient | None = None, pause_s: float = 0.05,
                 sleep: Callable[[float], None] = time.sleep):
        self.http = http or HttpClient()
        self.pause_s = pause_s
        self._sleep = sleep

    def _get(self, path: str, params: dict | None = None):
        try:
            return self.http.get_json(self.BASE + path, params)
        except HttpError as exc:
            raise DataError(str(exc)) from exc

    def instruments(self) -> dict[str, Instrument]:
        out: dict[str, Instrument] = {}
        data = self._get("/contracts")
        if not isinstance(data, list):
            raise DataError("gate returned an unexpected contract list")
        for r in data:
            try:
                name = r["name"]
                if not name.endswith("_USDT") or r.get("in_delisting") or r.get("type") != "direct":
                    continue
                base = name[:-5]
                symbol = base + "USDT"
                if not is_crypto_perp(symbol):
                    continue
                out[symbol] = Instrument(
                    symbol=symbol, venue_id=name, base=base, contract_size=float(r["quanto_multiplier"]),
                    lot_size=1.0, min_size=float(r.get("order_size_min") or 1.0),
                    tick_size=float(r["order_price_round"]), max_leverage=float(r.get("leverage_max") or 1.0),
                    listed_ms=int(float(r.get("create_time") or 0) * 1000))
            except (KeyError, ValueError, TypeError):
                continue
        if not out:
            raise DataError("gate returned no usable USDT perpetuals")
        return out

    def candles(self, inst: Instrument, limit: int, interval_ms: int = HOUR_MS) -> np.ndarray:
        rows: list[tuple] = []
        end_s: int | None = None
        step_s = interval_ms // 1000
        while len(rows) < limit:
            n = min(1999, limit - len(rows))
            params: dict = {"contract": inst.venue_id, "interval": self._BARS[interval_ms]}
            if end_s is None:
                params["limit"] = n
            else:
                params["from"] = end_s - step_s * (n - 1)
                params["to"] = end_s
            page = self._get("/candlesticks", params)
            if not isinstance(page, list) or not page:
                break
            for k in page:
                o, h, l, c = float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"])
                # "v" is contracts; "sum" is the quote turnover when present.
                quote = float(k.get("sum") or 0.0) or float(k.get("v") or 0.0) * inst.contract_size * c
                rows.append((int(k["t"]) * 1000, o, h, l, c, float(k.get("v") or 0.0) * inst.contract_size,
                             quote, 0.0, 0.0))
            oldest_s = min(int(k["t"]) for k in page)
            if end_s is not None and oldest_s >= end_s:
                break
            end_s = oldest_s - step_s
            if self.pause_s:
                self._sleep(self.pause_s)
        return _rows_to_array(rows)[-limit:]

    def funding(self, inst: Instrument) -> np.ndarray:
        data = self._get("/funding_rate", {"contract": inst.venue_id, "limit": 200})
        rows = []
        for r in data if isinstance(data, list) else []:
            t = int(r["t"]) * 1000
            rate = float(r["r"])
            if abs(rate) < 0.05:
                rows.append((t - t % 60_000, rate))
        if not rows:
            return np.empty((0, 2))
        arr = np.asarray(rows, dtype="float64")
        return arr[np.argsort(arr[:, 0], kind="stable")]

    def _tickers(self) -> list[tuple[str, float, float]]:
        data = self._get("/tickers")
        out = []
        for r in data if isinstance(data, list) else []:
            name = r.get("contract", "")
            if name.endswith("_USDT"):
                try:
                    out.append((name[:-5] + "USDT", float(r["last"]), float(r.get("volume_24h_quote") or 0.0)))
                except (KeyError, ValueError, TypeError):
                    continue
        return out

    def last_prices(self) -> dict[str, float]:
        return {s: last for s, last, _ in self._tickers()}

    def day_volumes(self) -> dict[str, float]:
        return {s: vol for s, _, vol in self._tickers()}


SOURCES: dict[str, type] = {"okx": OkxData, "gate": GateData}


def make_source(name: str, http: HttpClient | None = None) -> MarketSource:
    if name not in SOURCES:
        raise ValueError(f"unknown market data source {name!r}; available: {sorted(SOURCES)}")
    return SOURCES[name](http)
