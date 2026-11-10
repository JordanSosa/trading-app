"""Shared fixtures: deterministic synthetic panels (no network, no data files)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from compounder.data.panel import HOUR_MS, Panel  # noqa: E402
from compounder.engine import EngineParams  # noqa: E402

T0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC


def make_panel(close: np.ndarray, symbols: list[str] | None = None, funding: np.ndarray | None = None,
               wick: float = 0.0, t0: int = T0, rank: np.ndarray | None = None) -> Panel:
    """Panel from a (T, N) close matrix; open = previous close, high/low widened by ``wick``."""
    close = np.asarray(close, dtype="float64")
    if close.ndim == 1:
        close = close[:, None]
    t, n = close.shape
    op = np.vstack([close[:1], close[:-1]])
    hi = np.maximum(op, close) * (1.0 + wick)
    lo = np.minimum(op, close) * (1.0 - wick)
    symbols = symbols or [f"S{i}USDT" for i in range(n)]
    if rank is None:
        rank = np.tile(np.arange(1, n + 1, dtype="int16"), (t, 1))
    return Panel(HOUR_MS, t0 + np.arange(t, dtype="int64") * HOUR_MS, tuple(symbols), op, hi, lo, close,
                 np.full((t, n), 1e6), np.full((t, n), 5e5), np.zeros((t, n)) if funding is None else funding, rank)


def random_walk(t: int, n: int, vol: float = 0.01, drift: float = 0.0, seed: int = 1,
                start: float = 100.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(drift, vol, (t, n)), axis=0))


def free_params(**kw) -> EngineParams:
    """Frictionless engine with every governor rule off unless overridden."""
    base = dict(fee_rate=0.0, slippage=0.0, stop_slippage=0.0, stop_overshoot=0.0, min_notional=0.0,
                min_position_mult=1.0, max_gross=10.0, max_weight=10.0, rebalance_band=0.0, daily_target=None,
                target_mode="off", daily_loss_limit=None, dd_soft=1.0, dd_hard=1.0, kill_dd=None)
    base.update(kw)
    return EngineParams(**base)


@pytest.fixture
def walk_panel() -> Panel:
    return make_panel(random_walk(24 * 40, 6, seed=3))
