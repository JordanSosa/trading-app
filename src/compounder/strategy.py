"""The trading strategy: volatility-scaled, multi-asset signals on liquid perpetuals.

``compute_targets`` turns a candle panel into target weights (signed fractions
of equity) for every bar. Everything is causal and uses finite windows, so the
weights at bar ``t`` computed from the last ``warmup_bars + 1`` bars equal the
weights computed from the full history.

Signal components, each scaled to [-1, 1] per symbol:

* ``trend``     risk-adjusted time-series momentum averaged over several horizons
* ``carry``     against the funding rate (short what longs pay heavily for, long what shorts pay for)
* ``reversal``  fade a sharp short-term move that goes against the prevailing trend
* ``xsmom``     cross-sectional momentum: long the strongest, short the weakest, market-neutral

The combined score is converted to weights by inverse-volatility sizing and
then scaled so the *portfolio's* expected daily volatility equals the risk
target (or stays below it), using a constant-correlation model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from . import signals as sg
from .data.panel import Panel

BARS_PER_DAY = 24


@dataclass(frozen=True)
class StrategyParams:
    # --- universe ---------------------------------------------------------------
    universe_n: int = 20                 # trade the N most liquid crypto perpetuals (point-in-time)
    # --- volatility ---------------------------------------------------------------
    vol_window: int = 336                # bars used for each symbol's volatility (14 days)
    vol_floor: float = 0.01              # floor on a symbol's daily volatility (avoids oversized weights)
    # --- trend ----------------------------------------------------------------------
    trend_weight: float = 1.0
    trend_horizons: tuple[int, ...] = (48, 96, 192, 384, 768)   # bars (2 .. 32 days)
    trend_response: str = "linear"       # "linear": clip(z, -1, 1); "sign": sign(z)
    trend_z_scale: float = 1.0           # z-score at which the linear response saturates
    # --- carry ----------------------------------------------------------------------
    carry_weight: float = 0.0
    carry_window: int = 168              # bars of funding history averaged (7 days)
    carry_scale: float = 0.0005          # mean funding per 8h at which the signal saturates (0.05%)
    # --- reversal -------------------------------------------------------------------
    reversal_weight: float = 0.0
    reversal_lookback: int = 24          # bars of the move being faded
    reversal_z: float = 2.0              # size of the move (in sigmas) that triggers
    reversal_hold: int = 24              # bars the position is held after the last trigger
    reversal_trend: int = 768            # bars defining the prevailing trend (0 = unconditional)
    # --- cross-sectional momentum -----------------------------------------------------
    xsmom_weight: float = 0.0
    xsmom_lookback: int = 336
    # --- portfolio --------------------------------------------------------------------
    short_scale: float = 1.0             # multiply short weights by this (0 = long only)
    long_scale: float = 1.0
    target_vol: float = 0.02             # portfolio daily volatility target (or cap)
    vol_mode: str = "target"             # "target": always scale to target_vol; "budget": fixed budget, capped
    risk_budget: float = 0.004           # budget mode: daily risk per symbol at full signal
    rho: float = 0.7                     # assumed pairwise correlation between symbols
    max_symbol_risk: float = 0.5         # target mode: cap on one symbol's share of the risk target
    stop_vol_mult: float = 3.0           # protective stop distance in daily volatilities (0 = no stop)
    stop_min: float = 0.03               # bounds on the stop distance as a fraction of price
    stop_max: float = 0.25

    def __post_init__(self) -> None:
        if self.trend_response not in ("linear", "sign"):
            raise ValueError("trend_response must be 'linear' or 'sign'")
        if self.vol_mode not in ("target", "budget"):
            raise ValueError("vol_mode must be 'target' or 'budget'")
        if self.universe_n < 1:
            raise ValueError("universe_n must be >= 1")
        if not 0 <= self.rho <= 1:
            raise ValueError("rho must be in [0, 1]")
        object.__setattr__(self, "trend_horizons", tuple(int(h) for h in self.trend_horizons))

    @property
    def warmup_bars(self) -> int:
        """Bars of history needed before the first valid weight (longest window + 1)."""
        need = [self.vol_window + 1]
        if self.trend_weight:
            need.append(max(self.trend_horizons) + 1)
        if self.carry_weight:
            need.append(self.carry_window + 1)
        if self.reversal_weight:
            need.append(self.reversal_lookback + self.reversal_hold + 1)
            need.append(self.reversal_trend + self.reversal_hold + 1)
        if self.xsmom_weight:
            need.append(self.xsmom_lookback + 1)
        return int(max(need)) + self.vol_window

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trend_horizons"] = list(self.trend_horizons)
        return d


@dataclass
class Targets:
    weights: np.ndarray        # (T, N) target weights before the risk governor
    stop_frac: np.ndarray      # (T, N) protective stop distance as a fraction of price (NaN = none)
    member: np.ndarray         # (T, N) bool: symbol is in the tradable universe at that bar
    daily_vol: np.ndarray      # (T, N) each symbol's daily volatility estimate
    score: np.ndarray          # (T, N) combined signal in [-1, 1]
    port_vol: np.ndarray       # (T,) ex-ante portfolio daily volatility of the final weights
    parts: dict[str, np.ndarray] = field(default_factory=dict)


def universe_mask(panel: Panel, n: int) -> np.ndarray:
    """Symbols in the top ``n`` by point-in-time liquidity that have a bar right now."""
    return (panel.rank > 0) & (panel.rank <= n) & np.isfinite(panel.close)


def trend_score(close: np.ndarray, vol: np.ndarray, p: StrategyParams) -> np.ndarray:
    acc = np.zeros_like(close)
    cnt = np.zeros_like(close)
    for h in p.trend_horizons:
        z = sg.momentum_z(close, vol, h)
        if p.trend_response == "sign":
            s = np.sign(z)
        else:
            s = np.clip(z / p.trend_z_scale, -1.0, 1.0)
        ok = np.isfinite(s)
        acc += np.where(ok, s, 0.0)
        cnt += ok
    # Require every horizon: a symbol with too little history has no trend signal yet.
    return np.where(cnt == len(p.trend_horizons), acc / max(1, len(p.trend_horizons)), 0.0)


def carry_score(funding: np.ndarray, p: StrategyParams) -> np.ndarray:
    """-1 .. +1: positive when shorts pay longs (negative funding), negative when longs pay."""
    per_bar = sg.rolling_mean(funding, p.carry_window, max(2, int(p.carry_window * 0.8)))
    per_8h = per_bar * 8.0
    return -np.clip(np.nan_to_num(per_8h) / p.carry_scale, -1.0, 1.0)


def reversal_score(close: np.ndarray, vol: np.ndarray, p: StrategyParams) -> np.ndarray:
    z = sg.momentum_z(close, vol, p.reversal_lookback)
    if p.reversal_trend > 0:
        trend = np.sign(np.nan_to_num(sg.momentum_z(close, vol, p.reversal_trend)))
        dip = (z <= -p.reversal_z) & (trend > 0)          # sharp drop inside an uptrend -> buy
        rip = (z >= p.reversal_z) & (trend < 0)           # sharp pop inside a downtrend -> sell
    else:
        dip = z <= -p.reversal_z
        rip = z >= p.reversal_z
    long_ = sg.hold_latch(dip, p.reversal_hold)
    short_ = sg.hold_latch(rip, p.reversal_hold)
    return long_.astype("float64") - short_.astype("float64")


def xsmom_score(close: np.ndarray, vol: np.ndarray, member: np.ndarray, p: StrategyParams) -> np.ndarray:
    z = sg.momentum_z(close, vol, p.xsmom_lookback)
    return sg.cross_rank(z, member)


def compute_targets(panel: Panel, p: StrategyParams, keep_parts: bool = False) -> Targets:
    close = panel.close
    hvol = sg.realised_vol(close, p.vol_window)
    dvol = np.maximum(hvol * np.sqrt(BARS_PER_DAY), p.vol_floor)
    member = universe_mask(panel, p.universe_n) & np.isfinite(hvol) & (hvol > 0)

    parts: dict[str, np.ndarray] = {}
    score = np.zeros_like(close)
    total = 0.0
    if p.trend_weight:
        parts["trend"] = trend_score(close, hvol, p)
        score += p.trend_weight * parts["trend"]
        total += abs(p.trend_weight)
    if p.carry_weight:
        parts["carry"] = carry_score(panel.funding, p)
        score += p.carry_weight * parts["carry"]
        total += abs(p.carry_weight)
    if p.reversal_weight:
        parts["reversal"] = reversal_score(close, hvol, p)
        score += p.reversal_weight * parts["reversal"]
        total += abs(p.reversal_weight)
    if p.xsmom_weight:
        parts["xsmom"] = xsmom_score(close, hvol, member, p)
        score += p.xsmom_weight * parts["xsmom"]
        total += abs(p.xsmom_weight)
    if total > 0:
        score = np.clip(score / total, -1.0, 1.0)
    score = np.where(member, score, 0.0)
    score = np.where(score > 0, score * p.long_scale, score * p.short_scale)

    with np.errstate(invalid="ignore", divide="ignore"):
        raw = np.where(member, score / dvol, 0.0)           # risk-parity: weight ~ score / volatility
    raw = np.nan_to_num(raw)
    # ``raw * sigma`` equals the score, so ``pv`` is the portfolio's volatility per unit of
    # "risk per symbol at full score". ``unit`` below is that per-symbol risk.
    pv = sg.portfolio_vol(raw, dvol, p.rho)
    if p.vol_mode == "budget":
        # Fixed risk per unit of score: conviction (how many symbols agree, how strongly)
        # drives total risk, capped at the portfolio target.
        with np.errstate(invalid="ignore", divide="ignore"):
            unit = np.where(pv * p.risk_budget > p.target_vol, p.target_vol / pv, p.risk_budget)
    else:
        # Constant portfolio volatility, but never let one symbol carry more than
        # max_symbol_risk of the target on its own.
        with np.errstate(invalid="ignore", divide="ignore"):
            unit = np.where(pv > 0, p.target_vol / pv, 0.0)
        unit = np.minimum(unit, p.max_symbol_risk * p.target_vol)
    weights = raw * unit[:, None]
    port_vol = sg.portfolio_vol(weights, dvol, p.rho)

    if p.stop_vol_mult > 0:
        stop = np.clip(p.stop_vol_mult * dvol, p.stop_min, p.stop_max)
    else:
        stop = np.full_like(close, np.nan)
    return Targets(weights=weights, stop_frac=stop, member=member, daily_vol=dvol, score=score,
                   port_vol=port_vol, parts=parts if keep_parts else {})
