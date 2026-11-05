import math

import pytest

from tradebot.engine import Engine, EngineParams, EngineState

H = 3_600_000


def params(**kw) -> EngineParams:
    base = dict(interval_ms=H, fee_rate=0.001, slippage=0.0003, min_notional=10.0, qty_step=0.00001,
                max_exposure=1.0, min_rebalance=0.10, cb_dd=0.10, cb_ms=48 * H, campaign_stop_loss=0.15,
                stop_rearm=True, stop_cooldown_bars=0, end_action="flatten")
    base.update(kw)
    return EngineParams(**base)


def engine(cash=10_000.0, **kw) -> Engine:
    return Engine(params(**kw), EngineState.fresh(cash))


def bar(e: Engine, i: int, o, h, l, c, target, stop=math.nan):
    e.on_close(i * H, o, h, l, c, target, stop)


def test_decision_fills_at_next_open_with_costs():
    e = engine()
    bar(e, 0, 100, 101, 99, 100, 1.0)
    assert e.trades == [] and e.state.pending["target"] == 1.0
    bar(e, 1, 110, 111, 109, 110, 1.0)  # fill at 110 open, not the 100 close
    t = e.trades[0]
    fill = 110 * 1.0003
    assert t["side"] == "buy" and t["price"] == pytest.approx(fill)
    expected_qty = math.floor(10_000 / (fill * 1.001) / 0.00001) * 0.00001
    assert t["qty"] == pytest.approx(expected_qty)
    assert t["fee"] == pytest.approx(expected_qty * fill * 0.001)
    assert e.state.cash >= 0
    assert e.state.cash == pytest.approx(10_000 - expected_qty * fill * 1.001)


def test_round_trip_accounting_is_consistent():
    e = engine()
    bar(e, 0, 100, 101, 99, 100, 1.0)
    bar(e, 1, 100, 106, 99, 105, 1.0)
    bar(e, 2, 105, 108, 104, 107, 0.0)
    bar(e, 3, 108, 109, 107, 108, 0.0)  # sell at 108 open
    s = e.state
    assert s.qty == pytest.approx(0.0, abs=1e-5)
    # Realised P&L (net of all fees) equals the change in equity once flat.
    assert s.realized_pnl == pytest.approx(s.cash + s.qty * 108 - 10_000, abs=1e-6)
    assert s.fees_paid == pytest.approx(sum(t["fee"] for t in e.trades))
    assert len(e.trips) == 1 and e.trips[0]["pnl"] == pytest.approx(s.realized_pnl, abs=1e-6)


def test_rebalance_band_skips_small_changes():
    e = engine()
    bar(e, 0, 100, 100, 100, 100, 1.0)
    bar(e, 1, 100, 100, 100, 100, 0.95)  # exposure ~1.0 -> 0.95: below 0.10 band
    bar(e, 2, 100, 100, 100, 100, 0.95)
    assert len(e.trades) == 1
    bar(e, 3, 100, 100, 100, 100, 0.5)
    bar(e, 4, 100, 100, 100, 100, 0.5)
    assert len(e.trades) == 2 and e.trades[-1]["side"] == "sell"
    assert e.state.qty * 100 / e.equity_at(100) == pytest.approx(0.5, abs=0.01)


def test_target_is_clipped_to_max_exposure():
    e = engine(max_exposure=0.6)
    bar(e, 0, 100, 100, 100, 100, 1.0)
    bar(e, 1, 100, 100, 100, 100, 1.0)
    assert e.state.qty * 100 / e.equity_at(100) == pytest.approx(0.6, abs=0.001)


def test_protective_stop_fills_at_stop_or_gap_open():
    e = engine()
    bar(e, 0, 100, 100, 100, 100, 1.0, stop=5.0)
    bar(e, 1, 100, 101, 99, 100, 1.0, stop=5.0)  # entry ~100.03, stop ~95.03
    stop = e.state.stop_price
    assert stop == pytest.approx(100 * 1.0003 - 5.0)
    bar(e, 2, 99, 99.5, 94, 96, 1.0, stop=5.0)   # low 94 < stop -> filled at stop
    t = e.trades[-1]
    assert t["reason"] == "stop" and t["ref_price"] == pytest.approx(stop)
    assert t["price"] == pytest.approx(stop * (1 - 0.0003))
    # gap-down case: open below stop fills at the open
    e2 = engine()
    bar(e2, 0, 100, 100, 100, 100, 1.0, stop=5.0)
    bar(e2, 1, 100, 101, 99, 100, 1.0, stop=5.0)
    bar(e2, 2, 90, 92, 89, 91, 1.0, stop=5.0)
    assert e2.trades[-1]["ref_price"] == pytest.approx(90)


def test_trailing_stop_only_ratchets_up():
    e = engine()
    bar(e, 0, 100, 100, 100, 100, 1.0, stop=5.0)
    bar(e, 1, 100, 110, 100, 110, 1.0, stop=5.0)
    assert e.state.stop_price == pytest.approx(105)
    bar(e, 2, 110, 110, 106, 107, 1.0, stop=5.0)
    assert e.state.stop_price == pytest.approx(105)


def test_rearm_blocks_reentry_until_signal_resets():
    e = engine()
    bar(e, 0, 100, 100, 100, 100, 1.0, stop=5.0)
    bar(e, 1, 100, 100, 100, 100, 1.0, stop=5.0)
    bar(e, 2, 100, 100, 90, 92, 1.0, stop=5.0)   # stopped out
    assert e.state.rearm_required
    for i in range(3, 6):
        bar(e, i, 92, 93, 91, 92, 1.0, stop=5.0)
    assert len(e.trades) == 2  # no re-entry while target stays positive
    bar(e, 6, 92, 93, 91, 92, 0.0, stop=5.0)    # signal resets
    bar(e, 7, 92, 93, 91, 92, 1.0, stop=5.0)
    bar(e, 8, 92, 93, 91, 92, 1.0, stop=5.0)
    assert len(e.trades) == 3 and e.trades[-1]["side"] == "buy"


def test_circuit_breaker_flattens_and_pauses():
    e = engine(campaign_stop_loss=None)
    bar(e, 0, 100, 100, 100, 100, 1.0)
    bar(e, 1, 100, 100, 100, 100, 1.0)
    bar(e, 2, 100, 100, 88, 88, 1.0)  # -12% from HWM
    assert e.state.halt_until_ms == 3 * H + 48 * H
    assert e.state.pending["target"] == 0.0
    bar(e, 3, 88, 88, 88, 88, 1.0)
    assert e.state.qty < 1e-5
    for i in range(4, 50):
        bar(e, i, 88, 88, 88, 88, 1.0)
    assert len(e.trades) == 2  # stays flat during the halt
    bar(e, 50, 88, 88, 88, 88, 1.0)
    bar(e, 51, 88, 88, 88, 88, 1.0)
    assert e.trades[-1]["side"] == "buy"
    assert e.state.halt_until_ms is None


def test_campaign_stop_loss_stops_trading_permanently():
    e = engine(cb_dd=0.99)
    bar(e, 0, 100, 100, 100, 100, 1.0)
    bar(e, 1, 100, 100, 100, 100, 1.0)
    bar(e, 2, 100, 100, 80, 82, 1.0)
    assert e.state.stopped and e.state.status == "stopped"
    for i in range(3, 10):
        bar(e, i, 90, 95, 85, 95, 1.0)
    assert e.trades[-1]["side"] == "sell" and len(e.trades) == 2


def test_campaign_end_flattens_and_completes():
    st = EngineState.fresh(10_000.0, start_ms=0, end_ms=5 * H)
    e = Engine(params(), st)
    for i in range(5):
        bar(e, i, 100, 100, 100, 100, 1.0)
    assert st.ended and st.status == "ending" and st.pending["target"] == 0.0
    e.on_open(5 * H, 101)
    assert st.status == "completed" and st.qty < 1e-5
    bar(e, 5, 101, 101, 101, 101, 1.0)
    assert len(e.trades) == 2  # no trading after completion


def test_events_are_idempotent():
    e = engine()
    bar(e, 0, 100, 100, 100, 100, 1.0)
    bar(e, 1, 100, 100, 100, 100, 1.0)
    n = len(e.trades)
    bar(e, 1, 100, 100, 100, 100, 0.0)  # duplicate close ignored
    e.on_open(1 * H, 50)                # duplicate open ignored
    assert len(e.trades) == n and e.state.pending is None


def test_min_notional_and_cash_never_negative():
    e = engine(cash=5.0)
    bar(e, 0, 100, 100, 100, 100, 1.0)
    bar(e, 1, 100, 100, 100, 100, 1.0)
    assert e.trades == [] and e.state.cash == 5.0
    e2 = engine()
    for i in range(200):
        p = 100 + (i % 7) - 3
        bar(e2, i, p, p + 1, p - 1, p, (i // 3) % 2)
        assert e2.state.cash >= -1e-9 and e2.state.qty >= 0


def test_state_round_trip_resumes_identically():
    import random
    rnd = random.Random(3)
    bars = []
    p = 100.0
    for i in range(400):
        o = p
        c = o * (1 + rnd.gauss(0, 0.01))
        bars.append((i, o, max(o, c) * 1.003, min(o, c) * 0.997, c, 1.0 if (i // 25) % 2 == 0 else 0.0, 2.5))
        p = c
    a = engine()
    for b in bars:
        bar(a, *b)
    b_eng = engine()
    for k, b in enumerate(bars):
        if k % 7 == 0:  # serialise/deserialise mid-run
            b_eng = Engine(b_eng.params, EngineState.from_dict(b_eng.state.to_dict()))
        bar(b_eng, *b)
    assert b_eng.state.to_dict() == a.state.to_dict()
