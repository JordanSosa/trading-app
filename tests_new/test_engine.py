"""Engine accounting, execution rules and the risk governor."""

from __future__ import annotations

import json

import numpy as np
import pytest

from compounder.backtest import run_backtest
from compounder.engine import Engine, EngineParams, EngineState, drawdown_multiplier

from conftest import T0, free_params, make_panel, random_walk

HOUR = 3_600_000


def test_frictionless_long_tracks_price():
    px = random_walk(24 * 20, 1, seed=1)[:, 0]
    p = make_panel(px)
    res = run_backtest(p, np.ones((p.n_bars, 1)), free_params(), 1000.0)
    # Decided at the close of bar 0, filled at the open of bar 1 (= close of bar 0).
    assert res.state.last_equity == pytest.approx(1000.0 * px[-1] / px[0], rel=1e-12)


def test_constant_leverage_compounds_bar_by_bar():
    px = random_walk(24 * 20, 1, seed=2)[:, 0]
    p = make_panel(px)
    res = run_backtest(p, 2.0 * np.ones((p.n_bars, 1)), free_params(), 1000.0)
    r = px[1:] / px[:-1] - 1.0
    assert res.state.last_equity == pytest.approx(1000.0 * np.prod(1.0 + 2.0 * r), rel=1e-10)


def test_short_profits_when_price_falls_and_receives_positive_funding():
    px = np.linspace(100.0, 80.0, 49)
    funding = np.zeros((49, 1))
    funding[7::8] = 0.001
    p = make_panel(px, funding=funding)
    res = run_backtest(p, -np.ones((49, 1)), free_params(rebalance_band=10.0), 1000.0)
    assert res.state.last_equity > 1000.0
    assert res.state.funding_paid < 0.0            # a short receives positive funding


def test_long_pays_positive_funding():
    px = np.full(30, 100.0)
    funding = np.zeros((30, 1))
    funding[10] = 0.01
    p = make_panel(px, funding=funding)
    res = run_backtest(p, np.ones((30, 1)), free_params(), 1000.0)
    assert res.state.funding_paid == pytest.approx(10.0)
    assert res.state.last_equity == pytest.approx(990.0)


def test_fees_and_slippage_are_charged_on_every_fill():
    px = np.full(10, 100.0)
    p = make_panel(px)
    params = free_params(fee_rate=0.001, slippage=0.002, rebalance_band=0.5)
    w = np.ones((10, 1))
    w[5:] = 0.0
    res = run_backtest(p, w, params, 1000.0)
    assert res.state.trade_count == 2
    # Buy 1000 notional at 100.2, sell the same quantity at 99.8, fee on each fill.
    qty = 1000.0 / 100.0
    expected = 1000.0 - qty * 100.2 * 1.001 + qty * 99.8 * 0.999
    assert res.state.last_equity == pytest.approx(expected, rel=1e-12)
    assert res.state.fees_paid == pytest.approx(qty * 100.2 * 0.001 + qty * 99.8 * 0.001)


def test_orders_fill_at_next_open_not_at_the_signal_close():
    # The price jumps on bar 3; a signal computed at the close of bar 3 must not earn that jump.
    px = np.array([100.0, 100.0, 100.0, 110.0, 110.0, 110.0])
    p = make_panel(px)
    w = np.zeros((6, 1))
    w[3:] = 1.0
    res = run_backtest(p, w, free_params(), 1000.0)
    assert res.state.last_equity == pytest.approx(1000.0)
    assert res.trades[0]["price"] == pytest.approx(110.0)


def test_protective_stop_fills_at_trigger_with_overshoot_and_blocks_reentry():
    px = np.array([100.0, 100.0, 100.0, 80.0, 82.0, 85.0, 90.0])
    p = make_panel(px)
    params = free_params(stop_overshoot=0.5, stop_slippage=0.0)
    res = run_backtest(p, np.ones((7, 1)), params, 1000.0, stop_frac=0.10)
    stops = [t for t in res.trades if t["reason"] == "stop"]
    assert len(stops) == 1
    # Trigger 90, bar low 80: half of the 10-point overshoot is given up.
    assert stops[0]["price"] == pytest.approx(85.0)
    assert res.state.stop_count == 1
    # The signal never left the long side, so the stopped side stays blocked.
    assert res.state.qty[0] == 0.0 and res.state.blocked[0] == 1.0


def test_stop_gap_fills_at_open_when_the_bar_opens_beyond_the_trigger():
    close = np.array([[100.0], [100.0], [100.0], [70.0]])
    p = make_panel(close)
    p.open[3, 0] = 75.0                      # gaps through the 90 stop
    p.high[3, 0] = 75.0
    p.low[3, 0] = 70.0
    res = run_backtest(p, np.ones((4, 1)), free_params(), 1000.0, stop_frac=0.10)
    stop = [t for t in res.trades if t["reason"] == "stop"][0]
    assert stop["price"] == pytest.approx(75.0)


def test_blocked_side_is_released_when_signal_flips():
    px = np.array([100.0, 100.0, 100.0, 80.0, 80.0, 80.0, 80.0, 80.0])
    p = make_panel(px)
    w = np.ones((8, 1))
    w[4] = 0.0          # signal leaves the long side: block released
    res = run_backtest(p, w, free_params(), 1000.0, stop_frac=0.10)
    assert res.state.blocked[0] == 0.0
    assert res.state.qty[0] > 0.0


def test_short_stop():
    px = np.array([100.0, 100.0, 100.0, 125.0, 125.0])
    p = make_panel(px)
    res = run_backtest(p, -np.ones((5, 1)), free_params(), 1000.0, stop_frac=0.10)
    stop = [t for t in res.trades if t["reason"] == "stop"][0]
    assert stop["side"] == "buy" and stop["price"] == pytest.approx(110.0)
    assert res.state.blocked[0] == -1.0


def test_daily_target_flatten_goes_flat_until_next_day():
    # +3% in the first hours of the day, then a crash the same day that must be avoided.
    px = np.full(72, 100.0)
    px[26:30] = 103.0
    px[30:48] = 90.0
    px[48:] = 90.0
    p = make_panel(px)
    params = free_params(daily_target=0.02, target_mode="flatten")
    res = run_backtest(p, np.ones((72, 1)), params, 1000.0)
    assert res.state.days_target_hit == 1
    day1 = res.daily_returns[1]
    assert day1 == pytest.approx(0.03, abs=1e-9)
    assert "T" in res.day_flags[1]
    # Re-entered the next day.
    assert res.state.qty[0] > 0


def test_daily_target_trail_locks_after_giveback():
    px = np.full(60, 100.0)
    px[26] = 104.0     # +4% on the day
    px[27] = 102.5     # gives back more than half of the 2 points above target -> lock
    px[28:48] = 95.0
    p = make_panel(px)
    params = free_params(daily_target=0.02, target_mode="trail", trail_giveback=0.5)
    res = run_backtest(p, np.ones((60, 1)), params, 1000.0)
    assert res.state.days_target_hit == 1
    assert res.daily_returns[1] == pytest.approx(0.025, abs=1e-9)


def test_daily_loss_limit_stops_trading_for_the_day():
    px = np.full(72, 100.0)
    px[26:48] = np.linspace(98.0, 60.0, 22)
    px[48:] = 60.0
    p = make_panel(px)
    params = free_params(daily_loss_limit=0.03)
    res = run_backtest(p, np.ones((72, 1)), params, 1000.0)
    assert res.state.days_loss_limit == 1
    # First close below -3% is bar 27 (96.1905); the exit fills at the next open (= that close).
    assert res.daily_returns[1] == pytest.approx(px[27] / 100.0 - 1.0, abs=1e-9)
    assert res.daily_returns[1] > -0.05
    assert "L" in res.day_flags[1]


def test_drawdown_multiplier_shape():
    assert drawdown_multiplier(0.05, 0.10, 0.30, 0.25) == 1.0
    assert drawdown_multiplier(0.20, 0.10, 0.30, 0.25) == pytest.approx(0.625)
    assert drawdown_multiplier(0.35, 0.10, 0.30, 0.25) == 0.25


def test_drawdown_scaling_reduces_exposure():
    px = np.concatenate([np.full(5, 100.0), np.linspace(100.0, 75.0, 60), np.full(10, 75.0)])
    p = make_panel(px)
    params = free_params(dd_soft=0.05, dd_hard=0.25, min_risk_mult=0.2)
    res = run_backtest(p, np.ones((len(px), 1)), params, 1000.0)
    assert res.state.risk_mult < 0.6
    assert res.gross[-1] < 0.6
    assert res.state.last_equity > 750.0            # lost less than the unmanaged 25%


def test_kill_switch_flattens_and_stays_flat():
    px = np.concatenate([np.full(5, 100.0), np.linspace(100.0, 50.0, 40), np.linspace(50.0, 100.0, 40)])
    p = make_panel(px)
    params = free_params(kill_dd=0.30)
    res = run_backtest(p, np.ones((len(px), 1)), params, 1000.0)
    assert res.state.killed and not res.state.liquidated
    assert res.state.qty[0] == 0.0
    assert 0.60 < res.state.last_equity / 1000.0 < 0.72     # stopped near the 30% line, missed the rebound
    assert any(e["kind"] == "alert" and "KILL" in e["message"] for e in res.events)


def test_liquidation_wipes_an_overleveraged_account():
    px = np.array([100.0, 100.0, 100.0, 85.0, 85.0, 85.0])
    p = make_panel(px)
    params = free_params(max_gross=10.0, max_weight=10.0)
    res = run_backtest(p, 8.0 * np.ones((6, 1)), params, 1000.0)
    assert res.state.liquidated and res.state.killed
    assert res.state.last_equity == pytest.approx(0.0)
    assert res.metrics["liquidated"]


def test_gross_and_weight_caps():
    px = random_walk(50, 4, seed=5)
    p = make_panel(px)
    params = free_params(max_gross=1.5, max_weight=0.5)
    w = np.tile(np.array([2.0, 2.0, -2.0, -2.0]), (50, 1))
    res = run_backtest(p, w, params, 1000.0)
    assert res.gross.max() < 1.6
    assert res.metrics["max_gross"] < 1.6


def test_spot_mode_never_shorts_or_borrows():
    px = random_walk(200, 3, seed=6)
    p = make_panel(px)
    rng = np.random.default_rng(0)
    w = rng.normal(0.3, 0.5, (200, 3))
    params = free_params(allow_short=False, max_gross=1.0, max_weight=1.0, fee_rate=0.001)
    eng_state = EngineState.fresh(p.symbols, 1000.0)
    eng = Engine(params, eng_state)
    for i in range(200):
        eng.on_close(int(p.times[i]), p.open[i], p.high[i], p.low[i], p.close[i], w[i])
        assert (eng_state.qty >= -1e-12).all()
        assert eng_state.cash >= -1e-6


def test_rebalance_band_suppresses_small_trades():
    px = random_walk(24 * 10, 1, seed=7)[:, 0]
    p = make_panel(px)
    w = np.full((p.n_bars, 1), 0.5)
    tight = run_backtest(p, w, free_params(rebalance_band=0.0), 1000.0)
    banded = run_backtest(p, w, free_params(rebalance_band=0.25), 1000.0)
    assert banded.state.trade_count < tight.state.trade_count / 5


def test_small_account_concentrates_instead_of_holding_nothing():
    px = random_walk(60, 8, seed=8)
    p = make_panel(px)
    w = np.tile(np.array([0.15, -0.12, 0.10, -0.08, 0.06, 0.05, -0.03, 0.01]), (60, 1))
    params = free_params(min_notional=5.0, min_position_mult=2.0, max_weight=0.6, max_gross=3.0, rebalance_band=0.25)
    res = run_backtest(p, w, params, 100.0)
    held = np.abs(res.state.qty * p.close[-1])
    assert 2 <= (held > 0).sum() <= 5
    assert held[held > 0].min() >= 7.0                 # every position clears the venue minimum
    assert res.gross[-1] == pytest.approx(0.6, abs=0.15)
    big = run_backtest(p, w, params, 100_000.0)
    assert (big.state.qty != 0).sum() == 8


def test_missing_bars_keep_position_marked_at_last_price():
    px = random_walk(40, 2, seed=9)
    p = make_panel(px)
    for arr in (p.open, p.high, p.low, p.close):
        arr[20:30, 1] = np.nan
    res = run_backtest(p, np.full((40, 2), 0.5), free_params(), 1000.0)
    assert np.isfinite(res.equity).all()
    assert res.state.qty[1] != 0.0


def test_state_round_trips_through_json_and_resumes_identically(walk_panel):
    p = walk_panel
    rng = np.random.default_rng(4)
    w = rng.normal(0, 0.2, (p.n_bars, p.n_symbols))
    params = EngineParams(min_notional=5.0)
    full = Engine(params, EngineState.fresh(p.symbols, 5000.0))
    for i in range(p.n_bars):
        full.on_close(int(p.times[i]), p.open[i], p.high[i], p.low[i], p.close[i], w[i], np.full(p.n_symbols, 0.08),
                      p.funding[i])
    state = EngineState.fresh(p.symbols, 5000.0)
    for i in range(p.n_bars):
        eng = Engine(params, EngineState.from_dict(json.loads(json.dumps(state.to_dict()))))
        eng.on_close(int(p.times[i]), p.open[i], p.high[i], p.low[i], p.close[i], w[i], np.full(p.n_symbols, 0.08),
                     p.funding[i])
        state = eng.state
    assert state.last_equity == pytest.approx(full.state.last_equity, rel=1e-12)
    assert np.allclose(state.qty, full.state.qty, rtol=1e-12, atol=1e-12)
    assert state.trade_count == full.state.trade_count


def test_state_realigns_to_a_new_symbol_list():
    st = EngineState.fresh(("AUSDT", "BUSDT"), 100.0)
    st.qty[0] = 2.0
    st.last_price[:] = [10.0, 20.0]
    moved = EngineState.from_dict(st.to_dict(), symbols=("AUSDT", "CUSDT"))
    assert moved.symbols == ("AUSDT", "CUSDT")
    assert moved.qty.tolist() == [2.0, 0.0]
    assert np.isnan(moved.last_price[1])
    with pytest.raises(ValueError):
        EngineState.from_dict(st.to_dict(), symbols=("BUSDT",))


def test_on_close_is_idempotent_per_bar(walk_panel):
    p = walk_panel
    eng = Engine(EngineParams(), EngineState.fresh(p.symbols, 1000.0))
    w = np.full(p.n_symbols, 0.1)
    for i in range(30):
        eng.on_close(int(p.times[i]), p.open[i], p.high[i], p.low[i], p.close[i], w)
    before = (eng.state.last_equity, eng.state.trade_count, eng.state.bars_processed)
    eng.on_close(int(p.times[29]), p.open[29], p.high[29], p.low[29], p.close[29], w)
    eng.on_open(int(p.times[29]), p.open[29])
    assert (eng.state.last_equity, eng.state.trade_count, eng.state.bars_processed) == before


def test_day_ledger_compounds_to_final_equity(walk_panel):
    p = walk_panel
    rng = np.random.default_rng(11)
    w = rng.normal(0, 0.3, (p.n_bars, p.n_symbols))
    res = run_backtest(p, w, EngineParams(kill_dd=None), 2000.0)
    # The first trading day starts at the initial equity; days chain without gaps.
    assert 2000.0 * np.prod(1.0 + res.daily_returns) == pytest.approx(res.equity[np.searchsorted(
        res.equity_ms, res.day_end_ms[-1])], rel=1e-9)
    assert (res.day_end_ms % 86_400_000 == 0).all()
