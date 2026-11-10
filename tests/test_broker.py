"""Live broker against a simulated exchange: mirroring, order sizing, stops and safety interlocks."""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from compounder.broker import BrokerError, LiveBroker, ccxt_symbol
from compounder.config import Config, DataConfig, LiveConfig, RiskConfig, RunConfig, UniverseConfig
from compounder.engine import Engine, EngineState
from compounder.forward import MarketData, tick
from compounder.state import StateStore
from compounder.strategy import StrategyParams

from fakes import FakeExchange, FakeSource
from test_forward import WARM_DAYS, world

SYMS = ("AUSDT", "BUSDT", "CUSDT")
PX = {"AUSDT": 100.0, "BUSDT": 50.0, "CUSDT": 10.0}


def live_cfg(tmp_path=None, **live) -> Config:
    strat = StrategyParams(universe_n=4, trend_horizons=(12, 24, 48), vol_window=36, xsmom_lookback=48,
                           smooth_bars=4, target_vol=0.03, stop_vol_mult=2.0)
    return Config(run=RunConfig(mode="live", initial_equity=1000.0, state_dir=str(tmp_path or "state")),
                  universe=UniverseConfig(size=4), strategy=strat, risk=RiskConfig(),
                  data=DataConfig(sources=("okx",), catchup_bars=60),
                  live=LiveConfig(**{"dry_run": False, "sandbox": True, "max_order_notional": 5000.0, **live}))


class Snap:
    def __init__(self, prices):
        self.last = np.array(prices, dtype="float64")


def attached(ex: FakeExchange, cfg: Config | None = None, state: EngineState | None = None):
    cfg = cfg or live_cfg()
    state = state or EngineState.fresh(SYMS, cfg.run.initial_equity)
    eng = Engine(cfg.engine_params(), state)
    broker = LiveBroker(cfg, ex)
    events: list[dict] = []
    broker.attach(eng, Snap([PX[s] for s in SYMS]), events, 1_000)
    return broker, eng, events


def set_pending(eng: Engine, weights, stop=0.1) -> None:
    eng.state.pending = np.array(weights, dtype="float64")
    eng.state.pending_stop = np.full(len(weights), stop)


def notional(ex: FakeExchange, sym: str) -> float:
    p = ex.pos.get(ccxt_symbol(sym))
    return 0.0 if not p else p["contracts"] * ex.contract_size * PX[sym]


def test_sync_mirrors_the_exchange_account():
    ex = FakeExchange(PX, balance=2500.0)
    ex.create_order(ccxt_symbol("AUSDT"), "market", "buy", 300.0)        # 300 contracts * 0.01 * 100 = 300 USDT
    broker, eng, _ = attached(ex)
    s = eng.state
    assert s.initial_equity == pytest.approx(2500.0)                      # the account's own equity, not the config's
    assert s.qty[0] == pytest.approx(3.0) and s.entry_price[0] == 100.0
    assert eng.equity_at(np.array([100.0, 50.0, 10.0])) == pytest.approx(2500.0)
    ex.prices["AUSDT"] = 110.0
    broker.prices = np.array([110.0, 50.0, 10.0])
    broker.sync()
    assert eng.equity_at(broker.prices) == pytest.approx(2530.0)


def test_execute_reaches_the_target_weights_and_places_stops():
    ex = FakeExchange(PX, balance=1000.0)
    broker, eng, events = attached(ex)
    set_pending(eng, [0.30, -0.20, 0.0], stop=0.10)
    broker.execute(5_000)
    assert notional(ex, "AUSDT") == pytest.approx(300.0, abs=1.0)
    assert notional(ex, "BUSDT") == pytest.approx(-200.0, abs=1.0)
    assert ccxt_symbol("CUSDT") not in ex.pos
    assert eng.state.pending is None and eng.state.last_open_ms == 5_000
    stops = {o["symbol"]: o for o in ex.stops}
    assert stops[ccxt_symbol("AUSDT")]["side"] == "sell" and stops[ccxt_symbol("AUSDT")]["triggerPrice"] == pytest.approx(90.0)
    assert stops[ccxt_symbol("BUSDT")]["side"] == "buy" and stops[ccxt_symbol("BUSDT")]["triggerPrice"] == pytest.approx(55.0)
    assert all(o["reduceOnly"] for o in ex.stops)
    assert eng.state.stop_price[0] == pytest.approx(90.0)
    assert any("sent 2 order" in e["message"] for e in events)
    # A second identical instruction changes nothing and keeps exactly one stop per position.
    set_pending(eng, [0.30, -0.20, 0.0])
    n_orders = len(ex.orders)
    broker.execute(6_000)
    assert len(ex.orders) == n_orders and len(ex.stops) == 2


def test_dry_run_sends_nothing():
    ex = FakeExchange(PX, balance=1000.0)
    broker, eng, events = attached(ex, live_cfg(dry_run=True))
    set_pending(eng, [0.30, -0.20, 0.0])
    broker.execute(5_000)
    assert not ex.orders and not ex.stops and not ex.pos
    assert sum("DRY RUN" in e["message"] for e in events) == 2


def test_reversal_closes_reduce_only_then_opens_the_other_side():
    ex = FakeExchange(PX, balance=1000.0)
    broker, eng, _ = attached(ex)
    set_pending(eng, [0.30, 0.0, 0.0])
    broker.execute(5_000)
    set_pending(eng, [-0.20, 0.0, 0.0])
    broker.execute(6_000)
    legs = [o for o in ex.orders if o["symbol"] == ccxt_symbol("AUSDT")][1:]
    assert [(o["side"], bool(o["params"].get("reduceOnly"))) for o in legs] == [("sell", True), ("sell", False)]
    assert notional(ex, "AUSDT") == pytest.approx(-200.0, abs=2.0)
    assert [o["side"] for o in ex.stops if o["symbol"] == ccxt_symbol("AUSDT")] == ["buy"]


def test_full_exit_leaves_no_dust_and_cancels_the_stop():
    ex = FakeExchange(PX, balance=1000.0)
    broker, eng, _ = attached(ex)
    set_pending(eng, [0.3337, 0.0, 0.0])
    broker.execute(5_000)
    assert ex.stops
    set_pending(eng, [0.0, 0.0, 0.0])
    broker.execute(6_000)
    assert not ex.pos and not ex.stops
    assert eng.state.qty[0] == 0.0 and np.isnan(eng.state.stop_price[0])
    # Closing a position on purpose is not a stop-out: nothing is blocked, nothing is counted.
    assert eng.state.blocked[0] == 0.0 and eng.state.stop_count == 0


def test_only_okx_is_supported_for_live_trading():
    from compounder.broker import make_live_broker
    from compounder.config import ConfigError
    with pytest.raises(ConfigError, match="not supported"):
        make_live_broker(live_cfg(exchange="binanceusdm"), env={})


def test_stop_filled_on_the_exchange_is_detected_and_blocks_reentry():
    ex = FakeExchange(PX, balance=1000.0)
    broker, eng, events = attached(ex)
    set_pending(eng, [0.30, 0.0, 0.0])
    broker.execute(5_000)
    ex.prices["AUSDT"] = 89.0
    ex.trigger_stop("AUSDT")
    broker.prices = np.array([89.0, 50.0, 10.0])
    broker.sync()
    assert eng.state.qty[0] == 0.0 and eng.state.blocked[0] == 1.0
    assert any(e["kind"] == "stop" for e in events)
    assert eng.equity_at(broker.prices) == pytest.approx(1000.0 - 3.0 * 11.0)


def test_oversized_fill_is_closed_and_trips_the_kill_switch():
    ex = FakeExchange(PX, balance=1000.0)
    ex.size_bug = 6.0                                   # the venue fills six times the requested size
    broker, eng, events = attached(ex)
    set_pending(eng, [0.30, 0.0, 0.0])
    broker.execute(5_000)
    assert eng.state.killed
    assert any("far larger than the intended" in e["message"] for e in events)
    assert len([o for o in ex.orders if o["params"].get("reduceOnly")]) >= 1


def test_order_above_the_notional_limit_is_refused():
    ex = FakeExchange(PX, balance=1000.0)
    broker, eng, events = attached(ex, live_cfg(max_order_notional=100.0))
    set_pending(eng, [0.30, 0.05, 0.0])
    broker.execute(5_000)
    assert ccxt_symbol("AUSDT") not in ex.pos                             # 300 USDT > 100 limit
    assert notional(ex, "BUSDT") == pytest.approx(50.0, abs=1.0)
    assert any("max_order_notional" in e["message"] for e in events)


def test_failed_order_is_reported_and_does_not_stop_the_others():
    ex = FakeExchange(PX, balance=1000.0)
    broker, eng, events = attached(ex)
    set_pending(eng, [0.30, 0.20, 0.0])
    ex.fail_orders = True
    broker.execute(5_000)
    assert sum("order failed" in e["message"] for e in events) == 2
    assert not ex.pos


def test_refuses_accounts_it_should_not_trade():
    ex = FakeExchange(PX, balance=1_000_000.0)
    with pytest.raises(BrokerError, match="max_account_equity"):
        attached(ex)
    ex = FakeExchange({**PX, "ZUSDT": 5.0}, balance=1000.0)
    ex.create_order(ccxt_symbol("ZUSDT"), "market", "buy", 100.0)
    with pytest.raises(BrokerError, match="not tracking"):
        attached(ex)

    class Hedge(FakeExchange):
        def fetch_positions(self, symbols=None):
            return [{"symbol": ccxt_symbol("AUSDT"), "contracts": 5.0, "side": None, "contractSize": 0.01}]

    with pytest.raises(BrokerError, match="hedge"):
        attached(Hedge(PX))


def test_live_ticks_drive_the_exchange_account(tmp_path):
    """End to end: the forward tick in live mode trades the simulated exchange and mirrors its equity."""
    w = world(seed=51)
    cfg = live_cfg(tmp_path / "state")
    src = FakeSource(w, "okx")
    store = StateStore(cfg.run.state_dir)
    md = MarketData(cfg, sources=[src])
    start = WARM_DAYS * 24 + 3
    ex = FakeExchange({s: float(w.close[start, j]) for j, s in enumerate(w.symbols)}, balance=3000.0,
                      contract_size=0.001, lot=0.01)
    results = []
    for i in range(start, start + 60):
        src.now_ms = int(w.times[i]) + 60_000
        for j, s in enumerate(w.symbols):
            ex.prices[s] = float(w.open[i, j])
        results.append(tick(cfg, store, md, now_ms=src.now_ms, broker=LiveBroker(cfg, ex)))
    assert all(r.ok for r in results)
    doc = store.load()
    eng = doc["engine"]
    assert eng["initial_equity"] == pytest.approx(3000.0)
    assert len(ex.orders) > 5 and ex.pos
    # The state mirrors the exchange: same positions, same equity.
    for sym, p in ex.pos.items():
        internal = sym.split("/")[0] + "USDT"
        assert eng["qty"][internal] == pytest.approx(p["contracts"] * ex.contract_size, rel=1e-9)
    assert doc["live_equity"] == pytest.approx(ex.fetch_balance()["USDT"]["total"], rel=1e-6)
    # Every open position is protected by exactly one resting stop.
    assert sorted(o["symbol"] for o in ex.stops) == sorted(ex.pos)
    assert eng["fees_paid"] == 0.0 and eng["funding_paid"] == 0.0        # the exchange keeps those books
