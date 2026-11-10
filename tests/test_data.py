"""Archive parsing and the live market-data adapters, against canned responses (no network)."""

from __future__ import annotations

import hashlib
import io
import zipfile

import numpy as np
import pytest

from compounder.data import archive
from compounder.data.live import DataError, GateData, HOUR_MS, OkxData
from compounder.data.universe import base_asset, is_crypto_perp


def zip_bytes(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


KLINE_HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"


def test_parse_klines_handles_header_duplicates_and_order():
    t0 = 1_704_067_200_000
    rows = [f"{t0 + 3_600_000},101,103,100,102,10,{t0 + 7_199_999},1020,5,4,408,0",
            f"{t0},100,102,99,101,10,{t0 + 3_599_999},1010,5,4,404,0",
            f"{t0},100,102,99,101.5,11,{t0 + 3_599_999},1015,5,4,404,0"]      # later duplicate wins
    arr = archive.parse_klines(zip_bytes("x.csv", KLINE_HEADER + "\n".join(rows) + "\n"), 3_600_000)
    assert arr.shape == (2, 9)
    assert arr[:, 0].tolist() == [t0, t0 + 3_600_000]
    assert arr[0, 4] == 101.5 and arr[0, 6] == 1015 and arr[1, 8] == 408


def test_parse_klines_without_header_and_microsecond_timestamps():
    t0 = 1_704_067_200_000
    text = f"{t0 * 1000},100,102,99,101,10,{t0 * 1000 + 1},1010,5,4,404,0\n"
    arr = archive.parse_klines(zip_bytes("x.csv", text), 3_600_000)
    assert arr[0, 0] == t0


def test_parse_funding_rounds_settlement_jitter():
    text = "calc_time,funding_interval_hours,last_funding_rate\n1704096000004,8,0.0001\n1704124800001,8,-0.0002\n"
    arr = archive.parse_funding(zip_bytes("f.csv", text))
    assert arr[:, 0].tolist() == [1704096000000, 1704124800000]
    assert arr[:, 1].tolist() == [0.0001, -0.0002]


def test_archive_rejects_multi_member_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.csv", "1,2")
        zf.writestr("b.csv", "1,2")
    with pytest.raises(archive.ArchiveError):
        archive.parse_klines(buf.getvalue(), 3_600_000)


class StubHttp:
    def __init__(self, blobs: dict[str, bytes | None]):
        self.blobs = blobs
        self.requested: list[str] = []

    def get_bytes(self, url: str, params=None):
        self.requested.append(url)
        return self.blobs.get(url)


def test_fetch_verified_checks_md5_and_caches(tmp_path):
    blob = zip_bytes("x.csv", "1,2,3\n")
    key = "data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip"
    obj = archive.ArchiveObject(key, len(blob), hashlib.md5(blob).hexdigest())
    http = StubHttp({f"{archive.ARCHIVE}/{key}": blob})
    assert archive.fetch_verified(http, obj, tmp_path) == blob
    assert archive.fetch_verified(http, obj, tmp_path) == blob        # second call served from the cache
    assert len(http.requested) == 1
    bad = archive.ArchiveObject(key, len(blob), "0" * 32)
    (tmp_path / key).unlink()
    with pytest.raises(archive.ArchiveError):
        archive.fetch_verified(http, bad, tmp_path)
    assert not (tmp_path / key).exists()                              # corrupt data is never stored


def test_fetch_verified_falls_back_to_published_sha256(tmp_path):
    blob = zip_bytes("x.csv", "1,2,3\n")
    key = "data/x/y.zip"
    obj = archive.ArchiveObject(key, len(blob), "abc-2")              # multipart ETag: not an MD5
    sha = hashlib.sha256(blob).hexdigest()
    http = StubHttp({f"{archive.ARCHIVE}/{key}": blob, f"{archive.ARCHIVE}/{key}.CHECKSUM": f"{sha}  y.zip\n".encode()})
    assert archive.fetch_verified(http, obj, tmp_path) == blob
    http2 = StubHttp({f"{archive.ARCHIVE}/{key}": blob, f"{archive.ARCHIVE}/{key}.CHECKSUM": None})
    with pytest.raises(archive.ArchiveError):
        archive.fetch_verified(http2, obj, tmp_path / "other")


def test_universe_filter():
    assert is_crypto_perp("BTCUSDT") and is_crypto_perp("1000PEPEUSDT") and is_crypto_perp("BILLUSDT")
    for s in ("USDCUSDT", "BTCDOMUSDT", "XAUUSDT", "NVDAUSDT", "KORUUSDT", "BTCUSDT_230929"):
        assert not is_crypto_perp(s), s
    assert base_asset("BTC-USDT-SWAP") == "BTC" and base_asset("ethusdt") == "ETH"


class JsonHttp:
    """Returns canned JSON by URL path suffix; records calls."""

    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple[str, dict]] = []

    def get_json(self, url: str, params=None):
        self.calls.append((url, dict(params or {})))
        for suffix, fn in self.routes.items():
            if url.endswith(suffix):
                return fn(dict(params or {}))
        raise AssertionError(f"unexpected url {url}")


def okx_instrument(inst_id: str, category: str = "1", settle: str = "USDT", state: str = "live"):
    return {"instId": inst_id, "settleCcy": settle, "ctType": "linear", "state": state, "instCategory": category,
            "ctVal": "0.01", "ctMult": "1", "lotSz": "0.01", "minSz": "0.01", "tickSz": "0.1", "lever": "100",
            "listTime": "1573557408000"}


def test_okx_instruments_keep_only_live_crypto_usdt_swaps():
    http = JsonHttp({"/public/instruments": lambda p: {"code": "0", "data": [
        okx_instrument("BTC-USDT-SWAP"), okx_instrument("NVDA-USDT-SWAP", category="3"),
        okx_instrument("XAU-USDT-SWAP", category="4"), okx_instrument("BTC-USD-SWAP", settle="BTC"),
        okx_instrument("OLD-USDT-SWAP", state="suspend"), okx_instrument("USDC-USDT-SWAP")]}})
    inst = OkxData(http, pause_s=0).instruments()
    assert list(inst) == ["BTCUSDT"]
    assert inst["BTCUSDT"].contract_size == 0.01 and inst["BTCUSDT"].venue_id == "BTC-USDT-SWAP"
    assert inst["BTCUSDT"].min_notional(75_000.0) == pytest.approx(7.5)


def test_okx_candles_paginate_backwards_and_sort():
    t0 = 1_704_067_200_000
    bars = [[str(t0 + i * HOUR_MS), "100", "101", "99", "100.5", "10", "1", "1005", "1"] for i in range(450)]

    def candles(p):
        after = int(p.get("after", 10 ** 18))
        older = [b for b in reversed(bars) if int(b[0]) < after]
        return {"code": "0", "data": older[: int(p["limit"])]}

    http = JsonHttp({"/market/candles": candles, "/market/history-candles": candles})
    src = OkxData(http, pause_s=0)
    inst = type("I", (), {"venue_id": "BTC-USDT-SWAP"})()
    arr = src.candles(inst, 420)
    assert arr.shape == (420, 9)
    assert (np.diff(arr[:, 0]) == HOUR_MS).all() and arr[-1, 0] == t0 + 449 * HOUR_MS
    assert arr[0, 6] == 1005                                          # quote volume column
    assert http.calls[0][0].endswith("/market/candles") and http.calls[1][0].endswith("/history-candles")


def test_okx_error_payload_raises():
    http = JsonHttp({"/public/instruments": lambda p: {"code": "50011", "msg": "rate limited", "data": []}})
    with pytest.raises(DataError):
        OkxData(http, pause_s=0).instruments()


def test_okx_funding_and_tickers():
    http = JsonHttp({
        "/public/funding-rate-history": lambda p: {"code": "0", "data": [
            {"fundingTime": "1704096000003", "realizedRate": "0.0001", "fundingRate": "0.0002"},
            {"fundingTime": "1704067200000", "realizedRate": "", "fundingRate": "-0.0003"},
            {"fundingTime": "1704124800000", "realizedRate": "0.9", "fundingRate": "0.9"}]},   # implausible: dropped
        "/market/tickers": lambda p: {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "last": "75000", "volCcy24h": "1000"},
            {"instId": "ETH-USD-SWAP", "last": "2000", "volCcy24h": "5"}]}})
    src = OkxData(http, pause_s=0)
    f = src.funding(type("I", (), {"venue_id": "BTC-USDT-SWAP"})())
    assert f.tolist() == [[1704067200000, -0.0003], [1704096000000, 0.0001]]
    assert src.last_prices() == {"BTCUSDT": 75000.0}
    assert src.day_volumes() == {"BTCUSDT": 75_000_000.0}


def test_gate_candles_and_instruments():
    t0 = 1_704_067_200
    http = JsonHttp({
        "/contracts": lambda p: [
            {"name": "BTC_USDT", "type": "direct", "quanto_multiplier": "0.0001", "order_size_min": 1,
             "order_price_round": "0.1", "leverage_max": "100", "create_time": 1574035200, "in_delisting": False},
            {"name": "OLD_USDT", "type": "direct", "quanto_multiplier": "1", "order_price_round": "0.1",
             "in_delisting": True},
            {"name": "TSLA_USDT", "type": "direct", "quanto_multiplier": "1", "order_price_round": "0.1"}],
        "/candlesticks": lambda p: [{"t": t0 + i * 3600, "o": "100", "h": "101", "l": "99", "c": "100", "v": 50,
                                     "sum": "500"} for i in range(5)]})
    src = GateData(http, pause_s=0)
    inst = src.instruments()
    assert list(inst) == ["BTCUSDT"]
    arr = src.candles(inst["BTCUSDT"], 5)
    assert arr.shape == (5, 9) and arr[0, 0] == t0 * 1000 and arr[0, 6] == 500.0
