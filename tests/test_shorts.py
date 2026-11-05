"""Short positions (perp mode): accounting, funding, stops, flips."""

import math

import pandas as pd
import pytest

from tradebot.config import ConfigError, config_from_dict
from tradebot.engine import Engine, EngineState
from test_engine import H, bar, params


def eng(**kw) -> Engine:
    base = dict(max_short=1.0, fee_rate=0.0005)
    base.update(kw)
    return Engine(params(**base), EngineState.fresh(10_000.0))


def test_short_profits_when_price_falls_and_books_costs():
    e = eng()
    bar(e, 0, 100, 100, 100, 100, -1.0)
    bar(e, 1, 100, 100, 100, 100, -1.0)      # short at 100 * (1 - slip)
    assert e.state.qty < 0 and e.trades[0]["side"] == "sell"
    bar(e, 2, 100, 100, 90, 90, 0.0)          # decide to cover
    bar(e, 3, 90, 90, 90, 90, 0.0)            # cover at 90 * (1 + slip)
    s = e.state
    assert s.qty == 0.0
    q = e.trades[0]["qty"]
    expected = q * 100 * (1 - 0.0003) * (1 - 0.0005) - q * 90 * (1 + 0.0003) * (1 + 0.0005)
    assert s.cash - 10_000 == pytest.approx(expected, rel=1e-9)
    assert s.realized_pnl == pytest.approx(expected, rel=1e-9)
    assert e.trips[0]["side"] == "short" and e.trips[0]["pnl"] == pytest.approx(expected, rel=1e-9)


def test_short_loses_when_price_rises_and_equity_is_marked_correctly():
    e = eng()
    bar(e, 0, 100, 100, 100, 100, -1.0)
    bar(e, 1, 100, 100, 100, 100, -1.0)
    bar(e, 2, 100, 106, 100, 105, -1.0)
    s = e.state
    assert s.last_equity == pytest.approx(s.cash + s.qty * 105)
    assert s.last_equity < 10_000 * 0.96


def test_funding_sign_long_pays_short_receives():
    long_e, short_e = eng(), eng()
    for e, tgt in ((long_e, 1.0), (short_e, -1.0)):
        bar(e, 0, 100, 100, 100, 100, tgt)
        bar(e, 1, 100, 100, 100, 100, tgt)
        cash_before = e.state.cash
        e.on_close(2 * H, 100, 100, 100, 100, tgt, math.nan, 0.001)  # +0.1% funding
        notional = abs(e.state.qty) * 100
        sign = 1 if tgt > 0 else -1
        assert e.state.cash == pytest.approx(cash_before - sign * notional * 0.001)
        assert e.state.funding_paid == pytest.approx(sign * notional * 0.001)


def test_flip_long_to_short_closes_then_opens():
    e = eng()
    bar(e, 0, 100, 100, 100, 100, 1.0)
    bar(e, 1, 100, 100, 100, 100, -1.0)
    bar(e, 2, 100, 100, 100, 100, -1.0)
    sides = [t["side"] for t in e.trades]
    assert sides == ["buy", "sell", "sell"]
    s = e.state
    assert s.qty * 100 / e.equity_at(100) == pytest.approx(-1.0, abs=0.01)
    assert e.trips[0]["side"] == "long"
    # and back to long: cover then buy
    bar(e, 3, 100, 100, 100, 100, 1.0)
    bar(e, 4, 100, 100, 100, 100, 1.0)
    assert [t["side"] for t in e.trades][-2:] == ["buy", "buy"]
    assert e.state.qty > 0 and e.trips[-1]["side"] == "short"


def test_short_stop_fills_at_stop_or_gap_and_rearms_only_short_side():
    e = eng()
    bar(e, 0, 100, 100, 100, 100, -1.0, stop=5.0)
    bar(e, 1, 100, 100, 100, 100, -1.0, stop=5.0)
    stop = e.state.stop_price
    assert stop == pytest.approx(100 * (1 - 0.0003) + 5.0)
    bar(e, 2, 101, 107, 100, 104, -1.0, stop=5.0)   # high crosses stop
    assert e.trades[-1]["reason"] == "stop" and e.trades[-1]["ref_price"] == pytest.approx(stop)
    assert e.state.rearm_required and e.state.rearm_side == -1
    bar(e, 3, 104, 104, 104, 104, -1.0, stop=5.0)
    bar(e, 4, 104, 104, 104, 104, -1.0, stop=5.0)
    assert len(e.trades) == 2                        # no re-short while target stays negative
    bar(e, 5, 104, 104, 104, 104, 1.0)               # a long is allowed and clears re-arm
    bar(e, 6, 104, 104, 104, 104, 1.0)
    assert e.trades[-1]["side"] == "buy" and e.state.qty > 0
    # gap above the stop fills at the open
    e2 = eng()
    bar(e2, 0, 100, 100, 100, 100, -1.0, stop=5.0)
    bar(e2, 1, 100, 100, 100, 100, -1.0, stop=5.0)
    bar(e2, 2, 112, 113, 111, 112, -1.0, stop=5.0)
    assert e2.trades[-1]["ref_price"] == pytest.approx(112)


def test_long_only_params_never_short():
    e = eng(max_short=0.0)
    for i in range(5):
        bar(e, i, 100, 100, 100, 100, -1.0)
    assert e.trades == [] and e.state.qty == 0.0


def test_campaign_end_covers_short_and_state_round_trips():
    st = EngineState.fresh(10_000.0, start_ms=0, end_ms=4 * H)
    e = Engine(params(max_short=0.5, fee_rate=0.0005), st)
    bar(e, 0, 100, 100, 100, 100, -1.0)            # clipped to -0.5
    restored = Engine(e.params, EngineState.from_dict(e.state.to_dict()))
    for i in range(1, 4):
        bar(restored, i, 100, 100, 100, 99, -1.0)
    assert restored.state.qty * 99 / restored.state.last_equity == pytest.approx(-0.5, abs=0.01)
    restored.on_open(4 * H, 98)
    assert restored.state.status == "completed" and restored.state.qty == 0.0


def test_config_requires_perp_for_shorts():
    with pytest.raises(ConfigError, match="perp"):
        config_from_dict({"risk": {"max_short": 0.5}})
    cfg = config_from_dict({"run": {"instrument": "perp"}, "risk": {"max_short": 0.5}})
    assert cfg.risk.max_short == 0.5


def test_funding_is_booked_at_the_bar_containing_settlement():
    import numpy as np
    from tradebot.backtest import funding_per_bar
    open_ms = np.arange(0, 10 * H, H, dtype="int64")
    fund = pd.Series([0.001, 0.002], index=[8 * H, 3 * H + 1])   # 08:00 and just after 03:00
    f = funding_per_bar(open_ms, H, fund)
    assert f[7] == 0.001 and f[3] == 0.002 and f.sum() == pytest.approx(0.003)
