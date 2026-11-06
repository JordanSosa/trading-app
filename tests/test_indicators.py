import numpy as np
import pytest

from tradebot import indicators as ind


@pytest.mark.parametrize("fn", [
    lambda d: ind.sma(d["close"], 20), lambda d: ind.ema(d["close"], 50), lambda d: ind.atr(d, 14),
    lambda d: ind.rsi(d["close"], 14), lambda d: ind.realized_vol(d["close"], 48),
    lambda d: ind.ewm_vol(d["close"], 24), lambda d: ind.donchian_high(d["high"], 20),
    lambda d: ind.donchian_low(d["low"], 20), lambda d: ind.zscore(d["close"], 30),
    lambda d: ind.efficiency_ratio(d["close"], 30), lambda d: ind.adx(d, 14),
])
def test_indicators_are_causal(candles, fn):
    full = fn(candles)
    for k in (60, 333, 2000):
        part = fn(candles.iloc[:k])
        a, b = part.iloc[-1], full.iloc[k - 1]
        assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b, rel=1e-9)


def test_donchian_excludes_current_bar(candles):
    dh = ind.donchian_high(candles["high"], 5)
    assert dh.iloc[10] == candles["high"].iloc[5:10].max()


def test_rsi_bounds(candles):
    r = ind.rsi(candles["close"], 14).dropna()
    assert r.between(0, 100).all()
