import time

import numpy as np
import pytest

from tradebot.research import check_causal
from tradebot.strategies import REGISTRY, available, get_strategy

NAMES = [n for n in available() if not n.startswith("_")]


@pytest.mark.parametrize("name", NAMES)
def test_strategy_contract(name, candles):
    s = get_strategy(name)
    assert s.warmup_bars >= 1
    t = time.time()
    sig = s.signals(candles)
    assert time.time() - t < 5.0, "signals too slow"
    tgt = sig["target"].dropna()
    assert ((tgt >= -1) & (tgt <= 1)).all()
    sd = sig["stop_dist"].dropna()
    assert (sd >= 0).all()
    assert not np.isinf(sig.to_numpy()).any()


@pytest.mark.parametrize("name", NAMES)
def test_strategy_is_causal(name, candles):
    s = get_strategy(name)
    assert check_causal(s, candles, n_checks=12) == []


def test_unknown_params_rejected():
    with pytest.raises(ValueError):
        get_strategy("buy_hold", {"nope": 1})
    with pytest.raises(KeyError):
        get_strategy("does_not_exist")


def test_registry_has_benchmark():
    assert "buy_hold" in REGISTRY
