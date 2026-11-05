"""Public market-data providers for BTC/USDT candles (no API keys required).

Every provider returns the most recent ``limit`` candles (oldest first) in the
canonical frame from :mod:`tradebot.data.candles`. The result may include the
currently forming bar; callers split it off with ``split_closed``.

``binance_vision`` (Binance's official market-data-only mirror) is the primary
source: it is the deepest BTC/USDT book and, unlike ``api.binance.com``, it is
reachable from US-hosted runners such as GitHub Actions.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd
import requests

from ..config import TIMEFRAME_MS
from .candles import empty_frame, make_frame, normalise, split_closed, validate

log = logging.getLogger(__name__)

USER_AGENT = "tradebot/0.1 (+paper trading research bot)"


class ProviderError(RuntimeError):
    """Raised when a provider cannot deliver usable data."""


class HttpClient:
    """Small JSON-over-HTTPS client with retries and exponential backoff."""

    def __init__(self, timeout_s: float = 15.0, retries: int = 3, backoff_s: float = 1.0,
                 session: requests.Session | None = None, sleep: Callable[[float], None] = time.sleep):
        self.timeout_s = timeout_s
        self.retries = max(1, retries)
        self.backoff_s = backoff_s
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._sleep = sleep

    max_json_bytes = 8 * 1024 * 1024
    max_blob_bytes = 64 * 1024 * 1024

    def _read(self, resp: requests.Response, limit: int, deadline: float) -> bytes:
        """Read a streamed body with an overall deadline and a size cap."""
        buf = bytearray()
        for chunk in resp.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > limit:
                raise ProviderError(f"response larger than {limit} bytes")
            if time.monotonic() > deadline:
                raise ProviderError("response took too long")
        return bytes(buf)

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            fatal = False
            try:
                deadline = time.monotonic() + 2 * self.timeout_s
                with self.session.get(url, params=params, timeout=self.timeout_s, stream=True) as resp:
                    if resp.status_code == 429 or resp.status_code >= 500:
                        raise ProviderError(f"HTTP {resp.status_code} from {url}")
                    if resp.status_code >= 400:
                        # 4xx other than 429 will not fix itself: fail fast.
                        fatal = True
                        raise ProviderError(f"HTTP {resp.status_code} from {url}")
                    body = self._read(resp, self.max_json_bytes, deadline)
                return json.loads(body)
            except (requests.RequestException, ValueError, ProviderError) as exc:
                last_exc = exc
                if fatal or attempt == self.retries - 1:
                    break
                self._sleep(self.backoff_s * (2 ** attempt))
        raise ProviderError(f"request failed: {url}: {last_exc}") from last_exc

    def get_bytes(self, url: str) -> bytes | None:
        """GET raw bytes; returns None on 404 (used for bulk archives)."""
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                deadline = time.monotonic() + max(self.timeout_s, 60.0) * 4
                with self.session.get(url, timeout=max(self.timeout_s, 60.0), stream=True) as resp:
                    if resp.status_code == 404:
                        return None
                    if resp.status_code >= 400:
                        raise ProviderError(f"HTTP {resp.status_code} from {url}")
                    return self._read(resp, self.max_blob_bytes, deadline)
            except (requests.RequestException, ProviderError) as exc:
                last_exc = exc
                if attempt < self.retries - 1:
                    self._sleep(self.backoff_s * (2 ** attempt))
        raise ProviderError(f"download failed: {url}: {last_exc}") from last_exc


@dataclass
class Provider:
    """Base class. Subclasses implement ``_page`` and ``ticker``."""

    http: HttpClient
    name: str = "base"
    page_size: int = 1000
    pause_s: float = 0.0

    def supports(self, interval: str) -> bool:
        return interval in self._intervals()

    def _intervals(self) -> dict[str, Any]:
        raise NotImplementedError

    def _page(self, interval: str, end_ms: int | None, n: int) -> list[tuple]:
        """Return up to ``n`` bars with open_time <= end_ms (or latest), any order."""
        raise NotImplementedError

    def ticker(self) -> float:
        raise NotImplementedError

    def fetch(self, interval: str, limit: int, end_ms: int | None = None) -> pd.DataFrame:
        if not self.supports(interval):
            raise ProviderError(f"{self.name} does not support interval {interval}")
        step = TIMEFRAME_MS[interval]
        rows: list[tuple] = []
        cursor = end_ms
        seen_oldest: int | None = None
        while len(rows) < limit:
            n = min(self.page_size, limit - len(rows))
            page = self._page(interval, cursor, n)
            if not page:
                break
            oldest = min(int(r[0]) for r in page)
            if seen_oldest is not None and oldest >= seen_oldest:
                break  # provider stopped going further back
            seen_oldest = oldest
            rows.extend(page)
            cursor = oldest - step
            if self.pause_s:
                time.sleep(self.pause_s)
        df = make_frame([tuple(float(x) for x in r[:6]) for r in rows]) if rows else empty_frame()
        if len(df) > limit:
            df = df.iloc[-limit:]
        return df


class BinanceVision(Provider):
    BASE = "https://data-api.binance.vision"

    def __init__(self, http: HttpClient):
        super().__init__(http=http, name="binance_vision", page_size=1000)

    def _intervals(self) -> dict[str, Any]:
        return {k: k for k in ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d")}

    def _page(self, interval: str, end_ms: int | None, n: int) -> list[tuple]:
        params: dict[str, Any] = {"symbol": "BTCUSDT", "interval": interval, "limit": n}
        if end_ms is not None:
            params["endTime"] = end_ms
        data = self.http.get_json(f"{self.BASE}/api/v3/klines", params)
        return [(int(k[0]), k[1], k[2], k[3], k[4], k[5]) for k in data]

    def ticker(self) -> float:
        data = self.http.get_json(f"{self.BASE}/api/v3/ticker/price", {"symbol": "BTCUSDT"})
        return float(data["price"])


class OKX(Provider):
    BASE = "https://www.okx.com"

    def __init__(self, http: HttpClient):
        super().__init__(http=http, name="okx", page_size=100, pause_s=0.12)

    def _intervals(self) -> dict[str, Any]:
        return {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "2h": "2H", "4h": "4H", "1d": "1Dutc"}

    def _page(self, interval: str, end_ms: int | None, n: int) -> list[tuple]:
        params: dict[str, Any] = {"instId": "BTC-USDT", "bar": self._intervals()[interval], "limit": min(n, 100)}
        if end_ms is not None:
            params["after"] = end_ms + 1  # 'after' returns records strictly older than ts
        data = self.http.get_json(f"{self.BASE}/api/v5/market/history-candles", params)
        if str(data.get("code")) != "0":
            raise ProviderError(f"okx error: {data.get('msg')}")
        return [(int(k[0]), k[1], k[2], k[3], k[4], k[5]) for k in data["data"]]

    def ticker(self) -> float:
        data = self.http.get_json(f"{self.BASE}/api/v5/market/ticker", {"instId": "BTC-USDT"})
        return float(data["data"][0]["last"])


class KuCoin(Provider):
    BASE = "https://api.kucoin.com"

    def __init__(self, http: HttpClient):
        super().__init__(http=http, name="kucoin", page_size=1500, pause_s=0.2)

    def _intervals(self) -> dict[str, Any]:
        return {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1hour",
                "2h": "2hour", "4h": "4hour", "1d": "1day"}

    def _page(self, interval: str, end_ms: int | None, n: int) -> list[tuple]:
        step_s = TIMEFRAME_MS[interval] // 1000
        end_s = (end_ms // 1000) if end_ms is not None else int(time.time())
        params = {"type": self._intervals()[interval], "symbol": "BTC-USDT",
                  "startAt": end_s - step_s * (n - 1), "endAt": end_s + 1}
        data = self.http.get_json(f"{self.BASE}/api/v1/market/candles", params)
        if str(data.get("code")) != "200000":
            raise ProviderError(f"kucoin error: {data.get('msg')}")
        # KuCoin rows: [time_s, open, close, high, low, volume, turnover]
        return [(int(k[0]) * 1000, k[1], k[3], k[4], k[2], k[5]) for k in data["data"]]

    def ticker(self) -> float:
        data = self.http.get_json(f"{self.BASE}/api/v1/market/orderbook/level1", {"symbol": "BTC-USDT"})
        return float(data["data"]["price"])


class Gate(Provider):
    BASE = "https://api.gateio.ws"

    def __init__(self, http: HttpClient):
        super().__init__(http=http, name="gate", page_size=1000, pause_s=0.1)

    def _intervals(self) -> dict[str, Any]:
        return {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d"}

    def _page(self, interval: str, end_ms: int | None, n: int) -> list[tuple]:
        step_s = TIMEFRAME_MS[interval] // 1000
        end_s = (end_ms // 1000) if end_ms is not None else int(time.time())
        params = {"currency_pair": "BTC_USDT", "interval": self._intervals()[interval],
                  "from": end_s - step_s * (n - 1), "to": end_s}
        data = self.http.get_json(f"{self.BASE}/api/v4/spot/candlesticks", params)
        # Gate rows: [time_s, quote_volume, close, high, low, open, base_volume, window_closed]
        return [(int(k[0]) * 1000, k[5], k[3], k[4], k[2], k[6]) for k in data]

    def ticker(self) -> float:
        data = self.http.get_json(f"{self.BASE}/api/v4/spot/tickers", {"currency_pair": "BTC_USDT"})
        return float(data[0]["last"])


class Coinbase(Provider):
    BASE = "https://api.exchange.coinbase.com"

    def __init__(self, http: HttpClient):
        super().__init__(http=http, name="coinbase", page_size=300, pause_s=0.15)

    def _intervals(self) -> dict[str, Any]:
        return {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86400}

    def _page(self, interval: str, end_ms: int | None, n: int) -> list[tuple]:
        gran = self._intervals()[interval]
        end_s = (end_ms // 1000) if end_ms is not None else int(time.time())
        start_s = end_s - gran * (n - 1)
        iso = lambda s: datetime.fromtimestamp(s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
        params = {"granularity": gran, "start": iso(start_s), "end": iso(end_s)}
        data = self.http.get_json(f"{self.BASE}/products/BTC-USDT/candles", params)
        if isinstance(data, dict):
            raise ProviderError(f"coinbase error: {data.get('message')}")
        # Coinbase rows: [time_s, low, high, open, close, volume]
        return [(int(k[0]) * 1000, k[3], k[2], k[1], k[4], k[5]) for k in data]

    def ticker(self) -> float:
        data = self.http.get_json(f"{self.BASE}/products/BTC-USDT/ticker")
        return float(data["price"])


PROVIDERS: dict[str, type[Provider]] = {
    "binance_vision": BinanceVision,
    "okx": OKX,
    "kucoin": KuCoin,
    "gate": Gate,
    "coinbase": Coinbase,
}


@dataclass
class FetchResult:
    candles: pd.DataFrame          # closed bars only, oldest first
    forming_open: float | None     # open price of the currently forming bar
    forming_open_ms: int | None    # its open time
    provider: str
    warnings: list[str]
    forming_close: float | None = None  # latest traded price inside the forming bar


class MarketData:
    """Fetch candles from the first healthy provider in priority order."""

    close_grace_ms = 2_000
    max_forming_jump = 0.05  # forming open must be within 5% of the last close

    def __init__(self, provider_names: tuple[str, ...] | list[str], http: HttpClient | None = None,
                 clock: Callable[[], float] = time.time):
        unknown = [p for p in provider_names if p not in PROVIDERS]
        if unknown:
            raise ValueError(f"unknown providers: {unknown}; available: {sorted(PROVIDERS)}")
        self.http = http or HttpClient()
        self.providers = [PROVIDERS[name](self.http) for name in provider_names]
        self.clock = clock

    def fetch(self, interval: str, closed_bars: int, min_bars: int) -> FetchResult:
        step = TIMEFRAME_MS[interval]
        errors: list[str] = []
        for prov in self.providers:
            if not prov.supports(interval):
                continue
            try:
                # Read the clock BEFORE the request: a bar can only be final if
                # its close time had passed when the exchange built the response.
                # The grace absorbs exchange-side latency in finalising a bar.
                now_ms = int(self.clock() * 1000)
                raw = prov.fetch(interval, closed_bars + 1)
                closed, forming = split_closed(raw, now_ms - self.close_grace_ms, step)
                warns = validate(closed, interval_ms=step)
                if len(closed) < min_bars:
                    raise ProviderError(f"only {len(closed)} closed bars (< {min_bars} required)")
                last_close_ms = int(closed.index[-1].value // 1_000_000) + step
                if now_ms - last_close_ms > 2 * step:
                    raise ProviderError("data is stale (latest closed bar is more than 2 intervals old)")
                f_open = f_ms = f_close = None
                if forming is not None:
                    f_open = float(forming["open"])
                    f_close = float(forming["close"])
                    f_ms = int(forming.name.value // 1_000_000)
                    ref = float(closed["close"].iloc[-1])
                    sane = (math.isfinite(f_open) and math.isfinite(f_close) and f_open > 0 and f_close > 0
                            and abs(f_open / ref - 1) <= self.max_forming_jump
                            and f_ms == int(closed.index[-1].value // 1_000_000) + step)
                    if not sane:
                        # Its open is the execution price of forward fills; never
                        # trade on an implausible value. The order then fills at
                        # this bar's (validated) open once it has closed.
                        warns.append(f"forming bar rejected (open {f_open}, last close {ref})")
                        f_open = f_ms = f_close = None
                return FetchResult(closed.iloc[-closed_bars:], f_open, f_ms, prov.name, warns, f_close)
            except Exception as exc:  # try the next provider
                msg = f"{prov.name}: {exc}"
                log.warning("market data provider failed: %s", msg)
                errors.append(msg)
        raise ProviderError("all market data providers failed: " + " | ".join(errors))

    def fetch_funding(self, now_ms: int | None = None) -> pd.Series:
        """Recent BTC/USDT perpetual funding settlements (~33 days), epoch-ms indexed.

        Binance's futures API is geo-blocked from US-hosted runners, so the live
        run uses OKX's public history (Bitget as fallback). Backtests use the
        Binance archive; venue funding rates are highly correlated but not
        identical, which is a documented approximation.
        """
        errors = []
        sources = (
            ("okx", "https://www.okx.com/api/v5/public/funding-rate-history",
             {"instId": "BTC-USDT-SWAP", "limit": 100}),
            ("bitget", "https://api.bitget.com/api/v2/mix/market/history-fund-rate",
             {"symbol": "BTCUSDT", "productType": "usdt-futures", "pageSize": 100}),
        )
        for name, url, params in sources:
            try:
                data = self.http.get_json(url, params)
                if name == "okx":
                    if str(data.get("code")) != "0":
                        raise ProviderError(f"okx error: {data.get('msg')}")
                    rows = [(int(r["fundingTime"]), float(r.get("realizedRate") or r["fundingRate"]))
                            for r in data["data"]]
                else:
                    if str(data.get("code")) != "00000":
                        raise ProviderError(f"bitget error: {data.get('msg')}")
                    rows = [(int(r["fundingTime"]), float(r["fundingRate"])) for r in data["data"]]
                if not rows:
                    raise ProviderError("empty funding history")
                times = [t - t % 60_000 for t, _ in rows]
                ser = pd.Series([r for _, r in rows], index=times, dtype="float64").sort_index()
                ser = ser[~ser.index.duplicated(keep="last")]
                if not ser.abs().lt(0.05).all():
                    raise ProviderError("implausible funding rate in response")
                return ser
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                log.warning("funding source %s failed: %s", name, exc)
        raise ProviderError("all funding sources failed: " + " | ".join(errors))

    def cross_check(self, primary: str, closed: pd.DataFrame, interval: str, max_dev: float,
                    bars: int = 6) -> tuple[bool, str]:
        """Compare the latest closed bars with the same bars from another provider.

        Returns ``(ok, message)``. ``ok`` is False only when a secondary source
        was reachable *and* disagrees by more than ``max_dev`` on some bar's
        close, which indicates corrupt data rather than normal venue basis.
        """
        if closed.empty:
            return True, "no data to cross-check"
        for prov in self.providers:
            if prov.name == primary or not prov.supports(interval):
                continue
            try:
                other = prov.fetch(interval, bars + 1)
            except Exception as exc:  # noqa: BLE001 - try the next one
                log.warning("cross-check provider %s failed: %s", prov.name, exc)
                continue
            common = closed.index.intersection(other.index)
            if len(common) == 0:
                continue
            a = closed.loc[common, "close"].to_numpy()
            b = other.loc[common, "close"].to_numpy()
            dev = float((abs(a - b) / b).max())
            return dev <= max_dev, (
                f"{primary} vs {prov.name}: max close deviation {dev:.3%} over {len(common)} bar(s)"
            )
        return True, "no secondary provider reachable; cross-check skipped"


def normalise_frame(df: pd.DataFrame) -> pd.DataFrame:  # re-export convenience
    return normalise(df)
