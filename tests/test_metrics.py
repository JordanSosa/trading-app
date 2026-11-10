"""Daily statistics, calendar aggregation and the block bootstrap."""

from __future__ import annotations

import math

import numpy as np
import pytest

from compounder.backtest import quick_returns, to_daily
from compounder.metrics import block_bootstrap, daily_stats, max_drawdown, monthly_returns, ruin_report, yearly_returns

from conftest import T0, make_panel, random_walk

DAY = 86_400_000


def test_two_percent_a_day_is_what_the_arithmetic_says():
    st = daily_stats(np.full(365, 0.02))
    assert st["compound_daily"] == pytest.approx(0.02)
    assert st["total_return"] == pytest.approx(1.02 ** 365 - 1)        # about 1,377x
    assert st["pct_days_at_target"] == 1.0 and st["max_drawdown"] == 0.0


def test_compound_daily_is_the_geometric_mean_not_the_average():
    r = np.array([0.5, -0.5] * 50)
    st = daily_stats(r)
    assert st["mean_daily"] == pytest.approx(0.0)
    assert st["compound_daily"] == pytest.approx(math.sqrt(0.75) - 1)   # -13.4% a day
    assert st["total_return"] == pytest.approx(0.75 ** 50 - 1)


def test_drawdown_and_streaks():
    r = np.array([0.10, -0.20, -0.10, 0.05, 0.40])
    st = daily_stats(r)
    assert st["max_drawdown"] == pytest.approx(1 - 0.8 * 0.9)
    assert st["longest_drawdown_days"] == 3
    assert st["worst_day"] == -0.20 and st["best_day"] == 0.40
    assert max_drawdown(np.array([1.0, 2.0, 1.0, 3.0])) == pytest.approx(0.5)
    assert daily_stats(np.array([]))["days"] == 0


def test_wipe_out_is_flagged():
    st = daily_stats(np.array([0.01, -1.0, 0.01]))
    assert st["wiped_out"] and st["cagr"] == -1.0


def test_calendar_aggregation_assigns_a_day_to_the_date_it_ended():
    ends = np.array([T0 + DAY, T0 + 31 * DAY, T0 + 32 * DAY])      # 2024-01-01, 2024-01-31, 2024-02-01
    rets = np.array([0.10, 0.10, -0.50])
    m = monthly_returns(ends, rets)
    assert m == {"2024-01": pytest.approx(0.21), "2024-02": pytest.approx(-0.5)}
    assert yearly_returns(ends, rets) == {"2024": pytest.approx(1.21 * 0.5 - 1)}


def test_block_bootstrap_preserves_the_distribution_and_is_reproducible():
    rng = np.random.default_rng(3)
    r = rng.normal(0.001, 0.02, 800)
    a = block_bootstrap(r, 365, n_paths=500, block=10, seed=1)
    b = block_bootstrap(r, 365, n_paths=500, block=10, seed=1)
    assert a.shape == (500, 365) and np.array_equal(a, b)
    assert a.mean() == pytest.approx(r.mean(), abs=0.0008)
    assert set(np.unique(a)).issubset(set(r))


def test_ruin_report_orders_risk_sensibly():
    rng = np.random.default_rng(5)
    calm = rng.normal(0.001, 0.01, 1000)
    wild = calm * 6.0
    low, high = ruin_report(calm, 365, n_paths=1000), ruin_report(wild, 365, n_paths=1000)
    assert low["prob_drawdown"]["50%"] < 0.01 < high["prob_drawdown"]["50%"]
    assert high["p95_max_drawdown"] > low["p95_max_drawdown"]
    assert low["final_multiple"]["p05"] < low["final_multiple"]["median"] < low["final_multiple"]["p95"]


def test_quick_returns_charge_costs_and_respect_the_band():
    p = make_panel(random_walk(24 * 30, 3, seed=2))
    rng = np.random.default_rng(1)
    w = 0.3 + rng.normal(0, 0.01, (p.n_bars, 3))                     # targets that jitter by a few percent
    free, _, turn_free = quick_returns(p, w, cost=0.0)
    paid, _, _ = quick_returns(p, w, cost=0.001)
    banded, _, turn_band = quick_returns(p, w, cost=0.001, band=0.25)
    assert paid.sum() < free.sum()
    assert turn_band < turn_free / 10                                 # the band ignores the jitter
    assert banded.sum() > paid.sum()


def test_to_daily_compounds_hourly_returns_into_utc_days():
    p = make_panel(random_walk(72, 1, seed=4))
    r = np.full(72, 0.001)
    daily, ends = to_daily(r, p.times + p.interval_ms)
    assert len(daily) == 3 and (ends % DAY == 0).all()
    assert daily[0] == pytest.approx(1.001 ** 24 - 1)
