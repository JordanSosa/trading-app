import json

import pandas as pd
import pytest

import helpers_strategies  # noqa: F401  (registers test strategies)
from conftest import make_cfg
from tradebot.backtest import run_campaigns
from tradebot.engine import EngineState
from tradebot.forward import MAX_SOFT_FAILURES, tick
from tradebot.simulate import HistoricalMarketData, replay
from tradebot.state import StateError, StateStore
from tradebot.strategies import get_strategy

H = 3_600_000


def ms(ts):
    return int(pd.Timestamp(ts).value // 1_000_000)


@pytest.fixture
def scfg():
    return make_cfg("_test_sma_stop", data={"lookback_bars": 1500, "sanity_check": False})


def test_replay_matches_campaign_backtest_exactly(candles, scfg, tmp_path):
    strat = get_strategy("_test_sma_stop")
    start = candles.index[2000]
    camp = run_campaigns(candles, strat, scfg, str(start), None, days=30, step_hours=10**6)
    res = replay(scfg, candles, start, start + pd.Timedelta(days=32), tmp_path, tick_minutes=20)
    assert res[-1].status == "completed"
    st = EngineState.from_dict(StateStore(tmp_path).load()["engine"])
    assert st.cash / 10_000 - 1 == pytest.approx(camp.iloc[0]["return"], abs=1e-12)
    assert st.trade_count == camp.iloc[0]["n_trades"] and st.trade_count > 0
    trades = pd.read_csv(tmp_path / "trades.csv")
    assert len(trades) == st.trade_count  # ledger complete, no duplicates


def test_missed_ticks_catch_up_to_identical_state(candles, scfg, tmp_path):
    start = candles.index[2500]
    end = start + pd.Timedelta(days=6)
    replay(scfg, candles, start, end, tmp_path / "a", tick_minutes=15)
    # same period, but ticking only every 9 hours (missed ticks)
    replay(scfg, candles, start, end, tmp_path / "b", tick_minutes=9 * 60)
    a = StateStore(tmp_path / "a").load()["engine"]
    b = StateStore(tmp_path / "b").load()["engine"]
    # b may lag a by < 9h; advance b to exactly a's horizon
    md = HistoricalMarketData(candles, H)
    md.now_ms = ms(end)
    tick(scfg, StateStore(tmp_path / "b"), md, now_ms=ms(end), write_reports=False)
    b = StateStore(tmp_path / "b").load()["engine"]
    for k in ("cash", "qty", "fees_paid", "trade_count", "last_close_ms", "max_drawdown"):
        assert a[k] == pytest.approx(b[k]), k


def test_first_tick_never_trades_on_pre_start_bar(candles, scfg, tmp_path):
    md = HistoricalMarketData(candles, H)
    now = int(candles.index[3000].value // 1_000_000) + 30 * 60_000  # mid-bar
    md.now_ms = now
    store = StateStore(tmp_path)
    r = tick(scfg, store, md, now_ms=now, write_reports=False)
    assert r.ok and r.trades == []
    doc = store.load()
    assert doc["engine"]["last_open_ms"] == int(candles.index[3000].value // 1_000_000)
    assert doc["engine"]["pending"] is None
    r2 = tick(scfg, store, md, now_ms=now + 60_000, write_reports=False)  # same bar: no-op
    assert r2.bars_processed == 0 and r2.trades == []


def test_config_change_is_refused_unless_accepted(candles, scfg, tmp_path):
    md = HistoricalMarketData(candles, H)
    md.now_ms = ms(candles.index[3000])
    store = StateStore(tmp_path)
    tick(scfg, store, md, now_ms=md.now_ms, write_reports=False)
    other = make_cfg("_test_sma_stop", params={"fast": 12}, data={"sanity_check": False})
    with pytest.raises(StateError, match="configuration changed"):
        tick(other, store, md, now_ms=md.now_ms + H, write_reports=False)
    r = tick(other, store, md, now_ms=md.now_ms + H, accept_config_change=True, write_reports=False)
    assert r.ok


def test_data_outage_soft_then_hard_failure(candles, scfg, tmp_path):
    md = HistoricalMarketData(candles, H)
    store = StateStore(tmp_path)
    md.now_ms = ms(candles.index[3000])
    tick(scfg, store, md, now_ms=md.now_ms, write_reports=False)
    codes = []
    for i in range(MAX_SOFT_FAILURES):
        md.fail_next = 1
        codes.append(tick(scfg, store, md, now_ms=md.now_ms + (i + 1) * 60_000, write_reports=False).exit_code)
    assert codes == [0] * (MAX_SOFT_FAILURES - 1) + [2]
    assert store.load()["health"]["consecutive_failures"] == MAX_SOFT_FAILURES
    md.now_ms += 3 * H
    r = tick(scfg, store, md, now_ms=md.now_ms, write_reports=False)
    assert r.ok and r.bars_processed == 3
    assert store.load()["health"]["consecutive_failures"] == 0


def test_sanity_check_failure_blocks_processing(candles, tmp_path):
    cfg = make_cfg("_test_sma_stop", data={"sanity_check": True})
    md = HistoricalMarketData(candles, H)
    md.cross_check = lambda *a, **k: (False, "venue disagreement 5%")
    store = StateStore(tmp_path)
    md.now_ms = ms(candles.index[3000])
    tick(cfg, store, md, now_ms=md.now_ms, write_reports=False)
    before = store.load()["engine"]["last_close_ms"]
    md.now_ms += 2 * H
    r = tick(cfg, store, md, now_ms=md.now_ms, write_reports=False)
    assert not r.ok and "sanity" in r.message
    assert store.load()["engine"]["last_close_ms"] == before


def test_concurrent_tick_is_refused(tmp_path):
    store = StateStore(tmp_path)
    with store.lock():
        with pytest.raises(StateError, match="locked"):
            with StateStore(tmp_path).lock():
                pass


def test_reports_are_generated(candles, scfg, tmp_path):
    start = candles.index[2200]
    replay(scfg, candles, start, start + pd.Timedelta(days=3), tmp_path, tick_minutes=60, write_reports=True)
    for f in ("STATUS.md", "equity.svg", "dashboard.html", "summary.json", "state.json", "equity.csv"):
        assert (tmp_path / f).exists(), f
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["status"] == "running" and s["days_total"] == 30
    assert "<svg" in (tmp_path / "equity.svg").read_text()
    assert "Paper money only" in (tmp_path / "STATUS.md").read_text()
    json.loads((tmp_path / "state.json").read_text())  # strict JSON (no NaN)


def test_mid_bar_start_matches_campaign_at_that_bar(candles, scfg, tmp_path):
    strat = get_strategy("_test_sma_stop")
    bar0 = candles.index[2100]
    camp = run_campaigns(candles, strat, scfg, str(bar0), None, days=30, step_hours=10**6)
    replay(scfg, candles, bar0 + pd.Timedelta(minutes=37), bar0 + pd.Timedelta(days=32), tmp_path,
           tick_minutes=30)
    st = EngineState.from_dict(StateStore(tmp_path).load()["engine"])
    assert st.status == "completed"
    assert st.cash / 10_000 - 1 == pytest.approx(camp.iloc[0]["return"], abs=1e-12)


def test_completed_run_is_frozen(candles, scfg, tmp_path):
    cfg = make_cfg("_test_sma_stop", run={"duration_days": 2.0}, data={"sanity_check": False})
    start = candles.index[2300]
    replay(cfg, candles, start, start + pd.Timedelta(days=3), tmp_path, tick_minutes=60)
    store = StateStore(tmp_path)
    doc = store.load()
    assert doc["run"]["completed_at"] and doc["run"]["final_price"]

    class Boom:
        def fetch(self, *a, **k):
            raise AssertionError("completed runs must not fetch data")

    r = tick(cfg, store, Boom(), now_ms=ms(start + pd.Timedelta(days=10)))
    assert r.status == "completed"
    assert store.load() == doc


def test_run_window_changes_are_refused_even_with_accept(candles, scfg, tmp_path):
    md = HistoricalMarketData(candles, H)
    md.now_ms = ms(candles.index[3000])
    store = StateStore(tmp_path)
    tick(scfg, store, md, now_ms=md.now_ms, write_reports=False)
    other = make_cfg("_test_sma_stop", run={"initial_cash": 50_000.0}, data={"sanity_check": False})
    with pytest.raises(StateError, match="initial_cash"):
        tick(other, store, md, now_ms=md.now_ms + H, accept_config_change=True, write_reports=False)


@pytest.mark.parametrize("instrument", ["perp", "hybrid"])
def test_replay_matches_campaign_with_shorts_and_funding(candles, tmp_path, instrument):
    import numpy as np
    cfg = make_cfg("_test_sma_ls", run={"instrument": instrument}, risk={"max_short": 1.0},
                   data={"sanity_check": False})
    t0 = int(candles.index[0].value // 1_000_000)
    times = np.arange(t0, t0 + len(candles) * H, 8 * H)
    rng = np.random.default_rng(5)
    funding = pd.Series(rng.normal(0.0001, 0.0002, len(times)), index=times)
    strat = get_strategy("_test_sma_ls")
    start = candles.index[2400]
    camp = run_campaigns(candles, strat, cfg, str(start), None, days=30, step_hours=10**6, funding=funding)
    replay(cfg, candles, start, start + pd.Timedelta(days=32), tmp_path, tick_minutes=45, funding=funding)
    st = EngineState.from_dict(StateStore(tmp_path).load()["engine"])
    assert st.status == "completed"
    assert st.trade_count == camp.iloc[0]["n_trades"] and st.trade_count > 4
    assert st.funding_paid == pytest.approx(camp.iloc[0]["funding"], rel=1e-9, abs=1e-9)
    assert st.funding_paid != 0.0
    assert st.cash / 10_000 - 1 == pytest.approx(camp.iloc[0]["return"], abs=1e-12)
