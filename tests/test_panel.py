"""Panel construction, funding alignment and point-in-time liquidity ranks."""

from __future__ import annotations

import numpy as np
import pytest

from compounder.data.panel import DAY_MS, HOUR_MS, Panel, build_panel, liquidity_rank, refresh_instant

from conftest import T0, make_panel, random_walk


def klines(t0: int, n: int, price: float = 100.0, vol: float = 1000.0, step: int = HOUR_MS) -> np.ndarray:
    ts = t0 + np.arange(n) * step
    return np.column_stack([ts, np.full(n, price), np.full(n, price * 1.01), np.full(n, price * 0.99),
                            np.full(n, price), np.full(n, 10.0), np.full(n, vol), np.full(n, 5.0),
                            np.full(n, vol / 2)])


def test_build_panel_aligns_symbols_and_marks_missing_bars():
    a = klines(T0, 10)
    b = klines(T0 + 3 * HOUR_MS, 4, price=50.0)
    p = build_panel(HOUR_MS, {"AUSDT": a, "BUSDT": b})
    assert p.symbols == ("AUSDT", "BUSDT")
    assert p.n_bars == 10
    assert np.isnan(p.close[:3, 1]).all() and np.isnan(p.close[7:, 1]).all()
    assert (p.close[3:7, 1] == 50.0).all()
    assert (p.quote_volume[:3, 1] == 0).all()


def test_funding_is_booked_on_the_bar_that_closes_at_settlement():
    a = klines(T0, 24)
    f = np.array([[T0 + 8 * HOUR_MS, 0.0001], [T0 + 16 * HOUR_MS, -0.0002]])
    p = build_panel(HOUR_MS, {"AUSDT": a}, {"AUSDT": f})
    assert p.funding[7, 0] == pytest.approx(0.0001)      # bar 07:00-08:00 closes at 08:00
    assert p.funding[15, 0] == pytest.approx(-0.0002)
    assert np.count_nonzero(p.funding) == 2


def test_explicit_symbols_keep_a_column_without_data():
    p = build_panel(HOUR_MS, {"AUSDT": klines(T0, 5)}, symbols=["AUSDT", "GONEUSDT"])
    assert p.symbols == ("AUSDT", "GONEUSDT")
    assert np.isnan(p.close[:, 1]).all()


def test_panel_save_load_round_trip(tmp_path):
    p = make_panel(random_walk(30, 3))
    p.save(tmp_path / "p.npz")
    q = Panel.load(tmp_path / "p.npz")
    assert q.symbols == p.symbols and np.array_equal(q.times, p.times)
    assert np.allclose(q.close, p.close) and np.array_equal(q.rank, p.rank)


def test_refresh_instant_is_monday_midnight_utc():
    t = int(np.datetime64("2026-11-10T01:00", "ms").astype("int64"))
    r = refresh_instant(t, 7)
    assert str(np.datetime64(r, "ms")) == "2026-11-09T00:00:00.000"
    assert np.datetime64(r, "ms").astype("datetime64[D]").astype(object).weekday() == 0


def test_liquidity_rank_is_point_in_time():
    # B becomes far more liquid than A on day 40; its rank may only change at the next Monday refresh.
    days = 80
    t0 = refresh_instant(T0, 7)
    times = t0 + np.arange(days * 24, dtype="int64") * HOUR_MS
    vol = np.ones((days * 24, 2))
    vol[:, 0] = 100.0
    vol[:, 1] = 50.0
    switch = 40 * 24
    vol[switch:, 1] = 100_000.0
    close = np.full((days * 24, 2), 10.0)
    rank = liquidity_rank(times, vol, close, HOUR_MS, window_days=30, min_history_days=30, refresh_days=7)
    assert (rank[: 30 * 24] == 0).all()                       # not enough history yet
    first_ranked = np.argmax(rank[:, 0] > 0)
    assert times[first_ranked] % (7 * DAY_MS) == refresh_instant(int(times[first_ranked]), 7) % (7 * DAY_MS)
    assert rank[first_ranked].tolist() == [1, 2]
    flipped = np.argmax(rank[:, 1] == 1)
    assert flipped > switch                                   # never before the volume actually changed
    assert int(times[flipped]) == refresh_instant(int(times[flipped]), 7)
    # A rank computed from a prefix of the data equals the full-history rank there (no look-ahead).
    k = flipped + 5
    prefix = liquidity_rank(times[:k], vol[:k], close[:k], HOUR_MS)
    assert np.array_equal(prefix, rank[:k])


def test_liquidity_rank_requires_a_live_market_and_respects_exclusions():
    days = 50
    t0 = refresh_instant(T0, 7)
    times = t0 + np.arange(days * 24, dtype="int64") * HOUR_MS
    vol = np.full((days * 24, 3), 100.0)
    vol[:, 2] = 1000.0
    close = np.full((days * 24, 3), 10.0)
    close[40 * 24:, 1] = np.nan                                # symbol 1 is delisted on day 40
    rank = liquidity_rank(times, vol, close, HOUR_MS, exclude=np.array([False, False, True]))
    assert (rank[:, 2] == 0).all()                             # excluded, however liquid
    assert rank[-1, 1] == 0 and rank[-1, 0] == 1               # the delisted symbol drops out


def test_funding_alignment_tolerates_jitter_and_repeats():
    a = klines(T0, 24)
    f = np.array([[T0 + 8 * HOUR_MS - 30_000, 0.0001],        # stamped half a minute early
                  [T0 + 8 * HOUR_MS, 0.0001],                  # the same settlement repeated
                  [T0 + 16 * HOUR_MS + 4, -0.0002],
                  [T0 + 20 * HOUR_MS + 600_000, 0.0009]])      # ten minutes off the grid: not a settlement
    p = build_panel(HOUR_MS, {"AUSDT": a}, {"AUSDT": f})
    assert p.funding[7, 0] == pytest.approx(0.0001)           # booked once, not twice
    assert p.funding[15, 0] == pytest.approx(-0.0002)
    assert np.count_nonzero(p.funding) == 2
