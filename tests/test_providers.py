import time

import pandas as pd
import pytest

from tradebot.data.providers import (BinanceVision, Coinbase, Gate, HttpClient, KuCoin, MarketData, OKX,
                                     ProviderError)

H = 3_600_000
T0 = 1_793_836_800_000  # 2026-11-05 00:00 UTC


class FakeHttp(HttpClient):
    def __init__(self, payload):
        super().__init__(retries=1)
        self.payload = payload
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload(url, params) if callable(self.payload) else self.payload


def _one(prov_cls, payload):
    p = prov_cls(FakeHttp(payload))
    df = p.fetch("1h", 1)
    assert len(df) == 1
    return df.iloc[0]


def test_column_mappings_for_every_venue():
    # o=100 h=110 l=90 c=105 v=7 in each venue's native layout
    r = _one(BinanceVision, [[T0, "100", "110", "90", "105", "7", T0 + H - 1, "0", 1, "0", "0", "0"]])
    assert (r.open, r.high, r.low, r.close, r.volume) == (100, 110, 90, 105, 7)
    r = _one(OKX, {"code": "0", "data": [[str(T0), "100", "110", "90", "105", "7", "0", "0", "1"]]})
    assert (r.open, r.high, r.low, r.close) == (100, 110, 90, 105)
    r = _one(KuCoin, {"code": "200000", "data": [[str(T0 // 1000), "100", "105", "110", "90", "7", "0"]]})
    assert (r.open, r.high, r.low, r.close, r.volume) == (100, 110, 90, 105, 7)
    r = _one(Gate, [[str(T0 // 1000), "0", "105", "110", "90", "100", "7", "true"]])
    assert (r.open, r.high, r.low, r.close, r.volume) == (100, 110, 90, 105, 7)
    r = _one(Coinbase, [[T0 // 1000, 90, 110, 100, 105, 7]])
    assert (r.open, r.high, r.low, r.close, r.volume) == (100, 110, 90, 105, 7)


def _rows(n, end_ms):
    return [[end_ms - (n - 1 - i) * H, "100", "101", "99", "100.5", "1", 0, 0, 0, 0, 0, 0] for i in range(n)]


def test_binance_paginates_backwards():
    def payload(url, params):
        end = params.get("endTime", T0)
        n = params["limit"]
        return _rows(n, end - (end % H))
    p = BinanceVision(FakeHttp(payload))
    df = p.fetch("1h", 2500)
    assert len(df) == 2500 and df.index.is_monotonic_increasing
    steps = df.index.to_series().diff().dropna().unique()
    assert len(steps) == 1


def test_market_data_falls_back_and_rejects_stale():
    now = T0 + 10 * 60_000
    good = lambda url, params: _rows(params["limit"], T0)  # noqa: E731

    class Broken(BinanceVision):
        def _page(self, *a, **k):
            raise ProviderError("boom")

    md = MarketData(["binance_vision"], clock=lambda: now / 1000)
    md.providers = [Broken(FakeHttp(None)), BinanceVision(FakeHttp(good))]
    md.providers[1].name = "backup"
    res = md.fetch("1h", 200, 100)
    assert res.provider == "backup" and len(res.candles) == 200
    assert res.forming_open_ms == T0 and int(res.candles.index[-1].value // 1_000_000) == T0 - H

    stale = lambda url, params: _rows(params["limit"], T0 - 10 * H)  # noqa: E731
    md2 = MarketData(["binance_vision"], http=FakeHttp(stale), clock=lambda: now / 1000)
    with pytest.raises(ProviderError, match="stale"):
        md2.fetch("1h", 200, 100)


def test_http_client_retries_then_fails_fast_on_4xx():
    class Resp:
        def __init__(self, code, body=b'{"ok": true}'):
            self.status_code = code
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, n):
            for i in range(0, len(self.body), n):
                yield self.body[i:i + n]

    class Sess:
        def __init__(self, codes, body=b'{"ok": true}'):
            self.codes = list(codes)
            self.headers = {}
            self.n = 0
            self.body = body

        def get(self, url, params=None, timeout=None, stream=False):
            self.n += 1
            return Resp(self.codes.pop(0), self.body)

    s = Sess([500, 429, 200])
    c = HttpClient(retries=3, session=s, sleep=lambda _: None)
    assert c.get_json("u") == {"ok": True} and s.n == 3
    s = Sess([451, 200])
    c = HttpClient(retries=3, session=s, sleep=lambda _: None)
    with pytest.raises(ProviderError):
        c.get_json("u")
    assert s.n == 1
    # oversized bodies are refused
    s = Sess([200], body=b"[" + b"1," * 600_000 + b"1]")
    c = HttpClient(retries=1, session=s, sleep=lambda _: None)
    c.max_json_bytes = 1_000_000
    with pytest.raises(ProviderError, match="larger"):
        c.get_json("u")


@pytest.mark.network
def test_live_binance_vision():
    md = MarketData(["binance_vision"])
    res = md.fetch("1h", 300, 200)
    assert len(res.candles) == 300
    assert time.time() * 1000 - (int(res.candles.index[-1].value // 1_000_000) + H) < 2 * H


def test_clock_is_read_before_the_request():
    # The response is built at 10:59:58 (bar 10:00 still forming), the request
    # returns after 11:00. The 10:00 bar must NOT be treated as closed.
    t_req = T0 + 59 * 60_000 + 58_000
    clock = {"t": t_req}

    def payload(url, params):
        clock["t"] = T0 + H + 30_000  # time passes during the call
        return _rows(params["limit"], T0)

    md = MarketData(["binance_vision"], http=FakeHttp(payload), clock=lambda: clock["t"] / 1000)
    res = md.fetch("1h", 200, 100)
    assert int(res.candles.index[-1].value // 1_000_000) == T0 - H
    assert res.forming_open_ms == T0


def test_implausible_forming_bar_is_rejected():
    now = T0 + 10 * 60_000

    def payload(url, params):
        rows = _rows(params["limit"], T0)
        rows[-1][1] = "1.0"   # garbage open on the forming bar
        rows[-1][3] = "1.0"
        return rows

    md = MarketData(["binance_vision"], http=FakeHttp(payload), clock=lambda: now / 1000)
    res = md.fetch("1h", 200, 100)
    assert res.forming_open is None and any("rejected" in w for w in res.warnings)


@pytest.mark.network
def test_live_funding_feed():
    s = MarketData(["binance_vision"]).fetch_funding()
    assert len(s) >= 50 and s.abs().max() < 0.05
    assert time.time() * 1000 - s.index[-1] < 9 * 3_600_000
