from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from tradebot.config import Config, load_config
from tradebot.data.candles import make_frame

HOUR = 3_600_000


def synthetic_candles(n: int = 6000, seed: int = 1, start: str = "2021-01-01", drift_cycle: int = 1500,
                      vol: float = 0.008, start_price: float = 30_000.0) -> pd.DataFrame:
    """Deterministic 1h random walk with alternating bull/bear drift regimes."""
    rng = np.random.default_rng(seed)
    t0 = int(pd.Timestamp(start, tz="UTC").value // 1_000_000)
    drift = np.where((np.arange(n) // drift_cycle) % 2 == 0, 0.0004, -0.0003)
    r = drift + vol * rng.standard_normal(n)
    close = start_price * np.exp(np.cumsum(r))
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = np.abs(rng.standard_normal((n, 2))) * vol * close[:, None] * 0.6
    high = np.maximum(open_, close) + wick[:, 0]
    low = np.minimum(open_, close) - wick[:, 1]
    vol_ = rng.uniform(100, 1000, n)
    rows = [(t0 + i * HOUR, open_[i], high[i], low[i], close[i], vol_[i]) for i in range(n)]
    return make_frame(rows)


@pytest.fixture(scope="session")
def candles() -> pd.DataFrame:
    return synthetic_candles()


def make_cfg(strategy: str = "buy_hold", params: dict | None = None, **sections) -> Config:
    cfg = load_config()
    cfg = dataclasses.replace(cfg, strategy=dataclasses.replace(cfg.strategy, name=strategy, params=params or {}))
    for name, overrides in sections.items():
        cfg = dataclasses.replace(cfg, **{name: dataclasses.replace(getattr(cfg, name), **overrides)})
    return cfg


@pytest.fixture
def cfg() -> Config:
    return make_cfg()
