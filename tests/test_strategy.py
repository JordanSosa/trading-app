"""Signals and target weights: causality, window stability and sizing."""

from __future__ import annotations

import numpy as np
import pytest

from compounder import signals as sg
from compounder.strategy import StrategyParams, compute_targets

from conftest import make_panel, random_walk

ALL_ON = StrategyParams(universe_n=5, trend_weight=1.0, carry_weight=0.5, reversal_weight=0.5, xsmom_weight=0.5,
                        trend_horizons=(12, 24, 48), vol_window=36, carry_window=24, reversal_lookback=6,
                        reversal_z=1.0, reversal_hold=6, reversal_trend=48, xsmom_lookback=24)


def panel_with_funding(t: int = 600, n: int = 7, seed: int = 21):
    rng = np.random.default_rng(seed)
    funding = np.zeros((t, n))
    funding[7::8] = rng.normal(0.0001, 0.0003, (len(range(7, t, 8)), n))
    rank = np.tile(np.arange(1, n + 1, dtype="int16"), (t, 1))
    rank[300:, [0, n - 1]] = rank[300:, [n - 1, 0]]             # the universe changes mid-way
    return make_panel(random_walk(t, n, vol=0.012, seed=seed), funding=funding, wick=0.002, rank=rank)


@pytest.mark.parametrize("params", [StrategyParams(universe_n=5, trend_horizons=(12, 24, 48), vol_window=36),
                                    StrategyParams(universe_n=5, trend_horizons=(12, 24, 48), vol_window=36,
                                                   trend_response="sign", vol_mode="budget"),
                                    ALL_ON])
def test_weights_do_not_look_ahead(params):
    p = panel_with_funding()
    full = compute_targets(p, params)
    for k in (params.warmup_bars + 5, 350, 480, 599):
        part = compute_targets(p.slice(0, k), params)
        assert np.allclose(part.weights[-1], full.weights[k - 1], rtol=1e-9, atol=1e-12)
        assert np.allclose(part.stop_frac[-1], full.stop_frac[k - 1], rtol=1e-9, atol=1e-12, equal_nan=True)


@pytest.mark.parametrize("params", [StrategyParams(universe_n=5, trend_horizons=(12, 24, 48), vol_window=36), ALL_ON])
def test_rolling_window_reproduces_full_history(params):
    """The forward run sees only the last warmup_bars + 1 bars; it must compute the same weights."""
    p = panel_with_funding()
    full = compute_targets(p, params)
    w = params.warmup_bars
    for k in (w + 1, 300, 301, 450, 600):
        window = compute_targets(p.slice(k - w - 1, k), params)
        assert np.allclose(window.weights[-1], full.weights[k - 1], rtol=1e-7, atol=1e-10), k


def test_future_bars_do_not_change_past_weights():
    p = panel_with_funding()
    params = ALL_ON
    base = compute_targets(p, params).weights.copy()
    shocked_close = p.close.copy()
    shocked_close[400:] *= 3.0
    q = make_panel(shocked_close, list(p.symbols), funding=p.funding, rank=p.rank)
    after = compute_targets(q, params).weights
    assert np.allclose(after[:399], base[:399], rtol=1e-9, atol=1e-12)


def test_only_universe_members_get_weights():
    p = panel_with_funding()
    params = StrategyParams(universe_n=3, trend_horizons=(12, 24, 48), vol_window=36)
    tg = compute_targets(p, params)
    outside = ~((p.rank > 0) & (p.rank <= 3))
    assert (tg.weights[outside] == 0).all()
    assert (np.abs(tg.weights).sum(axis=1)[200:] > 0).mean() > 0.9


def test_target_mode_hits_the_portfolio_volatility_target():
    p = panel_with_funding(t=800, n=6)
    params = StrategyParams(universe_n=6, trend_horizons=(12, 24, 48), vol_window=36, target_vol=0.02, rho=0.6,
                            max_symbol_risk=10.0)
    tg = compute_targets(p, params)
    live = tg.port_vol[200:]
    assert np.allclose(live[live > 0], 0.02, rtol=1e-9)
    assert np.allclose(sg.portfolio_vol(tg.weights, tg.daily_vol, 0.6), tg.port_vol)


def test_budget_mode_never_exceeds_the_cap_and_scales_with_conviction():
    p = panel_with_funding(t=800, n=6)
    params = StrategyParams(universe_n=6, trend_horizons=(12, 24, 48), vol_window=36, vol_mode="budget",
                            risk_budget=0.004, target_vol=0.01)
    tg = compute_targets(p, params)
    assert tg.port_vol.max() <= 0.01 * (1 + 1e-9)
    assert tg.port_vol[200:].std() > 0                      # risk varies with signal strength


def test_short_scale_zero_is_long_only():
    p = panel_with_funding()
    tg = compute_targets(p, StrategyParams(universe_n=5, trend_horizons=(12, 24, 48), vol_window=36, short_scale=0.0))
    assert (tg.weights >= 0).all() and (tg.weights > 0).any()


def test_stop_distance_is_bounded_and_scales_with_volatility():
    p = panel_with_funding()
    params = StrategyParams(universe_n=5, trend_horizons=(12, 24, 48), vol_window=36, stop_vol_mult=3.0,
                            stop_min=0.03, stop_max=0.25)
    tg = compute_targets(p, params)
    s = tg.stop_frac[np.isfinite(tg.stop_frac)]
    assert s.min() >= 0.03 and s.max() <= 0.25
    off = compute_targets(p, StrategyParams(universe_n=5, trend_horizons=(12, 24, 48), vol_window=36,
                                            stop_vol_mult=0.0))
    assert np.isnan(off.stop_frac).all()


def test_trend_signal_follows_a_clean_trend():
    t = 400
    up = 100.0 * np.exp(np.cumsum(np.full(t, 0.002) + np.random.default_rng(1).normal(0, 0.003, t)))
    dn = 100.0 * np.exp(np.cumsum(np.full(t, -0.002) + np.random.default_rng(2).normal(0, 0.003, t)))
    p = make_panel(np.column_stack([up, dn]))
    tg = compute_targets(p, StrategyParams(universe_n=2, trend_horizons=(12, 24, 48), vol_window=36))
    assert (tg.weights[200:, 0] > 0).all() and (tg.weights[200:, 1] < 0).all()


def test_carry_signal_opposes_funding():
    t, n = 300, 2
    funding = np.zeros((t, n))
    funding[7::8, 0] = 0.001        # longs pay heavily on symbol 0
    funding[7::8, 1] = -0.001       # shorts pay heavily on symbol 1
    p = make_panel(random_walk(t, n, seed=4), funding=funding)
    tg = compute_targets(p, StrategyParams(universe_n=2, trend_weight=0.0, carry_weight=1.0, carry_window=48,
                                           vol_window=36))
    assert (tg.weights[150:, 0] < 0).all() and (tg.weights[150:, 1] > 0).all()


def test_cross_rank_and_helpers():
    x = np.array([[1.0, 3.0, 2.0, np.nan], [5.0, 4.0, np.nan, np.nan]])
    mask = np.array([[True, True, True, True], [True, True, True, True]])
    r = sg.cross_rank(x, mask)
    assert r[0].tolist() == [-1.0, 1.0, 0.0, 0.0]
    assert r[1].tolist() == [0.0, 0.0, 0.0, 0.0]            # fewer than three names: no ranking
    latch = sg.hold_latch(np.array([[0], [1], [0], [0], [0]], dtype=bool), 3)
    assert latch[:, 0].tolist() == [False, True, True, True, False]
    pv = sg.portfolio_vol(np.array([[1.0, -1.0]]), np.array([[0.02, 0.02]]), 1.0)
    assert pv[0] == pytest.approx(0.0)                      # perfectly correlated, fully hedged
    pv = sg.portfolio_vol(np.array([[1.0, 1.0]]), np.array([[0.02, 0.02]]), 0.0)
    assert pv[0] == pytest.approx(0.02 * np.sqrt(2))


def test_parameter_validation():
    with pytest.raises(ValueError):
        StrategyParams(trend_response="cubic")
    with pytest.raises(ValueError):
        StrategyParams(vol_mode="other")
    with pytest.raises(ValueError):
        StrategyParams(rho=1.5)
