"""Forward ticks: parity with the backtest, idempotency, catch-up and failure handling."""

from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest

from compounder.backtest import run_backtest
from compounder.config import Config, RiskConfig, RunConfig, UniverseConfig, DataConfig
from compounder.data.panel import DAY_MS, HOUR_MS, Panel, liquidity_rank, refresh_instant
from compounder.forward import MarketData, rank_rows, rank_universe, tick, universe_due
from compounder.notify import Notifier
from compounder.state import StateError, StateStore
from compounder.strategy import StrategyParams, compute_targets

from conftest import make_panel, random_walk
from fakes import FakeSource

N_SYM = 6
WARM_DAYS = 45


def world(seed: int = 31, days: int = 55, swap_day: int | None = 40) -> Panel:
    t0 = refresh_instant(1_704_067_200_000, 7)               # a Monday 00:00 UTC
    t = days * 24
    close = random_walk(t, N_SYM, vol=0.012, seed=seed, start=50.0)
    rng = np.random.default_rng(seed + 1)
    funding = np.zeros((t, N_SYM))
    funding[7::8] = rng.normal(0.0001, 0.0002, (len(range(7, t, 8)), N_SYM))
    p = make_panel(close, [f"C{i}USDT" for i in range(N_SYM)], funding=funding, wick=0.003, t0=t0)
    vol = np.tile(np.array([6e6, 5e6, 4e6, 3e6, 2e6, 1e6]), (t, 1))
    if swap_day is not None:
        vol[swap_day * 24:, 5] = 9e7                         # the least liquid symbol becomes the most liquid
    rank = liquidity_rank(p.times, vol, p.close, HOUR_MS)
    return Panel(**{**p.__dict__, "quote_volume": vol, "rank": rank})


def config(tmp_path, **risk) -> Config:
    # The production signal path in miniature: trend + cross-sectional momentum, smoothed.
    strat = StrategyParams(universe_n=4, trend_horizons=(12, 24, 48), vol_window=36, xsmom_weight=1.0,
                           xsmom_lookback=48, smooth_bars=4, short_scale=0.5, target_vol=0.03, stop_vol_mult=2.0)
    return Config(run=RunConfig(initial_equity=5000.0, state_dir=str(tmp_path / "state")),
                  universe=UniverseConfig(size=4), strategy=strat,
                  risk=RiskConfig(**{"daily_target": 0.02, "daily_loss_limit": 0.03, **risk}),
                  data=DataConfig(sources=("okx", "gate"), catchup_bars=60))


def run_forward(cfg, w: Panel, start_bar: int, end_bar: int, minutes: list[int], sources=None, notifier=None):
    """Tick at the given minute offsets inside every hour from ``start_bar`` to ``end_bar``."""
    src = FakeSource(w)
    store = StateStore(cfg.run.state_dir)
    md = MarketData(cfg, sources=sources or [src], cache_dir=store.root / "cache")
    results = []
    for i in range(start_bar, end_bar):
        for m in minutes:
            now = int(w.times[i]) + m * 60_000
            for s in md.sources:
                s.now_ms = now
            results.append(tick(cfg, store, md, now_ms=now, notifier=notifier))
    return store, results, md


def test_forward_reproduces_the_backtest(tmp_path):
    w = world()
    cfg = config(tmp_path)
    start = WARM_DAYS * 24 + 3
    end = w.n_bars - 1
    store, results, _ = run_forward(cfg, w, start, end, minutes=[1, 31])
    assert all(r.ok for r in results)

    tg = compute_targets(w, cfg.strategy)
    # The forward run's last tick is inside bar end-1, so bars start .. end-2 have closed and the
    # order decided at the close of end-2 has been filled at the open of end-1.
    bt = run_backtest(w, tg.weights, cfg.engine_params(), 5000.0, int(w.times[start]), int(w.times[end - 1]),
                      stop_frac=tg.stop_frac)
    doc = store.load()
    eng = doc["engine"]
    assert eng["bars_processed"] == bt.state.bars_processed
    assert bt.state.trade_count > 20
    assert eng["last_equity"] == pytest.approx(bt.state.last_equity, rel=1e-9)
    assert eng["funding_paid"] == pytest.approx(bt.state.funding_paid, rel=1e-9, abs=1e-9)
    # The forward run has also executed the final pending order at the forming bar's open,
    # which the backtest (ending one bar earlier) has not.
    trades = store.read_csv("trades.csv")
    settled = [t for t in trades if int(t["ts_ms"]) < int(w.times[end - 1])]
    assert sum(float(t["fee"]) for t in settled) == pytest.approx(bt.state.fees_paid, rel=1e-9)
    bt_keys = [(t["ts_ms"], t["symbol"], t["side"]) for t in bt.trades]
    fw_keys = [(int(t["ts_ms"]), t["symbol"], t["side"]) for t in trades]
    assert fw_keys[: len(bt_keys)] == bt_keys
    for a, b in zip(trades, bt.trades):
        assert float(a["notional"]) == pytest.approx(b["notional"], rel=1e-7)


def test_sparse_ticks_catch_up_to_the_same_result(tmp_path):
    w = world(seed=33)
    start = WARM_DAYS * 24 + 3
    end = w.n_bars - 1
    cfg_a = config(tmp_path / "a")
    cfg_b = config(tmp_path / "b")
    store_a, _, _ = run_forward(cfg_a, w, start, end, minutes=[2])
    # Same period, but the scheduler only fires every 7 hours (and always on the last bar).
    src = FakeSource(w)
    store_b = StateStore(cfg_b.run.state_dir)
    md = MarketData(cfg_b, sources=[src], cache_dir=store_b.root / "cache")
    for i in sorted(set(range(start, end, 7)) | {end - 1}):
        src.now_ms = int(w.times[i]) + 5 * 60_000
        assert tick(cfg_b, store_b, md, now_ms=src.now_ms).ok
    a, b = store_a.load()["engine"], store_b.load()["engine"]
    assert b["bars_processed"] == a["bars_processed"]
    assert b["last_equity"] == pytest.approx(a["last_equity"], rel=1e-9)
    assert b["trade_count"] == a["trade_count"]


def test_second_tick_in_the_same_bar_is_a_no_op(tmp_path):
    w = world(seed=35)
    cfg = config(tmp_path)
    start = WARM_DAYS * 24 + 3
    store, results, md = run_forward(cfg, w, start, start + 30, minutes=[1])
    before = json.dumps(store.load()["engine"], sort_keys=True)
    n_trades = len(store.read_csv("trades.csv"))
    again = tick(cfg, store, md, now_ms=md.sources[0].now_ms + 60_000)
    assert again.ok and again.bars_processed == 0 and not again.trades
    assert json.dumps(store.load()["engine"], sort_keys=True) == before
    assert len(store.read_csv("trades.csv")) == n_trades


def test_universe_ranking_matches_the_backtest_definition(tmp_path):
    w = world()
    cfg = config(tmp_path)
    src = FakeSource(w)
    for day in (35, 42, 49):
        r = refresh_instant(int(w.times[0]) + day * DAY_MS, 7)
        src.now_ms = r + 5 * 60_000
        ranks = rank_universe(src, src.instruments(), r, cfg)
        i = int(np.searchsorted(w.times, r))
        expected = {s: int(k) for s, k in zip(w.symbols, w.rank[i]) if k > 0}
        assert ranks == expected
    # After the volume switch the formerly least liquid symbol leads the ranking.
    assert ranks["C5USDT"] == 1


def test_universe_refresh_is_due_once_per_week(tmp_path):
    cfg = config(tmp_path)
    monday = refresh_instant(1_704_067_200_000 + 60 * DAY_MS, 7)
    assert universe_due(None, monday + 1000, cfg) == monday
    doc = {"universe": {"history": [{"from_ms": monday, "ranks": {"AUSDT": 1}}]}}
    assert universe_due(doc, monday + 6 * DAY_MS, cfg) is None
    assert universe_due(doc, monday + 7 * DAY_MS, cfg) == monday + 7 * DAY_MS


def test_rank_rows_uses_the_refresh_in_force_at_each_bar():
    hist = [{"from_ms": 1000, "ranks": {"A": 1, "B": 2}}, {"from_ms": 2000, "ranks": {"A": 2, "B": 1}}]
    rows = rank_rows(np.array([500, 1000, 1999, 2000, 5000]), ("A", "B", "C"), hist)
    assert rows.tolist() == [[1, 2, 0], [1, 2, 0], [1, 2, 0], [2, 1, 0], [2, 1, 0]]


def test_data_outage_is_soft_then_hard_and_recovers(tmp_path):
    w = world(seed=37)
    cfg = config(tmp_path)
    start = WARM_DAYS * 24 + 3
    store, _, md = run_forward(cfg, w, start, start + 5, minutes=[1])
    src = md.sources[0]
    equity_before = store.load()["engine"]["last_equity"]
    src.fail = True
    codes = []
    for k in range(3):
        src.now_ms = int(w.times[start + 5 + k]) + 60_000
        res = tick(cfg, store, md, now_ms=src.now_ms)
        assert not res.ok
        codes.append(res.exit_code)
    assert codes == [0, 0, 2]                                   # third consecutive failure is hard
    assert store.load()["engine"]["last_equity"] == equity_before
    src.fail = False
    src.now_ms = int(w.times[start + 9]) + 60_000
    res = tick(cfg, store, md, now_ms=src.now_ms)
    assert res.ok and res.bars_processed == 5                   # bars start+4 .. start+8, caught up exactly
    assert store.load()["health"]["consecutive_failures"] == 0


def test_fallback_source_is_used_when_the_primary_is_down(tmp_path):
    w = world(seed=39)
    cfg = config(tmp_path)
    primary, backup = FakeSource(w, "okx"), FakeSource(w, "gate")
    start = WARM_DAYS * 24 + 3
    store, _, md = run_forward(cfg, w, start, start + 4, minutes=[1], sources=[primary, backup])
    primary.fail = True
    backup.now_ms = int(w.times[start + 4]) + 60_000
    res = tick(cfg, store, md, now_ms=backup.now_ms)
    assert res.ok and res.provider == "gate"


def test_fallback_source_cannot_define_the_first_universe(tmp_path):
    w = world(seed=39)
    cfg = config(tmp_path)
    primary, backup = FakeSource(w, "okx"), FakeSource(w, "gate")
    primary.fail = True
    backup.now_ms = int(w.times[WARM_DAYS * 24 + 3]) + 60_000
    store = StateStore(cfg.run.state_dir)
    res = tick(cfg, store, MarketData(cfg, sources=[primary, backup]), now_ms=backup.now_ms)
    assert not res.ok and res.status == "not started"
    assert store.load() is None


def test_cross_check_blocks_trading_on_bad_prices(tmp_path):
    w = world(seed=41)
    cfg = config(tmp_path)
    primary, other = FakeSource(w, "okx"), FakeSource(w, "gate", price_scale=1.10)
    start = WARM_DAYS * 24 + 3
    store = StateStore(cfg.run.state_dir)
    md = MarketData(cfg, sources=[primary, other])
    for k in range(3):
        primary.now_ms = other.now_ms = int(w.times[start + k]) + 60_000
        res = tick(cfg, store, md, now_ms=primary.now_ms)
    assert not res.ok and "sanity check" in res.message
    assert store.load()["engine"]["bars_processed"] == 0


def test_config_change_is_refused_until_accepted(tmp_path):
    w = world(seed=43)
    cfg = config(tmp_path)
    start = WARM_DAYS * 24 + 3
    store, _, md = run_forward(cfg, w, start, start + 3, minutes=[1])
    changed = dataclasses.replace(cfg, risk=dataclasses.replace(cfg.risk, daily_loss_limit=0.05))
    md.sources[0].now_ms = int(w.times[start + 3]) + 60_000
    with pytest.raises(StateError):
        tick(changed, store, md, now_ms=md.sources[0].now_ms)
    assert tick(changed, store, md, now_ms=md.sources[0].now_ms, accept_config_change=True).ok


def test_position_in_a_dead_market_is_closed_with_an_alert(tmp_path):
    w = world(seed=45, swap_day=None)
    cfg = config(tmp_path)
    start = WARM_DAYS * 24 + 3
    store, _, md = run_forward(cfg, w, start, start + 40, minutes=[1])
    src = md.sources[0]
    held = [s for s, q in store.load()["engine"]["qty"].items() if q]
    assert held
    victim = held[0]
    # The market stops printing bars: blank its history from here on.
    j = w.index_of(victim)
    for arr in (w.open, w.high, w.low, w.close):
        arr[start + 40:, j] = np.nan
    events = []
    for k in range(40, 70):
        src.now_ms = int(w.times[start + k]) + 60_000
        res = tick(cfg, store, md, now_ms=src.now_ms)
        assert res.ok
        events += res.events
    assert store.load()["engine"]["qty"][victim] in (0, 0.0, None)
    assert any("no bars for" in e["message"] and victim in e["message"] for e in events)


def test_reports_and_notifications_are_produced(tmp_path):
    w = world(seed=47)
    cfg = config(tmp_path)
    sent = []
    note = Notifier(dataclasses.replace(cfg.notify, on_trade=True), env={"COMPOUNDER_NTFY_TOPIC": "t"},
                    post=lambda *a, **k: sent.append((a, k)) or type("R", (), {"status_code": 200})())
    start = WARM_DAYS * 24 + 3
    store, _, _ = run_forward(cfg, w, start, start + 60, minutes=[1], notifier=note)
    for name in ("STATUS.md", "equity.svg", "dashboard.html", "summary.json", "daily.csv", "equity.csv"):
        assert (store.root / name).exists(), name
    summary = json.loads((store.root / "summary.json").read_text())
    assert summary["days_completed"] >= 2 and summary["status"] in ("running", "day-locked", "day-stopped")
    assert "Daily target" in (store.root / "STATUS.md").read_text()
    assert any("Daily summary" in str(k.get("headers")) for _, k in sent)
    assert any(b"fill(s)" in k.get("data", b"") for _, k in sent)
